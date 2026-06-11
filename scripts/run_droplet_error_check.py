#!/usr/bin/env python3
"""
scripts/run_droplet_error_check.py
==================================
Terminal runner for src/protocols/printing_droplet_error_check.py.

Built on the SAME HTTP-API pattern as scripts/run_vial_print_robot.py (port 31950),
which is the reliable transport for engine-era protocols (the robot server supplies the
deck configuration, so no AreaNotInDeckConfigurationError). The only addition is a final
SCP step to pull this run's before/after images back to the laptop.

  python scripts/run_droplet_error_check.py --robot-ip 169.254.46.57 --live --paper-column 1 --replicates 3
  python scripts/run_droplet_error_check.py --robot-ip 169.254.46.57 --live --paper-column 5 \
      --dispense-z 1.0 --droplet-ul 25 --air-gap-ul 0 --label lowgap
  python scripts/run_droplet_error_check.py --robot-ip 169.254.46.57            # dry run (no liquid/images)

Image pull uses the OT-2 key (ROBOT_SSH_KEY_PATH / ~/.ssh/id_rsa_opentrons); the run
itself uses the HTTP API and needs no SSH.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import Config

REPO = Path(__file__).resolve().parent.parent
DEFAULT_PROTOCOL = REPO / "src" / "protocols" / "printing_droplet_error_check.py"
# Must match CONFIG["camera"]["robot_image_dir"] in the protocol.
REMOTE_VISION_BASE = "/data/vision/droplet_error_check"
LOCAL_VISION_BASE  = REPO / "vision_runs" / "droplet_error_check"
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "id_rsa_opentrons"
HEADERS = {"opentrons-version": "*"}
TERMINAL_STATUSES = {"succeeded", "failed", "stopped"}


# ── HTTP API helpers (templated from run_vial_print_robot.py) ─────────────────────

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
        raise RuntimeError(f"Protocol upload response did not include data.id:\n{json.dumps(payload, indent=2)}")
    print(f"Protocol ID: {protocol_id}")
    return protocol_id


def _create_run(robot_ip: str, protocol_id: str, rtp: dict[str, Any]) -> str:
    body = {"data": {"protocolId": protocol_id, "runTimeParameterValues": rtp}}
    print("\n[create run]")
    print(json.dumps(rtp, indent=2))
    payload = _request("POST", robot_ip, "/runs", json=body)
    run_id = payload.get("data", {}).get("id")
    if not run_id:
        raise RuntimeError(f"Create-run response did not include data.id:\n{json.dumps(payload, indent=2)}")
    print(f"Run ID (server): {run_id}")
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


# ── SSH/SCP helpers — used ONLY to pull images (the run uses the HTTP API) ─────────

def _ssh_user_host(robot_ip: str) -> str:
    user = getattr(Config, "ROBOT_SSH_USER", "root") or "root"
    return f"{user}@{robot_ip}"


def _resolve_ssh_key(cli_key: str | None) -> str:
    """--ssh-key > .env ROBOT_SSH_KEY_PATH > ~/.ssh/id_rsa_opentrons (NOT the bare id_rsa)."""
    for candidate in (cli_key, getattr(Config, "ROBOT_SSH_KEY_PATH", ""), str(DEFAULT_SSH_KEY)):
        key = str(candidate or "").strip()
        if key:
            return key
    return str(DEFAULT_SSH_KEY)


def _pull_images(robot_ip: str, cli_key: str | None, run_tag: int, local_name: str) -> None:
    key = _resolve_ssh_key(cli_key)
    remote_dir = f"{REMOTE_VISION_BASE}/run_{run_tag}"
    if not Path(key).exists():
        print(f"\nWARNING: SSH key {key} not found; skipping image pull. "
              f"Images remain on the robot at {remote_dir}.")
        return
    opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no", "-i", key]
    LOCAL_VISION_BASE.mkdir(parents=True, exist_ok=True)
    dest = LOCAL_VISION_BASE / local_name   # readable local name; created by scp
    cmd = ["scp", "-O", "-r", *opts, f"{_ssh_user_host(robot_ip)}:{remote_dir}", str(dest)]
    print(f"\n[pull images] {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        print(f"WARNING: could not pull {remote_dir}. The camera may have failed to capture; "
              f"check the run log above for 'Captured'/'Warning' lines.")
        return
    imgs = sorted(p.name for p in dest.glob("*.jpg")) if dest.exists() else []
    print(f"Images saved to: {dest}")
    print(f"  {', '.join(imgs) if imgs else '(no .jpg found — check camera on the robot)'}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Upload + run the droplet print error-check protocol via the OT-2 HTTP API, "
                    "then pull this run's before/after images.")
    ap.add_argument("--robot-ip", default=Config.ROBOT_IP, help="Robot IP address.")
    ap.add_argument("--protocol", default=str(DEFAULT_PROTOCOL), help="Protocol file to upload.")
    ap.add_argument("--live", action="store_true", help="Run liquid motion + images. Default is a dry run.")
    ap.add_argument("--label", default="", help="Optional suffix on the image-folder run-id.")
    ap.add_argument("--paper-column", type=int, help="Paper column to print on (1 = far left).")
    ap.add_argument("--replicates", type=int, help="Number of prints (each = 8 droplets).")
    ap.add_argument("--droplet-ul", type=float, help="Volume per droplet per channel.")
    ap.add_argument("--dispense-z", type=float, help="Tip height above the paper-proxy well bottom (mm).")
    ap.add_argument("--air-gap-ul", type=float, help="Anti-drip air gap (uL); 0 disables.")
    ap.add_argument("--blow-out", action="store_true", help="Blow out at the paper after each dispense.")
    ap.add_argument("--ssh-key", default=None, help="SSH key for the image pull. Default: id_rsa_opentrons.")
    ap.add_argument("--no-start", action="store_true", help="Upload + create the run, but do not press play.")
    ap.add_argument("--poll-seconds", type=float, default=5.0, help="Status polling interval.")
    # No-ops for command consistency with the other runners (this protocol has no build step).
    ap.add_argument("--skip-build", action="store_true", help="No-op (no build step).")
    ap.add_argument("--skip-validate", action="store_true", help="No-op (no validate step).")
    args = ap.parse_args()

    robot_ip = args.robot_ip
    os.environ["NO_PROXY"] = f"{os.environ.get('NO_PROXY', 'localhost,127.0.0.1')},{robot_ip}"

    protocol_path = Path(args.protocol).resolve()
    if not protocol_path.exists():
        raise FileNotFoundError(f"Protocol not found: {protocol_path}")

    dry_run = not args.live
    run_tag = int(time.time())   # numeric tag the protocol uses to name its robot-side image folder
    stamp = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    image_run_id = f"{stamp}_{args.label}" if args.label else stamp   # readable LOCAL folder name

    # Only send the knobs the user actually set; the rest use the protocol defaults.
    rtp: dict[str, Any] = {"dry_run": dry_run, "run_tag": run_tag}
    if args.paper_column is not None: rtp["paper_start_column"] = args.paper_column
    if args.replicates is not None:   rtp["num_replicates"] = args.replicates
    if args.droplet_ul is not None:   rtp["droplet_volume_ul"] = args.droplet_ul
    if args.dispense_z is not None:   rtp["dispense_z_mm"] = args.dispense_z
    if args.air_gap_ul is not None:   rtp["air_gap_ul"] = args.air_gap_ul
    if args.blow_out:                 rtp["blow_out"] = True

    print("\n=== Droplet Print Error-Check Runner (HTTP API) ===")
    print(f"Robot      : {robot_ip}")
    print(f"Protocol   : {protocol_path}")
    print(f"Mode       : {'LIVE LIQUID RUN' if args.live else 'DRY RUN (no liquid/images)'}")
    print(f"Image run  : {image_run_id}")
    print("Deck       : slot 4 plate (column 9 must hold liquid), slot 5 paper, slot 9 tips.")

    protocol_id = _upload_protocol(robot_ip, protocol_path)
    server_run_id = _create_run(robot_ip, protocol_id, rtp)

    if args.no_start:
        print(f"\nCreated run but did not start it. Server run id: {server_run_id}")
        return 0

    _play_run(robot_ip, server_run_id)
    status = _monitor(robot_ip, server_run_id, args.poll_seconds)
    print(f"\nRun finished with status: {status}")

    if status == "succeeded" and args.live:
        _pull_images(robot_ip, args.ssh_key, run_tag, image_run_id)

    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
