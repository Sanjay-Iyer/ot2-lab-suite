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
import shlex
import subprocess
import sys
import time
from datetime import datetime
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
REMOTE_VISION_DIR = "/data/vision/vial_dilution_print"
LOCAL_VISION_BASE = REPO / "vision_runs" / "vial_dilution_print"
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "id_rsa_opentrons"
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


def _ssh_user_host(robot_ip: str) -> str:
    user = getattr(Config, "ROBOT_SSH_USER", "root") or "root"
    return f"{user}@{robot_ip}"


def _resolve_ssh_key(cli_key: str | None) -> str:
    """--ssh-key > .env ROBOT_SSH_KEY_PATH > ~/.ssh/id_rsa_opentrons."""
    for candidate in (cli_key, getattr(Config, "ROBOT_SSH_KEY_PATH", ""), str(DEFAULT_SSH_KEY)):
        key = str(candidate or "").strip()
        if key:
            return key
    return str(DEFAULT_SSH_KEY)


def _ssh_opts(key: str, *, timeout_s: int = 10) -> list[str]:
    return [
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={timeout_s}",
        "-o", "StrictHostKeyChecking=no",
        "-i", key,
    ]


def _remote_image_dir() -> str:
    """Return the robot-side image directory used by the generated protocol."""
    return REMOTE_VISION_DIR


def _prepare_remote_image_dir(
    robot_ip: str,
    cli_key: str | None,
    run_log: RobotRunLog | None = None,
) -> bool:
    """Clear old camera images so the post-run pull contains only fresh files."""
    key = _resolve_ssh_key(cli_key)
    remote_dir = _remote_image_dir()
    if not Path(key).exists():
        print(f"\nWARNING: SSH key {key} not found; cannot clear robot image folder.")
        print(f"Images may still be captured on the robot at {remote_dir}, but automatic pullback will be skipped.")
        if run_log:
            run_log.event("image_prepare_skipped", reason="ssh_key_missing", remote_dir=remote_dir, ssh_key=key)
        return False

    quoted = shlex.quote(remote_dir)
    cmd = [
        "ssh",
        *_ssh_opts(key),
        _ssh_user_host(robot_ip),
        f"mkdir -p {quoted} && rm -f {quoted}/*.jpg {quoted}/*.jpeg {quoted}/*.png",
    ]
    print(f"\n[prepare images] clearing {remote_dir}")
    if run_log:
        run_log.event("image_prepare_started", remote_dir=remote_dir)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"WARNING: could not clear robot image folder before run:\n{result.stderr.strip()}")
        if run_log:
            run_log.event("image_prepare_failed", remote_dir=remote_dir, stderr=result.stderr)
        return False
    if run_log:
        run_log.event("image_prepare_finished", remote_dir=remote_dir)
    return True


def _pull_images(
    robot_ip: str,
    cli_key: str | None,
    local_name: str,
    run_log: RobotRunLog | None = None,
) -> None:
    key = _resolve_ssh_key(cli_key)
    remote_dir = _remote_image_dir()
    if not Path(key).exists():
        print(f"\nWARNING: SSH key {key} not found; skipping image pull.")
        print(f"Images remain on the robot at {remote_dir}.")
        if run_log:
            run_log.event("image_pull_skipped", reason="ssh_key_missing", remote_dir=remote_dir, ssh_key=key)
        return

    LOCAL_VISION_BASE.mkdir(parents=True, exist_ok=True)
    dest = LOCAL_VISION_BASE / local_name
    cmd = [
        "scp",
        "-O",
        "-r",
        *_ssh_opts(key),
        f"{_ssh_user_host(robot_ip)}:{remote_dir}",
        str(dest),
    ]
    print(f"\n[pull images] {' '.join(cmd)}")
    if run_log:
        run_log.event("image_pull_started", remote_dir=remote_dir, local_dest=repo_relative(dest))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"WARNING: could not pull images from {remote_dir}:\n{result.stderr.strip()}")
        if run_log:
            run_log.event("image_pull_failed", remote_dir=remote_dir, local_dest=repo_relative(dest), stderr=result.stderr)
        return

    imgs = sorted(p.name for p in dest.glob("*.jpg")) if dest.exists() else []
    if run_log:
        run_log.event("image_pull_finished", local_dest=repo_relative(dest), image_count=len(imgs), images=imgs)
    print(f"Images saved to: {dest}")
    print(f"  {', '.join(imgs) if imgs else '(no .jpg found; check camera capture warnings in the run output)'}")


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
    parser.add_argument("--ssh-key", default=None, help="SSH key for image pullback. Default: ROBOT_SSH_KEY_PATH or ~/.ssh/id_rsa_opentrons.")
    parser.add_argument("--no-pull-images", action="store_true", help="Do not pull camera images from the robot after a live run.")
    parser.add_argument("--no-clean-remote-images", action="store_true", help="Do not clear the robot image folder before starting a live run.")
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
        image_run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
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
            pull_images=not args.no_pull_images,
            clean_remote_images=not args.no_clean_remote_images,
            robot_image_dir=_remote_image_dir(),
            local_image_run_id=image_run_id,
            local_image_dir=repo_relative(LOCAL_VISION_BASE / image_run_id),
            local_image_base=repo_relative(LOCAL_VISION_BASE),
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
        if args.live and not args.no_pull_images:
            print(f"Images    : will pull {_remote_image_dir()} to {LOCAL_VISION_BASE / image_run_id} after the run.")

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

        if args.live and not args.no_pull_images and not args.no_clean_remote_images:
            _prepare_remote_image_dir(robot_ip, args.ssh_key, run_log)

        _play_run(robot_ip, run_id)
        run_log.event("run_started", run_id=run_id)
        status = _monitor(robot_ip, run_id, args.poll_seconds)
        print(f"\nRun finished with status: {status}")
        if args.live and not args.no_pull_images:
            _pull_images(robot_ip, args.ssh_key, image_run_id, run_log)
        exit_code = 0 if status == "succeeded" else 1
        run_log.finish(status, exit_code=exit_code)
        return exit_code
    except Exception as exc:
        run_log.finish("error", exit_code=1, error=str(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
