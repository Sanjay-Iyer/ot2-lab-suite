"""
vision/ot2_camera_workflow.py
=============================
Full first-step camera workflow:

1. Capture images on the OT-2 via SSH + curl.
2. Transfer images to the local laptop via SCP.
3. Print where the files are saved.

Usage::

    python vision/ot2_camera_workflow.py
    python vision/ot2_camera_workflow.py --count 2 --delay 3
    python vision/ot2_camera_workflow.py --dry-run
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

# Ensure the project root is on sys.path so ``vision`` is importable
# when running as ``python vision/ot2_camera_workflow.py``.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import core functions from sibling modules
from vision.capture_ot2_images import load_vision_config, capture_images  # noqa: E402
from vision.transfer_ot2_images import transfer_images  # noqa: E402


# ─── Workflow ───────────────────────────────────────────────────

def run_camera_workflow(
    count: Optional[int] = None,
    delay: Optional[int] = None,
    dry_run: bool = False,
) -> bool:
    """Execute the full capture-and-transfer workflow.

    Parameters
    ----------
    count : int, optional
        Number of images to capture (default from config).
    delay : int, optional
        Seconds between captures (default from config).
    dry_run : bool
        If ``True``, print commands without executing.

    Returns
    -------
    bool
        ``True`` if both steps succeeded.
    """
    # ── 1. Load config ───────────────────────────────────────────
    cfg = load_vision_config()

    count = count if count is not None else cfg["capture"]["default_count"]
    delay = delay if delay is not None else cfg["capture"]["default_delay_seconds"]

    local_raw = (PROJECT_ROOT / cfg["local"]["raw_dir"]).resolve()

    print("=" * 55)
    print("  OT-2 Camera Workflow")
    print("=" * 55)
    print(f"  Robot IP       : {cfg['robot']['ip']}")
    print(f"  Images         : {count}")
    print(f"  Delay          : {delay}s")
    print(f"  Local save dir : {local_raw}")
    if dry_run:
        print("  Mode           : DRY-RUN")
    print("=" * 55)
    print()

    # ── 2. Capture images on the OT-2 ────────────────────────────
    print("── Step 1: Capture images on OT-2 ──")
    capture_ok = capture_images(cfg, count, delay, dry_run=dry_run)
    print()

    if not capture_ok and not dry_run:
        print("[ABORT] Capture failed — skipping transfer.")
        return False

    # ── 3. Transfer images to local machine ──────────────────────
    print("── Step 2: Transfer images to laptop ──")
    transfer_ok = transfer_images(cfg, dry_run=dry_run)
    print()

    # ── 4. Summary ───────────────────────────────────────────────
    print("=" * 55)
    print("  Workflow Summary")
    print("=" * 55)
    print(f"  Capture  : {'OK' if capture_ok else 'FAILED'}")
    print(f"  Transfer : {'OK' if transfer_ok else 'FAILED'}")
    print(f"  Local dir: {local_raw}")
    if dry_run:
        print(f"  Mode     : DRY-RUN (no files were captured or copied)")
    print("=" * 55)

    return capture_ok and transfer_ok


# ─── CLI ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Full OT-2 camera workflow: capture images then transfer to laptop.",
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
        help="Print commands without executing them.",
    )
    args = parser.parse_args()

    success = run_camera_workflow(
        count=args.count,
        delay=args.delay,
        dry_run=args.dry_run,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
