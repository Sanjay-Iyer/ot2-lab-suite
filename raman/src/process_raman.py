#!/usr/bin/env python
"""process_raman.py - config-driven Raman spectrum processor.

This is the ONE script you run. You never edit it to change the analysis -
you point it at a different YAML config instead.

Examples (run from the raman/ directory):

    # default = configs/peaks_1080.yaml, processes every file in raw/
    python src/process_raman.py

    # pick a config by short name (resolved to configs/<name>.yaml)
    python src/process_raman.py --config peaks_1080_1580
    python src/process_raman.py --config peaks_all

    # one specific file, any config
    python src/process_raman.py --config peaks_1080 --input raw/Randomized_Scan_00649.csv

    # override the input folder / output folder
    python src/process_raman.py --input /path/to/scans --output results

Outputs land under results/ (see raman_lib/io_utils for the layout) and every
peak is also appended to results/master_log.csv for easy batch review.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

# Make raman_lib importable no matter where the script is invoked from.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
_RAMAN_ROOT = _HERE.parent  # the raman/ directory

from raman_lib import config as cfgmod          # noqa: E402
from raman_lib import io_utils, preprocessing, detection, plotting, plate as platemod  # noqa: E402


def resolve_config_path(value: str) -> Path:
    """Accept a full path or a bare config name (looked up in configs/)."""
    p = Path(value)
    if p.is_file():
        return p
    # bare name -> configs/<name>.yaml
    candidate = _RAMAN_ROOT / "configs" / value
    if candidate.is_file():
        return candidate
    candidate_yaml = _RAMAN_ROOT / "configs" / f"{value}.yaml"
    if candidate_yaml.is_file():
        return candidate_yaml
    raise SystemExit(f"[error] config not found: {value} "
                     f"(looked for {p}, {candidate}, {candidate_yaml})")


def resolve_dir(value: str) -> Path:
    """Resolve a path relative to raman/ root if it isn't absolute."""
    p = Path(value)
    return p if p.is_absolute() else (_RAMAN_ROOT / p)


def resolve_plate_path(value: str) -> Path:
    """Accept a full path or a bare plate name (looked up in plates/)."""
    p = Path(value)
    if p.is_file():
        return p
    for cand in (_RAMAN_ROOT / "plates" / value, _RAMAN_ROOT / "plates" / f"{value}.yaml"):
        if cand.is_file():
            return cand
    raise SystemExit(f"[error] plate not found: {value} (looked for {p} and in plates/)")


def gather_inputs(args, cfg) -> list[Path]:
    if args.input:
        target = resolve_dir(args.input)
        if target.is_file():
            return [target]
        if target.is_dir():
            return sorted(target.glob(cfg["io"]["file_glob"]))
        raise SystemExit(f"[error] --input not found: {target}")
    input_dir = resolve_dir(cfg["io"]["input_dir"])
    files = sorted(input_dir.glob(cfg["io"]["file_glob"]))
    if not files:
        raise SystemExit(f"[error] no files matching {cfg['io']['file_glob']} in {input_dir}")
    return files


