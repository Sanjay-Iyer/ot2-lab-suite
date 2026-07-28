#!/usr/bin/env python
"""Create metadata-rich Raman filenames from configs/raman_rename.yaml."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

RAMAN_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(RAMAN_ROOT / "src"))

from raman_lib.naming import execute_renames, plan_renames, write_manifest  # noqa: E402
from raman_lib.workflow_config import load_rename_config  # noqa: E402

DEFAULT_CONFIG = RAMAN_ROOT / "configs" / "raman_rename.yaml"


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else RAMAN_ROOT / path


def main() -> int:
    """Preflight and execute the YAML-configured copy/move operation."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        config = load_rename_config(DEFAULT_CONFIG)
        rename = config["rename"]
        input_root = _resolve(rename["input_root"])
        output_root = _resolve(rename["output_root"])
        planned = plan_renames(input_root, output_root, rename)
        # Persist the full plan before any copy/move so a partial I/O failure
        # cannot erase the intended old-to-new mapping.
        manifest = write_manifest(
            planned,
            output_root,
            rename["manifest_file"],
            dry_run=bool(rename["dry_run"]),
        )
        completed = execute_renames(
            planned,
            rename,
            progress_manifest=manifest if not rename["dry_run"] else None,
        )
        if not rename["dry_run"]:
            manifest = write_manifest(
                completed,
                output_root,
                rename["manifest_file"],
                dry_run=False,
            )
    except Exception as exc:
        logging.exception("Raman filename migration failed: %s", exc)
        return 1

    status = "DRY RUN" if rename["dry_run"] else rename["operation"].upper()
    logging.info("%s: %d files planned", status, len(completed))
    for item in completed:
        logging.info("%s -> %s", item.source.name, item.destination.name)
    logging.info("Manifest: %s", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
