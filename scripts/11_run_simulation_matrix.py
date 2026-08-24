#!/usr/bin/env python3
"""Version 11 Opentrons simulation matrix - home laptop, no robot.

    python scripts/11_run_simulation_matrix.py
    python scripts/11_run_simulation_matrix.py --only clover_print
    python scripts/11_run_simulation_matrix.py --case clv_sep_3mm --verbose
    python scripts/11_run_simulation_matrix.py --random 40 --seed 11

For every case in configs/tests/11_simulation_matrix.yaml:

    template + overrides -> V11 loader -> V11 builder -> generated protocol
    -> opentrons.simulate (in-process) -> inspect the command stream

Command counting is done here rather than in each builder so all three
workflows are measured identically.
"""
from __future__ import annotations

import argparse
import copy
import random
import re
import sys
import traceback
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import yaml

from src.printing.v11.labware import LABWARE, PAPER, V11ConfigError

MATRIX = REPO / "configs" / "tests" / "11_simulation_matrix.yaml"
SCRATCH = REPO / ".test_tmp" / "v11-matrix"
LABWARE_DIR = REPO / "labware"

WORKFLOWS = {
    "standard_print": {
        "template": REPO / "configs" / "templates" / "11_standard_print_template.yaml",
        "loader": ("src.printing.v11.standard_loader", "load_standard_print_config"),
        "builder": ("src.printing.v11.standard_builder", "build_standard_print_protocol"),
    },
    "clover_print": {
        "template": REPO / "configs" / "templates" / "11_clover_print_template.yaml",
        "loader": ("src.printing.v11.clover_loader", "load_clover_config"),
        "builder": ("src.printing.v11.clover_builder", "build_clover_protocol"),
    },
    "dilution": {
        "template": REPO / "configs" / "templates" / "11_dilution_template.yaml",
        "loader": ("src.printing.v11.dilution_loader", "load_dilution_config"),
        "builder": ("src.printing.v11.dilution_builder", "build_dilution_protocol"),
    },
}


def deep_merge(base: dict, overrides: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (overrides or {}).items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _import(spec: tuple[str, str]):
    module_name, attribute = spec
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, attribute)


def simulate_protocol(path: Path) -> tuple[bool, list[dict], str]:
    """In-process opentrons simulation; returns (ok, run_log, text)."""
    import os

    import numpy as np

    np.trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))
    config_dir = REPO / ".test_tmp" / "opentrons-simulator"
    config_dir.mkdir(parents=True, exist_ok=True)
    os.environ["OT_API_CONFIG_DIR"] = str(config_dir)

    from opentrons.simulate import simulate as opentrons_simulate

    with path.open("rb") as handle:
        run_log, _ = opentrons_simulate(handle, custom_labware_paths=[str(LABWARE_DIR)])
    text = "\n".join(entry["payload"].get("text", "") for entry in run_log)
    return True, run_log, text


def count_commands(text: str) -> dict[str, int]:
    """Classify the simulated command stream."""
    lines = text.splitlines()
    counts = {
        "tip_pickups": 0, "tip_drops": 0, "aspirates": 0, "dispenses": 0,
        "air_gaps": 0, "blow_outs": 0, "mixes": 0, "delays": 0, "deposits": 0,
        "paper_dispenses": 0,
    }
    for line in lines:
        low = line.lower()
        if low.startswith("picking up tip"):
            counts["tip_pickups"] += 1
        elif low.startswith("dropping tip") or low.startswith("returning tip"):
            counts["tip_drops"] += 1
        elif low.startswith("aspirating"):
            counts["aspirates"] += 1
        elif low.startswith("dispensing"):
            counts["dispenses"] += 1
            if "paper" in low:
                counts["paper_dispenses"] += 1
        elif low.startswith("air gap"):
            counts["air_gaps"] += 1
        elif low.startswith("blowing out"):
            counts["blow_outs"] += 1
        elif low.startswith("mixing"):
            counts["mixes"] += 1
        elif low.startswith("delaying") or low.startswith("pausing"):
            counts["delays"] += 1
    counts["deposits"] = counts["paper_dispenses"]
    return counts


