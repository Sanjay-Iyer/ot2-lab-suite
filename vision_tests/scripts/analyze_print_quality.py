"""Run grid-aware offline CV quality checks on printed food-dye droplets.

Examples (from the repository root, in the ``ai`` environment on the simulation
laptop)::

    python vision_tests/scripts/analyze_print_quality.py --suite
    python vision_tests/scripts/analyze_print_quality.py --benchmark ot2_blue_3x8
    python vision_tests/scripts/analyze_print_quality.py \
        --image frame_1.jpg frame_2.jpg frame_3.jpg --profile ot2_fixed

This script only reads local image files and writes local reports.  It never
connects to a robot.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "vision_tests"))

from print_quality import (  # noqa: E402
    GridDefinition,
    analyze_print,
    annotate_analysis,
    flatten_droplets,
    make_montage,
    read_image,
)

DEFAULT_CONFIG = PROJECT_ROOT / "vision_tests" / "configs" / "print_quality.yaml"


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Print-quality config not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _grid_from_profile(profile: dict[str, Any], *, columns: int | None = None) -> GridDefinition:
    grid = profile["grid"]
    return GridDefinition(
        rows=int(profile.get("rows", 8)),
        columns=int(columns if columns is not None else profile["columns"]),
        origin=tuple(float(v) for v in grid["origin"]),
        row_step=tuple(float(v) for v in grid["row_step"]),
        column_step=tuple(float(v) for v in grid["column_step"]),
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _analyze_entry(
    *,
    name: str,
    image_path: Path,
    profile_name: str,
    profile: dict[str, Any],
    columns: int | None,
    series: str,
    output_dir: Path,
) -> dict[str, Any]:
    bgr = read_image(image_path)
    grid = _grid_from_profile(profile, columns=columns)
    analysis = analyze_print(
        bgr,
        grid,
        source=str(profile.get("source", profile_name)),
        presence_threshold=float(profile.get("presence_threshold", 1.5)),
        unassessable_rows=profile.get("unassessable_rows", ()),
        unassessable_positions=profile.get("unassessable_positions", ()),
    )
    analysis["benchmark"] = name
    analysis["series"] = series
    analysis["image"] = str(image_path)
    analysis["profile"] = profile_name
    analysis["generated_utc"] = datetime.now(tz=timezone.utc).isoformat()

    run_dir = output_dir / name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "analysis.json").write_text(
        json.dumps(analysis, indent=2), encoding="utf-8"
    )
    _write_csv(run_dir / "droplets.csv", flatten_droplets([analysis]))
    cv2.imwrite(str(run_dir / "annotated.jpg"), annotate_analysis(bgr, analysis))
    cv2.imwrite(str(run_dir / "droplet_montage.jpg"), make_montage(bgr, analysis))
    return analysis


def _comparison_rows(analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for analysis in analyses:
        summary = analysis["summary"]
        present = [d for d in analysis["droplets"] if d["present"]]
        color_resolved = [d for d in present if d["color_reliable"]]
        ring_counts = {
            label: sum(1 for d in present if d["coffee_ring"] == label)
            for label in (
                "strong",
                "possible",
                "not-evident",
                "uncertain-low-resolution",
            )
        }
        shape_counts = {
            label: sum(1 for d in present if d["shape"] == label)
            for label in ("round", "blob/irregular", "uncertain-low-resolution")
        }
        rows.append(
            {
                "benchmark": analysis["benchmark"],
                "source": summary["source"],
                "image": analysis["image"],
                "expected": summary["expected_droplets"],
                "assessable": summary["assessable_droplets"],
                "unassessable": summary["unassessable_droplets"],
                "found": summary["found_droplets"],
                "missing": summary["missing_droplets"],
                "borderline": summary["borderline_droplets"],
                "color_resolved": len(color_resolved),
                "median_diameter_px": summary["median_measured_diameter_pixels"],
                "shape_reliable": summary["shape_reliable_droplets"],
                "shape_round": shape_counts["round"],
                "shape_blob_or_irregular": shape_counts["blob/irregular"],
                "shape_low_resolution": shape_counts["uncertain-low-resolution"],
                "coffee_ring_reliable": summary["coffee_ring_reliable_droplets"],
                "coffee_ring_strong": ring_counts["strong"],
                "coffee_ring_possible": ring_counts["possible"],
                "coffee_ring_not_evident": ring_counts["not-evident"],
                "coffee_ring_low_resolution": ring_counts["uncertain-low-resolution"],
            }
        )
    return rows


def _series_rows(
    analyses: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Summarize repeatability across multiple photos of the same print."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for analysis in analyses:
        grouped.setdefault(analysis["series"], []).append(analysis)

    series_rows: list[dict[str, Any]] = []
    position_rows: list[dict[str, Any]] = []
    for series, frames in grouped.items():
        frame_count = len(frames)
        majority_needed = frame_count // 2 + 1
        by_position: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for frame in frames:
            for droplet in frame["droplets"]:
                key = (int(droplet["row"]), int(droplet["column"]))
                by_position.setdefault(key, []).append(droplet)

        seen_any = 0
        seen_majority = 0
        assessable_positions = 0
        color_resolved = 0
        shape_supported = 0
        ring_supported = 0
        ring_strong_or_possible = 0
        for (row, column), values in sorted(by_position.items()):
            assessed = [value for value in values if value["assessable"]]
            detected = [value for value in assessed if value["present"]]
            detected_frames = len(detected)
            position_assessable = bool(assessed)
            position_seen_any = detected_frames > 0
            position_seen_majority = detected_frames >= majority_needed
            assessable_positions += int(position_assessable)
            seen_any += int(position_seen_any)
            seen_majority += int(position_seen_majority)

            colors = [
                value["color_name"] for value in detected if value["color_reliable"]
            ]
            color_consensus = None
            if colors:
                color_consensus = max(set(colors), key=lambda label: colors.count(label))
                color_resolved += 1
            shape_supported += int(any(value["shape_reliable"] for value in detected))
            ring_supported += int(
                any(value["coffee_ring_reliable"] for value in detected)
            )
            ring_strong_or_possible += int(
                any(
                    value["coffee_ring"] in {"strong", "possible"}
                    for value in detected
                )
            )
            position_rows.append(
                {
                    "series": series,
                    "row": row,
                    "column": column,
                    "frames": frame_count,
                    "assessed_frames": len(assessed),
                    "detected_frames": detected_frames,
                    "seen_any": position_seen_any,
                    "seen_strict_majority": position_seen_majority,
                    "color_consensus": color_consensus or "unresolved",
                }
            )

        expected = len(by_position)
        series_rows.append(
            {
                "series": series,
                "source": frames[0]["summary"]["source"],
                "frames": frame_count,
                "expected_positions": expected,
                "assessable_positions": assessable_positions,
                "unassessable_positions": expected - assessable_positions,
                "seen_in_any_frame": seen_any,
                "seen_in_strict_majority": seen_majority,
                "never_seen": assessable_positions - seen_any,
                "not_seen_in_strict_majority": assessable_positions - seen_majority,
                "color_resolved_in_any_frame": color_resolved,
                "shape_supported_in_any_frame": shape_supported,
                "coffee_ring_supported_in_any_frame": ring_supported,
                "coffee_ring_strong_or_possible_in_any_frame": ring_strong_or_possible,
            }
        )
    return series_rows, position_rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline grid-aware CV analysis of printed food-dye droplets."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--suite", action="store_true", help="Run all configured benchmarks.")
    selection.add_argument("--benchmark", help="Run one configured benchmark by name.")
    selection.add_argument(
        "--image",
        type=Path,
        nargs="+",
        help="Analyze one or more local images as one repeated-photo series.",
    )
    parser.add_argument("--profile", help="Profile for --image (for example ot2_fixed).")
    parser.add_argument("--columns", type=int, help="Override the profile's expected columns.")
    parser.add_argument(
        "--series-name",
        help="Optional report name for a set passed with --image.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "vision_tests" / "outputs" / "print_quality",
    )
    args = parser.parse_args()

    config = _load_config(args.config.resolve())
    profiles = config.get("profiles", {})
    benchmarks = config.get("benchmarks", {})
    output_dir = args.out.resolve()

    entries: list[tuple[str, Path, str, int | None, str]] = []
    if args.suite:
        for name, benchmark in benchmarks.items():
            entries.append(
                (
                    str(name),
                    (PROJECT_ROOT / benchmark["image"]).resolve(),
                    str(benchmark["profile"]),
                    int(benchmark["columns"]) if "columns" in benchmark else None,
                    str(benchmark.get("series", name)),
                )
            )
    elif args.benchmark:
        if args.benchmark not in benchmarks:
            parser.error(f"Unknown benchmark: {args.benchmark}")
        benchmark = benchmarks[args.benchmark]
        entries.append(
            (
                args.benchmark,
                (PROJECT_ROOT / benchmark["image"]).resolve(),
                str(benchmark["profile"]),
                int(benchmark["columns"]) if "columns" in benchmark else None,
                str(benchmark.get("series", args.benchmark)),
            )
        )
    else:
        if not args.profile:
            parser.error("--image requires --profile")
        series = args.series_name or "custom_image_series"
        for image_path in args.image:
            entries.append(
                (
                    image_path.stem,
                    image_path.resolve(),
                    args.profile,
                    args.columns,
                    series,
                )
            )

    analyses = []
    for name, image_path, profile_name, columns, series in entries:
        if profile_name not in profiles:
            parser.error(f"Unknown profile: {profile_name}")
        if not image_path.exists():
            parser.error(f"Image does not exist: {image_path}")
        analysis = _analyze_entry(
            name=name,
            image_path=image_path,
            profile_name=profile_name,
            profile=profiles[profile_name],
            columns=columns,
            series=series,
            output_dir=output_dir,
        )
        analyses.append(analysis)
        summary = analysis["summary"]
        print(
            f"{name}: {summary['found_droplets']}/{summary['expected_droplets']} found; "
            f"shape reliable {summary['shape_reliable_droplets']}; "
            f"coffee-ring reliable {summary['coffee_ring_reliable_droplets']}"
        )

    comparison = {
        "generated_utc": datetime.now(tz=timezone.utc).isoformat(),
        "analyses": _comparison_rows(analyses),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2), encoding="utf-8"
    )
    _write_csv(output_dir / "comparison.csv", comparison["analyses"])
    _write_csv(output_dir / "all_droplets.csv", flatten_droplets(analyses))
    series_rows, position_rows = _series_rows(analyses)
    series_comparison = {
        "generated_utc": datetime.now(tz=timezone.utc).isoformat(),
        "series": series_rows,
    }
    (output_dir / "series_comparison.json").write_text(
        json.dumps(series_comparison, indent=2), encoding="utf-8"
    )
    _write_csv(output_dir / "series_comparison.csv", series_rows)
    _write_csv(output_dir / "series_positions.csv", position_rows)
    print(f"Reports written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
