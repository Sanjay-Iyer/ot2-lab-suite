#!/usr/bin/env python3
"""
scripts/run_robot_template.py  —  TEMPLATE, do not run as-is
============================================================
Copy this file to scripts/run_<your_protocol>.py and fill in the >>> CUSTOMIZE <<<
spots. This is the standard, reliable way to run an OT-2 protocol from the lab laptop.

WHY THIS PATTERN
----------------
It talks to the robot over the **HTTP API (port 31950)** — the same transport as
run_vial_print_robot.py. The robot server supplies the *deck configuration*, so
engine-era protocols (apiLevel >= 2.14, e.g. 2.28) load labware without the
`AreaNotInDeckConfigurationError` you get from bare `opentrons_execute`. It needs NO
SSH for the run itself. (Only add SSH/SCP if you must pull files back — see the
optional block at the bottom and scripts/run_droplet_error_check.py.)

CHECKLIST when adapting:
  1. Set DEFAULT_PROTOCOL to your protocol file (the exact file you upload).
  2. Make your protocol expose its knobs as RUNTIME PARAMETERS via add_parameters()
     (apiLevel >= 2.18). The keys you put in `rtp` below MUST match those names.
  3. Add a CLI flag per knob and map it into `rtp` (only send what the user set).
  4. Update the deck reminder text.
  5. Keep --live meaning "real liquid" (default = dry run) for consistency.

Full guide: docs/robot_runner_template_guide.md  ·  skills/ot2-robot-control/RUNNER_TEMPLATE.md
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
# >>> CUSTOMIZE <<< the protocol file this runner uploads.
DEFAULT_PROTOCOL = REPO / "src" / "protocols" / "your_protocol.py"
HEADERS = {"opentrons-version": "*"}
TERMINAL_STATUSES = {"succeeded", "failed", "stopped"}


# ── HTTP API helpers (copy verbatim — these rarely change) ────────────────────────

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


def _create_run(robot_ip: str, protocol_id: str, rtp: dict[str, Any]) -> str:
    body = {"data": {"protocolId": protocol_id, "runTimeParameterValues": rtp}}
    print("\n[create run]")
    print(json.dumps(rtp, indent=2))
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
    ap = argparse.ArgumentParser(description="Upload + run a protocol via the OT-2 HTTP API.")
    ap.add_argument("--robot-ip", default=Config.ROBOT_IP, help="Robot IP address.")
    ap.add_argument("--protocol", default=str(DEFAULT_PROTOCOL), help="Protocol file to upload.")
    ap.add_argument("--live", action="store_true", help="Run liquid motion. Default is a dry run.")
    ap.add_argument("--no-start", action="store_true", help="Upload + create the run, but do not play.")
    ap.add_argument("--poll-seconds", type=float, default=5.0, help="Status polling interval.")
    # >>> CUSTOMIZE <<< one flag per protocol knob, e.g.:
    # ap.add_argument("--my-column", type=int, help="...")
    args = ap.parse_args()

    robot_ip = args.robot_ip
    os.environ["NO_PROXY"] = f"{os.environ.get('NO_PROXY', 'localhost,127.0.0.1')},{robot_ip}"

    protocol_path = Path(args.protocol).resolve()
    if not protocol_path.exists():
        raise FileNotFoundError(f"Protocol not found: {protocol_path}")

    # >>> CUSTOMIZE <<< build the runtime parameters. Keys MUST match add_parameters()
    # in your protocol. Send only what the user set; the rest use protocol defaults.
    # 'dry_run' is the standard safety knob: live = real liquid, default = dry.
    rtp: dict[str, Any] = {"dry_run": not args.live}
    # if args.my_column is not None: rtp["my_column"] = args.my_column

    print("\n=== Robot Runner (HTTP API) ===")
    print(f"Robot    : {robot_ip}")
    print(f"Protocol : {protocol_path}")
    print(f"Mode     : {'LIVE LIQUID RUN' if args.live else 'DRY RUN'}")
    # >>> CUSTOMIZE <<< print the required deck layout here.

    protocol_id = _upload_protocol(robot_ip, protocol_path)
    run_id = _create_run(robot_ip, protocol_id, rtp)

    if args.no_start:
        print(f"\nCreated run but did not start it. Run ID: {run_id}")
        return 0

    _play_run(robot_ip, run_id)
    status = _monitor(robot_ip, run_id, args.poll_seconds)
    print(f"\nRun finished with status: {status}")

    # >>> OPTIONAL <<< pull files back via SCP after a successful run. Use the OT-2 key
    # (id_rsa_opentrons), -o BatchMode=yes, and scp -O. See run_droplet_error_check.py.

    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
