"""Central OT-2 connection configuration, discovery, and diagnostics.

All robot-facing code should use this module rather than embedding an address,
port, or robot URL.  A candidate is never persisted unless its ``/health``
response identifies the configured robot serial.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
from datetime import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from threading import Event
from typing import Any, Iterable

import requests
import yaml


REPO = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO / "configs" / "robot.yaml"
HEADERS = {"opentrons-version": "*"}
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_MAC_RE = re.compile(
    r"\b[0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5}\b"
)
_LAST_RESOLUTION: dict[str, dict[str, str]] = {}


@dataclass(frozen=True)
class DiagnosticStep:
    name: str
    passed: bool
    detail: str
    hard_error: bool = False


def _path(path: str | os.PathLike[str]) -> Path:
    value = Path(path)
    return value if value.is_absolute() else REPO / value


def load_robot_config(path: str = "configs/robot.yaml") -> dict:
    """Load and minimally validate the centralized robot connection config."""
    config_path = _path(path)
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Could not load robot config {config_path}: {exc}") from exc
    robot = loaded.get("robot")
    if not isinstance(robot, dict):
        raise RuntimeError(f"{config_path} is missing the robot block")
    for key in ("name", "serial", "mdns_host", "http_port", "ssh_port"):
        if robot.get(key) in (None, ""):
            raise RuntimeError(f"{config_path} robot.{key} is required")
    return loaded


def _api_version(value: Any) -> str:
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(isinstance(part, int) for part in value)
    ):
        return f"{value[0]}.{value[1]}"
    if isinstance(value, str) and re.fullmatch(r"\d+\.\d+", value):
        return value
    raise RuntimeError(f"Invalid protocol API version in /health: {value!r}")


def _resolved_ipv4(host: str) -> str:
    return socket.gethostbyname(host)


def base_url(host: str | None = None) -> str:
    """Return the configured robot-server base URL."""
    config = load_robot_config()
    selected = host or resolve_host()
    return f"http://{selected}:{int(config['robot']['http_port'])}"


def health(host: str | None = None, timeout: float = 2.0) -> dict:
    """Fetch and decode the robot ``/health`` response."""
    selected = host or resolve_host()
    url = f"{base_url(selected)}/health"
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=(timeout, timeout),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"GET {url} returned a non-object JSON response")
    return payload


def verify_host(
    host: str,
    *,
    timeout: float = 2.0,
    config: dict | None = None,
) -> tuple[str, dict]:
    """Verify an address by HTTP status and exact configured robot serial."""
    loaded = config or load_robot_config()
    ip = _resolved_ipv4(host)
    payload = health(ip, timeout=timeout)
    expected = str(loaded["robot"]["serial"])
    actual = str(payload.get("robot_serial", ""))
    if actual != expected:
        raise RuntimeError(
            f"{ip} answered /health as serial {actual or '(missing)'}, "
            f"expected {expected}"
        )
    return ip, payload


def _normal_mac(value: str) -> str:
    return value.lower().replace("-", ":")


def parse_arp_entries(output: str) -> list[tuple[str, str]]:
    """Parse IPv4/MAC pairs from Windows, macOS, or Linux ``arp -a`` output."""
    entries: list[tuple[str, str]] = []
    for line in output.splitlines():
        ips = _IPV4_RE.findall(line)
        macs = _MAC_RE.findall(line)
        if not ips or not macs:
            continue
        try:
            ip = str(ipaddress.IPv4Address(ips[0]))
        except ipaddress.AddressValueError:
            continue
        entries.append((ip, _normal_mac(macs[0])))
    return entries


def arp_entries() -> tuple[list[tuple[str, str]], str]:
    """Read the host ARP table without depending on PowerShell."""
    try:
        result = subprocess.run(
            ["arp", "-a"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"arp -a failed: {exc}"
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        return parse_arp_entries(output), (
            f"arp -a exited {result.returncode}: {output.strip() or '(no output)'}"
        )
    return parse_arp_entries(output), "arp -a completed"


def _allowed_arp_candidates(config: dict) -> tuple[list[str], str]:
    allowlist = tuple(
        _normal_mac(str(item)).rstrip(":")
        for item in config["robot"].get("mac_oui_allowlist", [])
    )
    entries, detail = arp_entries()
    matches = sorted(
        {
            ip
            for ip, mac in entries
            if any(mac.startswith(prefix + ":") or mac == prefix for prefix in allowlist)
        }
    )
    return matches, detail


def _zeroconf_candidates(timeout: float) -> tuple[list[str], str]:
    """Optionally browse HTTP services and collect their IPv4 addresses."""
    try:
        from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf
    except ImportError:
        return [], "zeroconf package not installed (optional)"

    addresses: set[str] = set()
    done = Event()

    class Listener(ServiceListener):
        def add_service(self, zc: Any, type_: str, name: str) -> None:
            info: ServiceInfo | None = zc.get_service_info(
                type_, name, timeout=max(100, int(timeout * 1000))
            )
            if info:
                addresses.update(info.parsed_scoped_addresses())

        def update_service(self, zc: Any, type_: str, name: str) -> None:
            self.add_service(zc, type_, name)

        def remove_service(self, zc: Any, type_: str, name: str) -> None:
            return None

    zc = Zeroconf()
    browser = None
    try:
        browser = ServiceBrowser(zc, "_http._tcp.local.", Listener())
        done.wait(timeout)
    except Exception as exc:  # optional best-effort route
        return [], f"zeroconf browse failed: {exc}"
    finally:
        if browser is not None:
            browser.cancel()
        zc.close()
    return sorted(addresses), (
        f"zeroconf returned {len(addresses)} IPv4 candidate(s)"
    )


def discover_robot(
    *,
    timeout: float = 2.0,
    config_path: str = "configs/robot.yaml",
) -> dict:
    """Discover and verify the configured robot without sweeping the link-local /16."""
    config = load_robot_config(config_path)
    attempts: list[str] = []
    seen: set[str] = set()

    def try_candidate(candidate: str, method: str) -> dict | None:
        if not candidate or candidate in seen:
            return None
        seen.add(candidate)
        try:
            ip, payload = verify_host(candidate, timeout=timeout, config=config)
        except (OSError, RuntimeError) as exc:
            attempts.append(f"{method}: {candidate} -> {exc}")
            return None
        attempts.append(f"{method}: {candidate} -> verified {ip}")
        return {
            "ip": ip,
            "host": candidate,
            "method": method,
            "health": payload,
            "attempts": attempts,
        }

    mdns = str(config["robot"]["mdns_host"])
    try:
        mdns_ip = _resolved_ipv4(mdns)
    except OSError as exc:
        attempts.append(f"mdns: {mdns} -> resolution failed: {exc}")
    else:
        result = try_candidate(mdns_ip, "mdns")
        if result:
            result["host"] = mdns
            return result

    zc_addresses, zc_detail = _zeroconf_candidates(timeout)
    attempts.append(f"zeroconf: {zc_detail}")
    for candidate in zc_addresses:
        result = try_candidate(candidate, "zeroconf")
        if result:
            return result

    last_ip = str((config.get("discovered") or {}).get("ip") or "")
    result = try_candidate(last_ip, "last-known")
    if result:
        return result

    arp_candidates, arp_detail = _allowed_arp_candidates(config)
    attempts.append(
        f"arp: {arp_detail}; allowlisted candidates: "
        f"{', '.join(arp_candidates) if arp_candidates else '(none)'}"
    )
    for candidate in arp_candidates:
        result = try_candidate(candidate, "arp")
        if result:
            return result

    details = "\n  ".join(attempts)
    raise RuntimeError(
        "Could not discover and verify the configured OT-2. Methods tried:\n"
        f"  {details}"
    )


def _replace_top_level_block(text: str, name: str, replacement: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(name)}:\s*(?:#.*)?$")
    match = pattern.search(text)
    if not match:
        separator = "" if not text or text.endswith("\n\n") else "\n"
        return f"{text.rstrip()}{separator}\n{replacement.rstrip()}\n"
    next_block = re.search(r"(?m)^[A-Za-z_][A-Za-z0-9_-]*:\s*(?:#.*)?$", text[match.end():])
    end = match.end() + (next_block.start() if next_block else len(text[match.end():]))
    return text[:match.start()] + replacement.rstrip() + "\n\n" + text[end:].lstrip("\r\n")


def _quoted(value: Any) -> str:
    return json.dumps(str(value))


def write_discovery(
    ip: str,
    method: str,
    payload: dict,
    *,
    config_path: str = "configs/robot.yaml",
    discovered_at: str | None = None,
) -> None:
    """Rewrite only generated discovery/capability blocks in robot.yaml."""
    path = _path(config_path)
    text = path.read_text(encoding="utf-8")
    timestamp = discovered_at or datetime.now().astimezone().isoformat(timespec="seconds")
    discovered = "\n".join(
        (
            "discovered:",
            f"  ip: {ip}",
            f"  method: {method}",
            f"  discovered_at: {_quoted(timestamp)}",
        )
    )
    capabilities = "\n".join(
        (
            "capabilities:",
            f"  robot_software: {_quoted(payload.get('api_version', ''))}",
            f"  system_version: {_quoted(payload.get('system_version', ''))}",
            "  max_protocol_api_version: "
            + _quoted(_api_version(payload.get("maximum_protocol_api_version"))),
            "  min_protocol_api_version: "
            + _quoted(_api_version(payload.get("minimum_protocol_api_version"))),
        )
    )
    updated = _replace_top_level_block(text, "discovered", discovered)
    updated = _replace_top_level_block(updated, "capabilities", capabilities)
    path.write_text(updated.rstrip() + "\n", encoding="utf-8")


def _remember(host: str, ip: str, method: str) -> None:
    _LAST_RESOLUTION[host] = {"host": host, "ip": ip, "method": method}


def resolve_host(cli_override: str | None = None, allow_discovery: bool = True) -> str:
    """Resolve the robot host using CLI, environment, mDNS, cache, then discovery."""
    if cli_override:
        host = cli_override.strip()
        _remember(host, _safe_resolve(host), "command line")
        return host
    env_host = os.environ.get("OT2_ROBOT_HOST", "").strip()
    if env_host:
        _remember(env_host, _safe_resolve(env_host), "OT2_ROBOT_HOST")
        return env_host

    config = load_robot_config()
    mdns = str(config["robot"]["mdns_host"])
    try:
        ip, _ = verify_host(mdns, config=config)
    except (OSError, RuntimeError):
        pass
    else:
        _remember(mdns, ip, "mDNS")
        return mdns

    last_ip = str((config.get("discovered") or {}).get("ip") or "")
    if last_ip:
        try:
            ip, _ = verify_host(last_ip, config=config)
        except (OSError, RuntimeError):
            pass
        else:
            _remember(last_ip, ip, "last known IP")
            return last_ip

    if allow_discovery:
        try:
            result = discover_robot()
        except RuntimeError as exc:
            raise RuntimeError(
                f"{exc}\nRun 'python scripts/find_robot.py --check' for diagnostics."
            ) from exc
        write_discovery(result["ip"], result["method"], result["health"])
        host = mdns if result["method"] == "mdns" else result["ip"]
        _remember(host, result["ip"], result["method"])
        return host

    raise RuntimeError(
        "No configured robot host verified. "
        "Run 'python scripts/find_robot.py --check' for diagnostics."
    )


def _safe_resolve(host: str) -> str:
    try:
        return _resolved_ipv4(host)
    except OSError:
        return host


def connection_summary(host: str) -> str:
    """Describe the selected name, resolved address, and selection route."""
    details = _LAST_RESOLUTION.get(host)
    if details is None:
        details = {"host": host, "ip": _safe_resolve(host), "method": "provided host"}
    name = str(load_robot_config()["robot"]["name"])
    shown = (
        f"{details['host']} ({details['ip']})"
        if details["host"] != details["ip"]
        else details["ip"]
    )
    return f"Robot: {name} at {shown} via {details['method']}"


def add_robot_host_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the preferred host flag and a backward-compatible hidden IP alias."""
    parser.add_argument(
        "--robot-host",
        dest="robot_host",
        default=None,
        help=(
            "Robot hostname or address. Default: OT2_ROBOT_HOST, then verified "
            "mDNS/config/discovery."
        ),
    )
    parser.add_argument(
        "--robot-ip",
        dest="robot_host",
        help=argparse.SUPPRESS,
    )


