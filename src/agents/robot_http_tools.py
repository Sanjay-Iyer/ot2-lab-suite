"""LangChain tools for launching OT-2 protocols through the robot HTTP API."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import requests
from langchain.tools import tool

from src.agents.robot_protocol_registry import describe_robot_protocols, get_robot_protocol
from src.core.config import Config
from src.lab.robot_connection import base_url, resolve_host
from src.utils.hashing import hash_file
from src.utils.paths import PROJECT_ROOT, SIMULATION_RECORDS_PATH


HEADERS = {"opentrons-version": "*"}


def _tail(text: str, max_chars: int = 2400) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _load_simulation_records(path: Path | None = None) -> dict[str, Any]:
    records_path = path or SIMULATION_RECORDS_PATH
    if not records_path.exists():
        return {}
    try:
        return json.loads(records_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _has_passing_simulation(protocol_path: Path) -> tuple[bool, str, str]:
    sha256 = hash_file(protocol_path)
    records = _load_simulation_records()
    record = records.get(sha256)
    if record and record.get("status") == "PASS":
        return True, sha256, "PASS simulation record found."
    return False, sha256, "No PASS simulation record found for this exact generated protocol."


def _robot_ip(cli_robot_ip: Optional[str]) -> str:
    return resolve_host(cli_robot_ip)


def _set_no_proxy(robot_ip: str) -> None:
    os.environ["NO_PROXY"] = f"{os.environ.get('NO_PROXY', 'localhost,127.0.0.1')},{robot_ip}"


@tool
def list_robot_http_protocols() -> str:
    """List the robot protocols registered for HTTP/API-style launch."""
    return describe_robot_protocols()


@tool
def check_robot_http_api(robot_ip: Optional[str] = None) -> str:
    """Check that the OT-2 robot server HTTP API is reachable on port 31950."""
    auth_error = Config.live_robot_llm_auth_error()
    if auth_error:
        return auth_error

    try:
        ip = _robot_ip(robot_ip)
    except RuntimeError as exc:
        return f"HTTP API check FAILED: {exc}"
    _set_no_proxy(ip)
    url = f"{base_url(ip)}/health"
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
    except requests.RequestException as exc:
        return f"HTTP API check FAILED for {url}: {exc}"
    if response.status_code >= 400:
        return f"HTTP API check FAILED for {url}: HTTP {response.status_code} {response.text[:500]}"
    try:
        payload = response.json()
    except ValueError:
        payload = response.text[:500]
    return f"HTTP API check PASSED for {url}.\n{payload}"


@tool
def run_vial_print_robot_http(
    confirmation: str,
    robot_ip: Optional[str] = None,
    live: bool = True,
    paper_start_column: Optional[int] = None,
    do_dilution: bool = True,
    do_print: bool = True,
    no_start: bool = False,
    poll_seconds: float = 5.0,
) -> str:
    """Run the vial-dilution-print protocol through scripts/run_vial_print_robot.py.

    Safety:
    - confirmation must be exactly RUN ROBOT.
    - The exact generated protocol must have a PASS simulation record.
    - The wrapper always uses --skip-build --skip-validate so the agent's
      previously built user-YAML artifact is what gets uploaded.
    """
    if confirmation != "RUN ROBOT":
        return "REFUSED: confirmation must be exactly RUN ROBOT."

    auth_error = Config.live_robot_llm_auth_error()
    if auth_error:
        return auth_error

    try:
        ip = _robot_ip(robot_ip)
    except RuntimeError as exc:
        return f"REFUSED: {exc}"

    entry = get_robot_protocol("vial_dilution_print")
    if not entry.runner_script.exists():
        return f"REFUSED: runner script not found: {entry.runner_script}"
    if not entry.protocol_path.exists():
        return f"REFUSED: generated protocol not found: {entry.protocol_path}. Build first."

    sim_ok, sha256, sim_msg = _has_passing_simulation(entry.protocol_path)
    if not sim_ok:
        return (
            "REFUSED: build/simulation gate has not passed for the current artifact.\n"
            f"Protocol: {entry.protocol_path.relative_to(PROJECT_ROOT)}\n"
            f"SHA256: {sha256}\n"
            f"{sim_msg}\n"
            "Run build_vial_print_protocol() and validate_vial_print_matrix() first."
        )

    _set_no_proxy(ip)
    cmd = [
        sys.executable,
        str(entry.runner_script),
        "--robot-host",
        ip,
        "--protocol",
        str(entry.protocol_path),
        "--skip-build",
        "--skip-validate",
        "--poll-seconds",
        str(poll_seconds),
    ]
    if live:
        cmd.append("--live")
    if not do_dilution:
        cmd.append("--no-dilution")
    if not do_print:
        cmd.append("--no-print")
    if no_start:
        cmd.append("--no-start")
    if paper_start_column is not None:
        cmd.extend(["--paper-start-column", str(paper_start_column)])

    try:
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    except Exception as exc:
        return f"HTTP runner failed before launch: {exc}"

    status = "SUCCESS" if result.returncode == 0 else "FAILED"
    command_text = " ".join(cmd)
    stdout = _tail(result.stdout)
    stderr = _tail(result.stderr)
    return (
        f"Vial-print HTTP robot run {status}.\n"
        f"Protocol: {entry.protocol_path.relative_to(PROJECT_ROOT)}\n"
        f"SHA256: {sha256}\n"
        f"Command: {command_text}\n"
        f"Exit code: {result.returncode}\n\n"
        f"STDOUT:\n{stdout or '(none)'}\n\n"
        f"STDERR:\n{stderr or '(none)'}"
    )