def process_file(path: Path, cfg: dict, output_dir: Path,
                 plate: dict | None = None, date_str: str = "") -> dict:
    """Run the full pipeline on one file. Returns a short result summary."""
    csv_cfg = cfg["io"]["csv"]
    x, y_raw = io_utils.load_spectrum(path, csv_cfg)

    # optional crop
    if cfg["analysis_range"]["enabled"]:
        lo = cfg["analysis_range"]["min"]
        hi = cfg["analysis_range"]["max"]
        mask = np.ones_like(x, dtype=bool)
        if lo is not None:
            mask &= x >= lo
        if hi is not None:
            mask &= x <= hi
        x, y_raw = x[mask], y_raw[mask]
        if x.size < 5:
            raise ValueError(f"analysis_range leaves < 5 points for {path.name}")

    # preprocessing
    baseline = preprocessing.apply_baseline(x, y_raw, cfg["preprocessing"]["baseline"])
    corrected = y_raw - baseline
    # Normalize the corrected signal (not just the smoothed one) so corrected,
    # smoothed and the noise estimate all live on a single, consistent scale.
    corrected, norm_scale = preprocessing.apply_normalize(x, corrected, cfg["preprocessing"]["normalize"])
    smoothed = preprocessing.apply_smoothing(corrected, cfg["preprocessing"]["smoothing"])
    # Noise (the SNR denominator) is estimated on the corrected, PRE-smoothing
    # signal. Estimating it after Savitzky-Golay would remove the very
    # point-to-point noise we are trying to measure and inflate every SNR.
    noise = preprocessing.estimate_noise(x, corrected, cfg["noise"])

    # detection runs on the smoothed, baseline-corrected, (optionally normalized) signal
    peaks = detection.detect(x, smoothed, noise, cfg)

    # ---- result naming: map scan number -> plate spot, else fall back to stem ----
    label, naming = platemod.build_label(path.stem, plate, date_str)
    prefix = f"{label}_"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    run_dir = io_utils.make_run_dir(output_dir, label, cfg["name"])
    spectrum = {
        "wavelength_cm1": x,
        "raw": y_raw,
        "baseline": baseline,
        "corrected": corrected,
        "smoothed": smoothed,
    }
    io_utils.write_processed_spectrum(run_dir, spectrum, prefix=prefix)
    io_utils.write_peaks_csv(run_dir, peaks, prefix=prefix)

    summary = {
        "label": label,
        "sample": naming.get("sample"),
        "scan_number": naming.get("scan_number"),
        "spot": naming.get("spot"),
        "column": naming.get("column"),
        "row": naming.get("row"),
        "spot_mapped": naming.get("mapped"),
        "plate": (plate["name"] if plate else None),
        "date": date_str,
        "original_filename": path.name,
        "source_file": str(path),
        "timestamp": ts,
        "config_name": cfg["name"],
        "config_source": cfg.get("_source_path"),
        "detection_mode": cfg["detection"]["mode"],
        "n_points": int(x.size),
        "range_cm1": [float(x.min()), float(x.max())],
        "noise_sigma": noise,
        "normalize_scale": float(norm_scale),
        "n_peaks_found": int(sum(1 for p in peaks if p.get("found"))),
        "peaks": peaks,
        "config": {k: v for k, v in cfg.items() if not k.startswith("_")},
    }
    io_utils.write_summary(run_dir, summary, prefix=prefix)

    # plots. The blue trace is the exact signal detection ran on (smoothed).
    # raw/baseline are only overlaid when NOT normalizing - otherwise they sit
    # on a different vertical scale and would swamp/flatten the plot.
    plot_spectrum = {"smoothed": smoothed}
    if cfg["preprocessing"]["normalize"]["method"] == "none":
        plot_spectrum["raw"] = y_raw
        plot_spectrum["baseline"] = baseline
    if cfg["plotting"]["overview"]:
        plotting.plot_overview(
            x, plot_spectrum, peaks, cfg,
            run_dir / f"{prefix}overview.png",
            title=f"{label}  |  {cfg['name']}  |  {path.name}",
        )
    if cfg["plotting"]["zoom"] and cfg["detection"]["mode"] == "targeted":
        for p in peaks:
            name = io_utils._slug(p.get("name", "peak"))
            plotting.plot_zoom(x, plot_spectrum, p, cfg, run_dir / f"{prefix}zoom_{name}.png")

    # master log rows (one per peak)
    rows = []
    for p in peaks:
        rows.append({
            "timestamp": ts,
            "label": label,
            "sample": naming.get("sample"),
            "scan_number": naming.get("scan_number"),
            "spot": naming.get("spot"),
            "column": naming.get("column"),
            "row": naming.get("row"),
            "original_filename": path.name,
            "config": cfg["name"],
            "peak_name": p.get("name"),
            "target_center_cm1": p.get("target_center_cm1"),
            "center_cm1": p.get("center_cm1"),
            "fit_center_cm1": p.get("fit_center_cm1"),
            "shift_from_target_cm1": p.get("shift_from_target_cm1"),
            "height": p.get("height"),
            "prominence": p.get("prominence"),
            "fwhm_cm1": p.get("fwhm_cm1"),
            "area": p.get("area"),
            "snr": p.get("snr"),
            "found": p.get("found"),
        })
    if rows:
        io_utils.append_master_log(output_dir, rows)

    return {"label": label, "run_dir": run_dir,
            "n_found": summary["n_peaks_found"], "n_total": len(peaks)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Config-driven Raman peak processor.")
    ap.add_argument("--config", "-c", default="peaks_1080",
                    help="Config name (in configs/) or path to a YAML file. Default: peaks_1080")
    ap.add_argument("--input", "-i", default=None,
                    help="Input file or directory. Overrides config io.input_dir.")
    ap.add_argument("--output", "-o", default=None,
                    help="Output directory. Overrides config io.output_dir.")
    ap.add_argument("--plate", "-p", default=None,
                    help="Plate name (in plates/) or path. Maps scan numbers -> spots for "
                         "naming. Overrides the config's 'plate:' key.")
    ap.add_argument("--date", "-d", default=None,
                    help="Date label for result names (default: today, format from the plate).")
    args = ap.parse_args(argv)

    cfg_path = resolve_config_path(args.config)
    cfg = cfgmod.load_config(cfg_path)
    output_dir = resolve_dir(args.output) if args.output else resolve_dir(cfg["io"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the plate (CLI wins over the config's default 'plate:' key).
    plate = None
    plate_ref = args.plate or cfg.get("plate")
    if plate_ref:
        plate = platemod.load_plate(resolve_plate_path(plate_ref))
    date_fmt = plate["date_format"] if plate else platemod.DEFAULT_DATE_FORMAT
    date_str = args.date or platemod.today_str(date_fmt)

    files = gather_inputs(args, cfg)

    print(f"Config : {cfg['name']}  ({cfg_path})")
    print(f"Mode   : {cfg['detection']['mode']}   baseline={cfg['preprocessing']['baseline']['method']}"
          f"  smoothing={cfg['preprocessing']['smoothing']['method']}")
    if plate:
        print(f"Plate  : {plate['name']}  ({len(plate['columns'])}x{len(plate['rows'])} "
              f"{plate['order']} from scan {plate['start_scan']})")
    print(f"Date   : {date_str}")
    print(f"Output : {output_dir}")
    print(f"Files  : {len(files)}")
    print("-" * 72)

    failures = 0
    for path in files:
        try:
            res = process_file(path, cfg, output_dir, plate=plate, date_str=date_str)
            print(f"  [ok] {res['label']:<28} peaks found: {res['n_found']}/{res['n_total']}"
                  f"   -> {res['run_dir'].relative_to(output_dir)}")
        except Exception as exc:  # keep batch going if one file is bad
            failures += 1
            print(f"  [FAIL] {path.name}: {type(exc).__name__}: {exc}")

    print("-" * 72)
    print(f"Done. {len(files) - failures} ok, {failures} failed. "
          f"Master log: {output_dir / 'master_log.csv'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
