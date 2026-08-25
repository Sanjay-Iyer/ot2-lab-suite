#!/usr/bin/env python3
"""
Run the column-1 build-up and its CV dye finish as ONE command.

    python scripts/run_column1_layers_then_dye.py --live

Two protocols, played back to back on the robot, with nothing for you to do in
between:

  1. configs/experiments/16_nanoparticle_column1_layer2_resume.yaml
     Nanoparticles from BRAND A10 onto E1 (+3 drops) and F1 (+9 drops), both
     paper sheets. Layers 2-10, 1.5 hours of drying between layers.
     24 deposits, 120 uL. Roughly 12 hours.

  2. configs/experiments/17_cv_dye_c11_layered_spots.yaml
     A two-hour no-motion hold -- which begins the moment the last nanoparticle
     drop lands -- then CV dye from BRAND C11 onto E1 and F1, both sheets.
     4 deposits, 20 uL.

The dye cannot live inside protocol 1. The standard executor is layer-major and
every print group starts at layer 1, so a dye group would deposit alongside the
FIRST nanoparticle layer instead of after the last. Keeping the dye as its own
protocol, with its two-hour wait built in as printing.initial_delay_s, is what
produces the intended order. This script simply plays them in sequence.

This is not a new execution path: each protocol is built by the same builder and
uploaded through the same HTTP upload / create-run / play / monitor cycle in
scripts/run_vial_print_robot.py that every other printing run uses.

--------------------------------------------------------------------------
THIS SCRIPT MUST STAY RUNNING FOR THE WHOLE SEQUENCE -- about 14 hours. It
waits for protocol 1 to finish before it uploads protocol 2. If the laptop
sleeps, the terminal closes, or the network drops, the robot will still finish
protocol 1 on its own, but protocol 2 WILL NOT BE STARTED. Recover by running
the dye step by hand:

    python scripts/run_printing_experiment_robot.py cv-dye-layered-spots --live

Disable sleep on the laptop before starting, or run the two steps separately if
you would rather not depend on an unattended connection.

If protocol 1 does not succeed, protocol 2 is not uploaded at all.
--------------------------------------------------------------------------

    python scripts/run_column1_layers_then_dye.py --live
    python scripts/run_column1_layers_then_dye.py --no-start   # rehearse step 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lab.robot_connection import (
    add_robot_host_arguments,
    connection_summary,
    resolve_host,
)
from src.utils.robot_run_log import RobotRunLog, repo_relative

from scripts.run_vial_print_robot import (
    _create_run,
    _monitor,
    _play_run,
    _report_run_error,
    _upload_protocol,
)
from src.printing.print_from_vial.builder import (
    GENERATED_PATH as UPLOAD_PATH,
    build_print_from_vial_protocol,
)
from src.printing.print_from_vial.loader import load_print_from_vial_config

REPO = Path(__file__).resolve().parent.parent

#: (label, config) in the order they are played. Step 2 carries its own
#: two-hour hold, so there is no waiting to do here.
STEPS = [
    (
        "nanoparticle layers 2-10",
        "configs/experiments/16_nanoparticle_column1_layer2_resume.yaml",
    ),
    (
        "CV dye after a 2 hour hold",
        "configs/experiments/17_cv_dye_c11_layered_spots.yaml",
    ),
]


def _build(config: str) -> tuple[Path, dict, dict]:
    """Build one protocol and stage it at the shared upload path."""
    cfg, run_modes = load_print_from_vial_config(config)
    built = build_print_from_vial_protocol(cfg, run_modes=run_modes)
    UPLOAD_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_PATH.write_bytes(built.protocol_path.read_bytes())
    return UPLOAD_PATH, cfg, run_modes


def _summarize(label: str, config: str, cfg: dict, run_modes: dict) -> None:
    groups = cfg["print_groups"]
    slots = [spec["slot"] for role, spec in cfg["deck"].items() if "paper" in role]
    per_sheet = sum(len(g["targets"]) * g["droplets"] for g in groups)
    deposits = per_sheet * len(slots)
    volume = deposits * float(cfg["printing"]["droplet_volume_ul"])
    dry_run = bool(run_modes.get("dry_run", False))
    print(f"\n  {label}")
    print(f"    config    : {config}")
    print(f"    source    : slot {cfg['deck']['source']['slot']} "
          f"{', '.join(cfg['source']['wells'])} ({cfg['source']['material']})")
    print(f"    papers    : slots {', '.join(str(s) for s in slots)}")
    for group in groups:
        print(f"    targets   : {', '.join(group['targets'])} "
              f"x {group['droplets']} drop(s)")
    print(f"    deposits  : {deposits} = {volume:g} uL")
    print(f"    tip       : {cfg['tips']['print_tip']}")
    print(f"    dry_run   : {dry_run}")


def _play_step(
    robot_host: str,
    protocol_path: Path,
    run_log: RobotRunLog,
    label: str,
    poll_seconds: float,
) -> str:
    """Upload, create, play and monitor one protocol. Returns its final status."""
    protocol_id = _upload_protocol(robot_host, protocol_path)
    run_log.event("protocol_uploaded", step=label, protocol_id=protocol_id)

    # API 2.15 with no add_parameters() block: run modes are baked in at build
    # time, so no runtime parameters are sent.
    run_id = _create_run(
        robot_host,
        protocol_id,
        dry_run=False,
        do_dilution=False,
        do_print=True,
        send_runtime_parameters=False,
    )
    run_log.event("run_created", step=label, run_id=run_id)

    _play_run(robot_host, run_id)
    run_log.event("run_started", step=label, run_id=run_id)
    print(f"\n  Playing: {label} (run {run_id})")
    print("  Waiting for it to finish -- leave this running.")

    status = _monitor(robot_host, run_id, poll_seconds)
    print(f"  {label} finished with status: {status}")
    if status != "succeeded":
        _report_run_error(robot_host, run_id, run_log)
    run_log.event("run_finished", step=label, run_id=run_id, status=status)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_robot_host_arguments(parser)
    parser.add_argument(
        "--live", action="store_true",
        help="Required to play. Without it the script builds both protocols, "
             "prints the plan, and stops before contacting the robot.",
    )
    parser.add_argument(
        "--no-start", action="store_true",
        help="Upload and create the FIRST run only, without pressing play. The "
             "dye step is not touched.",
    )
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)

    run_log = RobotRunLog(Path(__file__).name)
    print(f"Run log   : {run_log.path}")

    try:
        # Build and describe BOTH protocols before anything is played, so a
        # config error surfaces now rather than 12 hours from now.
        print("\nPlan:")
        plans = []
        for label, config in STEPS:
            _, cfg, run_modes = _build(config)
            _summarize(label, config, cfg, run_modes)
            plans.append((label, config))

        if not args.live and not args.no_start:
            print(
                "\nREFUSED: this sequence prints for real and takes about 14 "
                "hours. Pass --live to confirm, or --no-start to upload the "
                "first protocol without pressing play.",
                file=sys.stderr,
            )
            run_log.finish("refused_no_live_confirmation", exit_code=1)
            return 1

        robot_host = resolve_host(args.robot_host)
        print(f"\n{connection_summary(robot_host)}")
        run_log.update(
            workflow="column1-layers-then-dye",
            robot_host=robot_host,
            steps=[config for _, config in STEPS],
            live=args.live,
            no_start=args.no_start,
        )

        for index, (label, config) in enumerate(plans, start=1):
            print(f"\n=== STEP {index} of {len(plans)}: {label} ===")
            # Rebuild immediately before upload so the staged file is this
            # step's protocol, not the previous step's.
            protocol_path, _, _ = _build(config)
            print(f"  protocol  : {repo_relative(protocol_path)}")

            if args.no_start:
                protocol_id = _upload_protocol(robot_host, protocol_path)
                run_id = _create_run(
                    robot_host, protocol_id, dry_run=False, do_dilution=False,
                    do_print=True, send_runtime_parameters=False,
                )
                print(f"\nCreated run but did not start it. Run ID: {run_id}")
                print("Stopping here; the dye step was not uploaded.")
                run_log.finish("created_not_started", exit_code=0)
                return 0

            status = _play_step(
                robot_host, protocol_path, run_log, label, args.poll_seconds
            )
            if status != "succeeded":
                print(
                    f"\nStep {index} ({label}) did not succeed -- stopping. "
                    "The remaining step was NOT uploaded.",
                    file=sys.stderr,
                )
                run_log.finish(f"step_{index}_{status}", exit_code=1)
                return 1

        print("\nSequence complete: nanoparticle layers 2-10, then CV dye.")
        run_log.finish("succeeded", exit_code=0)
        return 0
    except Exception as exc:  # noqa: BLE001 - the operator needs the raw reason
        run_log.finish("error", exit_code=1, error=str(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
