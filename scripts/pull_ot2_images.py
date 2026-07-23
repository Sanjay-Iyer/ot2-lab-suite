#!/usr/bin/env python
"""
scripts/pull_ot2_images.py
==========================
CLI entry-point for the OT-2 image-pull workflow.

Workflow
--------
1. Load & validate configuration.
2. Check robot connectivity.
3. Discover image files on the OT-2.
4. Transfer files to a local timestamped folder.
5. Generate an image inventory CSV.
6. Validate transferred files.
7. Print a clean summary.

Examples
--------
::

    python scripts/pull_ot2_images.py
    python scripts/pull_ot2_images.py --dry-run
    python scripts/pull_ot2_images.py --overwrite
    python scripts/pull_ot2_images.py --remote-dir /data/runs
    python scripts/pull_ot2_images.py --output-dir C:\\data\\custom_output
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Ensure the project root is on sys.path so ``vision`` is importable
# when running as ``python scripts/pull_ot2_images.py``.
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from vision.config import load_vision_config  # noqa: E402
from vision.transfer_images import transfer_images  # noqa: E402
from vision.image_inventory import build_inventory  # noqa: E402
from vision.validate_images import validate_run_folder  # noqa: E402
from src.lab.robot_connection import (  # noqa: E402
    add_robot_host_arguments,
    connection_summary,
)


# ─── Logging setup ──────────────────────────────────────────────

def _setup_logging(logs_dir: Path) -> None:
    """Configure file + console logging for this run."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"vision_transfer_{timestamp}.log"

    # Root logger for the 'vision' namespace
    root_logger = logging.getLogger("vision")
    root_logger.setLevel(logging.DEBUG)

    # File handler — detailed
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(name)-24s | %(levelname)-7s | %(message)s"
        )
    )

    # Console handler — concise
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(
        logging.Formatter("%(levelname)-7s | %(message)s")
    )

    root_logger.addHandler(fh)
    root_logger.addHandler(ch)

    root_logger.info("Log file: %s", log_file)


# ─── CLI ────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull image files from an Opentrons OT-2 robot.",
    )
    add_robot_host_arguments(parser)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be transferred without actually copying files.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Overwrite existing local files if they already exist.",
    )
    parser.add_argument(
        "--remote-dir",
        type=str,
        default=None,
        help="Limit the search to a single remote directory (e.g. /data/runs).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Write transferred files to this local directory instead of the default.",
    )
    return parser.parse_args()


# ─── Main ───────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    # ── 1. Load configuration ────────────────────────────────────
    try:
        cfg = load_vision_config(robot_host=args.robot_host)
        print(connection_summary(cfg["robot"]["host"]))
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n[FATAL] Configuration error:\n  {exc}", file=sys.stderr)
        sys.exit(1)

    # ── 2. Setup logging ─────────────────────────────────────────
    _setup_logging(cfg["local"]["logs_dir"])
    logger = logging.getLogger("vision.cli")
    logger.info("=" * 60)
    logger.info("OT-2 Vision Image Pull — %s", datetime.now().isoformat())
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("*** DRY-RUN MODE — no files will be transferred ***")

    # ── 3. Transfer images ───────────────────────────────────────
    remote_dirs = [args.remote_dir] if args.remote_dir else None
    output_dir = Path(args.output_dir) if args.output_dir else None

    result = transfer_images(
        cfg,
        remote_dirs_override=remote_dirs,
        output_dir_override=output_dir,
        dry_run_override=args.dry_run,
        overwrite_override=args.overwrite,
    )

    # ── 4. Inventory ─────────────────────────────────────────────
    if result["files_transferred"] > 0 and not args.dry_run:
        scan_dir = Path(result["local_run_dir"]) if result["local_run_dir"] else None
        inv_df = build_inventory(cfg, scan_dir_override=scan_dir)
        logger.info("Inventory: %d file(s) catalogued.", len(inv_df))
    else:
        inv_df = None
        logger.info("Skipping inventory (no files transferred or dry-run).")

    # ── 5. Validation ────────────────────────────────────────────
    if result["files_transferred"] > 0 and not args.dry_run:
        run_path = Path(result["local_run_dir"]) if result["local_run_dir"] else None
        val_df = validate_run_folder(cfg, run_dir=run_path)
        passed = int(val_df["valid"].sum()) if val_df is not None else 0
        failed = (len(val_df) - passed) if val_df is not None else 0
    else:
        passed = failed = 0
        logger.info("Skipping validation (no files transferred or dry-run).")

    # ── 6. Summary ───────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  OT-2 Vision — Transfer Summary")
    print("=" * 60)
    print(f"  Robot IP        : {cfg['robot']['host']}")
    print(f"  Dry-run         : {args.dry_run}")
    print(f"  Overwrite       : {args.overwrite}")
    print(f"  Files transferred: {result['files_transferred']}")
    if result["local_run_dir"]:
        print(f"  Local run dir   : {result['local_run_dir']}")
    if result["failed_paths"]:
        print(f"  Failed paths    : {len(result['failed_paths'])}")
        for fp in result["failed_paths"]:
            print(f"    - {fp}")
    if result["warnings"]:
        print(f"  Warnings        : {len(result['warnings'])}")
        for w in result["warnings"]:
            print(f"    - {w}")
    if not args.dry_run and result["files_transferred"] > 0:
        print(f"  Validation      : {passed} passed, {failed} failed")
    print("=" * 60 + "\n")

    if not result["success"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
