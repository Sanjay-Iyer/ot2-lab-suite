#!/usr/bin/env python3
"""
Terminal runner for the vial dilution -> paper print demo.

This uses the robot HTTP API instead of opentrons_execute, which is important for
this apiLevel 2.28 protocol. By default it creates a dry run. Pass --live to run
the real liquid-handling print.
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
from src.utils.robot_run_log import RobotRunLog, repo_relative


REPO = Path(__file__).resolve().parent.parent
DEFAULT_PROTOCOL = REPO / "src" / "protocols" / "generated" / "vial_dilution_print_latest.py"
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


def _create_run(
    robot_ip: str,
    protocol_id: str,
    *,
    dry_run: bool,
    do_dilution: bool,
    do_print: bool,
    paper_start_column: int | None = None,
) -> str:
    run_time_parameter_values: dict[str, Any] = {
        "dry_run": dry_run,
        "do_dilution": do_dilution,
        "do_print": do_print,
    }
    # Only forward the paper start column when explicitly overridden on the CLI;
    # otherwise the protocol uses the value baked in from the workflow YAML at build.
    if paper_start_column is not None:
        run_time_parameter_values["print_start_column"] = paper_start_column
    body = {
        "data": {
            "protocolId": protocol_id,
            "runTimeParameterValues": run_time_parameter_values,
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
        description="Upload and run the vial dilution print protocol through the OT-2 HTTP API."
    )
    parser.add_argument("--robot-ip", default=Config.ROBOT_IP, help="Robot IP address.")
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL), help="Generated protocol file to upload.")
    parser.add_argument("--live", action="store_true", help="Run liquid motion. Default is dry run.")
    parser.add_argument("--no-dilution", action="store_true", help="Skip dilution phase.")
    parser.add_argument("--no-print", action="store_true", help="Skip print phase.")
    parser.add_argument(
        "--paper-start-column", type=int, default=None, metavar="N",
        help="Override the leftmost paper column to print on (1-12). Default: the "
             "value baked into the generated protocol from the workflow YAML.")
    parser.add_argument("--skip-build", action="store_true", help="Do not rebuild the generated protocol first.")
    parser.add_argument("--skip-validate", action="store_true", help="Do not run scripts/validate_vial_print.py first.")
    parser.add_argument("--no-start", action="store_true", help="Upload and create the run, but do not press play.")
    parser.add_argument("--poll-seconds", type=float, default=5.0, help="Status polling interval.")
    args = parser.parse_args()
    run_log = RobotRunLog(Path(__file__).name)
    print(f"Run log   : {run_log.path}")

    try:
        robot_ip = args.robot_ip
        os.environ["NO_PROXY"] = f"{os.environ.get('NO_PROXY', 'localhost,127.0.0.1')},{robot_ip}"

        protocol_path = Path(args.protocol).resolve()
        if not args.skip_build:
            run_log.event("local_step", label="build + simulate")
            _run_local_step([sys.executable, "scripts/build_vial_dilution_print.py"], "build + simulate")
        if not args.skip_validate:
            run_log.event("local_step", label="validate matrix")
            _run_local_step([sys.executable, "scripts/validate_vial_print.py"], "validate matrix")
        if not protocol_path.exists():
            raise FileNotFoundError(f"Generated protocol not found: {protocol_path}")

        dry_run = not args.live
        do_dilution = not args.no_dilution
        do_print = not args.no_print
        rtp: dict[str, Any] = {
            "dry_run": dry_run,
            "do_dilution": do_dilution,
            "do_print": do_print,
        }
        if args.paper_start_column is not None:
            rtp["print_start_column"] = args.paper_start_column
        run_log.update(
            robot_ip=robot_ip,
            protocol_path=repo_relative(protocol_path),
            live=args.live,
            no_start=args.no_start,
            skip_build=args.skip_build,
            skip_validate=args.skip_validate,
            run_time_parameter_values=rtp,
        )

        print("\n=== Vial Dilution Print Robot Runner ===")
        print(f"Robot     : {robot_ip}")
        print(f"Protocol  : {protocol_path}")
        print(f"Mode      : {'LIVE LIQUID RUN' if args.live else 'DRY RUN'}")
        print(f"Dilution  : {do_dilution}")
        print(f"Print     : {do_print}")
        if args.paper_start_column is not None:
            print(f"Paper col : {args.paper_start_column} (CLI override)")
        print("\nDeck must be: slot 7 vial rack (A1 water, A2 blue dye, A3 orange dye), slot 4 plate, slot 5 paper, slot 9 tips.")
        print("Tip plan  : setup tips H12 water, H11 orange, H10 blue; 8-channel print tips from columns 1 orange and 2 blue.")

        protocol_id = _upload_protocol(robot_ip, protocol_path)
        run_log.event("protocol_uploaded", protocol_id=protocol_id)
        run_id = _create_run(
            robot_ip,
            protocol_id,
            dry_run=dry_run,
            do_dilution=do_dilution,
            do_print=do_print,
            paper_start_column=args.paper_start_column,
        )
        run_log.event("run_created", protocol_id=protocol_id, run_id=run_id)

        if args.no_start:
            print(f"\nCreated run but did not start it. Run ID: {run_id}")
            run_log.finish("created_not_started", exit_code=0)
            return 0

        _play_run(robot_ip, run_id)
        run_log.event("run_started", run_id=run_id)
        status = _monitor(robot_ip, run_id, args.poll_seconds)
        print(f"\nRun finished with status: {status}")
        exit_code = 0 if status == "succeeded" else 1
        run_log.finish(status, exit_code=exit_code)
        return exit_code
    except Exception as exc:
        run_log.finish("error", exit_code=1, error=str(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