def max_api_level(refresh: bool = False) -> str:
    """Return the robot's cached or freshly verified maximum protocol API level."""
    config = load_robot_config()
    cached = str(
        (config.get("capabilities") or {}).get("max_protocol_api_version") or ""
    )
    if cached and not refresh:
        return _api_version(cached)

    payload = refresh_robot_config()
    return _api_version(payload.get("maximum_protocol_api_version"))


def refresh_robot_config(host: str | None = None) -> dict:
    """Verify a robot and refresh only the generated config blocks from /health."""
    config = load_robot_config()
    selected = host or resolve_host()
    ip, payload = verify_host(selected, config=config)
    method = _LAST_RESOLUTION.get(selected, {}).get("method", "health refresh")
    write_discovery(ip, method, payload)
    return payload


def _run_text(command: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return result.returncode, "\n".join(
        part for part in (result.stdout, result.stderr) if part
    )


def _local_link_local_addresses() -> tuple[list[str], str]:
    command = ["ipconfig"] if sys.platform == "win32" else ["ip", "-o", "-4", "addr"]
    code, output = _run_text(command)
    addresses = sorted(
        {
            match
            for match in _IPV4_RE.findall(output)
            if match.startswith("169.254.")
        }
    )
    return addresses, (
        f"{command[0]} exited {code}; found {len(addresses)} link-local address(es)"
    )


def _ethernet_up() -> tuple[bool, str]:
    if sys.platform == "win32":
        code, output = _run_text(["netsh", "interface", "show", "interface"])
        rows = [
            line.strip()
            for line in output.splitlines()
            if re.search(r"\bEthernet\s*$", line, re.IGNORECASE)
        ]
        up = any(
            re.search(r"\bConnected\b", row, re.IGNORECASE) for row in rows
        )
        return up, (
            rows[0] if rows else f"Ethernet row not found (netsh exit {code})"
        )
    code, output = _run_text(["ip", "-o", "link", "show", "dev", "eth0"])
    return code == 0 and "UP" in output, output.strip() or f"ip exited {code}"


def _tcp_open(host: str | None, port: int, timeout: float) -> tuple[bool, str]:
    if not host:
        return False, "not attempted: mDNS did not resolve"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} accepted a TCP connection"
    except OSError as exc:
        return False, f"{host}:{port} failed: {exc}"


