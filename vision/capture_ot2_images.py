"""
vision/capture_ot2_images.py
============================
SSH into the OT-2 and take pictures using the built-in camera API.

Images are saved on the robot in the remote vision directory
(default ``/data/vision``) with timestamped filenames.

Usage::

    python vision/capture_ot2_images.py
    python vision/capture_ot2_images.py --count 5 --delay 2
    python vision/capture_ot2_images.py --dry-run
"""

import argparse
import subprocess
import sys
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional


# ─── Project root (vision/capture_ot2_images.py → parent.parent) ─
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ─── Config loader ──────────────────────────────────────────────

def load_vision_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load ``configs/vision.yaml`` from the project root.

    Parameters
    ----------
    config_path : Path, optional
        Override the default config file location.

    Returns
    -------
    dict
        Parsed YAML configuration.
    """
    if config_path is None:
        config_path = PROJECT_ROOT / "configs" / "vision.yaml"

    if not config_path.exists():
        print(
            f"[ERROR] Config file not found: {config_path}\n"
            f"  Expected at: {PROJECT_ROOT / 'configs' / 'vision.yaml'}",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─── Command builders ──────────────────────────────────────────

def build_remote_capture_command(
    cfg: Dict[str, Any],
    count: int,
    delay: int,
) -> str:
    """Build the shell command that runs *on the OT-2* to capture images.

    The generated command is equivalent to::

        mkdir -p /data/vision ;
        curl -s -X POST -H 'opentrons-version: *' \\
            http://localhost:31950/camera/picture \\
            --output /data/vision/ot2_image_$(date +%Y%m%d_%H%M%S).jpg ;
        echo 'Captured image 1' ;
        sleep 3 ;
        ...
    """
    remote_dir = cfg["remote"]["vision_dir"]
    endpoint = cfg["remote"]["camera_endpoint"]
    header_val = cfg["remote"]["opentrons_version_header"]
    prefix = cfg["capture"]["filename_prefix"]

    # Start by ensuring the remote directory exists
    parts: List[str] = [f"mkdir -p {remote_dir}"]

    for i in range(1, count + 1):
        filename = f"{prefix}_$(date +%Y%m%d_%H%M%S).jpg"
        output_path = f"{remote_dir}/{filename}"

        parts.append(
            f"curl -s -X POST "
            f"-H 'opentrons-version: {header_val}' "
            f"{endpoint} "
            f"--output {output_path}"
        )
        parts.append(f"echo 'Captured image {i}'")

        # Add delay between captures (not after the last one)
        if i < count:
            parts.append(f"sleep {delay}")

    return " ; ".join(parts)


def build_ssh_command(
    cfg: Dict[str, Any],
    remote_command: str,
) -> List[str]:
    """Build the ``subprocess`` argument list for an SSH call.

    Returns a list like::

        ["ssh", "-o", "BatchMode=yes", ..., "-i", key,
         "root@169.254.46.57", "<remote_command>"]
    """
    ip = cfg["robot"]["ip"]
    user = cfg["robot"]["user"]
    key = cfg["robot"]["ssh_key_path"]

    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=30",
        "-o", "StrictHostKeyChecking=no",
        "-i", key,
        f"{user}@{ip}",
        remote_command,
    ]


# ─── Core function ──────────────────────────────────────────────

def capture_images(
    cfg: Dict[str, Any],
    count: int,
    delay: int,
    dry_run: bool = False,
) -> bool:
    """Capture *count* images on the OT-2.

    Parameters
    ----------
    cfg : dict
        Parsed vision config.
    count : int
        Number of images to take.
    delay : int
        Seconds to wait between captures.
    dry_run : bool
        If ``True``, print the command without executing.

    Returns
    -------
    bool
        ``True`` if the capture succeeded (or dry-run).
    """
    # Validate SSH key exists locally (skip during dry-run)
    key_path = Path(cfg["robot"]["ssh_key_path"])
    if not dry_run and not key_path.exists():
        print(
            f"[ERROR] SSH key not found: {key_path}\n"
            f"  Check robot.ssh_key_path in configs/vision.yaml",
            file=sys.stderr,
        )
        return False

    remote_cmd = build_remote_capture_command(cfg, count, delay)
    ssh_cmd = build_ssh_command(cfg, remote_cmd)

    if dry_run:
        print("[DRY-RUN] Would execute:")
        print(f"  SSH target  : {cfg['robot']['user']}@{cfg['robot']['ip']}")
        print(f"  Remote cmd  : {remote_cmd}")
        print(f"  Full command: {' '.join(ssh_cmd)}")
        return True

    print(f"Capturing {count} image(s) on OT-2 ({cfg['robot']['ip']})...")
    print(f"  Delay between captures: {delay}s")
    print(f"  Remote save dir       : {cfg['remote']['vision_dir']}")
    print()

    result = subprocess.run(ssh_cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print("Capture completed successfully.")
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                print(f"  {line}")
        return True
    else:
        print(
            f"[ERROR] Capture FAILED (return code {result.returncode}).",
            file=sys.stderr,
        )
        print(f"  Command : {' '.join(ssh_cmd)}", file=sys.stderr)
        if result.stdout.strip():
            print(f"  stdout  : {result.stdout.strip()}", file=sys.stderr)
        if result.stderr.strip():
            print(f"  stderr  : {result.stderr.strip()}", file=sys.stderr)
        return False


# ─── CLI ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture images on the Opentrons OT-2 robot.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Number of images to capture (default: from config).",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=None,
        help="Seconds between captures (default: from config).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the command without executing it.",
    )
    args = parser.parse_args()

    cfg = load_vision_config()
    count = args.count if args.count is not None else cfg["capture"]["default_count"]
    delay = args.delay if args.delay is not None else cfg["capture"]["default_delay_seconds"]

    success = capture_images(cfg, count, delay, dry_run=args.dry_run)

    if success:
        print("\n--- Capture Summary ---")
        print(f"  Images requested : {count}")
        print(f"  Remote directory : {cfg['remote']['vision_dir']}")
        print(f"  Status           : {'DRY-RUN' if args.dry_run else 'OK'}")
    else:
        print("\n--- Capture Summary ---")
        print(f"  Status           : FAILED")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
