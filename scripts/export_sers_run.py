#!/usr/bin/env python3
"""Export one SERS experiment session as a Supporting Information package.

    python scripts/export_sers_run.py --list
    python scripts/export_sers_run.py --session <session-id>
    python scripts/export_sers_run.py --session <session-id> --zip
    python scripts/export_sers_run.py --session <session-id> --out C:/si/exp1
    python scripts/export_sers_run.py --session <session-id> --verify-only

The export is a copy with a generated README.md explaining every file. The
original session directory is never modified, and no credential of any kind is
recorded in a session, so none can be exported.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.sers_engine.provenance.export import (  # noqa: E402
    ExportError,
    export_session,
    find_session,
    list_sessions,
    verify,
)


def _print_sessions() -> int:
    rows = list_sessions()
    if not rows:
        print("No recorded sessions yet. Run scripts/sers_agent.py or")
        print("scripts/run_sers_experiment.py to create one.")
        return 1
    print(f"{'SESSION':10}  {'CREATED':26}  {'MODE':13}  {'REV':>3}  {'RUNS':>4}  EXPERIMENT")
    for row in rows:
        flag = "  [INCOMPLETE RECORD]" if row["degraded"] else ""
        print(
            f"{str(row['session_id'] or '?'):10}  {str(row['created_at'] or ''):26}  "
            f"{str(row['mode'] or ''):13}  {str(row['revisions'] or 0):>3}  "
            f"{str(row['robot_runs'] or 0):>4}  {row['experiment_name'] or '(no experiment)'}{flag}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="Session id, directory name, or path.")
    parser.add_argument("--list", action="store_true", help="List recorded sessions and exit.")
    parser.add_argument("--out", default=None, help="Destination directory for the package.")
    parser.add_argument("--zip", action="store_true", help="Also write a .zip of the package.")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Re-check every hash the manifest claims; export nothing.",
    )
    parser.add_argument("--json", action="store_true", help="Print the result as JSON.")
    args = parser.parse_args(argv)

    if args.list or not args.session:
        if not args.session and not args.list:
            print("Nothing to do: pass --session <id>, or --list to see what exists.\n")
        return _print_sessions()

    try:
        if args.verify_only:
            directory = find_session(args.session)
            problems = verify(directory)
            if args.json:
                print(json.dumps({"session_dir": str(directory), "problems": problems}, indent=2))
            elif problems:
                print(f"{directory}: {len(problems)} problem(s)")
                for problem in problems:
                    print(f"  {problem}")
            else:
                print(f"{directory}: every recorded hash matches.")
            return 1 if problems else 0

        result = export_session(
            args.session,
            destination=Path(args.out) if args.out else None,
            archive=args.zip,
        )
    except ExportError as exc:
        print(f"ERROR  {exc}")
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"Exported session {result['session_id']}")
    print(f"  from      {result['session_dir']}")
    print(f"  to        {result['export_dir']}")
    if result.get("archive"):
        print(f"  archive   {result['archive']}")
    print(f"  files     {result['file_count']}")
    for name in result["files"]:
        print(f"    {name}")
    if result["verification"]:
        print("  INTEGRITY PROBLEMS:")
        for problem in result["verification"]:
            print(f"    {problem}")
    else:
        print("  integrity  every recorded hash matches")
    if result["degraded"]:
        print("  WARNING    this session recorded provenance failures; see README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
