#!/usr/bin/env python3
"""
scripts/run_droplet_error_check.py
==================================
Terminal runner for src/protocols/printing_droplet_error_check.py (apiLevel 2.15).

It does the whole loop from the lab laptop in one command:
  1. picks a unique RUN ID (timestamp, plus an optional --label),
  2. scp's the protocol onto the robot,
  3. runs it over SSH with `opentrons_execute`, passing the run ID + debug knobs as
     PRINT_* env vars (the protocol captures print_before.jpg / print_after.jpg into a
     per-run folder on the robot),
  4. scp's that run folder back to  vision_runs/droplet_error_check/<run_id>/  here.

  python scripts/run_droplet_error_check.py --robot-ip 169.254.46.57 --live --paper-column 1 --replicates 3
  python scripts/run_droplet_error_check.py --robot-ip 169.254.46.57 --live --paper-column 5 \
      --dispense-z 1.0 --droplet-ul 25 --air-gap-ul 0 --label lowgap
  python scripts/run_droplet_error_check.py --robot-ip 169.254.46.57            # dry run (no liquid/images)

Live runs (--live) capture print_before.jpg / print_after.jpg on the robot and pull them back
to vision_runs/droplet_error_check/<run_id>/. Default (no --live) is a dry motion check.

Requires SSH/SCP access to the robot (the lab laptop's key), same as scripts/sync_robot.py.
This file only orchestrates ssh/scp; the liquid handling lives in the protocol.
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

REPO            = Path(__file__).resolve().parent.parent
LOCAL_PROTOCOL  = REPO / "src" / "protocols" / "printing_droplet_error_check.py"
REMOTE_PROTOCOL = "/data/printing_droplet_error_check.py"
# Must match CONFIG["camera"]["robot_image_dir"] in the protocol.
REMOTE_VISION_BASE = "/data/vision/droplet_error_check"
LOCAL_VISION_BASE  = REPO / "vision_runs" / "droplet_error_check"


def _ssh_user_host(robot_ip: str) -> str:
    user = getattr(Config, "ROBOT_SSH_USER", "root") or "root"
    return f"{user}@{robot_ip}"


def _ssh_opts() -> list[str]:
    """SSH/SCP options matching the repo's camera tooling (test_ot2_camera_capture.py):
    use the dedicated robot key from .env (ROBOT_SSH_KEY_PATH), batch mode (fail fast
    instead of prompting for a password), and skip the host-key prompt.

    Without -i <key>, ssh falls back to ~/.ssh/id_rsa, which the robot does NOT accept
    (that is what caused the passphrase prompt + 'Permission denied (publickey)').
    """
    key = str(getattr(Config, "ROBOT_SSH_KEY_PATH", "") or "").strip()
    if not key:
        raise SystemExit(
            "ERROR: ROBOT_SSH_KEY_PATH is not set in .env.\n"
            "This runner uses SSH/SCP with the OT-2's dedicated key (same as "
            "scripts/test_ot2_camera_capture.py). Set ROBOT_SSH_KEY_PATH=<path to the "
            "OT-2 private key> in .env and retry."
        )
    if not Path(key).exists():
        raise SystemExit(f"ERROR: SSH key not found at {key} (from .env ROBOT_SSH_KEY_PATH).")
    return ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no", "-i", key]


def _run(cmd: list[str], label: str) -> None:
    print(f"\n[{label}] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def _build_env_prefix(run_id: str, args) -> str:
    """PRINT_* assignments for the remote shell. Only include knobs the user set."""
    env = [f"PRINT_RUN_ID={run_id}"]
    if args.paper_column is not None:
        env.append(f"PRINT_PAPER_COLUMN={args.paper_column}")
    if args.replicates is not None:
        env.append(f"PRINT_REPLICATES={args.replicates}")
    if args.droplet_ul is not None:
        env.append(f"PRINT_DROPLET_UL={args.droplet_ul}")
    if args.dispense_z is not None:
        env.append(f"PRINT_DISPENSE_Z={args.dispense_z}")
    if args.air_gap_ul is not None:
        env.append(f"PRINT_AIR_GAP_UL={args.air_gap_ul}")
    if args.blow_out:
        env.append("PRINT_BLOW_OUT=1")
    if not args.live:
        env.append("PRINT_DRY_RUN=1")
    return " ".join(env)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Deploy, run (via opentrons_execute over SSH), and pull the images "
                    "for the droplet print error-check protocol.")
    ap.add_argument("--robot-ip", default=Config.ROBOT_IP, help="Robot IP address.")
    ap.add_argument("--label", default="", help="Optional suffix added to the run-id folder name.")
    ap.add_argument("--paper-column", type=int, help="Paper column to print on (1 = far left).")
    ap.add_argument("--replicates", type=int, help="Number of prints (each = 8 droplets).")
    ap.add_argument("--droplet-ul", type=float, help="Volume per droplet per channel.")
    ap.add_argument("--dispense-z", type=float, help="Tip height above the paper-proxy well bottom (mm).")
    ap.add_argument("--air-gap-ul", type=float, help="Anti-drip air gap (uL); 0 disables.")
    ap.add_argument("--blow-out", action="store_true", help="Blow out at the paper after each dispense.")
    ap.add_argument("--live", action="store_true",
                    help="Run liquid motion and capture images. Default is a dry run (no liquid/images).")
    # Accepted no-ops so the usual `--live --skip-build [--skip-validate]` command works.
    # This protocol is self-contained (no build/validate step), so these do nothing here.
    ap.add_argument("--skip-build", action="store_true",
                    help="No-op (this protocol has no build step). Accepted for command consistency.")
    ap.add_argument("--skip-validate", action="store_true",
                    help="No-op (this protocol has no validate step). Accepted for command consistency.")
    args = ap.parse_args()

    robot_ip  = args.robot_ip
    user_host = _ssh_user_host(robot_ip)
    ssh_opts  = _ssh_opts()   # dedicated robot key from .env; fails clearly if unset

    if not LOCAL_PROTOCOL.exists():
        print(f"ERROR: protocol not found: {LOCAL_PROTOCOL}")
        return 1

    stamp  = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_id = f"{stamp}_{args.label}" if args.label else stamp
    remote_run_dir = f"{REMOTE_VISION_BASE}/{run_id}"
    local_dest     = LOCAL_VISION_BASE / run_id

    print("=== Droplet Print Error-Check Runner ===")
    print(f"Robot   : {user_host}")
    print(f"Run ID  : {run_id}")
    print(f"Mode    : {'LIVE LIQUID RUN' if args.live else 'DRY RUN (no liquid/images)'}")
    print("Deck    : slot 4 plate (column 9 must hold liquid), slot 5 paper, slot 9 tips.")

    # 1. Deploy the protocol.
    _run(["scp", "-O", *ssh_opts, str(LOCAL_PROTOCOL), f"{user_host}:{REMOTE_PROTOCOL}"], "deploy")

    # 2. Run it over SSH with the run-id + knobs as env vars.
    remote_cmd = f"{_build_env_prefix(run_id, args)} opentrons_execute {REMOTE_PROTOCOL}"
    _run(["ssh", *ssh_opts, user_host, remote_cmd], "run")

    # 3. Pull the per-run image folder back into vision_runs/droplet_error_check/<run_id>/.
    LOCAL_VISION_BASE.mkdir(parents=True, exist_ok=True)
    try:
        _run(["scp", "-O", "-r", *ssh_opts,
              f"{user_host}:{remote_run_dir}", str(LOCAL_VISION_BASE)], "pull images")
    except subprocess.CalledProcessError:
        print(f"\nWARNING: could not pull {remote_run_dir}. The run may have had the camera "
              f"disabled or failed to capture. Check the run log above.")
        return 1

    imgs = sorted(p.name for p in local_dest.glob("*.jpg")) if local_dest.exists() else []
    print(f"\nDone. Images saved to: {local_dest}")
    print(f"  {', '.join(imgs) if imgs else '(no .jpg files found — check camera on the robot)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
