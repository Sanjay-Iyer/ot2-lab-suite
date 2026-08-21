#!/usr/bin/env python3
"""Ask the Printing Agent to rebuild a validated experiment from plain language.

    python scripts/reproduce_ground_truth_from_language.py standard
    python scripts/reproduce_ground_truth_from_language.py four-clover
    python scripts/reproduce_ground_truth_from_language.py both

The agent is given only a natural-language request, the generalized template, the
registered machine profile, its skill, and its tools. It is NOT given the
hand-validated ground-truth YAML, the resolved action list, or any expected hash.

Whatever it produces is saved separately under ``configs/generated/``. Both the
ground truth and the generated configuration are then put through the SAME
deterministic resolver, and the two resulting physical plans are compared. YAML
formatting, ids, titles, and step names are ignored; what must match is what the
robot would physically do.

Nothing here contacts a robot.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

RULE = "=" * 78

GENERATED_DIR = REPO / "configs" / "generated"

STANDARD_GROUND_TRUTH = "configs/experiments/01_printing_standard.yaml"
CLOVER_GROUND_TRUTH = "configs/experiments/02_printing_four_clover.yaml"

STANDARD_GENERATED = GENERATED_DIR / "01_printing_standard.generated.yaml"
CLOVER_GENERATED = GENERATED_DIR / "02_printing_four_clover.generated.yaml"


# The scientist's own words. Deliberately phrased as a request, with no field
# names, no well lists beyond the columns, and no hint of the ground-truth file.
STANDARD_REQUEST = """Using the standard printing workflow, set up a SERS experiment on one sheet
of the 96-position paper.

I have four things loaded in the 20 mL vial rack: a plasmonic nanoparticle stock
in A1, its diluent in A2, a crystal violet stock in A3, and its diluent in A4.
Assume 5000 uL of each and keep 2600 uL of each in reserve.

Prepare an eight-point twofold nanoparticle dilution series down plate column 1,
from undiluted stock down to one over one hundred and twenty-eight, and leave
30 uL usable in every point. Prepare a matching eight-point twofold crystal
violet series down plate column 2. Mix three times with 3 uL when making each
point, and keep the undiluted first point of each series under the name of the
stock it came from.

Then print four columns of the paper, 5 uL a droplet, eight rows each:

Column 1 gets one nanoparticle droplet per row, most concentrated at the top,
then five minutes to dry, then stock crystal violet on top.

Column 2 is the same eight nanoparticle conditions but three droplets per
position with five minutes of drying after every one of the three layers, and
that third rest is also what dries the position before the stock crystal violet
goes on top. Do not add a separate extra rest for the overlay.

Column 3 is a control column: stock crystal violet only, eight replicates, no
nanoparticles.

Column 4 is the crystal violet series, one droplet per row, no nanoparticles,
also a control.

Resuspend the source well before each droplet is drawn from any prepared
dilution series, nanoparticle or crystal violet. Use a fresh tip whenever
consecutive targets get different liquids, and a single tip for a step where
every target gets the same liquid.
"""

CLOVER_REQUEST = """Using the four-clover printing workflow, print four clover patterns with 5 uL
droplets to compare droplet separation.

Put them along row B, three wells apart so they cannot bleed into each other:
B2, B5, B8 and B11. The first should have 2 mm between opposing droplets, the
second 3 mm, the third 4 mm and the fourth 5 mm.

