#!/usr/bin/env python3
"""Simulate or explicitly execute the P20-only dry-motion smoke test."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Sequence

import requests


REPO = Path(__file__).resolve().parent.parent
DEFAULT_PROTOCOL = REPO / "src" / "protocols" / "generated" / "smoke_test.py"
DEFAULT_LABWARE = REPO / "labware" / "corning_96_wellplate_360ul_custom.json"
HEADERS = {"opentrons-version": "*"}
TERMINAL_STATUSES = {"succeeded", "failed", "stopped"}
_SIMULATOR_SHIM = (
    "import numpy as np; "
    "np.trapz = getattr(np, 'trapezoid', np.trapz if hasattr(np, 'trapz') else None); "
    "from opentrons.simulate import main; main()"
)
_SIMULATION_ERROR_RE = re.compile(
    r"Traceback \(most recent call last\)|RuntimeError|LabwareNotFoundError|"
    r"ProtocolCommandFailedError|InvalidProtocolData|KeyError|AttributeError",
    re.IGNORECASE,
)
_SIMULATION_SUCCESS_TEXT = "P20 dry-motion smoke test complete"


def _api_url(robot_ip: str, path: str) -> str:
    return f"http://{robot_ip}:31950{path}"


def _request_json(
    method: str, robot_ip: str, path: str, **kwargs: Any
) -> dict[str, Any]:
    response = requests.request(
        method,
        _api_url(robot_ip, path),
        headers=HEADERS,
        timeout=30,
        **kwargs,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    if response.status_code >= 400:
        raise RuntimeError(
            f"{method} {path} failed with HTTP {response.status_code}:\n"
            f"{json.dumps(payload, indent=2)}"
        )
    return payload


def _upload_protocol(robot_ip: str, protocol_path: Path, labware_path: Path) -> str:
    with ExitStack() as stack:
        protocol_file = stack.enter_context(protocol_path.open("rb"))
        labware_file = stack.enter_context(labware_path.open("rb"))
        response = requests.post(
            _api_url(robot_ip, "/protocols"),
            headers=HEADERS,
            files=[
                ("files", (protocol_path.name, protocol_file, "text/x-python")),
                ("files", (labware_path.name, labware_file, "application/json")),
            ],
            timeout=120,
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    if response.status_code >= 400:
        raise RuntimeError(
            f"Protocol upload failed with HTTP {response.status_code}:\n"
            f"{json.dumps(payload, indent=2)}"
        )
    protocol_id = payload.get("data", {}).get("id")
    if not protocol_id:
        raise RuntimeError(
            f"Upload response did not include data.id:\n{json.dumps(payload, indent=2)}"
        )
    return str(protocol_id)


def simulate_protocol(protocol_path: Path, labware_path: Path) -> int:
    """Run only the local Opentrons simulator; this function never uses the network."""
    command = [
        sys.executable,
        "-B",
        "-c",
        _SIMULATOR_SHIM,
        "-L",
        str(labware_path.parent),
        str(protocol_path),
    ]
    print("Local simulation only (no robot connection).")
    print(f"Protocol: {protocol_path}")
    result = subprocess.run(
        command,
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    output = result.stdout + result.stderr
    if (
        result.returncode != 0
        or _SIMULATION_ERROR_RE.search(output)
        or _SIMULATION_SUCCESS_TEXT not in output
    ):
        print("P20 local simulation FAILED.", file=sys.stderr)
        return 1
    print("P20 local simulation PASSED.")
    return 0


def execute_protocol(
    robot_ip: str,
    protocol_path: Path,
    labware_path: Path,
    poll_seconds: float,
) -> int:
    """Upload, start, and monitor the test. Call only after explicit --execute."""
    os.environ["NO_PROXY"] = (
        f"{os.environ.get('NO_PROXY', 'localhost,127.0.0.1')},{robot_ip}"
    )
    print("PHYSICAL P20 DRY-MOTION TEST")
    print("Confirm: empty plate in slot 4; paper holder in slot 5; one 20 uL tip at slot 9 A1.")
    print("No liquid should be present. Stop the robot immediately if any labware is misaligned.")

    protocol_id = _upload_protocol(robot_ip, protocol_path, labware_path)
    run_payload = _request_json(
        "POST",
        robot_ip,
        "/runs",
        json={"data": {"protocolId": protocol_id}},
    )
    run_id = run_payload.get("data", {}).get("id")
    if not run_id:
        raise RuntimeError(
            f"Create-run response did not include data.id:\n{json.dumps(run_payload, indent=2)}"
        )
    _request_json(
        "POST",
        robot_ip,
        f"/runs/{run_id}/actions",
        json={"data": {"actionType": "play"}},
    )

    last_status = None
    while True:
        payload = _request_json("GET", robot_ip, f"/runs/{run_id}")
        status = str(payload.get("data", {}).get("status", "unknown"))
        if status != last_status:
            print(f"Status: {status}")
            last_status = status
        if status in TERMINAL_STATUSES:
            return 0 if status == "succeeded" else 1
        time.sleep(poll_seconds)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="P20-only dry-motion smoke test (local simulation by default)."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--simulate",
        action="store_true",
        help="Run locally with the Opentrons simulator (the default).",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Explicitly upload and run physical dry motion through the OT-2 HTTP API.",
    )
    parser.add_argument("--robot-ip", default="169.254.46.57")
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    parser.add_argument("--labware", default=str(DEFAULT_LABWARE))
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    protocol_path = Path(args.protocol).resolve()
    labware_path = Path(args.labware).resolve()
    if not protocol_path.is_file():
        raise FileNotFoundError(f"Protocol not found: {protocol_path}")
    if not labware_path.is_file():
        raise FileNotFoundError(f"Custom labware definition not found: {labware_path}")

    if args.execute:
        return execute_protocol(
            args.robot_ip,
            protocol_path,
            labware_path,
            args.poll_seconds,
        )
    return simulate_protocol(protocol_path, labware_path)


if __name__ == "__main__":
    raise SystemExit(main())
