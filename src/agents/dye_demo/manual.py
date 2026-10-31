"""Manual YAML -> NanoDrop workflow preview, build, or local simulation."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
from uuid import uuid4

import yaml

from src.agents.dye_demo.model import REPO
from src.agents.dye_demo.render import render_plan, render_report
from src.agents.dye_demo.tools import build_protocol, prepare_workflow


def main(kind: str, argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    default = "dilution/example.yaml" if kind == "dilution" else "paper_printing/example.yaml"
    parser.add_argument("--config", default=f"configs/experiments/{default}")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--build", action="store_true", help="Write protocol + resolved YAML; no robot contact")
    action.add_argument("--simulate", action="store_true", help="Build and run the local Opentrons simulator")
    parser.add_argument("--output", type=Path, help="New artifact directory (must not already exist)")
    parser.add_argument("--simulator-python", help="Existing Opentrons simulator interpreter")
    args = parser.parse_args(argv)
    if args.output and not (args.build or args.simulate):
        parser.error("--output requires --build or --simulate")
    if args.simulator_python and not args.simulate:
        parser.error("--simulator-python requires --simulate")
    try:
        compiled = prepare_workflow(args.config)
        print(render_plan(compiled.spec, title="MANUAL WORKFLOW"))
        print(render_report(compiled.report))
        if not compiled.ok or compiled.plan is None:
            return 1
        counts = Counter(op.kind for op in compiled.plan.operations)
        print(f"Operations: {dict(counts)}; tips: {len(compiled.plan.tips)}")
        if not (args.build or args.simulate):
            print("Preview only. No protocol written, no robot contact.")
            return 0
        directory = args.output or REPO / "runs" / "manual" / uuid4().hex
        if not directory.is_absolute():
            directory = REPO / directory
        protocol = build_protocol(compiled, directory)
        print(f"Protocol: {protocol}\nResolved YAML: {directory / 'config.yaml'}")
        if args.simulate:
            from scripts.manual.build_vial_dilution_print import simulate
            simulator = args.simulator_python
            if simulator:
                simulator_path = Path(simulator)
                simulator = str(simulator_path if simulator_path.is_absolute() else REPO / simulator_path)
            ok, log = simulate(protocol, simulator)
            (directory / "simulation.txt").write_text(log, encoding="utf-8")
            print("Local simulation: " + ("PASS" if ok else "FAIL (see simulation.txt)"))
            return 0 if ok else 1
        return 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        print(f"Workflow error: {exc}", file=sys.stderr)
        return 1
