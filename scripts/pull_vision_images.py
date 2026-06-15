#!/usr/bin/env python3
"""
scripts/pull_vision_images.py
=============================
Pull a robot-side vision folder (camera images) down to the laptop, into a timestamped
local folder so each pull is preserved (the robot overwrites same-named files each run).

Standalone — run it any time AFTER a robot run to grab that run's images.

  # default: pull the vial-print images
  python scripts/pull_vision_images.py --robot-ip 169.254.46.57

  # any other vision folder on the robot
  python scripts/pull_vision_images.py --robot-ip 169.254.46.57 --remote-dir /data/vision/droplet_error_check
  python scripts/pull_vision_images.py --robot-ip 169.254.46.57 --label run3

Uses the OT-2 key (--ssh-key > .env ROBOT_SSH_KEY_PATH > ~/.ssh/id_rsa_opentrons),
BatchMode (no password prompt), and `scp -O` (required by the OT-2's SSH server).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import Config
from src.utils.robot_run_log import RobotRunLog, repo_relative

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "id_rsa_opentrons"
DEFAULT_REMOTE_DIR = "/data/vision/vial_dilution_print"
LOCAL_BASE = REPO / "vision_runs"


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


def main() -> int:
    ap = argparse.ArgumentParser(description="Pull a robot vision/image folder to the laptop.")
    ap.add_argument("--robot-ip", default=Config.ROBOT_IP, help="Robot IP address.")
    ap.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR,
                    help=f"Robot folder to pull. Default: {DEFAULT_REMOTE_DIR}")
    ap.add_argument("--dest", default=None,
                    help="Local destination folder. Default: vision_runs/<name>/<timestamp>.")
    ap.add_argument("--label", default="", help="Optional suffix on the local folder name.")
    ap.add_argument("--ssh-key", default=None, help="SSH key. Default: id_rsa_opentrons.")
    args = ap.parse_args()
    run_log = RobotRunLog(Path(__file__).name)
    print(f"Run log    : {run_log.path}")

    key = _resolve_ssh_key(args.ssh_key)
    run_log.update(
        robot_ip=args.robot_ip,
        remote_dir=args.remote_dir.rstrip("/"),
        ssh_key=key,
    )
    if not Path(key).exists():
        print(f"ERROR: SSH key not found at {key}. Pass --ssh-key or set .env ROBOT_SSH_KEY_PATH.")
        run_log.finish("error", exit_code=1, error=f"SSH key not found at {key}")
        return 1

    remote_dir = args.remote_dir.rstrip("/")
    name = remote_dir.rsplit("/", 1)[-1] or "vision"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = f"{stamp}_{args.label}" if args.label else stamp
    dest = Path(args.dest).resolve() if args.dest else (LOCAL_BASE / name / folder)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_log.update(local_dest=repo_relative(dest))

    opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no", "-i", key]
    cmd = ["scp", "-O", "-r", *opts, f"{_ssh_user_host(args.robot_ip)}:{remote_dir}", str(dest)]

    print("=== Pull Vision Images ===")
    print(f"Robot      : {_ssh_user_host(args.robot_ip)}")
    print(f"Remote dir : {remote_dir}")
    print(f"Local dest : {dest}")
    print(f"\n[scp] {' '.join(cmd)}")
    run_log.event("image_pull_started", remote_dir=remote_dir, local_dest=repo_relative(dest))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        print(f"\nERROR: pull failed. Does {remote_dir} exist on the robot, and did a run "
              f"capture images there? (Run the protocol with the camera enabled first.)")
        run_log.finish("error", exit_code=1, error=f"Pull failed for {remote_dir}")
        return 1

    imgs = sorted(p.name for p in dest.glob("*.jpg")) if dest.exists() else []
    run_log.event("image_pull_finished", image_count=len(imgs), images=imgs)
    run_log.finish("succeeded", exit_code=0)
    print(f"\nDone. {len(imgs)} image(s) saved to: {dest}")
    if imgs:
        print("  " + ", ".join(imgs))
    else:
        print("  (no .jpg found — the folder may be empty or the camera capture failed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
