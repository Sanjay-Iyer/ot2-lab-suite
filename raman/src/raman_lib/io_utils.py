"""Input/output helpers: loading spectra and writing organized, timestamped results.

Output layout (per run):

    <output_dir>/
        runs/
            <timestamp>__<config>__<sample>/
                peaks.csv               # one row per detected/target peak
                summary.json            # full metadata + config snapshot + peaks
                processed_spectrum.csv  # raw, baseline, corrected, smoothed columns
                overview.png            # full spectrum with peaks marked
                zoom_<peak>.png         # per-target zoom (targeted mode)
        master_log.csv                  # appended: one row per (run, peak)

This keeps every run self-contained *and* gives a single flat master log that
is trivial to open in Excel/pandas when many files have been processed.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _looks_like_header(first_line: str, delimiter: str) -> bool:
    """Heuristic: a header row has non-numeric tokens in the first two fields."""
    parts = first_line.strip().split(delimiter)
    if len(parts) < 2:
        return False
    for tok in parts[:2]:
        try:
            float(tok)
        except ValueError:
            return True
    return False


def _select_column(df: pd.DataFrame, selector: int | str, role: str) -> pd.Series:
    """Select a configured CSV column by zero-based index or exact header name."""
    if isinstance(selector, str):
        if selector not in df.columns:
            available = ", ".join(map(str, df.columns))
            raise ValueError(
                f"Configured {role} column {selector!r} was not found. "
                f"Available columns: {available}"
            )
        return df[selector]
    try:
        return df.iloc[:, int(selector)]
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Configured {role} column index {selector!r} is outside the "
            f"{df.shape[1]} parsed CSV columns."
        ) from exc


def load_spectrum(
    path: str | Path,
    csv_cfg: dict[str, Any],
    *,
    return_report: bool = False,
) -> tuple[np.ndarray, np.ndarray] | tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Load a Raman CSV -> (Raman shift in cm^-1, intensity), sorted ascending.

    Robust to: optional header (auto-detected when has_header is None),
    configurable column names or indices/delimiter, and unsorted or duplicated
    Raman-shift values. Non-finite rows are removed explicitly.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Raman spectrum file not found: {path}")
    delimiter = csv_cfg.get("delimiter", ",")
    has_header = csv_cfg.get("has_header", None)
    comment = csv_cfg.get("comment", None)
    wcol = csv_cfg.get(
        "raman_shift_column",
        csv_cfg.get("wavelength_col", 0),
    )
    icol = csv_cfg.get(
        "intensity_column",
        csv_cfg.get("intensity_col", 1),
    )
    if wcol == icol:
        raise ValueError("Raman-shift and intensity columns must be different.")

    if has_header is None:
        if isinstance(wcol, str) or isinstance(icol, str):
            has_header = True
        else:
            with path.open("r", encoding="utf-8") as fh:
                first = fh.readline()
                while comment and first.lstrip().startswith(comment):
                    first = fh.readline()
            has_header = _looks_like_header(first, delimiter)

    df = pd.read_csv(
        path,
        sep=delimiter,
        header=0 if has_header else None,
        comment=comment,
        engine="python",
    )

    original_rows = int(df.shape[0])
    x = pd.to_numeric(
        _select_column(df, wcol, "Raman-shift"),
        errors="coerce",
    ).to_numpy(dtype=float)
    y = pd.to_numeric(
        _select_column(df, icol, "intensity"),
        errors="coerce",
    ).to_numpy(dtype=float)

    mask = np.isfinite(x) & np.isfinite(y)
    removed_nonfinite = int((~mask).sum())
    if not np.all(mask) and not csv_cfg.get("remove_nonfinite", True):
        raise ValueError(
            f"Spectrum {path} contains non-finite Raman-shift or intensity values "
            "and preprocessing.remove_nonfinite is false."
        )
    x, y = x[mask], y[mask]
    if x.size < 5:
        raise ValueError(
            f"Fewer than five numeric rows were parsed from {path}; "
            "check the CSV column and delimiter configuration."
        )

    was_sorted = bool(np.all(np.diff(x) >= 0))
    if csv_cfg.get("sort_axis", True):
        order = np.argsort(x)
        x, y = x[order], y[order]
    elif np.any(np.diff(x) < 0):
        raise ValueError(
            f"Spectrum {path} has an unsorted Raman-shift axis and "
            "preprocessing.sort_axis is false."
        )
    # Collapse duplicate x by averaging (rare, but keeps downstream math clean).
    duplicate_rows_merged = 0
    if np.any(np.diff(x) == 0):
        if csv_cfg.get("merge_duplicate_shifts", True):
            before_merge = int(x.size)
            ux, inv = np.unique(x, return_inverse=True)
            uy = np.zeros_like(ux)
            counts = np.bincount(inv)
            np.add.at(uy, inv, y)
            y = uy / counts
            x = ux
            duplicate_rows_merged = before_merge - int(x.size)
        else:
            raise ValueError(
                f"Spectrum {path} contains duplicate Raman shifts and "
                "preprocessing.merge_duplicate_shifts is false."
            )
    if x.size < 5:
        raise ValueError(f"Spectrum {path} contains fewer than five unique valid shifts.")
    if not np.all(np.diff(x) > 0):
        raise ValueError(f"Spectrum {path} could not be converted to a strictly increasing axis.")
    report = {
        "input_rows": original_rows,
        "valid_numeric_rows": original_rows - removed_nonfinite,
        "removed_nonfinite_rows": removed_nonfinite,
        "axis_was_sorted": was_sorted,
        "axis_sorted_by_workflow": not was_sorted,
        "duplicate_rows_merged": duplicate_rows_merged,
        "duplicate_aggregation": "mean" if duplicate_rows_merged else None,
        "output_points": int(x.size),
    }
    return (x, y, report) if return_report else (x, y)


def make_run_dir(output_dir: str | Path, label: str, config_name: str) -> Path:
    """Create and return the run directory for a single processing run.

    The folder is named after the human-readable result label plus the analysis
    config, e.g. ``runs/bp_A1_072726__1080_only``. If that already exists (a
    re-run of the same spot with the same config), a HHMMSS suffix is appended
    so nothing is silently overwritten.
    """
    base = f"{_slug(label)}__{_slug(config_name)}"
    run_dir = Path(output_dir) / "runs" / base
    if run_dir.exists():
        run_dir = Path(output_dir) / "runs" / f"{base}__{datetime.now().strftime('%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _slug(text: str) -> str:
    keep = "-_."
    return "".join(c if (c.isalnum() or c in keep) else "_" for c in str(text)).strip("_") or "x"


def write_processed_spectrum(run_dir: Path, spectrum: dict[str, np.ndarray],
                             prefix: str = "") -> Path:
    """Write the raw/baseline/corrected/smoothed columns to CSV."""
    df = pd.DataFrame(spectrum)
    out = run_dir / f"{prefix}processed_spectrum.csv"
    df.to_csv(out, index=False)
    return out


def write_peaks_csv(run_dir: Path, peaks: list[dict[str, Any]], prefix: str = "") -> Path:
    out = run_dir / f"{prefix}peaks.csv"
    if peaks:
        pd.DataFrame(peaks).to_csv(out, index=False)
    else:
        # Still emit an empty file with headers so downstream tooling is happy.
        pd.DataFrame(columns=["name", "center_cm1", "found"]).to_csv(out, index=False)
    return out


def write_summary(run_dir: Path, summary: dict[str, Any], prefix: str = "") -> Path:
    out = run_dir / f"{prefix}summary.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=_json_default)
    return out


def append_master_log(output_dir: str | Path, rows: list[dict[str, Any]]) -> Path:
    """Append one row per peak to the flat master log CSV."""
    log_path = Path(output_dir) / "master_log.csv"
    df = pd.DataFrame(rows)
    header = not log_path.exists()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(log_path, mode="a", header=header, index=False)
    return log_path


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)
