#!/usr/bin/env python3
"""Score a user-test SOP session of the AI dye demo against the SOP's expected final state.

    python scripts/check_ai_dye_demo_sop.py --sop 3 --session runs/ai_dye_demo/20260911_101500
    python scripts/check_ai_dye_demo_sop.py --sop 4 --config first_run.yaml --config second_run.yaml

--session reads the executed configuration of every successful run in that session folder (session.json and
executed_config_runN.yaml) and checks the last run(s) the SOP requires, in order. --config checks YAML files instead,
for example configs/workflows/user/ai_dye_demo_<stamp>.yaml when the tester stopped before typing run.

The expected states are docs/ai_dye_demo/user_testing/expected/sop_0N_*.yaml; the checks are
src/agents/dye_demo/sop_check.py. Read-only: nothing is built, simulated or sent to a robot.
Exit code: 0 when every requirement is met, 1 otherwise, 2 for a usage error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.agents.dye_demo.model import load_config  # noqa: E402
from src.agents.dye_demo.sop_check import check_runs, folder_run_configs, load_expected  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sop", type=int, required=True, choices=range(1, 6), help="SOP number, 1-5.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--session", help="A runs/ai_dye_demo/<timestamp>/ session folder.")
    source.add_argument("--config", action="append", help="A configuration YAML (repeat for each run, in order).")
    parser.add_argument("--failures-only", action="store_true", help="Print only the requirements that were not met.")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    expected = load_expected(args.sop)
    if args.session:
        folder = Path(args.session)
        folder = folder if folder.is_absolute() else REPO / folder
        if not (folder / "session.json").is_file():
            print(f"not a session folder (no session.json): {folder}", file=sys.stderr)
            return 2
        configs = folder_run_configs(folder)
        where = str(folder)
    else:
        paths = [Path(item) if Path(item).is_absolute() else REPO / item for item in args.config]
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            print("configuration not found: " + ", ".join(missing), file=sys.stderr)
            return 2
        configs = [load_config(path) for path in paths]
        where = ", ".join(str(path) for path in paths)

    findings = check_runs(configs, expected)
    failed = [finding for finding in findings if not finding.ok]
    print(f"SOP {expected['sop']} - {expected['title']} (difficulty {expected['difficulty']}/5)")
    print(f"Checked: {where}")
    current = None
    for finding in findings:
        if args.failures_only and finding.ok:
            continue
        if finding.run != current:
            current = finding.run
            print(f"\n{current}")
        mark = "PASS" if finding.ok else "FAIL"
        found = "" if finding.ok or not finding.found else f"   (found {finding.found})"
        print(f"  {mark}  {finding.requirement}{found}")
    print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} - {len(findings) - len(failed)} of {len(findings)} "
          "requirements met")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