def measure_separation(resolved: dict) -> tuple[float | None, float | None]:
    geometry = resolved.get("geometry") or {}
    if geometry.get("separation_x_mm") is not None:
        return (float(geometry["separation_x_mm"]),
                float(geometry.get("separation_y_mm", geometry["separation_x_mm"])))
    if geometry.get("half_width_mm") is not None:
        return (2 * float(geometry["half_width_mm"]),
                2 * float(geometry.get("half_height_mm", geometry["half_width_mm"])))
    clovers = resolved.get("clovers") or []
    if clovers and isinstance(clovers[0], dict):
        g = clovers[0].get("geometry") or {}
        if g.get("half_width_mm") is not None:
            return (2 * float(g["half_width_mm"]),
                    2 * float(g.get("half_height_mm", g["half_width_mm"])))
    return None, None


def find_volumes(resolved: dict) -> tuple[float | None, float | None]:
    for stock_key, diluent_key, parent in (
        ("stock_volume_ul", "diluent_volume_ul", resolved.get("single") or {}),
        ("stock_volume_ul", "diluent_volume_ul", resolved.get("dilution") or {}),
        ("stock_volume_ul", "diluent_volume_ul", resolved),
    ):
        if stock_key in parent and diluent_key in parent:
            try:
                return float(parent[stock_key]), float(parent[diluent_key])
            except (TypeError, ValueError):
                continue
    return None, None


def run_case(workflow: str, case: dict, *, verbose: bool = False) -> tuple[bool, list[str], dict]:
    problems: list[str] = []
    info: dict[str, Any] = {}
    spec = WORKFLOWS[workflow]

    template = yaml.safe_load(spec["template"].read_text(encoding="utf-8"))
    config = deep_merge(template, case.get("overrides") or {})

    SCRATCH.mkdir(parents=True, exist_ok=True)
    config_path = SCRATCH / f"{workflow}_{case['name']}.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    load = _import(spec["loader"])
    build = _import(spec["builder"])

    resolved, run_modes = load(config_path)
    info["resolved"] = resolved

    # Never let a test run mutate the repository's upload-ready artifacts: the
    # matrix writes only into SCRATCH.
    import inspect

    build_kwargs = {"run_modes": run_modes, "output_dir": SCRATCH}
    if "write_latest" in inspect.signature(build).parameters:
        build_kwargs["write_latest"] = False
    built = build(resolved, **build_kwargs)
    protocol_path = getattr(built, "protocol_path", built)
    info["protocol"] = protocol_path

    ok, run_log, text = simulate_protocol(Path(protocol_path))
    counts = count_commands(text)
    info["counts"] = counts
    if verbose:
        print(f"      counts: {counts}")

    for key, want in (case.get("expect") or {}).items():
        got = counts.get(key)
        if got is None:
            problems.append(f"unknown expectation {key!r}")
        elif got != want:
            problems.append(f"{key}: expected {want}, simulated {got}")

    want_separation = case.get("expect_separation")
    if want_separation:
        sep_x, sep_y = measure_separation(resolved)
        if sep_x is None:
            problems.append("separation: not resolvable from config")
        else:
            if abs(sep_x - want_separation["x"]) > 1e-6:
                problems.append(
                    f"separation x: expected {want_separation['x']}, got {sep_x}")
            if abs(sep_y - want_separation["y"]) > 1e-6:
                problems.append(
                    f"separation y: expected {want_separation['y']}, got {sep_y}")

    want_volumes = case.get("expect_volumes")
    if want_volumes:
        stock, diluent = find_volumes(resolved)
        if stock is None:
            problems.append("volumes: not resolvable from config")
        else:
            if abs(stock - want_volumes["stock"]) > 1e-6:
                problems.append(
                    f"stock volume: expected {want_volumes['stock']}, got {stock}")
            if abs(diluent - want_volumes["diluent"]) > 1e-6:
                problems.append(
                    f"diluent volume: expected {want_volumes['diluent']}, got {diluent}")

    return not problems, problems, info


