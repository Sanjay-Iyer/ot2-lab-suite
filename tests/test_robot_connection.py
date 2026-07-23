from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket

import pytest

from src.lab import robot_connection as connection


ROBOT = {
    "robot": {
        "name": "OT2CEP20220929R02",
        "serial": "OT2CEP20220929R02",
        "mdns_host": "OT2CEP20220929R02.local",
        "http_port": 31950,
        "ssh_port": 22,
        "mac_oui_allowlist": ["b8:27:eb"],
    },
    "discovered": {"ip": "169.254.252.252", "method": "mdns"},
    "capabilities": {
        "max_protocol_api_version": "2.15",
        "min_protocol_api_version": "2.0",
    },
}
HEALTH = {
    "robot_serial": "OT2CEP20220929R02",
    "api_version": "7.0.2",
    "system_version": "v1.15.1",
    "maximum_protocol_api_version": [2, 15],
    "minimum_protocol_api_version": [2, 0],
}


def test_committed_robot_config_has_ground_truth() -> None:
    loaded = connection.load_robot_config()
    assert loaded["robot"] == ROBOT["robot"]
    assert loaded["capabilities"]["max_protocol_api_version"] == "2.15"


def test_parse_arp_entries_accepts_windows_and_unix_formats() -> None:
    output = """
      169.254.252.252       b8-27-eb-e5-a1-75     dynamic
    ? (169.254.1.8) at aa:bb:cc:dd:ee:ff on en0
    """
    assert connection.parse_arp_entries(output) == [
        ("169.254.252.252", "b8:27:eb:e5:a1:75"),
        ("169.254.1.8", "aa:bb:cc:dd:ee:ff"),
    ]


def test_verify_host_refuses_wrong_robot_serial(monkeypatch) -> None:
    monkeypatch.setattr(connection, "_resolved_ipv4", lambda host: "169.254.9.9")
    monkeypatch.setattr(
        connection,
        "health",
        lambda host, timeout=2.0: {**HEALTH, "robot_serial": "SOMEONE_ELSES_OT2"},
    )

    with pytest.raises(RuntimeError, match="expected OT2CEP20220929R02"):
        connection.verify_host("candidate.local", config=ROBOT)


def test_discovery_prefers_verified_mdns_and_stops(monkeypatch) -> None:
    monkeypatch.setattr(connection, "load_robot_config", lambda path: ROBOT)
    monkeypatch.setattr(
        connection,
        "_resolved_ipv4",
        lambda host: "169.254.252.252",
    )
    monkeypatch.setattr(
        connection,
        "verify_host",
        lambda host, timeout, config: ("169.254.252.252", HEALTH),
    )
    monkeypatch.setattr(
        connection,
        "_zeroconf_candidates",
        lambda timeout: (_ for _ in ()).throw(
            AssertionError("zeroconf must not run after verified mDNS")
        ),
    )

    result = connection.discover_robot()
    assert result["host"] == "OT2CEP20220929R02.local"
    assert result["ip"] == "169.254.252.252"
    assert result["method"] == "mdns"


def test_discovery_falls_through_to_allowlisted_arp(monkeypatch) -> None:
    monkeypatch.setattr(connection, "load_robot_config", lambda path: ROBOT)

    def resolve(host: str) -> str:
        if host.endswith(".local"):
            raise socket.gaierror("not found")
        return host

    def verify(host: str, *, timeout: float, config: dict):
        if host == "169.254.252.252":
            raise RuntimeError("stale")
        assert host == "169.254.77.88"
        return host, HEALTH

    monkeypatch.setattr(connection, "_resolved_ipv4", resolve)
    monkeypatch.setattr(connection, "verify_host", verify)
    monkeypatch.setattr(
        connection,
        "_zeroconf_candidates",
        lambda timeout: ([], "not installed"),
    )
    monkeypatch.setattr(
        connection,
        "_allowed_arp_candidates",
        lambda config: (["169.254.77.88"], "fixture"),
    )

    result = connection.discover_robot()
    assert result["ip"] == "169.254.77.88"
    assert result["method"] == "arp"
    assert any("last-known" in attempt for attempt in result["attempts"])


