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
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

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

RULE = "=" * 78


class WorkflowFailure(Exception):
    """A configuration or simulation problem the operator has to fix."""


# ── shared helpers ────────────────────────────────────────────────────────────────

def _banner(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
#
# Validation, coordinate resolution, and the review all come from
# src/printing/clover, which is the same code the AI-facing tools use. This
# script adds only the terminal presentation and the upload-ready artifact.

def run_four_clover(
    config_reference: str, *, summary_only: bool, simulate: bool
) -> bool:
    from src.printing.clover import builder as clover_builder
    from src.printing.clover.loader import (
        CloverJobLoadError,
        load_manual_executor_config,
    )
    from src.printing.clover.resolver import (
        CloverResolutionError,
        resolve_manual_config,
    )
    from src.printing.clover.review import render_clover_review

    _banner("WORKFLOW 2 - FOUR-CLOVER PRINTING")
    print(f"config   : {config_reference}")
    print(f"executor : {CLOVER_EXECUTOR.relative_to(REPO)}")

    try:
        config, run_modes = load_manual_executor_config(config_reference)
    except (CloverJobLoadError, ValueError) as exc:
        raise WorkflowFailure(
            f"CONFIG VALIDATION FAILED ({config_reference}):\n{exc}"
        ) from exc

    experiment_id = str(config.get("protocol_label") or "manual_clover_config")
    experiment_id = re.sub(r"[^a-z0-9_]+", "_", experiment_id.lower()).strip("_")
    try:
        plan = resolve_manual_config(config, experiment_id=experiment_id or "manual")
    except CloverResolutionError as exc:
        raise WorkflowFailure(str(exc)) from exc

    print()
    print(render_clover_review(plan))

    dry_run = bool(run_modes.get("dry_run", True))
    print()
    print(
        f"  run_modes.dry_run  : {dry_run} "
        + (
            "(PLAN ONLY - the arm will not move on the robot)"
            if dry_run
            else "(the robot WILL print when this file is uploaded)"
        )
    )

    if summary_only:
        print("\n--summary: stopping before build and simulation.")
        return True

    generated = clover_builder.render_protocol_source(
        plan.executor_config, run_modes=run_modes
    )
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
    passed, output = clover_builder.simulate_clover_protocol(CLOVER_UPLOAD)
    print(f"simulation   : {'PASS' if passed else 'FAIL'}")
    for line in output.splitlines():
        if any(
            token in line
            for token in (
                "Pre-flight", "Clovers:", "Print complete", "WARNING:",
                "Minimum intra", "Minimum inter",
            )
        ):
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
