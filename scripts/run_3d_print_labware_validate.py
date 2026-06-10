#!/usr/bin/env python3
"""
Upload and run src/protocols/3d_print_labware_validate.py on the OT-2.

This is a laptop-side helper. It talks to the robot HTTP API, because the
protocol uses apiLevel 2.28 and SINGLE-nozzle mode.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

if __name__ == "__main__" and not __package__:
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))

from src.core.config import Config


REPO = Path(__file__).resolve().parent.parent
DEFAULT_PROTOCOL = REPO / "src" / "protocols" / "3d_print_labware_validate.py"
HEADERS = {"opentrons-version": "*"}
TERMINAL_STATUSES = {"succeeded", "failed", "stopped"}


def api_url(robot_ip: str, path: str) -> str:
    return f"http://{robot_ip}:31950{path}"


def request_json(method: str, robot_ip: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = requests.request(method, api_url(robot_ip, path), headers=HEADERS, timeout=30, **kwargs)
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


def upload_protocol(robot_ip: str, protocol_path: Path) -> str:
    print(f"Uploading protocol: {protocol_path}")
    with protocol_path.open("rb") as handle:
        response = requests.post(
            api_url(robot_ip, "/protocols"),
            headers=HEADERS,
            files={"files": (protocol_path.name, handle, "text/x-python")},
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
        raise RuntimeError(f"Upload response did not include data.id:\n{json.dumps(payload, indent=2)}")
    print(f"Protocol ID: {protocol_id}")
    return protocol_id


def create_run(robot_ip: str, protocol_id: str) -> str:
    body = {"data": {"protocolId": protocol_id, "runTimeParameterValues": {}}}
    payload = request_json("POST", robot_ip, "/runs", json=body)
    run_id = payload.get("data", {}).get("id")
    if not run_id:
        raise RuntimeError(f"Create-run response did not include data.id:\n{json.dumps(payload, indent=2)}")
    print(f"Run ID: {run_id}")
    return run_id


def play_run(robot_ip: str, run_id: str) -> None:
    request_json("POST", robot_ip, f"/runs/{run_id}/actions", json={"data": {"actionType": "play"}})
    print("Run started.")


def monitor_run(robot_ip: str, run_id: str, poll_seconds: float) -> str:
    last_status = None
    while True:
        payload = request_json("GET", robot_ip, f"/runs/{run_id}")
        status = str(payload.get("data", {}).get("status", "unknown"))
        if status != last_status:
            print(f"Status: {status}")
            last_status = status
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the one-tip 3D printed labware validation protocol on the OT-2."
    )
    parser.add_argument("--robot-ip", default=Config.ROBOT_IP or "169.254.46.57")
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    parser.add_argument("--no-start", action="store_true", help="Upload/create the run but do not press play.")
    parser.add_argument("--no-monitor", action="store_true", help="Start the run and return immediately.")
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    args = parser.parse_args()

    robot_ip = args.robot_ip
    protocol_path = Path(args.protocol).resolve()
    if not protocol_path.exists():
        raise FileNotFoundError(f"Protocol not found: {protocol_path}")

    os.environ["NO_PROXY"] = f"{os.environ.get('NO_PROXY', 'localhost,127.0.0.1')},{robot_ip}"

    print("=== 3D Print Labware Validate Runner ===")
    print(f"Robot: {robot_ip}")
    print("Deck: slot 7 = 3D vial rack, slot 9 = 300 uL tips, right mount = p300_multi_gen2")
    print("Protocol should pick exactly one tip from H1, then move 20 uL A1 <-> A2.")
    print("Watch the first pickup. If more than one tip engages, stop immediately.")
    print()

    protocol_id = upload_protocol(robot_ip, protocol_path)
    run_id = create_run(robot_ip, protocol_id)

    if args.no_start:
        print("Created run but did not start it.")
        return 0

    play_run(robot_ip, run_id)
    if args.no_monitor:
        return 0

    status = monitor_run(robot_ip, run_id, args.poll_seconds)
    print(f"Final status: {status}")
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