def run_diagnostics(
    *,
    timeout: float = 2.0,
    config_path: str = "configs/robot.yaml",
) -> list[DiagnosticStep]:
    """Run the complete physical/network debugging sequence without writing config."""
    config = load_robot_config(config_path)
    robot = config["robot"]
    steps: list[DiagnosticStep] = []

    adapter_up, adapter_detail = _ethernet_up()
    steps.append(DiagnosticStep("Ethernet adapter is Up", adapter_up, adapter_detail))

    local_ips, local_detail = _local_link_local_addresses()
    steps.append(
        DiagnosticStep(
            "Local link-local address",
            bool(local_ips),
            f"{local_detail}: {', '.join(local_ips) if local_ips else '(none)'}",
        )
    )

    mdns = str(robot["mdns_host"])
    resolved: str | None = None
    try:
        resolved = _resolved_ipv4(mdns)
        mdns_detail = f"{mdns} -> {resolved}"
        mdns_ok = True
    except OSError as exc:
        mdns_detail = f"{mdns} did not resolve: {exc}"
        mdns_ok = False
    steps.append(DiagnosticStep("mDNS resolution", mdns_ok, mdns_detail))

    if resolved:
        command = (
            ["ping", "-n", "1", "-w", str(max(1, int(timeout * 1000))), resolved]
            if sys.platform == "win32"
            else ["ping", "-c", "1", "-W", str(max(1, int(timeout))), resolved]
        )
        ping_code, ping_output = _run_text(command, timeout=timeout + 2)
        ping_ok = ping_code == 0
        unreachable = re.search(
            r"Reply from\s+(" + _IPV4_RE.pattern + r"):\s+Destination host unreachable",
            ping_output,
            re.IGNORECASE,
        )
        if unreachable and unreachable.group(1) in local_ips:
            ping_detail = (
                f"Your own address {unreachable.group(1)} reported Destination host "
                "unreachable. The robot did not reply; ARP resolution failed."
            )
        else:
            ping_detail = (
                f"ping {'passed' if ping_ok else 'failed'} for {resolved}"
            )
    else:
        ping_ok = False
        ping_detail = "not attempted: mDNS did not resolve"
    steps.append(DiagnosticStep("ICMP ping", ping_ok, ping_detail))

    entries, arp_detail = arp_entries()
    allowlist = tuple(
        _normal_mac(str(item)).rstrip(":")
        for item in robot.get("mac_oui_allowlist", [])
    )
    matches = [
        (ip, mac)
        for ip, mac in entries
        if any(mac.startswith(prefix + ":") or mac == prefix for prefix in allowlist)
    ]
    steps.append(
        DiagnosticStep(
            "Allowlisted robot ARP entry",
            bool(matches),
            (
                ", ".join(f"{ip} {mac}" for ip, mac in matches)
                if matches
                else f"{arp_detail}; no allowed OUI found. Entry may be stale; "
                "from an elevated shell try: arp -d *"
            ),
        )
    )

    probe_host = resolved or (matches[0][0] if matches else None)
    http_ok, http_detail = _tcp_open(
        probe_host, int(robot["http_port"]), timeout
    )
    steps.append(DiagnosticStep("TCP robot API port", http_ok, http_detail))

    payload: dict | None = None
    if probe_host:
        try:
            _, payload = verify_host(probe_host, timeout=timeout, config=config)
            health_ok = True
            health_detail = (
                f"/health serial matches {robot['serial']} at {probe_host}"
            )
        except (OSError, RuntimeError) as exc:
            health_ok = False
            health_detail = str(exc)
    else:
        health_ok = False
        health_detail = "not attempted: no mDNS or ARP candidate"
    steps.append(DiagnosticStep("/health robot identity", health_ok, health_detail))

    cached = str(
        (config.get("capabilities") or {}).get("max_protocol_api_version") or ""
    )
    if payload:
        try:
            actual = _api_version(payload.get("maximum_protocol_api_version"))
            api_ok = actual == cached
            api_detail = f"live maximum {actual}; cached maximum {cached or '(missing)'}"
        except RuntimeError as exc:
            api_ok = False
            api_detail = str(exc)
    else:
        api_ok = False
        api_detail = "not checked: matching /health response unavailable"
    steps.append(
        DiagnosticStep(
            "Protocol API capability",
            api_ok,
            api_detail,
            hard_error=not api_ok,
        )
    )

    ssh_ok, ssh_detail = _tcp_open(probe_host, int(robot["ssh_port"]), timeout)
    steps.append(DiagnosticStep("TCP SSH port", ssh_ok, ssh_detail))
    return steps


def format_diagnostics(steps: Iterable[DiagnosticStep]) -> str:
    rows = list(steps)
    width = max(len(step.name) for step in rows)
    lines = []
    for step in rows:
        status = "PASS" if step.passed else "FAIL"
        marker = " [HARD ERROR]" if step.hard_error and not step.passed else ""
        lines.append(f"[{status}] {step.name:<{width}}  {step.detail}{marker}")
    return "\n".join(lines)