One layer each. The material is a plasmonic nanoparticle preparation in vial A2;
assume 5000 uL loaded and keep 100 uL in reserve. Pause two seconds after each
droplet, and finish one clover before starting the next.
"""


def _banner(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def _extract_config(messages: list, tool_name: str) -> dict | None:
    """Recover the exact experiment_config the agent submitted to a given tool."""
    for message in reversed(messages):
        for call in getattr(message, "tool_calls", None) or []:
            if call.get("name") == tool_name:
                args = call.get("args") or {}
                config = args.get("experiment_config")
                if isinstance(config, dict):
                    return config
    return None


def _run_agent(request: str, *, recursion_limit: int = 40) -> list:
    from src.agents.printing_agent import create_printing_experiment_agent

    agent = create_printing_experiment_agent()
    result = agent.invoke(
        {"messages": [("human", request)]},
        config={"recursion_limit": recursion_limit},
    )
    return list(result["messages"])


def _save(path: Path, config: dict) -> str:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# AI-GENERATED CONFIGURATION - not a hand-validated ground truth.\n"
        "# Produced by scripts/reproduce_ground_truth_from_language.py from a\n"
        "# natural-language request alone. Review it before any physical run.\n"
    )
    path.write_text(
        header + yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return str(path.relative_to(REPO))


def _semantic_layout(plan) -> dict[str, list[tuple[str, float, int]]]:
    """Map each paper position to what is printed there, ignoring liquid NAMES.

    A printed liquid is identified by which point of which preparation it is --
    "the third point of the first ladder" -- so two configurations that build the
    same series under different names still compare equal. A liquid that was
    loaded by hand rather than prepared keeps the labware and well it came from,
    which is likewise name-independent.
    """
    # Keyed on (liquid_id, source labware, source well) rather than the name
    # alone: one name can legitimately be both a hand-loaded stock in a vial and
    # the undiluted first point of a ladder sitting in a plate well, and those
    # are different sources even though the liquid is the same.
    prepared: dict[tuple[str, str], str] = {}
    for index, step in enumerate(plan.preparation_math):
        for position, product in enumerate(step["products"]):
            prepared[(product, step["destination_wells"][position])] = (
                f"prep{index}:point{position}/{step['factors'][position]}"
            )
    loaded: dict[tuple[str, str, str], str] = {
        (liquid.liquid_id, liquid.location.labware, liquid.location.well): (
            f"loaded:{liquid.location.labware}:{liquid.location.well}"
        )
        for liquid in plan.initial_liquids
    }

    layout: dict[str, list[tuple[str, float, int]]] = {}
    for action in plan.actions:
        if action.action != "PRINT":
            continue
        source = action.source
        name = loaded.get((action.liquid_id, source.labware, source.well))
        if name is None:
            name = prepared.get(
                (action.liquid_id, source.well),
                f"unknown:{action.liquid_id}@{source.labware}:{source.well}",
            )
        layout.setdefault(action.destination.well, []).append(
            (name, round(float(action.volume_ul), 6), int(action.drop_index))
        )
    return layout


def _report_layout_match(truth_plan, generated_plan) -> bool:
    """Print the experiment-level verdict and any position that really differs."""
    left, right = _semantic_layout(truth_plan), _semantic_layout(generated_plan)
    matched = left == right
    print(f"\nsubstrate positions      : ground truth {len(left)}, generated {len(right)}")
    print(f"printed layout match     : {matched}"
          "   (same liquid, dilution point, volume and repeat count per position)")
    if not matched:
        for well in sorted(set(left) | set(right)):
            if left.get(well) != right.get(well):
                print(f"  {well}")
                print(f"    ground truth : {left.get(well)}")
                print(f"    generated    : {right.get(well)}")
    return matched


# ── standard ──────────────────────────────────────────────────────────────────────

def reproduce_standard() -> bool:
    from src.printing.standard import equivalence
    from src.printing.standard.loader import (
        load_experiment_job,
        load_experiment_job_mapping,
    )
    from src.printing.standard.resolver import resolve_experiment_job

    _banner("STANDARD PRINTING - reproduce the ground truth from language")
    print("The agent receives the request below, the template, the machine profile,")
    print("its skill, and its tools. It never sees the ground-truth configuration.\n")
    print(STANDARD_REQUEST)

    messages = _run_agent(STANDARD_REQUEST)
    config = _extract_config(messages, "create_standard_printing_experiment_config")
    if config is None:
        config = _extract_config(messages, "inspect_standard_printing_layout")
    if config is None:
        config = _extract_config(messages, "validate_standard_printing_experiment")
    if config is None:
        print("the agent never submitted a standard experiment configuration")
        print(f"last message: {getattr(messages[-1], 'content', '')[:1500]}")
        return False

    generated_job = load_experiment_job_mapping(config)
    generated_plan = resolve_experiment_job(generated_job)
    path = _save(STANDARD_GENERATED, config)

    truth_job = load_experiment_job(STANDARD_GROUND_TRUTH)
    truth_plan = resolve_experiment_job(truth_job)

    report = equivalence.compare_plans(
        truth_plan, generated_plan, left_label="ground_truth", right_label="generated"
    )
    print(f"\ngenerated configuration : {path}")
    print(f"ground truth            : {STANDARD_GROUND_TRUTH}")
    print(f"ground-truth actions    : {report['left_action_count']}")
    print(f"generated actions       : {report['right_action_count']}")
    print(f"physical match          : {report['physical_match']}")
    print(f"setup match             : {report['setup_match']}")
    print(f"execution match         : {report['execution_match']}")
    print(f"structural match        : {report['structural_match']}")
    layout_matched = _report_layout_match(truth_plan, generated_plan)

    differences = report["physical_differences"]
    if differences:
        print(f"\nfirst {min(len(differences), 12)} physical-trace differences:")
        for line in differences[:12]:
            print(f"  - {line}")
        print(f"  ({len(differences)} in total)")

    print()
    print(f"EXPERIMENT DESIGN  : {'MATCH' if layout_matched else 'DIFFER'}")
    print(f"PHYSICAL TRACE     : {'MATCH' if report['execution_match'] else 'DIFFER'}")
    return bool(report["execution_match"])


# ── four clover ───────────────────────────────────────────────────────────────────

def reproduce_four_clover() -> bool:
    from src.printing.clover.loader import (
        load_experiment_job_mapping,
        load_manual_executor_config,
    )
    from src.printing.clover.resolver import resolve_experiment_job, resolve_manual_config
    from src.printing.clover.review import render_clover_coordinates

    _banner("FOUR-CLOVER PRINTING - reproduce the ground truth from language")
    print("The agent receives the request below, the template, the machine profile,")
    print("its skill, and its tools. It never sees the ground-truth configuration.\n")
    print(CLOVER_REQUEST)

    messages = _run_agent(CLOVER_REQUEST)
    config = _extract_config(messages, "create_four_clover_experiment_config")
    for fallback in (
        "simulate_four_clover_experiment",
        "preview_four_clover_experiment",
        "validate_four_clover_experiment",
    ):
        if config is None:
            config = _extract_config(messages, fallback)
    if config is None:
        print("the agent never submitted a four-clover experiment configuration")
        print(f"last message: {getattr(messages[-1], 'content', '')[:1500]}")
        return False

    generated_job = load_experiment_job_mapping(config)
    generated_plan = resolve_experiment_job(generated_job)
    path = _save(CLOVER_GENERATED, config)

    truth_config, _ = load_manual_executor_config(CLOVER_GROUND_TRUTH)
    truth_plan = resolve_manual_config(truth_config, experiment_id="ground_truth")

    print(f"\ngenerated configuration : {path}")
    print(f"ground truth            : {CLOVER_GROUND_TRUTH}")
    print()
    print("--- ground truth ---")
    print(render_clover_coordinates(truth_plan))
    print()
    print("--- AI generated ---")
    print(render_clover_coordinates(generated_plan))

    truth_fingerprint = truth_plan.physical_sha256()
    generated_fingerprint = generated_plan.physical_sha256()
    print()
    print(f"ground-truth physical sha256 : {truth_fingerprint}")
    print(f"generated physical sha256    : {generated_fingerprint}")
    matched = truth_fingerprint == generated_fingerprint
    print(f"physical match               : {matched}")
    if not matched:
        left, right = truth_plan.physical_payload(), generated_plan.physical_payload()
        for key in sorted(set(left) | set(right)):
            if left.get(key) != right.get(key):
                print(f"\n  {key}")
                print(f"    ground truth : {json.dumps(left.get(key))[:400]}")
                print(f"    generated    : {json.dumps(right.get(key))[:400]}")
    return matched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("workflow", choices=("standard", "four-clover", "both"))
    args = parser.parse_args(argv)

    selected = (
        ("standard", "four-clover") if args.workflow == "both" else (args.workflow,)
    )
    results: dict[str, bool] = {}
    for name in selected:
        try:
            results[name] = (
                reproduce_standard() if name == "standard" else reproduce_four_clover()
            )
        except Exception as exc:  # noqa: BLE001 - the operator needs the raw reason
            print(f"\n{type(exc).__name__}: {exc}", file=sys.stderr)
            results[name] = False

    _banner("RESULT")
    for name in selected:
        print(f"  {name:<12} {'MATCH' if results[name] else 'DIFFER'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
