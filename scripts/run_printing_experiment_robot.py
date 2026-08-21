#!/usr/bin/env python3
"""
Run an Experiment-01 printing workflow (standard or four-clover) on the physical
OT-2, over the SAME validated execution path as the vial-dilution demo.

This is not a new robot connection or transport. It reuses, unchanged:
  - src.lab.robot_connection  (mDNS/ARP discovery, /health check, host resolution
    -- exactly what `python scripts/find_robot.py --check` exercises)
  - the upload / create-run / play / monitor HTTP-API cycle already implemented
    in scripts/run_vial_print_robot.py (`_upload_protocol`, `_create_run`,
    `_play_run`, `_monitor`, `_report_run_error`), including its custom-labware
    upload step (generalized in that file to also read the standard executor's
    PLAN/actions shape, not just the CONFIG/deck shape).

What this script adds is only the front half: BUILD ONLY. It regenerates the
requested Experiment-01 workflow from its YAML (via scripts/run_printing_workflow.py,
the existing no-agent builder) and hands the resulting upload artifact to that
existing HTTP execution path. It never invokes opentrons.simulate -- the robot is
the executor. For a local simulation, use the standalone builder instead:
    python scripts/run_printing_workflow.py four-clover
Nothing here talks to the robot except through the functions above.

    python scripts/run_printing_experiment_robot.py four-clover
    python scripts/run_printing_experiment_robot.py standard --live

Safety -- READ BEFORE YOU RUN THIS:
  - The four-clover executor has a real dry_run gate (configs/experiments/
    02_printing_four_clover.yaml -> run_modes.dry_run), but the config AS
    COMMITTED is `dry_run: false`: uploading and playing it prints for real. Set
    it to `true` in the YAML first for a safe no-motion rehearsal.
  - The standard executor has NO dry_run gate at all -- once uploaded and
    played, it always prints for real. --live is required here as an explicit
    local confirmation before this script will play a standard run; without
    it, the script builds and stops before touching the robot.
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

import scripts.run_printing_workflow as workflow
from scripts.run_vial_print_robot import (
    _create_run,
    _monitor,
    _play_run,
    _report_run_error,
    _upload_protocol,
)
from src.printing.print_from_vial.builder import (
    GENERATED_PATH as PRINT_FROM_VIAL_UPLOAD,
    build_print_from_vial_protocol,
)
from src.printing.print_from_vial.loader import load_print_from_vial_config
from src.printing.dye_demo.builder import (
    GENERATED_PATH as DYE_DEMO_UPLOAD,
    build_dye_demo_protocol,
)
from src.printing.dye_demo.loader import load_dye_demo_config

REPO = Path(__file__).resolve().parent.parent

#: workflow name -> (family, default config). All rebuild fresh from the YAML.
WORKFLOWS = {
    "print-from-vial": ("standard", "configs/experiments/01_print_from_vial.yaml"),
    "print-from-corning-plate": (
        "standard", "configs/experiments/01_print_from_corning_plate.yaml"),
    "print-from-brand-plate": (
        "standard", "configs/experiments/01_print_from_brand_plate.yaml"),
    "clover": ("clover", "configs/experiments/02_printing_four_clover.yaml"),
    "clover-from-corning-plate": (
        "clover", "configs/experiments/02_clover_from_corning_plate.yaml"),
    "clover-from-brand-plate": (
        "clover", "configs/experiments/02_clover_from_brand_plate.yaml"),
    "dye-demo": ("dye-demo", "configs/experiments/03_dye_dilution_print_demo.yaml"),
    "single-spot": ("standard", "configs/experiments/04_single_spot_stack.yaml"),
    "standard": ("legacy-standard", None),
    "four-clover": ("clover", None),
}
PRINT_FROM_VIAL_CONFIG = WORKFLOWS["print-from-vial"][1]


def _build_standard(config: str | None) -> tuple[Path, bool]:
    ok = workflow.run_standard(
        config or workflow.STANDARD_CONFIG, summary_only=False, simulate=False
    )
    return workflow.STANDARD_UPLOAD, ok


def _build_four_clover(config: str | None) -> tuple[Path, bool]:
    ok = workflow.run_four_clover(
        config or workflow.CLOVER_CONFIG, summary_only=False, simulate=False
    )
    return workflow.CLOVER_UPLOAD, ok


def _build_print_from_vial(config: str | None) -> tuple[Path, bool]:
    cfg, run_modes = load_print_from_vial_config(config or PRINT_FROM_VIAL_CONFIG)
    dry_run = bool(run_modes.get("dry_run", True))
    print(
        f"run_modes.dry_run = {dry_run} "
        + ("(PLAN ONLY -- the arm will NOT move or print)"
           if dry_run else "(LIVE -- this WILL print for real)")
    )
    built = build_print_from_vial_protocol(cfg, run_modes=run_modes)
    PRINT_FROM_VIAL_UPLOAD.parent.mkdir(parents=True, exist_ok=True)
    PRINT_FROM_VIAL_UPLOAD.write_bytes(built.protocol_path.read_bytes())
    # Build only -- no local opentrons.simulate pass. The robot is the executor.
    return PRINT_FROM_VIAL_UPLOAD, True


def _build_dye_demo(config: str | None) -> tuple[Path, bool]:
    cfg, run_modes = load_dye_demo_config(
        config or WORKFLOWS["dye-demo"][1]
    )
    dry_run = bool(run_modes.get("dry_run", True))
    print(
        f"run_modes.dry_run = {dry_run} "
        + ("(PLAN ONLY -- the arm will NOT move)"
           if dry_run else "(LIVE -- this WILL move liquid and print)")
    )
    print(
        f"pipette_tip_reuse = {cfg['tips']['pipette_tip_reuse']}"
    )
    built = build_dye_demo_protocol(cfg, run_modes=run_modes)
    DYE_DEMO_UPLOAD.parent.mkdir(parents=True, exist_ok=True)
    DYE_DEMO_UPLOAD.write_bytes(built.protocol_path.read_bytes())
    return DYE_DEMO_UPLOAD, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("workflow", choices=tuple(WORKFLOWS))
    parser.add_argument(
        "--config", default=None,
        help="Override the default YAML for the selected workflow.",
    )
    add_robot_host_arguments(parser)
    parser.add_argument(
        "--live", action="store_true",
        help="Required to actually play a STANDARD run (it has no dry_run gate: "
             "uploading and playing it always prints for real). For four-clover, "
             "whether liquid moves is controlled by the config's run_modes.dry_run "
             "-- this flag is not required and does not override that setting.",
    )
    parser.add_argument(
        "--no-start", action="store_true",
        help="Upload and create the run, but do not press play.",
    )
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)

    run_log = RobotRunLog(Path(__file__).name)
    print(f"Run log   : {run_log.path}")

    try:
        family, default_config = WORKFLOWS[args.workflow]
        config = args.config or default_config
        if family == "legacy-standard":
            protocol_path, ok = _build_standard(config)
        elif family == "clover":
            protocol_path, ok = _build_four_clover(config)
        elif family == "dye-demo":
            protocol_path, ok = _build_dye_demo(config)
        else:
            protocol_path, ok = _build_print_from_vial(config)
        if not ok:
            print("\nBuild failed; refusing to contact the robot.",
                  file=sys.stderr)
            run_log.finish("build_failed", exit_code=1)
            return 1

        if family == "legacy-standard" and not args.no_start and not args.live:
            print(
                "\nREFUSED: the standard executor has no dry_run gate -- playing "
                "this run moves real liquid. Pass --live to confirm, or --no-start "
                "to upload/create the run without pressing play.",
                file=sys.stderr,
            )
            run_log.finish("refused_no_live_confirmation", exit_code=1)
            return 1

        robot_host = resolve_host(args.robot_host)
        print(connection_summary(robot_host))

        print(f"\nWorkflow  : {args.workflow}")
        print(f"Protocol  : {protocol_path}")
        run_log.update(
            workflow=args.workflow,
            config=args.config,
            robot_host=robot_host,
            protocol_path=repo_relative(protocol_path),
            live=args.live,
            no_start=args.no_start,
        )

        protocol_id = _upload_protocol(robot_host, protocol_path)
        run_log.event("protocol_uploaded", protocol_id=protocol_id)

        # Both Experiment-01 artifacts are API 2.15 with no add_parameters() block;
        # their run modes are baked into the file at build time, so no runtime
        # parameters are sent (same handling the vial-dilution runner uses for its
        # own API-2.15-only versions).
        run_id = _create_run(
            robot_host,
            protocol_id,
            dry_run=False,
            do_dilution=False,
            do_print=True,
            send_runtime_parameters=False,
        )
        run_log.event("run_created", protocol_id=protocol_id, run_id=run_id)

        if args.no_start:
            print(f"\nCreated run but did not start it. Run ID: {run_id}")
            run_log.finish("created_not_started", exit_code=0)
            return 0

        _play_run(robot_host, run_id)
        run_log.event("run_started", run_id=run_id)
        status = _monitor(robot_host, run_id, args.poll_seconds)
        print(f"\nRun finished with status: {status}")
        if status != "succeeded":
            _report_run_error(robot_host, run_id, run_log)
        exit_code = 0 if status == "succeeded" else 1
        run_log.finish(status, exit_code=exit_code)
        return exit_code
    except Exception as exc:  # noqa: BLE001 - the operator needs the raw reason
        run_log.finish("error", exit_code=1, error=str(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
