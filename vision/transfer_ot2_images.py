"""
vision/transfer_ot2_images.py
=============================
Copy image files from the OT-2 to the local laptop using ``scp -O``.

The OT-2 does **not** have ``sftp-server`` installed, so normal SFTP
mode fails with ``sh: /usr/libexec/sftp-server: not found``.
The ``-O`` flag forces the legacy SCP protocol which works reliably.

Usage::

    python vision/transfer_ot2_images.py
    python vision/transfer_ot2_images.py --dry-run
    python vision/transfer_ot2_images.py --remote-dir /data/vision --local-dir vision/raw
"""

import argparse
import subprocess
import sys
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional


# ─── Project root (vision/transfer_ot2_images.py → parent.parent) ─
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


# ─── Helpers ────────────────────────────────────────────────────

def ensure_local_dir(local_dir: Path) -> None:
    """Create the local directory if it does not exist.

    SCP will fail if the destination directory is missing,
    so we always create it up-front.
    """
    local_dir.mkdir(parents=True, exist_ok=True)


def build_scp_command(
    cfg: Dict[str, Any],
    remote_dir: str,
    local_dir: str,
) -> List[str]:
    """Build the ``subprocess`` argument list for an SCP call.

    Returns a list like::

        ["scp", "-O", "-i", key, "-r",
         "root@169.254.46.57:/data/vision/*",
         "vision/raw"]

    .. important::

       ``-O`` (capital O) forces legacy SCP protocol.
       This is **required** because the OT-2 lacks ``sftp-server``.
       Do NOT confuse with ``-o`` (lowercase), which sets SSH options.
    """
    ip = cfg["robot"]["ip"]
    user = cfg["robot"]["user"]
    key = cfg["robot"]["ssh_key_path"]

    cmd: List[str] = [
        "scp",
        "-O",                        # Capital O — legacy SCP protocol
        "-i", key,
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=30",
        "-o", "StrictHostKeyChecking=no",
        "-r",                        # Recursive copy
        f"{user}@{ip}:{remote_dir}/*",
        local_dir,
    ]
    return cmd


# ─── Core function ──────────────────────────────────────────────

def transfer_images(
    cfg: Dict[str, Any],
    remote_dir: Optional[str] = None,
    local_dir: Optional[str] = None,
    dry_run: bool = False,
) -> bool:
    """Transfer images from the OT-2 to the local machine.

    Parameters
    ----------
    cfg : dict
        Parsed vision config.
    remote_dir : str, optional
        Override the remote directory (default from config).
    local_dir : str, optional
        Override the local directory (default from config).
    dry_run : bool
        If ``True``, print the command without executing.

    Returns
    -------
    bool
        ``True`` if the transfer succeeded (or dry-run).
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

    # Resolve directories
    r_dir = remote_dir or cfg["remote"]["vision_dir"]

    if local_dir:
        l_dir = Path(local_dir).resolve()
    else:
        # Resolve the config-relative path against the project root
        l_dir = (PROJECT_ROOT / cfg["local"]["raw_dir"]).resolve()

    # Ensure local directory exists before SCP
    if not dry_run:
        ensure_local_dir(l_dir)

    scp_cmd = build_scp_command(cfg, r_dir, str(l_dir))

    if dry_run:
        print("[DRY-RUN] Would execute:")
        print(f"  Remote source : {cfg['robot']['user']}@{cfg['robot']['ip']}:{r_dir}/*")
        print(f"  Local dest    : {l_dir}")
        print(f"  Full command  : {' '.join(scp_cmd)}")
        return True

    print(f"Transferring images from OT-2 ({cfg['robot']['ip']})...")
    print(f"  Remote dir : {r_dir}")
    print(f"  Local dir  : {l_dir}")
    print()

    result = subprocess.run(scp_cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print("Transfer completed successfully.")
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                print(f"  {line}")
        return True
    else:
        print(
            f"[ERROR] Transfer FAILED (return code {result.returncode}).",
            file=sys.stderr,
        )
        print(f"  Command : {' '.join(scp_cmd)}", file=sys.stderr)
        if result.stdout.strip():
            print(f"  stdout  : {result.stdout.strip()}", file=sys.stderr)
        if result.stderr.strip():
            print(f"  stderr  : {result.stderr.strip()}", file=sys.stderr)
        return False


# ─── CLI ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transfer images from the Opentrons OT-2 to the local machine.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the command without executing it.",
    )
    parser.add_argument(
        "--remote-dir",
        type=str,
        default=None,
        help="Remote directory to copy from (default: from config).",
    )
    parser.add_argument(
        "--local-dir",
        type=str,
        default=None,
        help="Local directory to copy into (default: from config).",
    )
    args = parser.parse_args()

    cfg = load_vision_config()

    # Resolve local dir for summary display
    if args.local_dir:
        display_local = Path(args.local_dir).resolve()
    else:
        display_local = (PROJECT_ROOT / cfg["local"]["raw_dir"]).resolve()

    success = transfer_images(
        cfg,
        remote_dir=args.remote_dir,
        local_dir=args.local_dir,
        dry_run=args.dry_run,
    )

    if success:
        print("\n--- Transfer Summary ---")
        print(f"  Remote source : {args.remote_dir or cfg['remote']['vision_dir']}")
        print(f"  Local dest    : {display_local}")
        print(f"  Status        : {'DRY-RUN' if args.dry_run else 'OK'}")
    else:
        print("\n--- Transfer Summary ---")
        print(f"  Status        : FAILED")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
