#!/usr/bin/env python3
"""
Terminal runner for the 96-well plate -> 20 mL waste vial disposal protocol.

Uses the robot HTTP API (not opentrons_execute), which is required for this
apiLevel 2.28 protocol. By default it creates a DRY RUN. Pass --live to run the
real liquid-handling waste removal.

  python scripts/run_plate_waste_disposal.py --robot-ip 169.254.46.57            # dry run
  python scripts/run_plate_waste_disposal.py --robot-ip 169.254.46.57 --live     # real run
  python scripts/run_plate_waste_disposal.py --robot-ip 169.254.46.57 --live --skip-build
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
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
DEFAULT_PROTOCOL = REPO / "src" / "protocols" / "generated" / "plate_waste_disposal_latest.py"
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
            f"{method} {path} failed with HTTP {response.status_code}:\n"
            f"{json.dumps(payload, indent=2)}"
        )
    return payload


def _run_local_step(command: list[str], label: str) -> None:
    print(f"\n[{label}] {' '.join(command)}")
    subprocess.run(command, cwd=REPO, check=True)


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
            f"Protocol upload failed with HTTP {response.status_code}:\n"
            f"{json.dumps(payload, indent=2)}"
        )
    protocol_id = payload.get("data", {}).get("id")
    if not protocol_id:
        raise RuntimeError(f"Protocol upload response did not include data.id:\n{json.dumps(payload, indent=2)}")
    print(f"Protocol ID: {protocol_id}")
    return protocol_id


def _create_run(robot_ip: str, protocol_id: str, *, dry_run: bool) -> str:
    body = {
        "data": {
            "protocolId": protocol_id,
            "runTimeParameterValues": {"dry_run": dry_run},
        }
    }
    print("\n[create run]")
    print(json.dumps(body["data"]["runTimeParameterValues"], indent=2))
    payload = _request("POST", robot_ip, "/runs", json=body)
    run_id = payload.get("data", {}).get("id")
    if not run_id:
        raise RuntimeError(f"Create-run response did not include data.id:\n{json.dumps(payload, indent=2)}")
    print(f"Run ID: {run_id}")
    return run_id


def _run_status(robot_ip: str, run_id: str) -> dict[str, Any]:
    payload = _request("GET", robot_ip, f"/runs/{run_id}")
    return payload.get("data", {})


def _play_run(robot_ip: str, run_id: str) -> None:
    print("\n[play]")
    _request("POST", robot_ip, f"/runs/{run_id}/actions", json={"data": {"actionType": "play"}})


def _monitor(robot_ip: str, run_id: str, poll_s: float) -> str:
    print("\n[monitor]")
    last = None
    while True:
        data = _run_status(robot_ip, run_id)
        status = str(data.get("status", "unknown"))
        current = f"status={status}"
        if current != last:
            print(current)
            last = current
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(poll_s)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload and run the plate -> waste vial disposal protocol via the OT-2 HTTP API."
    )
    parser.add_argument("--robot-ip", default=Config.ROBOT_IP, help="Robot IP address.")
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL), help="Generated protocol file to upload.")
    parser.add_argument("--live", action="store_true", help="Run liquid motion. Default is dry run.")
    parser.add_argument("--skip-build", action="store_true", help="Do not rebuild the generated protocol first.")
    parser.add_argument("--no-start", action="store_true", help="Upload and create the run, but do not press play.")
    parser.add_argument("--poll-seconds", type=float, default=5.0, help="Status polling interval.")
    args = parser.parse_args()

    robot_ip = args.robot_ip
    os.environ["NO_PROXY"] = f"{os.environ.get('NO_PROXY', 'localhost,127.0.0.1')},{robot_ip}"

    protocol_path = Path(args.protocol).resolve()
    if not args.skip_build:
        _run_local_step([sys.executable, "scripts/build_plate_waste_disposal.py"], "build + simulate")
    if not protocol_path.exists():
        raise FileNotFoundError(f"Generated protocol not found: {protocol_path}")

    dry_run = not args.live

    print("\n=== Plate -> Waste Vial Disposal Robot Runner ===")
    print(f"Robot     : {robot_ip}")
    print(f"Protocol  : {protocol_path}")
    print(f"Mode      : {'LIVE LIQUID RUN' if args.live else 'DRY RUN'}")
    print("\nDeck must be: slot 7 vial rack (waste vial seated), slot 4 plate, slot 9 tips.")
    print("Tip plan  : one setup tip H12 for the whole job (single nozzle).")

    protocol_id = _upload_protocol(robot_ip, protocol_path)
    run_id = _create_run(robot_ip, protocol_id, dry_run=dry_run)

    if args.no_start:
        print(f"\nCreated run but did not start it. Run ID: {run_id}")
        return 0

    _play_run(robot_ip, run_id)
    status = _monitor(robot_ip, run_id, args.poll_seconds)
    print(f"\nRun finished with status: {status}")
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