def random_cases(count: int, seed: int) -> list[tuple[str, dict]]:
    """Generate valid random configurations to find default-only assumptions."""
    rng = random.Random(seed)
    rows, columns = list("ABCDEFGH"), list(range(1, 13))
    plate_wells = [f"{r}{c}" for r in rows for c in columns]
    vial_wells = [f"{r}{c}" for r in "AB" for c in range(1, 5)]
    out: list[tuple[str, dict]] = []

    def source(kind: str) -> dict:
        if kind == "vial_rack":
            return {"type": "vial_rack", "slot": 7, "wells": [rng.choice(vial_wells)],
                    "loaded_volume_ul": 5000, "minimum_remaining_ul": 100,
                    "aspirate_height_mm": 4.0}
        slot = 4 if kind == "corning_plate" else 1
        return {"type": kind, "slot": slot, "wells": [rng.choice(plate_wells)],
                "loaded_volume_ul": 300, "minimum_remaining_ul": 20,
                "aspirate_height_mm": 1.0}

    for index in range(count):
        workflow = rng.choice(list(WORKFLOWS))
        kind = rng.choice(["vial_rack", "corning_plate", "well_plate"])
        overrides: dict[str, Any] = {
            "tips": {"pipette_tip_reuse": rng.choice([True, False])},
            "pipetting": {"air_gap_ul": rng.choice([0.0, 1.0, 1.5, 2.0])},
        }
        if workflow == "standard_print":
            overrides["source"] = source(kind)
            overrides["paper"] = {
                "slot": rng.choice([5, 11]),
                "print_height_mm": rng.choice([0.2, 0.5, 1.0, 2.0]),
            }
            overrides["printing"] = {
                "droplet_volume_ul": rng.choice([1.0, 2.0, 5.0, 10.0]),
                "order": rng.choice(["layer_major", "target_major"]),
            }
            column = rng.choice(columns)
            chosen_rows = rng.sample(rows, rng.randint(1, 4))
            overrides["groups"] = [{
                "target_selection": {"column": column, "rows": sorted(chosen_rows)},
                "droplets": rng.randint(1, 3),
            }]
            overrides["timing"] = {"inter_layer_delay_s": rng.choice([0, 1, 5])}
        elif workflow == "clover_print":
            overrides["source"] = source(kind)
            overrides["paper"] = {
                "slot": rng.choice([5, 11]),
                "print_height_mm": rng.choice([0.2, 0.5, 1.0]),
            }
            overrides["printing"] = {"droplet_volume_ul": rng.choice([1.0, 3.0, 5.0])}
            # Keep references off the outer ring so any separation stays in bounds.
            references = rng.sample(
                [f"{r}{c}" for r in "BCDEFG" for c in range(2, 12)],
                rng.randint(1, 3),
            )
            overrides["clovers"] = [{"reference": r} for r in references]
            separation = rng.choice([1.0, 2.0, 3.0, 4.0])
            overrides["geometry"] = {
                "separation_x_mm": separation,
                "separation_y_mm": rng.choice([1.0, 2.0, separation]),
                "rotation_deg": rng.choice([0, 45, 90]),
                # an actual separation supersedes the template's half values
                "half_width_mm": None, "half_height_mm": None,
            }
            overrides["layers"] = rng.randint(1, 3)
        else:
            stock_block = source(kind)
            stock_well = stock_block["wells"][0]
            overrides["stock_source"] = {
                k: v for k, v in stock_block.items() if k != "wells"
            } | {"well": stock_well}
            # The diluent defaults to vial A2; never let the stock land on it.
            if kind == "vial_rack" and stock_well == "A2":
                overrides["stock_source"]["well"] = "A1"
            if kind == "vial_rack" and overrides["stock_source"]["well"] == "A1":
                overrides["diluent_source"] = {"type": "vial_rack", "slot": 7,
                                               "well": "A2"}
            mode = rng.choice(["single", "series"])
            overrides["mode"] = mode
            overrides["transfer"] = {
                "max_chunk_ul": rng.choice([10.0, 15.0, 18.0, 20.0]),
                "air_gap_ul": rng.choice([0.0, 1.0, 1.5]),
            }
            overrides["mix"] = {
                "enabled": rng.choice([True, False]),
                "cycles": rng.randint(1, 8),
                "volume_ul": rng.choice([5.0, 10.0, 15.0]),
            }
            if mode == "single":
                factor = rng.choice([2, 4, 5, 10])
                final = rng.choice([50, 100, 200, 300])
                overrides["single"] = {"dilution_factor": factor,
                                       "final_volume_ul": final,
                                       "stock_volume_ul": None,
                                       "diluent_volume_ul": None}
                overrides["destination"] = {
                    "type": "well_plate", "slot": 1,
                    "wells": [rng.choice(plate_wells)],
                }
            else:
                steps = rng.randint(2, 5)
                start_row = rng.choice(rows[: 8 - steps]) if steps <= 8 else "A"
                direction = rng.choice(["row", "column"])
                start = (f"{start_row}1" if direction == "column"
                         else f"{rng.choice(rows)}1")
                overrides["series"] = {
                    "start_well": start, "direction": direction, "steps": steps,
                    "transfer_volume_ul": rng.choice([10.0, 15.0, 20.0]),
                    "diluent_volume_ul": rng.choice([40.0, 80.0]),
                }
                overrides["destination"] = {"type": "well_plate", "slot": 1}
        out.append((workflow, {"name": f"rand_{index:03d}", "overrides": overrides}))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=list(WORKFLOWS))
    parser.add_argument("--case")
    parser.add_argument("--random", type=int, default=0,
                        help="also run N generated valid configurations")
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    (REPO / ".test_tmp").mkdir(exist_ok=True)
    matrix = yaml.safe_load(MATRIX.read_text(encoding="utf-8"))
    workflows = [args.only] if args.only else list(WORKFLOWS)

    total = passed = 0
    failures: list[tuple[str, str, list[str]]] = []

    for workflow in workflows:
        entries = matrix.get(workflow) or []
        if args.case:
            entries = [c for c in entries if c.get("name") == args.case]
        if not entries:
            continue
        print(f"\n=== {workflow} ({len(entries)} simulations) ===")
        for case in entries:
            total += 1
            name = case["name"]
            try:
                ok, problems, _ = run_case(workflow, case, verbose=args.verbose)
            except Exception as exc:  # noqa: BLE001
                ok, problems = False, [f"{type(exc).__name__}: {exc}"]
                if args.verbose:
                    traceback.print_exc()
            if ok:
                passed += 1
                print(f"  PASS  {name}")
            else:
                failures.append((workflow, name, problems))
                print(f"  FAIL  {name}")
                for problem in problems:
                    print(f"          {problem}")

    if args.random:
        print(f"\n=== randomized ({args.random} cases, seed {args.seed}) ===")
        for workflow, case in random_cases(args.random, args.seed):
            if args.only and workflow != args.only:
                continue
            total += 1
            try:
                ok, problems, _ = run_case(workflow, case, verbose=args.verbose)
            except Exception as exc:  # noqa: BLE001
                ok, problems = False, [f"{type(exc).__name__}: {exc}"]
                if args.verbose:
                    traceback.print_exc()
            if ok:
                passed += 1
                print(f"  PASS  {workflow}/{case['name']}")
            else:
                failures.append((workflow, case["name"], problems))
                print(f"  FAIL  {workflow}/{case['name']}")
                for problem in problems:
                    print(f"          {problem}")

    print(f"\n{'=' * 60}")
    print(f"SIMULATIONS: {passed}/{total} passed")
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for workflow, name, problems in failures:
            print(f"  [{workflow}] {name}")
            for problem in problems:
                print(f"      {problem}")
        return 1
    print("ALL SIMULATIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
