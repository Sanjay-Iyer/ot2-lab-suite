#!/usr/bin/env python3
"""
scripts/plot_four_clover_layout.py
==================================
Offline map of a four-clover layout: paper boundary, the 96 well centers, each
clover center, its four droplet coordinates and its label. Nothing here talks to
a robot -- run it before uploading anything.

  conda activate ai
  python scripts/plot_four_clover_layout.py --config configs/printing/four_clover_v12.yaml
  python scripts/plot_four_clover_layout.py --config configs/printing/four_clover_v12.yaml --out layout.png
  python scripts/plot_four_clover_layout.py --config configs/printing/four_clover_v12.yaml --text-only

There is ONE source of truth for geometry: this script imports the resolver
functions out of src/protocols/printing/12_four_clover_paper_print.py and feeds
them the same YAML the builder embeds, so a plot can never disagree with what the
robot will do.

Coordinates here are labware-local millimetres taken from the paper labware JSON
(A1 center = 14.38, 74.24). The protocol reports the same layout in absolute deck
millimetres; the two differ only by the slot-5 origin, a pure translation.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PROTOCOL = REPO / "src" / "protocols" / "printing" / "12_four_clover_paper_print.py"
LABWARE = REPO / "labware"


def _numpy_trapz_shim() -> None:
    """opentrons_shared_data still imports numpy.trapz, removed in numpy 2.

    Same shim scripts/build_vial_dilution_print.py applies before simulating. It
    has to run before the protocol module is imported, because that module pulls
    in opentrons at import time.
    """
    try:
        import numpy
    except ImportError:
        return
    if not hasattr(numpy, "trapz") and hasattr(numpy, "trapezoid"):
        numpy.trapz = numpy.trapezoid


def load_geometry_module():
    """Import the protocol module so the plot uses the protocol's own resolvers."""
    _numpy_trapz_shim()
    spec = importlib.util.spec_from_file_location("four_clover_geometry", PROTOCOL)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import {PROTOCOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_config(config_path: Path) -> dict:
    """Merge a run config with its destination_config, exactly as the builder does."""
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config.pop("run_modes", None)
    reference = config.pop("destination_config", None)
    if reference:
        path = Path(reference)
        if not path.is_absolute():
            path = REPO / path
        if not path.exists():
            raise SystemExit(f"destination_config not found: {path}")
        shared = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        config["destination"] = shared.get("destination", shared)
    if "destination" not in config:
        raise SystemExit(f"{config_path} has no destination section")
    return config


def load_paper(load_name: str) -> dict:
    path = LABWARE / f"{load_name}.json"
    if not path.exists():
        raise SystemExit(
            f"custom labware {load_name}.json not found in {LABWARE}; this plotter "
            "reads well centers straight from the labware definition"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def build(config_path: Path):
    module = load_geometry_module()
    config = resolve_config(config_path)
    module.CONFIG = config

    load_name = config.get("deck", {}).get("paper", {}).get(
        "load_name", "paper_print_96_flat"
    )
    paper = load_paper(load_name)
    wells = paper["wells"]

    def well_xy(name):
        key = str(name).upper()
        if key not in wells:
            raise SystemExit(f"paper well {key} does not exist in {load_name}")
        return float(wells[key]["x"]), float(wells[key]["y"])

    clovers = module._resolve_clovers(well_xy)
    bounds = module._paper_bounds(well_xy, list(wells))
    intra, inter = module._distance_report(clovers)
    order_mode, plan = module._print_order(clovers)
    return module, config, paper, wells, well_xy, clovers, bounds, intra, inter, order_mode, plan


def report(config, paper, clovers, bounds, intra, inter, order_mode, plan) -> None:
    keys = ("d1", "d2", "d3", "d4")
    dimensions = paper["dimensions"]
    volume = float(config.get("printing", {}).get("droplet_volume_ul", 0.0))
    deposits = sum(clover["layers"] for clover in clovers) * 4

    print("FOUR CLOVER LAYOUT (labware-local millimetres)")
    print(
        f"  paper: {paper['parameters']['loadName']} "
        f"{dimensions['xDimension']:.2f} x {dimensions['yDimension']:.2f} mm"
    )
    print(
        f"  well-center grid: x [{bounds['grid']['min_x']:.2f}, "
        f"{bounds['grid']['max_x']:.2f}] y [{bounds['grid']['min_y']:.2f}, "
        f"{bounds['grid']['max_y']:.2f}]"
    )
    print(
        f"  usable box ({bounds['mode']} mode, margin {bounds['margin_mm']:g} mm): "
        f"x [{bounds['min_x']:.2f}, {bounds['max_x']:.2f}] "
        f"y [{bounds['min_y']:.2f}, {bounds['max_y']:.2f}]"
    )
    for note in bounds["notes"]:
        print(f"  NOTE: {note}")
    print(f"  clovers: {len(clovers)}   deposits: {deposits}   "
          f"liquid: {deposits * volume:g} uL   order: {order_mode}")
    print()

    for clover in clovers:
        offset_x, offset_y = clover["center_offset"]
        center_x, center_y = clover["center"]
        extents = clover["extents"]
        print(f"Clover: {clover['name']}")
        print(
            f"    reference well = {clover['reference_well']}, "
            f"center x offset = {offset_x:+.2f} mm, center y offset = {offset_y:+.2f} mm"
        )
        print(f"    center = x {center_x:.2f}, y {center_y:.2f}  "
              f"[geometry: {clover['geometry_source']}, layers: {clover['layers']}]")
        for key in keys:
            dx, dy = clover["droplets"][key]["offset"]
            ax, ay = clover["droplets"][key]["absolute"]
            print(
                f"    {key.upper()} = x {dx:+.2f} mm, y {dy:+.2f} mm  "
                f"-> paper x {ax:.2f}, y {ay:.2f}"
            )
        print(
            f"    extents: x [{extents['min_x']:.2f}, {extents['max_x']:.2f}] "
            f"y [{extents['min_y']:.2f}, {extents['max_y']:.2f}] "
            f"width {extents['width']:.2f} mm, height {extents['height']:.2f} mm"
        )
        print()

    if intra:
        tightest = min(intra, key=lambda item: item["min_distance"])
        print(
            f"minimum intra-clover distance: {tightest['min_distance']:.2f} mm "
            f"({tightest['clover']}, {tightest['pair'][0]}-{tightest['pair'][1]})"
        )
    if inter:
        tightest = min(inter, key=lambda item: item["min_distance"])
        print(
            f"minimum inter-clover distance: {tightest['min_distance']:.2f} mm "
            f"({tightest['clovers'][0]} to {tightest['clovers'][1]})"
        )
    else:
        print("minimum inter-clover distance: n/a (single clover)")
    print(f"execution steps: {len(plan)}")


def plot(config, paper, wells, clovers, bounds, out_path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle, Rectangle
    except ImportError:
        raise SystemExit(
            "matplotlib is not installed; rerun with --text-only for the same "
            "numbers without the picture"
        )

    keys = ("d1", "d2", "d3", "d4")
    dimensions = paper["dimensions"]
    radius = float(config.get("validation", {}).get("droplet_radius_mm", 0.0) or 0.0)

    figure, axes = plt.subplots(figsize=(12.0, 8.4))
    axes.add_patch(
        Rectangle(
            (0, 0), dimensions["xDimension"], dimensions["yDimension"],
            fill=False, edgecolor="0.4", linewidth=1.5, label="labware footprint",
        )
    )
    axes.add_patch(
        Rectangle(
            (bounds["min_x"], bounds["min_y"]),
            bounds["max_x"] - bounds["min_x"],
            bounds["max_y"] - bounds["min_y"],
            fill=False, edgecolor="tab:green", linewidth=1.2, linestyle="--",
            label=f"usable box ({bounds['mode']})",
        )
    )
    axes.scatter(
        [float(well["x"]) for well in wells.values()],
        [float(well["y"]) for well in wells.values()],
        s=6, color="0.75", zorder=1, label="well centers",
    )

    for index, clover in enumerate(clovers):
        center_x, center_y = clover["center"]
        axes.plot(
            center_x, center_y, marker="+", markersize=9,
            color="tab:red", zorder=3,
            label="clover center" if index == 0 else None,
        )
        xs = [clover["droplets"][key]["absolute"][0] for key in keys]
        ys = [clover["droplets"][key]["absolute"][1] for key in keys]
        axes.scatter(
            xs, ys, s=18, color="tab:blue", zorder=4,
            label="droplet" if index == 0 else None,
        )
        if radius > 0:
            for x, y in zip(xs, ys):
                axes.add_patch(
                    Circle(
                        (x, y), radius, fill=False, edgecolor="tab:blue",
                        alpha=0.35, linewidth=0.8, zorder=2,
                    )
                )
        for key in keys:
            x, y = clover["droplets"][key]["absolute"]
            axes.annotate(
                key.upper(), (x, y), textcoords="offset points", xytext=(3, 3),
                fontsize=6, color="tab:blue",
            )
        axes.annotate(
            clover["name"],
            (center_x, clover["extents"]["max_y"]),
            textcoords="offset points", xytext=(0, 8),
            ha="center", fontsize=8, color="tab:red",
        )

    axes.set_xlim(-5, dimensions["xDimension"] + 5)
    axes.set_ylim(-5, dimensions["yDimension"] + 5)
    axes.set_aspect("equal")
    axes.set_xlabel("x (mm, labware-local)")
    axes.set_ylabel("y (mm, labware-local)")
    axes.set_title(
        f"Four-clover layout: {len(clovers)} clovers, "
        f"{len(clovers) * 4} droplet positions"
    )
    axes.legend(loc="upper right", fontsize=7)
    axes.grid(True, linewidth=0.3, alpha=0.4)
    figure.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out_path, dpi=160)
    plt.close(figure)
    print(f"\nwrote {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plot and report a four-clover layout from its YAML config."
    )
    parser.add_argument(
        "--config", default="configs/printing/four_clover_v12.yaml",
        help="Run config (the one with destination_config).",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output image path. Default: four_clover_layouts/<config stem>.png",
    )
    parser.add_argument(
        "--text-only", action="store_true",
        help="Print the coordinate report and skip the image (no matplotlib needed).",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = REPO / config_path
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}")
        return 1

    try:
        (_, config, paper, wells, _, clovers, bounds,
         intra, inter, order_mode, plan) = build(config_path)
    except (ValueError, KeyError) as exc:
        print(f"GEOMETRY ERROR: {exc}")
        return 1

    report(config, paper, clovers, bounds, intra, inter, order_mode, plan)

    if args.text_only:
        return 0
    out_path = (
        Path(args.out) if args.out
        else REPO / "four_clover_layouts" / f"{config_path.stem}.png"
    )
    if not out_path.is_absolute():
        out_path = REPO / out_path
    plot(config, paper, wells, clovers, bounds, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
