#!/usr/bin/env python3
"""Manual fallback entry point for the two OT-2 printing workflows.

    python scripts/run_printing_workflow.py standard
    python scripts/run_printing_workflow.py four-clover
    python scripts/run_printing_workflow.py both

Each run takes a hand-edited YAML, validates it, resolves it deterministically,
prints a human-readable summary, writes the upload-ready protocol, and simulates
it locally. Nothing here contacts, discovers, or moves a robot, and nothing here
uses an agent, an LLM, a runtime skill, or an approval workflow.

    WORKFLOW 1 - standard 96-position SERS printing
      config    configs/experiments/01_printing_standard.yaml
      executor  src/protocols/printing/01_printing_standard.py
      upload    src/protocols/generated/01_printing_standard_latest.py

    WORKFLOW 2 - four-clover printing
      config    configs/experiments/02_printing_four_clover.yaml
      executor  src/protocols/printing/02_printing_four_clover.py
      upload    src/protocols/generated/02_printing_four_clover_latest.py

Useful flags:
    --summary       resolve and report only; skip building and simulating
    --no-sim        build the upload artifact but skip the simulation
    --config PATH   use a different YAML instead of the default for that workflow

Exit status is 0 only when every requested workflow validated, resolved, and (unless
skipped) simulated successfully.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

STANDARD_CONFIG = "configs/experiments/01_printing_standard.yaml"
STANDARD_EXECUTOR = REPO / "src" / "protocols" / "printing" / "01_printing_standard.py"
STANDARD_UPLOAD = (
    REPO / "src" / "protocols" / "generated" / "01_printing_standard_latest.py"
)

CLOVER_CONFIG = "configs/experiments/02_printing_four_clover.yaml"
CLOVER_EXECUTOR = REPO / "src" / "protocols" / "printing" / "02_printing_four_clover.py"
CLOVER_UPLOAD = (
    REPO / "src" / "protocols" / "generated" / "02_printing_four_clover_latest.py"
)

LABWARE_DIR = REPO / "labware"
RULE = "=" * 78


class WorkflowFailure(Exception):
    """A configuration or simulation problem the operator has to fix."""


# ── shared helpers ────────────────────────────────────────────────────────────────

def _banner(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _numpy_trapz_shim() -> None:
    """opentrons_shared_data still imports numpy.trapz, removed in numpy 2."""
    try:
        import numpy
    except ImportError:
        return
    if not hasattr(numpy, "trapz") and hasattr(numpy, "trapezoid"):
        numpy.trapz = numpy.trapezoid


def _load_module(path: Path, name: str):
    _numpy_trapz_shim()
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise WorkflowFailure(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── workflow 1: standard 96-position SERS printing ────────────────────────────────

def run_standard(config_reference: str, *, summary_only: bool, simulate: bool) -> bool:
    from src.printing.standard import builder
    from src.printing.standard.loader import (
        ExperimentJobLoadError,
        load_experiment_job,
    )
    from src.printing.standard.resolver import (
        ExperimentResolutionError,
        resolve_experiment_job,
    )
    from src.printing.standard.review import render_plan_review

    _banner("WORKFLOW 1 - STANDARD 96-POSITION SERS PRINTING")
    print(f"config   : {config_reference}")
    print(f"executor : {STANDARD_EXECUTOR.relative_to(REPO)}")

    try:
        job = load_experiment_job(config_reference)
    except (ExperimentJobLoadError, ValueError) as exc:
        raise WorkflowFailure(
            f"CONFIG VALIDATION FAILED ({config_reference}):\n{exc}"
        ) from exc
    print(f"machine  : {job.machine.robot_type} API {job.machine.api_level}")
    print(f"job_sha256   : {job.job_id}")

    try:
        plan = resolve_experiment_job(job)
    except ExperimentResolutionError as exc:
        raise WorkflowFailure(f"RESOLUTION FAILED\n{exc}") from exc
    print(f"plan_sha256  : {plan.plan_id}")

    print()
    print(render_plan_review(plan, job))

    totals = plan.totals
    tiprack = next(
        action for action in plan.actions
        if action.action == "LOAD_LABWARE" and action.role == "tiprack"
    )
    capacity = 96 if "96" in tiprack.load_name else 0
    print()
    print("--- resolved totals ---")
    print(f"  actions            : {totals.action_count}")
    print(f"  transfers          : {totals.transfer_count}")
    print(f"  mixes              : {totals.mix_count}")
    print(f"  prints             : {totals.print_count}")
    print(f"  delays             : {totals.delay_count} "
          f"({totals.configured_experimental_delay_s:g} s configured in total)")
    print(f"  printed liquid     : {totals.printed_liquid_ul:g} uL")
    print(f"  tips required      : {totals.tip_count} of {capacity} in "
          f"{tiprack.load_name} (slot {tiprack.slot})")
    if capacity and totals.tip_count > capacity:
        raise WorkflowFailure(
            f"TIP SHORTAGE: the plan needs {totals.tip_count} tips but slot "
            f"{tiprack.slot} holds {capacity}"
        )
    print(f"  tip capacity       : {'PASS' if capacity else 'UNKNOWN'}")
    print(f"  source accessibility: {plan.source_accessibility.status}")

    if summary_only:
        print("\n--summary: stopping before build and simulation.")
        return True

    artifact = builder.build_standard_protocol(plan)
    STANDARD_UPLOAD.parent.mkdir(parents=True, exist_ok=True)
    STANDARD_UPLOAD.write_bytes(artifact.protocol_path.read_bytes())
    print()
    print(f"upload file  : {STANDARD_UPLOAD.relative_to(REPO)}")
    print(f"sha256       : {_sha256(STANDARD_UPLOAD)}")

    if not simulate:
        print("--no-sim: skipping simulation.")
        return True

    print("simulating locally (no robot contact) ...")
    try:
        passed, run_log, text = builder.simulate_standard_protocol(
            STANDARD_UPLOAD, expected_sha256=_sha256(STANDARD_UPLOAD)
        )
    except Exception as exc:  # noqa: BLE001 - the operator needs the raw reason
        raise WorkflowFailure(f"SIMULATION FAILED\n{type(exc).__name__}: {exc}") from exc

    deposits = sum(
        1
        for entry in run_log
        if "Paper Print Surface" in entry["payload"].get("text", "")
        and entry["payload"].get("text", "").startswith("Dispensing ")
    )
    print(f"simulation   : {'PASS' if passed else 'FAIL'}")
    print(f"paper deposits: {deposits}")
    print(f"final comment : {text.splitlines()[-1]}")
    if deposits != totals.print_count:
        raise WorkflowFailure(
            f"simulation deposited on paper {deposits} times but the plan declares "
            f"{totals.print_count} prints"
        )
    return passed


# ── workflow 2: four-clover printing ──────────────────────────────────────────────

def _clover_config(config_reference: str) -> tuple[dict, dict]:
    """Load the clover YAML exactly the way the builder does.

    Returns ``(config, run_modes)`` where ``config`` is the mapping embedded into
    the protocol and ``run_modes`` carries the DEFAULT_* flags.
    """
    path = REPO / config_reference if not Path(config_reference).is_absolute() else Path(config_reference)
    if not path.is_file():
        raise WorkflowFailure(f"config not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise WorkflowFailure(f"config must be a mapping: {path}")

    reference = loaded.pop("destination_config", None)
    if reference:
        shared_path = Path(reference)
        if not shared_path.is_absolute():
            shared_path = REPO / shared_path
        if not shared_path.is_file():
            raise WorkflowFailure(f"destination_config not found: {shared_path}")
        shared = yaml.safe_load(shared_path.read_text(encoding="utf-8")) or {}
        loaded["destination"] = shared.get("destination", shared)
    if "destination" not in loaded:
        raise WorkflowFailure(f"{config_reference} has no 'destination' section")

    run_modes = loaded.pop("run_modes", {}) or {}
    return loaded, run_modes


def _paper_well_centres(load_name: str) -> dict[str, tuple[float, float]]:
    import json

    path = LABWARE_DIR / f"{load_name}.json"
    if not path.is_file():
        raise WorkflowFailure(
            f"custom labware {load_name}.json not found in {LABWARE_DIR}; the "
            "coordinate resolver reads well centres straight from the definition"
        )
    definition = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: (float(well["x"]), float(well["y"]))
        for name, well in definition["wells"].items()
    }, definition


def _fail_fast_clover(config: dict, centres: dict, config_reference: str) -> None:
    """Reject a manual edit before anything is generated.

    These duplicate the protocol's own pre-flight so a bad YAML field is named
    here, at the terminal, instead of surfacing inside a simulation traceback.
    """
    problems: list[str] = []

    deck = config.get("deck") or {}
    for role in ("source", "paper", "tiprack_p20"):
        if role not in deck:
            problems.append(f"deck.{role} is missing")
    slots: dict[int, str] = {}
    for role, spec in deck.items():
        slot = (spec or {}).get("slot")
        if not isinstance(slot, int) or not 1 <= slot <= 11:
            problems.append(f"deck.{role}.slot must be an integer 1-11, got {slot!r}")
            continue
        if slot in slots:
            problems.append(
                f"deck slot {slot} is claimed by both '{slots[slot]}' and '{role}'"
            )
        slots[slot] = role

    printing = config.get("printing") or {}
    safety = config.get("safety") or {}
    p20_max = float(safety.get("p20_max_volume_ul", 20.0))
    volume = printing.get("droplet_volume_ul")
    if not isinstance(volume, (int, float)) or isinstance(volume, bool) or not 0 < float(volume) <= p20_max:
        problems.append(
            f"printing.droplet_volume_ul must be a number in (0, {p20_max:g}], "
            f"got {volume!r}"
        )
        volume = 0.0
    volume = float(volume)

    for key in (
        "dispense_height_mm", "air_gap_ul", "air_gap_height_mm", "push_out_ul",
        "pre_air_chase_ul", "inter_drop_delay_s", "inter_layer_delay_s",
        "inter_clover_delay_s",
    ):
        value = printing.get(key, 0.0) or 0.0
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            problems.append(f"printing.{key} must be numeric, got {value!r}")
        elif float(value) < 0:
            problems.append(f"printing.{key} must be >= 0, got {value!r}")
    layers = printing.get("layers", 1)
    if isinstance(layers, bool) or not isinstance(layers, int) or layers < 1:
        problems.append(f"printing.layers must be an integer >= 1, got {layers!r}")

    source = config.get("source") or {}
    for name in ("well", "loaded_volume_ul", "aspirate_height_mm"):
        if name not in source:
            problems.append(f"source.{name} is missing")

    for spec in (config.get("destination") or {}).get("manual_clover_centers") or []:
        well = str(spec.get("reference_well", "")).upper()
        if well not in centres:
            problems.append(
                f"clover {spec.get('name', '?')!r}: reference_well {well!r} does not "
                "exist on the paper labware"
            )

    tip = ((config.get("tips") or {}).get("p20") or {}).get("print_tip")
    if not isinstance(tip, str) or not tip.strip():
        problems.append("tips.p20.print_tip must name a tip well, e.g. A1")

    if problems:
        raise WorkflowFailure(
            f"CONFIG VALIDATION FAILED ({config_reference}):\n- " + "\n- ".join(problems)
        )


def run_four_clover(
    config_reference: str, *, summary_only: bool, simulate: bool
) -> bool:
    builder_module = _load_module(
        REPO / "scripts" / "build_vial_dilution_print.py", "ot2_print_builder"
    )
    engine = _load_module(CLOVER_EXECUTOR, "clover_executor")

    _banner("WORKFLOW 2 - FOUR-CLOVER PRINTING")
    print(f"config   : {config_reference}")
    print(f"executor : {CLOVER_EXECUTOR.relative_to(REPO)}")

    config, run_modes = _clover_config(config_reference)
    config.pop("protocol_version", None)
    config["protocol_version"] = 18

    paper_load_name = ((config.get("deck") or {}).get("paper") or {}).get("load_name")
    if not paper_load_name:
        raise WorkflowFailure("deck.paper.load_name is missing")
    centres, paper_definition = _paper_well_centres(paper_load_name)
    _fail_fast_clover(config, centres, config_reference)

    # Resolve coordinates with the executor's own geometry functions, so this
    # report can never disagree with what the protocol will do.
    engine.CONFIG = config
    try:
        clovers = engine._resolve_clovers(lambda name: centres[str(name).upper()])
        order_mode, plan = engine._print_order(clovers)
        bounds = engine._paper_bounds(
            lambda name: centres[str(name).upper()], list(centres)
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise WorkflowFailure(f"COORDINATE RESOLUTION FAILED\n{exc}") from exc

    printing = config["printing"]
    volume = float(printing["droplet_volume_ul"])
    air_gap = float(printing.get("air_gap_ul", 0.0) or 0.0)
    p20_max = float((config.get("safety") or {}).get("p20_max_volume_ul", 20.0))
    radius = float((config.get("validation") or {}).get("droplet_radius_mm", 0.0) or 0.0)

    fatal = engine._capacity_errors(clovers, volume, air_gap, p20_max)
    fatal += engine._boundary_violations(clovers, bounds, radius)
    if fatal:
        raise WorkflowFailure("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(fatal))

    deposits = sum(clover["layers"] for clover in clovers) * len(engine.DROPLET_KEYS)
    required = deposits * volume
    source = config["source"]
    loaded = float(source["loaded_volume_ul"])
    reserve = float(source.get("minimum_remaining_ul", 0.0) or 0.0)
    aspirate_height = float(source["aspirate_height_mm"])

    paper_z = float(paper_definition["wells"]["A1"]["z"])
    dispense_height = float(printing["dispense_height_mm"])

    print()
    print("--- source ---")
    print(f"  labware            : {config['deck']['source']['load_name']} "
          f"(slot {config['deck']['source']['slot']})")
    print(f"  well               : {str(source['well']).upper()}  "
          f"[{source.get('material', 'unnamed')}]")
    print(f"  loaded             : {loaded:g} uL, reserve {reserve:g} uL")
    print(f"  aspirate height    : {aspirate_height:g} mm above the vial bottom")

    print()
    print("--- paper ---")
    print(f"  labware            : {paper_load_name} "
          f"(slot {config['deck']['paper']['slot']})")
    print(f"  surface height     : {paper_z:g} mm above the slot floor")
    print(f"  dispense height    : {dispense_height:g} mm above the surface "
          f"-> absolute {paper_z + dispense_height:g} mm "
          f"(standoff {dispense_height:g} mm)")
    print(f"  usable box         : x [{bounds['min_x']:.2f}, {bounds['max_x']:.2f}] "
          f"y [{bounds['min_y']:.2f}, {bounds['max_y']:.2f}] mm (paper-local)")

    print()
    print("--- pattern ---")
    print(f"  clover patterns    : {len(clovers)}")
    print(f"  drop volume        : {volume:g} uL liquid per droplet")
    print(f"  piston load / drop : "
          f"{engine._piston_load(printing.get('pre_air_chase_ul', 0.0), volume, air_gap)['total']:g}"
          f" uL of {p20_max:g} uL "
          f"(chase {float(printing.get('pre_air_chase_ul', 0.0) or 0.0):g} + liquid "
          f"{volume:g} + air gap {air_gap:g})")
    print(f"  order              : {order_mode}")
    print(f"  inter-drop delay   : {float(printing.get('inter_drop_delay_s', 0) or 0):g} s")
    print(f"  inter-layer delay  : {float(printing.get('inter_layer_delay_s', 0) or 0):g} s")
    print(f"  inter-clover delay : {float(printing.get('inter_clover_delay_s', 0) or 0):g} s")
    print(f"  execution steps    : {len(plan)}")

    print()
    print("--- resolved clover coordinates (paper-local millimetres) ---")
    for clover in clovers:
        centre_x, centre_y = clover["center"]
        offset_x, offset_y = clover["center_offset"]
        print(f"  {clover['name']}")
        print(f"      reference well {clover['reference_well']}"
              f" + offset ({offset_x:+.2f}, {offset_y:+.2f})"
              f" -> centre x {centre_x:.2f}, y {centre_y:.2f}")
        print(f"      layers {clover['layers']}, geometry {clover['geometry_source']}, "
              f"pre-air chase {clover['pre_air_chase_ul']:g} uL")
        for key in engine.DROPLET_KEYS:
            droplet = clover["droplets"][key]
            dx, dy = droplet["offset"]
            ax, ay = droplet["absolute"]
            print(f"      {key.upper()}  offset ({dx:+.2f}, {dy:+.2f})"
                  f"  ->  x {ax:.2f}, y {ay:.2f}, z {paper_z + dispense_height:g}")

    intra, inter = engine._distance_report(clovers)
    if intra:
        worst = min(intra, key=lambda entry: entry["min_distance"])
        print(f"\n  minimum intra-clover distance: {worst['min_distance']:.2f} mm "
              f"({worst['clover']}, {worst['pair'][0]}-{worst['pair'][1]})")
    if inter:
        worst = min(inter, key=lambda entry: entry["min_distance"])
        print(f"  minimum inter-clover distance: {worst['min_distance']:.2f} mm "
              f"({worst['clovers'][0]} to {worst['clovers'][1]})")

    print()
    print("--- consumption ---")
    print(f"  deposits           : {deposits}")
    print(f"  liquid required    : {required:g} uL")
    print(f"  remaining after run: {loaded - required:g} uL "
          f"(reserve {reserve:g} uL)")
    if loaded < required + reserve:
        raise WorkflowFailure(
            f"INSUFFICIENT SOURCE: the run needs {required:g} uL plus a {reserve:g} uL "
            f"reserve but source.loaded_volume_ul is {loaded:g}"
        )
    vial_diameter = float(
        __import__("json").loads(
            (LABWARE_DIR / f"{config['deck']['source']['load_name']}.json").read_text(
                encoding="utf-8"
            )
        )["wells"][str(source["well"]).upper()]["diameter"]
    )
    cover_volume = math.pi * (vial_diameter / 2.0) ** 2 * aspirate_height
    if loaded - required <= cover_volume:
        raise WorkflowFailure(
            f"SOURCE WOULD UNCOVER THE TIP: {loaded - required:g} uL would remain, "
            f"below the ~{cover_volume:g} uL needed to keep the {aspirate_height:g} mm "
            "aspiration height submerged"
        )
    print(f"  submerged margin   : {loaded - required - cover_volume:g} uL above the "
          f"~{cover_volume:g} uL needed to cover {aspirate_height:g} mm")
    print(f"  tips required      : 1 "
          f"({str(config['tips']['p20']['print_tip']).upper()}, held for the whole run, "
          f"return_tips={bool(config['tips'].get('return_tips', True))})")
    print("  tip capacity       : PASS")

    dry_run = bool(run_modes.get("dry_run", True))
    print()
    print(f"  run_modes.dry_run  : {dry_run} "
          + ("(PLAN ONLY - the arm will not move on the robot)" if dry_run
             else "(the robot WILL print when this file is uploaded)"))

    if summary_only:
        print("\n--summary: stopping before build and simulation.")
        return True

    # Regenerate the executor's CONFIG block in place, then emit the upload copy.
    base_text = CLOVER_EXECUTOR.read_text(encoding="utf-8")
    generated = builder_module.build_source(base_text, config, run_modes)
    CLOVER_EXECUTOR.write_text(generated, encoding="utf-8")
    CLOVER_UPLOAD.parent.mkdir(parents=True, exist_ok=True)
    CLOVER_UPLOAD.write_text(generated, encoding="utf-8")
    print()
    print(f"executor updated: {CLOVER_EXECUTOR.relative_to(REPO)}")
    print(f"upload file  : {CLOVER_UPLOAD.relative_to(REPO)}")
    print(f"sha256       : {_sha256(CLOVER_UPLOAD)}")

    if not simulate:
        print("--no-sim: skipping simulation.")
        return True

    print("simulating locally (no robot contact) ...")
    passed, output = builder_module.simulate(CLOVER_UPLOAD)
    print(f"simulation   : {'PASS' if passed else 'FAIL'}")
    interesting = [
        line for line in output.splitlines()
        if any(
            token in line
            for token in ("Pre-flight", "Clovers:", "Print complete", "WARNING:",
                          "Minimum intra", "Minimum inter")
        )
    ]
    for line in interesting:
        print(f"  {line.strip()}")
    if not passed:
        print("\n--- simulation output ---")
        print(output[-4000:])
        raise WorkflowFailure("SIMULATION FAILED")
    return True


# ── entry point ───────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "workflow",
        choices=("standard", "four-clover", "both"),
        help="which manual workflow to validate, resolve, build and simulate",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="override the default YAML for the selected workflow "
             "(not allowed with 'both')",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="resolve and report only; do not build or simulate",
    )
    parser.add_argument(
        "--no-sim", action="store_true",
        help="build the upload artifact but skip the local simulation",
    )
    args = parser.parse_args(argv)

    if args.config and args.workflow == "both":
        parser.error("--config cannot be combined with 'both'")

    selected = (
        ("standard", "four-clover") if args.workflow == "both" else (args.workflow,)
    )
    simulate = not args.no_sim
    results: dict[str, str] = {}

    for name in selected:
        try:
            if name == "standard":
                ok = run_standard(
                    args.config or STANDARD_CONFIG,
                    summary_only=args.summary,
                    simulate=simulate,
                )
            else:
                ok = run_four_clover(
                    args.config or CLOVER_CONFIG,
                    summary_only=args.summary,
                    simulate=simulate,
                )
            results[name] = "PASS" if ok else "FAIL"
        except WorkflowFailure as exc:
            print(f"\n{exc}", file=sys.stderr)
            results[name] = "FAIL"

    _banner("RESULT")
    for name in selected:
        print(f"  {name:<12} {results[name]}")
    if not args.summary and simulate:
        print("\nSimulation only. Nothing was sent to a robot.")
        print("Upload the *_latest.py file above through the Opentrons App when you "
              "are ready to run physically.")
    return 0 if all(value == "PASS" for value in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
