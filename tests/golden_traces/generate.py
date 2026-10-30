"""Regenerate golden motion traces. Any change to `motion` or `analysis` is a motion change: review the diff before
committing. `generated_from.commit` also changes on regeneration; it is informational and not compared.

    python -B -m tests.golden_traces.generate --update
    python -B -m tests.golden_traces.generate --update --analysis
    python -B -m tests.golden_traces.generate --update --case replicates

--analysis also runs each case through pinned Opentrons analysis, which takes a while. Touches only tests/golden_traces/traces.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any

from src.agents.dye_demo.model import REPO
from tests.golden_traces.cases import CASES, SOFTWARE_ONLY, compiled_config
from tests.golden_traces.recorder import analysis_trace, motion_trace

TRACES = Path(__file__).with_name("traces")     # not "data": .gitignore excludes every folder of that name
SCHEMA = "nanodrop-golden-trace/v1"
PHYSICAL_STATUS = ("Software reference of the code at generated_from. Freezes current behavior; not evidence that this "
                   "motion is physically validated.")


def trace_path(name: str) -> Path:
    return TRACES / f"{name}.json"


def load_golden(name: str) -> dict[str, Any]:
    return json.loads(trace_path(name).read_text(encoding="utf-8"))


def _lines(items: list[Any]) -> str:
    return "[\n" + ",\n".join("    " + json.dumps(item, sort_keys=True) for item in items) + "\n  ]" if items else "[]"


def dump(golden: dict[str, Any]) -> str:
    """One command per line, so a motion change reads as a small diff."""
    parts = [f'  "schema": {json.dumps(golden["schema"])}', f'  "case": {json.dumps(golden["case"])}',
             f'  "physical_status": {json.dumps(golden["physical_status"])}',
             f'  "software_only_plan": {json.dumps(golden["software_only_plan"])}',
             f'  "generated_from": {json.dumps(golden["generated_from"], sort_keys=True)}',
             f'  "config": {json.dumps(golden["config"], sort_keys=True)}', f'  "motion": {_lines(golden["motion"])}']
    if golden.get("analysis") is not None:
        parts.append(f'  "analysis_errors": {json.dumps(golden["analysis_errors"], sort_keys=True)}')
        parts.append(f'  "analysis": {_lines(golden["analysis"])}')
    return "{\n" + ",\n".join(parts) + "\n}\n"


def _git(*words: str) -> str:
    return subprocess.run(["git", *words], cwd=REPO, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()


def build_golden(name: str, *, analysis: bool, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    config = compiled_config(name)
    golden = {"schema": SCHEMA, "case": name, "physical_status": PHYSICAL_STATUS,
              "software_only_plan": name in SOFTWARE_ONLY,
              "generated_from": {"commit": _git("rev-parse", "--short", "HEAD"),
                                 "protocol": "src/protocols/printing/13_ai_agent_dilution_print_demo.py"},
              "config": config, "motion": motion_trace(config)}
    if analysis:
        result = analysis_trace(config)
        golden["analysis_errors"], golden["analysis"] = result["errors"], result["commands"]
    elif previous and previous.get("analysis") is not None:
        golden["analysis_errors"], golden["analysis"] = previous["analysis_errors"], previous["analysis"]
    return golden


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="Required: rewrite stored traces.")
    parser.add_argument("--analysis", action="store_true", help="Also record pinned Opentrons analysis.")
    parser.add_argument("--case", action="append", choices=sorted(CASES))
    args = parser.parse_args()
    if not args.update:
        parser.error("refusing to rewrite golden traces without --update")
    TRACES.mkdir(exist_ok=True)
    for name in args.case or sorted(CASES):
        previous = load_golden(name) if trace_path(name).is_file() else None
        golden = build_golden(name, analysis=args.analysis, previous=previous)
        trace_path(name).write_text(dump(golden), encoding="utf-8", newline="\n")
        print(f"{name}: {len(golden['motion'])} commands"
              + (f", {len(golden['analysis'])} analysis commands, {len(golden['analysis_errors'])} errors"
                 if golden.get("analysis") is not None else ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
