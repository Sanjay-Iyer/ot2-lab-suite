"""Plate mapping + result-file naming.

A *plate config* (plates/<sample>.yaml) describes how scan numbers map onto
physical dots on the printing paper:

    columns: [A, B, C]        # dynamic - any number of columns
    rows:    [1, 2, ... 8]    # dynamic - any number of rows
    start_scan: 673           # the scan number of the very first dot
    order: column_major       # fill a column top-to-bottom, then next column

With `order: column_major` and the example above, scan 00673 -> A1, 00674 -> A2,
... 00680 -> A8, 00681 -> B1, ... 00696 -> C8. `row_major` fills a row across
columns first instead.

The result file/folder is then named:

    {sample}_{spot}_{date}    e.g.  bp_A1_072726

If a scan can't be mapped (no plate given, scan number not found in the file
name, or scan out of the plate's range) it falls back to:

    {stem}_{date}             e.g.  Scan_00649_random_072726

so you can still tell the runs apart and figure out the sample later.
"""
from __future__ import annotations

import re
from datetime import date as _date
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SCAN_REGEX = r"(?i)scan[ _-]?0*(\d+)"
DEFAULT_DATE_FORMAT = "%m%d%y"                  # MMDDYY -> 072726
DEFAULT_NAME_PATTERN = "{sample}_{spot}_{date}"
DEFAULT_FALLBACK_PATTERN = "{stem}_{date}"
DEFAULT_ORDER = "column_major"
_ORDERS = {"column_major", "row_major"}


class PlateError(ValueError):
    """Raised when a plate config is structurally invalid."""


# ---------------------------------------------------------------------------
# Loading / validation
# ---------------------------------------------------------------------------
def load_plate(path: str | Path) -> dict[str, Any]:
    """Load and validate a plate YAML config, filling in naming defaults."""
    path = Path(path)
    if not path.is_file():
        raise PlateError(f"Plate config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise PlateError("Plate config root must be a mapping.")

    plate = {
        "name": raw.get("name") or path.stem,
        "description": raw.get("description", ""),
        "columns": raw.get("columns"),
        "rows": raw.get("rows"),
        "start_scan": raw.get("start_scan"),
        "order": raw.get("order", DEFAULT_ORDER),
        "scan_regex": raw.get("scan_regex", DEFAULT_SCAN_REGEX),
        "date_format": raw.get("date_format", DEFAULT_DATE_FORMAT),
        "name_pattern": raw.get("name_pattern", DEFAULT_NAME_PATTERN),
        "fallback_pattern": raw.get("fallback_pattern", DEFAULT_FALLBACK_PATTERN),
        "_source_path": str(path.resolve()),
    }
    _validate_plate(plate)
    return plate


def _validate_plate(plate: dict[str, Any]) -> None:
    if not isinstance(plate["columns"], list) or not plate["columns"]:
        raise PlateError("plate.columns must be a non-empty list, e.g. [A, B, C]")
    if not isinstance(plate["rows"], list) or not plate["rows"]:
        raise PlateError("plate.rows must be a non-empty list, e.g. [1, 2, 3, 4, 5, 6, 7, 8]")
    if plate["start_scan"] is None:
        raise PlateError("plate.start_scan is required (scan number of the first dot).")
    try:
        int(plate["start_scan"])
    except (TypeError, ValueError):
        raise PlateError(f"plate.start_scan must be an integer, got {plate['start_scan']!r}")
    if plate["order"] not in _ORDERS:
        raise PlateError(f"plate.order={plate['order']!r} not in {sorted(_ORDERS)}")
    # regex must compile
    try:
        re.compile(plate["scan_regex"])
    except re.error as exc:
        raise PlateError(f"plate.scan_regex is not a valid regex: {exc}")


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------
def capacity(plate: dict[str, Any]) -> int:
    return len(plate["columns"]) * len(plate["rows"])


def extract_scan_number(stem: str, scan_regex: str = DEFAULT_SCAN_REGEX) -> int | None:
    """Pull the scan number out of a filename stem. Returns None if not found."""
    m = re.search(scan_regex, stem)
    return int(m.group(1)) if m else None


def spot_for_index(i: int, plate: dict[str, Any]) -> dict[str, Any] | None:
    """Map a 0-based sequence index to a {column, row, spot}. None if out of range."""
    cols, rows = plate["columns"], plate["rows"]
    if i < 0 or i >= len(cols) * len(rows):
        return None
    if plate["order"] == "column_major":
        ci, ri = divmod(i, len(rows))       # fill a column (all rows) before next column
    else:  # row_major
        ri, ci = divmod(i, len(cols))       # fill a row (all columns) before next row
    col, row = cols[ci], rows[ri]
    return {"column": str(col), "row": str(row), "spot": f"{col}{row}", "index": i}


def spot_for_scan(scan_number: int, plate: dict[str, Any]) -> dict[str, Any] | None:
    """Map an absolute scan number to a spot dict. None if outside the plate range."""
    return spot_for_index(int(scan_number) - int(plate["start_scan"]), plate)


def build_map(plate: dict[str, Any], scan_range=None) -> list[dict[str, Any]]:
    """Return the full scan->spot table. Defaults to the plate's own scan range."""
    if scan_range is None:
        start = int(plate["start_scan"])
        scan_range = range(start, start + capacity(plate))
    table = []
    for s in scan_range:
        sp = spot_for_scan(s, plate)
        table.append({
            "scan": int(s),
            "column": sp["column"] if sp else None,
            "row": sp["row"] if sp else None,
            "spot": sp["spot"] if sp else None,
            "in_range": sp is not None,
        })
    return table


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------
def today_str(date_format: str = DEFAULT_DATE_FORMAT) -> str:
    return _date.today().strftime(date_format)


def build_label(stem: str, plate: dict[str, Any] | None, date_str: str) -> tuple[str, dict[str, Any]]:
    """Compute the output label for one input file plus a mapping-info dict.

    Uses the plate spot name when the file maps to a spot; otherwise falls back
    to {stem}_{date}.
    """
    scan_regex = plate["scan_regex"] if plate else DEFAULT_SCAN_REGEX
    scan_number = extract_scan_number(stem, scan_regex)

    if plate is not None and scan_number is not None:
        sp = spot_for_scan(scan_number, plate)
        if sp is not None:
            label = plate["name_pattern"].format(
                sample=plate["name"], spot=sp["spot"], column=sp["column"],
                row=sp["row"], date=date_str, scan=scan_number, stem=stem)
            info = {
                "mapped": True, "sample": plate["name"], "scan_number": scan_number,
                "column": sp["column"], "row": sp["row"], "spot": sp["spot"],
            }
            return _clean(label), info

    # ---- fallback ----
    fb = plate["fallback_pattern"] if plate else DEFAULT_FALLBACK_PATTERN
    label = fb.format(stem=stem, date=date_str,
                      scan=("" if scan_number is None else scan_number),
                      sample=(plate["name"] if plate else ""))
    info = {
        "mapped": False,
        "sample": (plate["name"] if plate else None),
        "scan_number": scan_number,
        "column": None, "row": None, "spot": None,
    }
    return _clean(label), info


def _clean(label: str) -> str:
    """Filesystem-safe label (spaces -> underscore, strip odd chars)."""
    label = label.strip().replace(" ", "_")
    keep = "-_."
    return "".join(c if (c.isalnum() or c in keep) else "_" for c in label).strip("_") or "run"
