"""
vision/validate_images.py
=========================
Validate transferred image and archive files:

* Existence and minimum file-size checks.
* Allowed-extension check.
* Integrity check — open images with Pillow, open ``.zip`` files
  with :mod:`zipfile`.
* Write a CSV validation report to the logs directory.
"""

import logging
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from PIL import Image

logger = logging.getLogger("vision.validate")

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
_ARCHIVE_EXTENSIONS = {".zip"}


# ─── Per-file validation ────────────────────────────────────────

def _validate_single_file(
    filepath: Path,
    min_size: int,
    allowed_ext: set,
) -> Dict[str, Any]:
    """Return a validation record dict for one file."""
    record: Dict[str, Any] = {
        "filename": filepath.name,
        "path": str(filepath),
        "valid": True,
        "reason": "OK",
        "size_bytes": 0,
    }

    # 1. Existence
    if not filepath.exists():
        record["valid"] = False
        record["reason"] = "File does not exist"
        return record

    stat = filepath.stat()
    record["size_bytes"] = stat.st_size

    # 2. Extension
    ext = filepath.suffix.lower()
    if ext not in allowed_ext:
        record["valid"] = False
        record["reason"] = f"Disallowed extension: {ext}"
        return record

    # 3. Minimum size
    if stat.st_size < min_size:
        record["valid"] = False
        record["reason"] = (
            f"File too small ({stat.st_size} B < {min_size} B threshold)"
        )
        return record

    # 4. Integrity check
    if ext in _IMAGE_EXTENSIONS:
        try:
            with Image.open(filepath) as img:
                img.verify()
        except Exception as exc:
            record["valid"] = False
            record["reason"] = f"Image corrupt or unreadable: {exc}"
            return record

    elif ext in _ARCHIVE_EXTENSIONS:
        try:
            with zipfile.ZipFile(filepath, "r") as zf:
                bad = zf.testzip()
                if bad is not None:
                    record["valid"] = False
                    record["reason"] = f"Corrupt entry in zip: {bad}"
                    return record
        except Exception as exc:
            record["valid"] = False
            record["reason"] = f"Zip unreadable: {exc}"
            return record

    return record


# ─── Batch validation ───────────────────────────────────────────

def validate_files(
    file_paths: List[Path],
    cfg: Dict[str, Any],
) -> pd.DataFrame:
    """Validate a list of file paths and return a DataFrame of results.

    Parameters
    ----------
    file_paths : list[Path]
        Files to check.
    cfg : dict
        Resolved vision config (used for thresholds).

    Returns
    -------
    pd.DataFrame
    """
    validation_cfg = cfg.get("validation", {})
    min_size = validation_cfg.get("min_file_size_bytes", 1000)
    allowed_ext = set(validation_cfg.get("allowed_extensions", [".jpg", ".jpeg", ".png", ".zip"]))

    records = [
        _validate_single_file(fp, min_size, allowed_ext)
        for fp in file_paths
    ]
    df = pd.DataFrame(
        records,
        columns=["filename", "path", "valid", "reason", "size_bytes"],
    )
    return df


def validate_run_folder(
    cfg: Dict[str, Any],
    run_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Validate every file in a run folder and save a CSV report.

    Parameters
    ----------
    cfg : dict
        Resolved vision config.
    run_dir : Path, optional
        Directory to scan.  Defaults to ``data/vision/raw``.

    Returns
    -------
    pd.DataFrame
    """
    scan_dir = run_dir or cfg["local"]["raw_dir"]
    logs_dir: Path = cfg["local"]["logs_dir"]
    logs_dir.mkdir(parents=True, exist_ok=True)

    allowed_ext = set(
        cfg.get("validation", {}).get(
            "allowed_extensions",
            [".jpg", ".jpeg", ".png", ".zip"],
        )
    )

    # Collect all matching files
    files = sorted(
        fp
        for fp in scan_dir.rglob("*")
        if fp.is_file() and fp.suffix.lower() in allowed_ext
    )

    logger.info("Validating %d file(s) under %s …", len(files), scan_dir)
    df = validate_files(files, cfg)

    csv_path = logs_dir / "image_validation_report.csv"
    df.to_csv(csv_path, index=False)

    passed = int(df["valid"].sum())
    failed = len(df) - passed
    logger.info(
        "Validation report → %s  |  %d passed, %d failed.",
        csv_path,
        passed,
        failed,
    )
    return df
