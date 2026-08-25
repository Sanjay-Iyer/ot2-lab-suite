#!/usr/bin/env python3
"""
Print ONE 5 uL water drop per paper sheet at a position you choose on the
command line -- the paper-alignment check.

This exists because the paper gets moved. It puts a single drop at the same
named position on BOTH paper sheets (slots 5 and 11) so you can see, with two
drops and about a minute of robot time, whether the sheets are seated where the
labware definition thinks they are.

    python scripts/run_alignment_print.py --row 4 --column 2 --live

Row and column are flags so you can walk the sheet without editing any file:

    --row      A-H, or 1-8   (default D / 4)
    --column   1-12          (default 2)

`--row 4 --column 2` and `--row D --column 2` both mean well D2.

Water comes from the 20 mL vial rack in slot 7, well A1. Exactly 2 drops are
printed per run -- one on slot 5, one on slot 11 -- for 10 uL total.

Nothing here is a new execution path. The chosen position is written into
configs/generated/alignment_print.yaml, loaded through the same
print-from-vial loader every other printing experiment uses, built by the same
builder, and handed to the same HTTP upload / create-run / play / monitor cycle
in scripts/run_vial_print_robot.py. The robot is the executor.

    python scripts/run_alignment_print.py --row D --column 2 --live
    python scripts/run_alignment_print.py --row A --column 1 --dry-run
    python scripts/run_alignment_print.py --row 4 --column 2 --no-start
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

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
    GENERATED_PATH as ALIGNMENT_UPLOAD,
    build_print_from_vial_protocol,
)
from src.printing.print_from_vial.loader import load_print_from_vial_config

REPO = Path(__file__).resolve().parent.parent

#: Where the resolved position is written before it is loaded. Regenerated on
#: every run, so it always shows the position of the run you just did.
GENERATED_CONFIG = REPO / "configs" / "generated" / "alignment_print.yaml"

ROWS = "ABCDEFGH"
COLUMNS = range(1, 13)

#: Water lives in the 20 mL vial rack, slot 7, well A1.
SOURCE_SLOT = 7
SOURCE_WELL = "A1"

#: Both paper sheets. One drop each -- 2 drops per run, by design.
PAPER_SLOTS = [5, 11]

DROPLET_VOLUME_UL = 5.0

#: A dedicated alignment tip, kept clear of the tips the experiment runs claim
#: (A1 nanoparticles, B1 dye, C1 B10 stock, D1 nanoparticles, E1 CV dye). It is
#: returned to the rack, and it only ever touches water.
DEFAULT_TIP = "H12"

#: 4.0 mm above the vial floor is the registered, validated aspiration height
#: for this rack. The vial is 28 mm across, so it needs roughly 2463 uL of water
#: present for the tip to be submerged at that height -- the pre-flight check
#: enforces exactly that and will refuse the run otherwise. Lower it with
#: --aspirate-height if the vial is running low; do not go below 0.5 mm.
DEFAULT_ASPIRATE_HEIGHT_MM = 4.0

#: How much water the script declares is in the vial. The robot cannot see the
#: real level, so this is what the pre-flight volume check is measured against:
#: set --vial-volume to the truth if the vial is not comfortably full.
DEFAULT_VIAL_VOLUME_UL = 5000.0
MINIMUM_REMAINING_UL = 100.0


def _resolve_row(value: str) -> str:
    """Accept a row as a letter (A-H) or a number (1-8); return the letter."""
    text = str(value).strip().upper()
    if text in ROWS:
        return text
    if text.isdigit() and 1 <= int(text) <= 8:
        return ROWS[int(text) - 1]
    raise argparse.ArgumentTypeError(f"row must be A-H or 1-8, got {value!r}")


def _resolve_column(value: str) -> int:
    """Accept a column as 1-12."""
    text = str(value).strip()
    if text.isdigit() and int(text) in COLUMNS:
        return int(text)
    raise argparse.ArgumentTypeError(f"column must be 1-12, got {value!r}")


def _build_config(
    well: str,
    *,
    dry_run: bool,
    tip: str,
    aspirate_height_mm: float,
    vial_volume_ul: float = DEFAULT_VIAL_VOLUME_UL,
) -> dict:
    """One print-from-vial config with a single target on both paper sheets."""
    return {
        "machine_profile": "configs/machines/ot2_standard_printing_p20_v1.yaml",
        "protocol_label": f"alignment_{well.lower()}",
        "run_modes": {"dry_run": dry_run},
        "source": {
            "type": "vial_rack",
            "slot": SOURCE_SLOT,
            "wells": [SOURCE_WELL],
            "material": "water (alignment)",
            "loaded_volume_ul": vial_volume_ul,
            "minimum_remaining_ul": MINIMUM_REMAINING_UL,
            "aspirate_height_mm": aspirate_height_mm,
        },
        "substrate": {"slots": PAPER_SLOTS},
        "printing": {
            "droplet_volume_ul": DROPLET_VOLUME_UL,
            "inter_drop_delay_s": 0.0,
            "inter_layer_delay_s": 0.0,
        },
        "print_groups": [
            {"source_well": SOURCE_WELL, "targets": [well], "droplets": 1}
        ],
        "tips": {
            "pipette_tip_reuse": True,
            "print_tip": tip,
            "return_tips": True,
        },
    }


def _write_config(config: dict, well: str) -> Path:
    GENERATED_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# GENERATED by scripts/run_alignment_print.py -- do not hand-edit.\n"
        "# Rewritten on every alignment run; this is the position last used.\n"
        f"# Target {well} on paper slots {PAPER_SLOTS[0]} and {PAPER_SLOTS[1]},\n"
        f"# {DROPLET_VOLUME_UL:g} uL water from slot {SOURCE_SLOT} {SOURCE_WELL}.\n"
    )
    GENERATED_CONFIG.write_text(
        header + yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    return GENERATED_CONFIG


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--row", type=_resolve_row, default="D",
        help="Paper row: A-H or 1-8 (default: D, i.e. row 4).",
    )
    parser.add_argument(
        "--column", type=_resolve_column, default=2,
        help="Paper column: 1-12 (default: 2).",
    )
    parser.add_argument(
        "--tip", default=DEFAULT_TIP,
        help=f"Tiprack well to use (default: {DEFAULT_TIP}). Returned to the rack.",
    )
    parser.add_argument(
        "--aspirate-height", type=float, default=DEFAULT_ASPIRATE_HEIGHT_MM,
        help=f"mm above the vial floor to aspirate (default: "
             f"{DEFAULT_ASPIRATE_HEIGHT_MM:g}). Lower it if the vial is low.",
    )
    parser.add_argument(
        "--vial-volume", type=float, default=DEFAULT_VIAL_VOLUME_UL,
        help=f"uL of water declared present in the vial (default: "
             f"{DEFAULT_VIAL_VOLUME_UL:g}). The pre-flight check needs roughly "
             f"2463 uL to cover a 4 mm aspiration height.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build and play a no-motion rehearsal instead of printing.",
    )
    add_robot_host_arguments(parser)
    parser.add_argument(
        "--live", action="store_true",
        help="Explicit local confirmation that this will print water for real.",
    )
    parser.add_argument(
        "--no-start", action="store_true",
        help="Upload and create the run, but do not press play.",
    )
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)

    well = f"{args.row}{args.column}"

    run_log = RobotRunLog(Path(__file__).name)
    print(f"Run log   : {run_log.path}")

    try:
        config_dict = _build_config(
            well,
            dry_run=args.dry_run,
            tip=args.tip,
            aspirate_height_mm=args.aspirate_height,
            vial_volume_ul=args.vial_volume,
        )
        config_path = _write_config(config_dict, well)
        print(f"Config    : {repo_relative(config_path)}")

        cfg, run_modes = load_print_from_vial_config(config_path)
        dry_run = bool(run_modes.get("dry_run", False))
        print(
            f"run_modes.dry_run = {dry_run} "
            + ("(PLAN ONLY -- the arm will NOT move or print)"
               if dry_run else "(LIVE -- this WILL print water for real)")
        )

        print(f"\nAlignment target : {well} (row {args.row}, column {args.column})")
        print(f"Paper slots      : {PAPER_SLOTS[0]} and {PAPER_SLOTS[1]}")
        print(f"Water source     : slot {SOURCE_SLOT} {SOURCE_WELL} "
              f"at {args.aspirate_height:g} mm")
        print(f"Tip              : {args.tip} (returned)")
        print(f"Drops            : 2 x {DROPLET_VOLUME_UL:g} uL "
              f"= {2 * DROPLET_VOLUME_UL:g} uL total")

        built = build_print_from_vial_protocol(cfg, run_modes=run_modes)
        ALIGNMENT_UPLOAD.parent.mkdir(parents=True, exist_ok=True)
        ALIGNMENT_UPLOAD.write_bytes(built.protocol_path.read_bytes())
        protocol_path = ALIGNMENT_UPLOAD

        if not dry_run and not args.no_start and not args.live:
            print(
                "\nREFUSED: this prints water onto both sheets. Pass --live to "
                "confirm, --dry-run for a no-motion rehearsal, or --no-start to "
                "upload without pressing play.",
                file=sys.stderr,
            )
            run_log.finish("refused_no_live_confirmation", exit_code=1)
            return 1

        robot_host = resolve_host(args.robot_host)
        print(connection_summary(robot_host))
        print(f"\nProtocol  : {protocol_path}")
        run_log.update(
            workflow="alignment-print",
            config=repo_relative(config_path),
            robot_host=robot_host,
            protocol_path=repo_relative(protocol_path),
            target_well=well,
            live=args.live,
            no_start=args.no_start,
        )

        protocol_id = _upload_protocol(robot_host, protocol_path)
        run_log.event("protocol_uploaded", protocol_id=protocol_id)

        # API 2.15 with no add_parameters() block: run modes are baked in at
        # build time, so no runtime parameters are sent.
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
