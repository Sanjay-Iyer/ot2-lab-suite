#!/usr/bin/env python3
"""
scripts/run_paper_print_motion_test.py
======================================
Upload + run src/protocols/paper_print_motion_test.py via the OT-2 HTTP API
(port 31950). This is a MOTION-ONLY test — no liquid is handled — so there is no
--live / dry_run knob: it never pipettes anything.

BEFORE RUNNING, deploy the custom labware once so the robot can resolve it:
    python -m scripts.deploy --labware labware/paper_print_96_flat.json

Deck:
    slot 8 = opentrons_96_tiprack_300ul  (with at least one full column of tips)
    slot 5 = paper_print_96_flat         (the 3D-printed paper holder)

Pattern copied verbatim from scripts/run_robot_template.py.
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
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import Config

REPO = Path(__file__).resolve().parent.parent
DEFAULT_PROTOCOL = REPO / "src" / "protocols" / "paper_print_motion_test.py"
HEADERS = {"opentrons-version": "*"}
TERMINAL_STATUSES = {"succeeded", "failed", "stopped"}


def _api_url(robot_ip: str, path: str) -> str:
    return f"http://{robot_ip}:31950{path}"


def _request(method: str, robot_ip: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = requests.request(method, _api_url(robot_ip, path), headers=HEADERS, timeout=30, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    if response.status_code >= 400:
        raise RuntimeError(
            f"{method} {path} failed with HTTP {response.status_code}:\n{json.dumps(payload, indent=2)}"
        )
    return payload


def _upload_protocol(robot_ip: str, protocol_path: Path) -> str:
    print(f"\n[upload] {protocol_path}")
    with protocol_path.open("rb") as handle:
        response = requests.post(
            _api_url(robot_ip, "/protocols"),
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
            f"Protocol upload failed with HTTP {response.status_code}:\n{json.dumps(payload, indent=2)}"
        )
    protocol_id = payload.get("data", {}).get("id")
    if not protocol_id:
        raise RuntimeError(f"Upload response missing data.id:\n{json.dumps(payload, indent=2)}")
    print(f"Protocol ID: {protocol_id}")
    return protocol_id


def _create_run(robot_ip: str, protocol_id: str) -> str:
    # No runtime parameters: the protocol is motion-only.
    body = {"data": {"protocolId": protocol_id}}
    print("\n[create run]")
    payload = _request("POST", robot_ip, "/runs", json=body)
    run_id = payload.get("data", {}).get("id")
    if not run_id:
        raise RuntimeError(f"Create-run response missing data.id:\n{json.dumps(payload, indent=2)}")
    print(f"Run ID: {run_id}")
    return run_id


def _play_run(robot_ip: str, run_id: str) -> None:
    print("\n[play]")
    _request("POST", robot_ip, f"/runs/{run_id}/actions", json={"data": {"actionType": "play"}})


def _monitor(robot_ip: str, run_id: str, poll_s: float) -> str:
    print("\n[monitor]")
    last = None
    while True:
        data = _request("GET", robot_ip, f"/runs/{run_id}").get("data", {})
        status = str(data.get("status", "unknown"))
        if status != last:
            print(f"status={status}")
            last = status
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(poll_s)


def main() -> int:
    ap = argparse.ArgumentParser(description="Upload + run the paper print motion test via the OT-2 HTTP API.")
    ap.add_argument("--robot-ip", default=Config.ROBOT_IP, help="Robot IP address.")
    ap.add_argument("--protocol", default=str(DEFAULT_PROTOCOL), help="Protocol file to upload.")
    ap.add_argument("--no-start", action="store_true", help="Upload + create the run, but do not play.")
    ap.add_argument("--poll-seconds", type=float, default=5.0, help="Status polling interval.")
    args = ap.parse_args()

    robot_ip = args.robot_ip
    os.environ["NO_PROXY"] = f"{os.environ.get('NO_PROXY', 'localhost,127.0.0.1')},{robot_ip}"

    protocol_path = Path(args.protocol).resolve()
    if not protocol_path.exists():
        raise FileNotFoundError(f"Protocol not found: {protocol_path}")

    print("\n=== Paper Print Motion Test (HTTP API) ===")
    print(f"Robot    : {robot_ip}")
    print(f"Protocol : {protocol_path}")
    print("Mode     : MOTION ONLY — no liquid")
    print("Deck     : slot 8 = opentrons_96_tiprack_300ul, slot 5 = paper_print_96_flat")

    protocol_id = _upload_protocol(robot_ip, protocol_path)
    run_id = _create_run(robot_ip, protocol_id)

    if args.no_start:
        print(f"\nCreated run but did not start it. Run ID: {run_id}")
        return 0

    _play_run(robot_ip, run_id)
    status = _monitor(robot_ip, run_id, args.poll_seconds)
    print(f"\nRun finished with status: {status}")
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
