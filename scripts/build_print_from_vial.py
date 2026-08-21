#!/usr/bin/env python3
"""Build + locally simulate the print-from-vial workflow. No robot contact.

    python scripts/build_print_from_vial.py
    python scripts/build_print_from_vial.py --config configs/experiments/01_print_from_vial.yaml
    python scripts/build_print_from_vial.py --summary

Reads configs/experiments/01_print_from_vial.yaml (or --config), flattens it
against its referenced machine profile, writes the upload-ready artifact to
src/protocols/generated/01_print_from_vial_latest.py, and simulates it locally
via opentrons.simulate. Nothing here contacts, discovers, or moves a robot.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_CONFIG = "configs/experiments/01_print_from_vial.yaml"
UPLOAD_PATH = REPO / "src" / "protocols" / "generated" / "01_print_from_vial_latest.py"

RULE = "=" * 78


class BuildFailure(Exception):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--summary", action="store_true", help="resolve and report only; skip build/simulation")
    parser.add_argument("--no-sim", action="store_true", help="build the upload artifact but skip simulation")
    args = parser.parse_args(argv)

    from src.printing.print_from_vial.builder import (
        build_print_from_vial_protocol,
        simulate_print_from_vial_protocol,
    )
    from src.printing.print_from_vial.loader import (
        PrintFromVialLoadError,
        load_print_from_vial_config,
    )

    print(f"\n{RULE}\nPRINT FROM VIAL\n{RULE}")
    print(f"config : {args.config}")

    try:
        config, run_modes = load_print_from_vial_config(args.config)
    except PrintFromVialLoadError as exc:
        print(f"\nCONFIG VALIDATION FAILED:\n{exc}", file=sys.stderr)
        return 1

    dry_run = bool(run_modes.get("dry_run", True))
    print(f"source     : well {config['source']['well']!r}, {config['source']['material']!r}")
    print(f"targets    : {', '.join(config['targets'])}")
    print(f"droplet_ul : {config['printing']['droplet_volume_ul']:g}")
    print(f"per target : {config['printing']['droplets_per_target']} droplet(s)")
    print(f"deposits   : {len(config['targets']) * config['printing']['droplets_per_target']}")
    print(
        f"dry_run    : {dry_run} "
        + ("(PLAN ONLY - the arm will not move)" if dry_run else "(the robot WILL print when uploaded)")
    )

    if args.summary:
        print("\n--summary: stopping before build and simulation.")
        return 0

    built = build_print_from_vial_protocol(config, run_modes=run_modes)
    UPLOAD_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_PATH.write_bytes(built.protocol_path.read_bytes())
    print(f"\nupload file : {UPLOAD_PATH.relative_to(REPO)}")
    print(f"sha256      : {_sha256(UPLOAD_PATH)}")

    if args.no_sim:
        print("--no-sim: skipping simulation.")
        return 0

    print("\nsimulating locally (no robot contact) ...")
    passed, output = simulate_print_from_vial_protocol(
        UPLOAD_PATH, expected_sha256=_sha256(UPLOAD_PATH)
    )
    print(f"simulation  : {'PASS' if passed else 'FAIL'}")
    for line in output.splitlines():
        if any(token in line for token in ("PLAN", "Pre-flight", "Print complete", "Targets:", "Source")):
            print(f"  {line.strip()}")
    if not passed:
        print("\n--- simulation output ---")
        print(output[-4000:])
        return 1
    print("\nSimulation only. Nothing was sent to a robot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