def test_resolve_host_precedence_cli_then_environment(monkeypatch) -> None:
    monkeypatch.setattr(
        connection,
        "_safe_resolve",
        lambda host: "192.0.2.44",
    )
    monkeypatch.setenv("OT2_ROBOT_HOST", "env-robot.local")

    assert connection.resolve_host("cli-robot.local") == "cli-robot.local"
    assert connection.resolve_host() == "env-robot.local"


def test_resolve_host_prefers_verified_mdns_over_cached_ip(monkeypatch) -> None:
    monkeypatch.delenv("OT2_ROBOT_HOST", raising=False)
    monkeypatch.setattr(connection, "load_robot_config", lambda: ROBOT)
    monkeypatch.setattr(
        connection,
        "verify_host",
        lambda host, config: ("169.254.252.252", HEALTH),
    )

    assert connection.resolve_host() == "OT2CEP20220929R02.local"
    assert "via mDNS" in connection.connection_summary(
        "OT2CEP20220929R02.local"
    )


def test_write_discovery_preserves_human_robot_block_and_comments(tmp_path) -> None:
    config_path = tmp_path / "robot.yaml"
    original_robot = """# keep this comment
robot:
  name: OT2CEP20220929R02
  serial: OT2CEP20220929R02
  mdns_host: OT2CEP20220929R02.local
  http_port: 31950
  ssh_port: 22
  mac_oui_allowlist:
    - "b8:27:eb"
"""
    config_path.write_text(
        original_robot
        + """
discovered:
  ip: 169.254.1.1
  method: old
  discovered_at: "old"

capabilities:
  robot_software: "old"
  system_version: "old"
  max_protocol_api_version: "2.0"
  min_protocol_api_version: "2.0"
""",
        encoding="utf-8",
    )

    connection.write_discovery(
        "169.254.252.252",
        "mdns",
        HEALTH,
        config_path=str(config_path),
        discovered_at="2026-07-23T15:12:04-04:00",
    )
    updated = config_path.read_text(encoding="utf-8")

    assert updated.startswith(original_robot)
    assert "ip: 169.254.252.252" in updated
    assert 'max_protocol_api_version: "2.15"' in updated
    assert updated.count("robot:") == 1
    assert updated.count("discovered:") == 1
    assert updated.count("capabilities:") == 1


def test_hidden_robot_ip_alias_targets_same_argument() -> None:
    parser = argparse.ArgumentParser()
    connection.add_robot_host_arguments(parser)
    assert parser.parse_args(["--robot-host", "name.local"]).robot_host == (
        "name.local"
    )
    assert parser.parse_args(["--robot-ip", "192.0.2.3"]).robot_host == "192.0.2.3"
    assert "--robot-ip" not in parser.format_help()


def test_api_capability_mismatch_is_formatted_as_hard_error() -> None:
    output = connection.format_diagnostics(
        [
            connection.DiagnosticStep(
                "Protocol API capability",
                False,
                "live maximum 2.14; cached maximum 2.15",
                hard_error=True,
            )
        ]
    )
    assert "[FAIL]" in output
    assert "[HARD ERROR]" in output


def test_no_hardcoded_link_local_addresses_outside_authorized_fixtures() -> None:
    root = Path(__file__).resolve().parent.parent
    allowed = {
        (root / "configs" / "robot.yaml").resolve(),
    }
    offenders: list[str] = []
    for path in root.rglob("*"):
        if (
            not path.is_file()
            or ".git" in path.parts
            or "tests" in path.parts
            or "logs" in path.parts
            or path.resolve() in allowed
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if connection._IPV4_RE.search(text):
            for match in connection._IPV4_RE.findall(text):
                if match.startswith("169.254."):
                    offenders.append(f"{path.relative_to(root)}: {match}")
    assert offenders == []
