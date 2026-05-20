"""
vision/image_inventory.py
=========================
Scan the local ``data/vision/raw`` tree and produce a CSV inventory
of every image and zip file found.

Usage
-----
>>> from vision.config import load_vision_config
>>> from vision.image_inventory import build_inventory
>>> cfg = load_vision_config()
>>> df = build_inventory(cfg)
>>> print(df.head())
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger("vision.inventory")

# Extensions we catalogue (lower-cased for comparison)
_INVENTORY_EXTENSIONS = {".jpg", ".jpeg", ".png", ".zip"}


def scan_files(
    root_dir: Path,
    extensions: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Walk *root_dir* recursively and collect metadata for matching files.

    Parameters
    ----------
    root_dir : Path
        Top-level directory to scan (e.g. ``data/vision/raw``).
    extensions : set, optional
        File extensions to include.  Defaults to image + zip types.

    Returns
    -------
    list[dict]
        One dict per file with keys:
        ``filename``, ``path``, ``extension``, ``size_bytes``,
        ``modified_time``, ``run_folder``.
    """
    if extensions is None:
        extensions = _INVENTORY_EXTENSIONS

    records: List[Dict[str, Any]] = []

    if not root_dir.exists():
        logger.warning("Raw directory does not exist: %s", root_dir)
        return records

    for filepath in sorted(root_dir.rglob("*")):
        if not filepath.is_file():
            continue
        ext = filepath.suffix.lower()
        if ext not in extensions:
            continue

        stat = filepath.stat()

        # Determine run folder — the immediate parent under root_dir
        try:
            relative = filepath.relative_to(root_dir)
            run_folder = relative.parts[0] if len(relative.parts) > 1 else ""
        except ValueError:
            run_folder = ""

        records.append({
            "filename": filepath.name,
            "path": str(filepath),
            "extension": ext,
            "size_bytes": stat.st_size,
            "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "run_folder": run_folder,
        })

    logger.info("Scanned %d file(s) under %s.", len(records), root_dir)
    return records


def build_inventory(
    cfg: Dict[str, Any],
    scan_dir_override: Optional[Path] = None,
) -> pd.DataFrame:
    """Build a ``DataFrame`` inventory and save it as CSV.

    The CSV is written to ``<logs_dir>/image_inventory.csv``.

    Parameters
    ----------
    cfg : dict
        Resolved vision config.
    scan_dir_override : Path, optional
        Scan this directory instead of the configured raw dir.

    Returns
    -------
    pd.DataFrame
    """
    raw_dir: Path = scan_dir_override or cfg["local"]["raw_dir"]
    logs_dir: Path = cfg["local"]["logs_dir"]
    logs_dir.mkdir(parents=True, exist_ok=True)

    records = scan_files(raw_dir)
    df = pd.DataFrame(
        records,
        columns=[
            "filename",
            "path",
            "extension",
            "size_bytes",
            "modified_time",
            "run_folder",
        ],
    )

    csv_path = logs_dir / "image_inventory.csv"
    df.to_csv(csv_path, index=False)
    logger.info("Inventory written to %s (%d rows).", csv_path, len(df))
    return df
