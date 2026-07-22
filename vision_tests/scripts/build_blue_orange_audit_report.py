"""Build a human-readable, position-by-position blue/orange CV audit report.

This script only reads a completed offline comparison CSV and writes Markdown.
It does not connect to an OT-2 or capture new images.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


BENCHMARKS = [
    ("ot2_after_deck", "OT-2: after deck"),
    ("ot2_after_plate", "OT-2: after plate"),
    ("phone_blue_orange_frame_1", "Phone: frame 1"),
    ("phone_blue_orange_frame_2", "Phone: frame 2"),
    ("phone_blue_orange_frame_3", "Phone: frame 3"),
]

COLUMN_LABELS = {
    1: "C1 Blue (leftmost intended)",
    2: "C2 Blue (middle)",
    3: "C3 Blue (rightmost)",
    4: "C4 Orange (leftmost)",
    5: "C5 Orange (middle)",
    6: "C6 Orange (rightmost intended)",
}

ROW_LABELS = {
    1: "R1 TOP (faintest)",
    2: "R2 upper",
    3: "R3 upper",
    4: "R4 upper-middle",
    5: "R5 lower-middle",
    6: "R6 lower",
    7: "R7 lower",
    8: "R8 BOTTOM (strongest)",
}


def _number(value: str, digits: int = 2) -> str:
    if value == "":
        return "-"
    return f"{float(value):.{digits}f}"


def _detection_cell(row: dict[str, str]) -> str:
    status = row["detection_status"]
    labels = {
        "detected": "D",
        "borderline": "B",
        "not-detected": "ND",
        "unassessable": "UA",
    }
    label = labels.get(status, status)
    confidence = _number(row["presence_confidence"])
    contrast = _number(row["color_contrast"])
    return f"**{label}**; score={confidence}; contrast={contrast}"


def _color_cell(row: dict[str, str]) -> str:
    if row["detection_status"] != "detected":
        return "-"
    final = row["color_name"]
    direct = row["direct_color_name"]
    if final == direct:
        return final
    return f"final **{final}**; direct {direct}"


def _shape_cell(row: dict[str, str]) -> str:
    if row["detection_status"] != "detected":
        return "-"
    diameter = _number(row["equivalent_diameter_pixels"])
    if row["shape_reliable"] != "True":
        return f"**unsupported**; D={diameter}px"
    return (
        f"**{row['shape']}**; D={diameter}px; "
        f"C={_number(row['circularity'], 3)}; AR={_number(row['aspect_ratio'], 3)}"
    )


def _ring_cell(row: dict[str, str]) -> str:
    if row["detection_status"] != "detected":
        return "-"
    if row["coffee_ring_reliable"] != "True":
        return "**unsupported**"
    return (
        f"**{row['coffee_ring']}**; ratio={_number(row['coffee_ring_ratio'], 3)}; "
        f"contrast={_number(row['coffee_ring_contrast'], 3)}"
    )


def _grid_table(
    indexed: dict[tuple[int, int], dict[str, str]],
    formatter,
) -> list[str]:
    headers = [COLUMN_LABELS[column] for column in range(1, 7)]
    lines = [
        "| Photograph row | " + " | ".join(headers) + " |",
        "|---|" + "---|" * 6,
    ]
    for row_number in range(1, 9):
        values = [
            formatter(indexed[(row_number, column)])
            for column in range(1, 7)
        ]
        lines.append(f"| **{ROW_LABELS[row_number]}** | " + " | ".join(values) + " |")
    return lines


def build_report(input_csv: Path, output_md: Path) -> None:
    with input_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    by_benchmark: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_benchmark.setdefault(row["benchmark"], []).append(row)

    output: list[str] = [
        "# Individual droplet manual-audit tables",
        "",
        "These tables map every CV result back to its physical location in each photograph.",
        "They are generated from `outputs/blue_orange_camera_comparison/all_droplets.csv`.",
        "",
        "## Photograph orientation and droplet IDs",
        "",
        "Read every table exactly as you see the photograph:",
        "",
        "- **Left to right:** C1, C2, C3 are the three intended blue columns; ",
        "  C4, C5, C6 are the three intended orange columns.",
        "- **Top to bottom:** R1 is the top/faintest dilution row and R8 is the ",
        "  bottom/strongest dilution row.",
        "- A droplet ID combines column and row. For example, `C4R2` is the ",
        "  leftmost orange column and the second row from the top.",
        "- The far-left blue/wet-paper artifact visible in some photos is outside ",
        "  the intended grid and is not C1.",
        "",
        "| Vertical position | C1 Blue | C2 Blue | C3 Blue | C4 Orange | C5 Orange | C6 Orange |",
        "|---|---|---|---|---|---|---|",
    ]
    for row_number in range(1, 9):
        ids = [f"`C{column}R{row_number}`" for column in range(1, 7)]
        output.append(f"| **{ROW_LABELS[row_number]}** | " + " | ".join(ids) + " |")

    output.extend(
        [
            "",
            "## Table legends",
            "",
            "- Detection: **D** = detected, **B** = borderline, **ND** = not detected, ",
            "  and **UA** = unassessable. `score` is an engineering confidence score, ",
            "  not a probability. `contrast` is signal above local paper noise.",
            "- Color: `final` may use same-column consensus; `direct` is the color ",
            "  sampled from that individual position before consensus.",
            "- Shape: `D` = equivalent diameter, `C` = circularity, and `AR` = aspect ",
            "  ratio. Unsupported means there were too few pixels for a reliable call.",
            "- Coffee ring: ratio compares edge signal with center signal; contrast is ",
            "  edge minus center. `not-evident` means no clear ring in that photograph, ",
            "  not proof that no physical ring exists.",
            "",
        ]
    )

    for benchmark, title in BENCHMARKS:
        benchmark_rows = by_benchmark.get(benchmark)
        if not benchmark_rows:
            raise ValueError(f"Missing benchmark {benchmark!r} in {input_csv}")
        indexed = {
            (int(row["row"]), int(row["column"])): row
            for row in benchmark_rows
        }
        if len(indexed) != 48:
            raise ValueError(f"Benchmark {benchmark!r} has {len(indexed)} positions, expected 48")

        image = Path(benchmark_rows[0]["image"])
        audit_dir = f"outputs/blue_orange_camera_comparison/{benchmark}"
        output.extend(
            [
                f"## {title}",
                "",
                f"Source image: `{image}`",
                "",
                f"[Open annotated grid]({audit_dir}/annotated.jpg) | "
                f"[Open enlarged droplet montage]({audit_dir}/droplet_montage.jpg)",
                "",
                "### Detection",
                "",
                *_grid_table(indexed, _detection_cell),
                "",
                "### Color",
                "",
                *_grid_table(indexed, _color_cell),
                "",
                "### Shape",
                "",
                *_grid_table(indexed, _shape_cell),
                "",
                "### Coffee-ring effect",
                "",
                *_grid_table(indexed, _ring_cell),
                "",
                "Manual review notes:",
                "",
                "- [ ] Grid circles are centered on the intended positions.",
                "- [ ] Detection calls agree with the photograph.",
                "- [ ] Color calls agree with the photograph.",
                "- [ ] Reliable shape calls agree with the footprint.",
                "- [ ] Reliable coffee-ring calls agree with edge-versus-center appearance.",
                "",
            ]
        )

    output_md.write_text("\n".join(output) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("vision_tests/outputs/blue_orange_camera_comparison/all_droplets.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("vision_tests/BLUE_ORANGE_DROPLET_AUDIT.md"),
    )
    args = parser.parse_args()
    build_report(args.input.resolve(), args.output.resolve())
    print(f"Manual audit report written to {args.output.resolve()}")


if __name__ == "__main__":
    main()
