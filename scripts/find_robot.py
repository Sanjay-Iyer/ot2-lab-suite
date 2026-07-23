#!/usr/bin/env python3
"""Discover, verify, cache, or diagnose the configured OT-2 address."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lab.robot_connection import (
    discover_robot,
    format_diagnostics,
    run_diagnostics,
    write_discovery,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find the configured OT-2 without sweeping the link-local network."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run all connection diagnostics and do not update configs/robot.yaml.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the verified host/address on success.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="Per-candidate connect timeout in seconds (default: 2).",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    if args.check:
        steps = run_diagnostics(timeout=args.timeout)
        print(format_diagnostics(steps))
        return 0 if all(step.passed for step in steps) else 1

    try:
        result = discover_robot(timeout=args.timeout)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    write_discovery(result["ip"], result["method"], result["health"])
    if args.quiet:
        print(result["host"])
    else:
        print(
            f"Verified {result['host']} ({result['ip']}) via {result['method']}; "
            "updated configs/robot.yaml."
        )
        for attempt in result["attempts"]:
            print(f"  {attempt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
