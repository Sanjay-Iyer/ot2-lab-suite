#!/usr/bin/env python3
"""Read OT-2 configuration offline; --check runs existing WORK diagnostics."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/checks/ot2.yaml")
    parser.add_argument("--check", action="store_true", help="WORK only: network/SSH diagnostics; no liquid motion")
    args = parser.parse_args(argv)
    try:
        path = Path(args.config)
        path = path if path.is_absolute() else REPO / path
        profile = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(profile, dict):
            raise ValueError("check configuration must be a mapping")
        robot_path = profile["robot_config"]
        timeout = float(profile.get("timeout_s", 2.0))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_s must be positive and finite")
        from src.lab.robot_connection import load_robot_config
        config = load_robot_config(robot_path)
        print(f"Configured robot: {config['robot']['name']}; source: {robot_path}")
        if not args.check:
            print("Configuration loaded. Offline only; no discovery, connection or settings update.")
            return 0
        from src.lab.robot_connection import format_diagnostics, run_diagnostics
        steps = run_diagnostics(timeout=timeout, config_path=robot_path)
        print(format_diagnostics(steps))
        return 0 if all(step.passed for step in steps) else 1
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, yaml.YAMLError) as exc:
        print(f"Check error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
