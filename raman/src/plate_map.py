#!/usr/bin/env python
"""plate_map.py - preview the scan-number -> plate-spot mapping for a sample.

Give it a plate and (optionally) a scan-number range, and it prints the dot
location + the result-file name each scan would produce. No spectra are
processed - this is purely for checking your plate layout before a run.

Examples (run from the raman/ directory):

    # whole plate (start_scan .. start_scan + columns*rows - 1)
    python src/plate_map.py --plate bp

    # an explicit range
    python src/plate_map.py --plate bp --range 673-696

    # start number only; end is inferred from the plate size
    python src/plate_map.py --plate bp --start 673

    # also save the mapping to a CSV
    python src/plate_map.py --plate bp --out results/plate_map_bp.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
_RAMAN_ROOT = _HERE.parent

from raman_lib import plate as platemod  # noqa: E402


def resolve_plate_path(value: str) -> Path:
    p = Path(value)
    if p.is_file():
        return p
    for cand in (_RAMAN_ROOT / "plates" / value,
                 _RAMAN_ROOT / "plates" / f"{value}.yaml"):
        if cand.is_file():
            return cand
    raise SystemExit(f"[error] plate not found: {value} (looked in plates/)")


def parse_range(text: str) -> range:
    text = text.strip()
    if "-" in text:
        lo, hi = text.split("-", 1)
        return range(int(lo), int(hi) + 1)
    n = int(text)
    return range(n, n + 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Preview scan->spot plate mapping.")
    ap.add_argument("--plate", "-p", required=True, help="Plate name (in plates/) or path to a YAML.")
    ap.add_argument("--range", "-r", default=None, help="Scan range 'START-END' (e.g. 673-696).")
    ap.add_argument("--start", "-s", type=int, default=None,
                    help="Start scan number; end inferred from plate size. Ignored if --range given.")
    ap.add_argument("--date", "-d", default=None, help="Date label override (default: today).")
    ap.add_argument("--out", "-o", default=None, help="Optional CSV output path.")
    args = ap.parse_args(argv)

    plate = platemod.load_plate(resolve_plate_path(args.plate))
    date_str = args.date or platemod.today_str(plate["date_format"])

    if args.range:
        scan_range = parse_range(args.range)
    elif args.start is not None:
        scan_range = range(args.start, args.start + platemod.capacity(plate))
    else:
        scan_range = None  # build_map defaults to the plate's own range

    table = platemod.build_map(plate, scan_range)

    # attach the result-file name each scan would produce
    for row in table:
        stem = f"Scan_{row['scan']:05d}"  # representative stem for preview
        label, _ = platemod.build_label(stem, plate, date_str)
        row["result_name"] = label

    cap = platemod.capacity(plate)
    print(f"Plate  : {plate['name']}  ({plate['_source_path']})")
    print(f"Layout : {len(plate['columns'])} cols x {len(plate['rows'])} rows "
          f"= {cap} spots, order={plate['order']}, start_scan={plate['start_scan']}")
    print(f"Date   : {date_str}")
    print("-" * 60)
    print(f"{'scan':>6}  {'spot':<6} {'in_range':<9} result_name")
    print("-" * 60)
    for row in table:
        spot = row["spot"] or "-"
        print(f"{row['scan']:>6}  {spot:<6} {str(row['in_range']):<9} {row['result_name']}")

    if args.out:
        import csv
        out = Path(args.out)
        if not out.is_absolute():
            out = _RAMAN_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["scan", "column", "row", "spot", "in_range", "result_name"])
            w.writeheader()
            w.writerows(table)
        print("-" * 60)
        print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
