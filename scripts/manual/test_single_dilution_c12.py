#!/usr/bin/env python3
"""One live, one-well dye dilution using the existing OT-2 HTTP runner helpers.

Run only on the real robot laptop after checking the physical deck:
    conda activate llm
    python scripts/manual/test_single_dilution_c12.py

This host script creates a self-contained API 2.15 protocol in a temporary file,
uploads it with the repo's custom labware definitions, and monitors the run.
It never imports Agent NanoDrop or uses its GUI or LLM. Settings are loaded from
configs/checks/single_dilution.yaml; --preview is an offline configuration check.
"""
from __future__ import annotations

import argparse
import json
import math
import pprint
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml


# Existing physical P20 and plate bounds. Experimental settings live in YAML.
P20_MAX_UL = 20.0
P20_MIN_UL = 1.0
MAX_WELL_FILL_UL = 340.0


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


DEFAULT_CONFIG = REPO / "configs" / "checks" / "single_dilution.yaml"


def _config(path: Path = DEFAULT_CONFIG) -> dict:
    path = Path(path)
    path = path if path.is_absolute() else REPO / path
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("single-dilution YAML must be a mapping")
    return config


def _check_settings(config: dict) -> tuple[float, float, float]:
    if not P20_MIN_UL <= config["p20_min_ul"] <= config["p20_max_ul"] <= P20_MAX_UL:
        raise ValueError("configured working range exceeds the existing P20 bounds")
    if not 0 < config["max_well_fill_ul"] <= MAX_WELL_FILL_UL:
        raise ValueError("configured fill limit exceeds the existing plate bound")
    if not math.isfinite(float(config["dilution_factor"])) or config["dilution_factor"] < 1:
        raise ValueError("dilution_factor must be finite and at least 1")
    dye = config["final_volume_ul"] / config["dilution_factor"]
    water = config["final_volume_ul"] - dye
    gap = config["air_gap_ul"]
    if not all(math.isfinite(float(value)) for value in (dye, water, gap, config["mix_volume_ul"])):
        raise ValueError("all volumes must be finite")
    if config["dilution_factor"] < 1 or dye < P20_MIN_UL or water < 0:
        raise ValueError("dilution factor must give at least 1 uL of dye and nonnegative water")
    if not 0 <= gap <= P20_MAX_UL - P20_MIN_UL:
        raise ValueError("air gap leaves less than the P20 minimum liquid volume")
    if not 0 < config["mix_volume_ul"] <= P20_MAX_UL or config["mix_repetitions"] < 1:
        raise ValueError("mix settings are outside the P20 range")
    if not 0 < config["final_volume_ul"] <= config["max_well_fill_ul"]:
        raise ValueError("final volume exceeds the existing dye-demo well-fill limit")
    if config["destination_well"] not in {f"{row}{column}" for row in "ABCDEFGH" for column in range(1, 13)}:
        raise ValueError("destination must be a 96-well plate position")
    plate_name = config["deck"]["plate"]["load_name"]
    plate_def = json.loads((REPO / "labware" / f"{plate_name}.json").read_text(encoding="utf-8"))
    well = plate_def["wells"][config["destination_well"]]
    depth = float(well["depth"])
    for height in (config["aspirate_height_mm"], config["dispense_height_mm"], config["mix_height_mm"]):
        if not 0 < height < depth:
            raise ValueError("plate test heights must be above 0 and below the well depth")
    area = math.pi * (float(well["diameter"]) / 2) ** 2
    minimum_for_mix = config["mix_volume_ul"] + max(config["aspirate_height_mm"], config["mix_height_mm"]) * area
    if config["final_volume_ul"] < minimum_for_mix:
        raise ValueError(f"mix would draw air: this geometry needs {minimum_for_mix:.2f} uL")
    return dye, water, minimum_for_mix


PROTOCOL_TEMPLATE = '''\
"""Generated one-well dye dilution; edit configs/checks/single_dilution.yaml on the work laptop."""
import math
from opentrons import protocol_api

metadata = {"protocolName": "Single Dye Dilution", "author": "OT-2 Lab Suite"}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}

# >>> CONFIG START >>>
CONFIG = __CONFIG__
# <<< CONFIG END <<<

def _chunks(volume, limit, minimum):
    chunks = []
    remaining = round(volume, 2)
    while remaining > 0.005:
        chunk = min(limit, remaining)
        chunks.append(round(chunk, 2))
        remaining = round(remaining - chunk, 2)
    if len(chunks) > 1 and chunks[-1] < minimum:
        pair = chunks[-2] + chunks[-1]
        chunks[-2:] = [round(pair / 2, 2), round(pair - round(pair / 2, 2), 2)]
    if any(not minimum <= chunk <= limit for chunk in chunks):
        raise RuntimeError("a transfer chunk is outside the P20 working range")
    return chunks

def run(protocol: protocol_api.ProtocolContext):
    c = CONFIG
    dye = c["final_volume_ul"] / c["dilution_factor"]
    water = c["final_volume_ul"] - dye
    gap = c["air_gap_ul"]
    liquid_limit = c["p20_max_ul"] - gap
    if not 0 < liquid_limit <= c["p20_max_ul"] or liquid_limit < c["p20_min_ul"]:
        raise RuntimeError("air gap exceeds P20 capacity")
    if not 0 < c["final_volume_ul"] <= c["max_well_fill_ul"]:
        raise RuntimeError("destination volume exceeds the well-fill limit")
    plate_spec, rack_spec = c["deck"]["plate"], c["deck"]["tuberack"]
    plate = protocol.load_labware(plate_spec["load_name"], plate_spec["slot"],
                                  namespace=plate_spec["namespace"], version=plate_spec["version"])
    rack = protocol.load_labware(rack_spec["load_name"], rack_spec["slot"],
                                 namespace=rack_spec["namespace"], version=rack_spec["version"])
    tips = protocol.load_labware(c["deck"]["tiprack"]["load_name"], c["deck"]["tiprack"]["slot"])
    p20 = protocol.load_instrument(c["pipette"]["name"], c["pipette"]["mount"], tip_racks=[tips])
    destination = plate[c["destination_well"]]
    area = math.pi * (float(destination.diameter) / 2) ** 2
    if c["final_volume_ul"] < c["mix_volume_ul"] + max(c["aspirate_height_mm"], c["mix_height_mm"]) * area:
        raise RuntimeError("mix would draw air at the configured plate height")
    for key in ("aspirate_height_mm", "dispense_height_mm", "mix_height_mm"):
        if not 0 < c[key] < destination.depth:
            raise RuntimeError(f"{key} is outside the destination well")
    p20.flow_rate.aspirate = c["aspirate_rate"]
    p20.flow_rate.dispense = c["dispense_rate"]
    protocol.comment(f"One well: {dye:g} uL dye + {water:g} uL water -> {c['destination_well']}.")
    p20.pick_up_tip(tips[c["start_tip"]])
    for source_name, amount, height in ((c["water_source"], water, c["water_vial_height_mm"]),
                                        (c["dye_source"], dye, c["dye_vial_height_mm"])):
        for chunk in _chunks(amount, liquid_limit, c["p20_min_ul"]):
            p20.aspirate(chunk, rack[source_name].bottom(height))
            if gap > 0:
                p20.air_gap(gap)
            position = destination.bottom(c["dispense_height_mm"])
            p20.dispense(chunk + gap, position)
            if c["blow_out"]:
                p20.blow_out(position)
            if c["touch_tip"]:
                p20.touch_tip(destination)
    for _ in range(c["mix_repetitions"]):
        p20.aspirate(c["mix_volume_ul"], destination.bottom(c["aspirate_height_mm"]))
        p20.dispense(c["mix_volume_ul"], destination.bottom(c["mix_height_mm"]))
    if c["blow_out"]:
        p20.blow_out(destination.bottom(c["mix_height_mm"]))
    if c["touch_tip"]:
        p20.touch_tip(destination)
    if c["return_tip"]:
        p20.return_tip()
    else:
        p20.drop_tip()
'''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--preview", action="store_true", help="Check YAML and show volumes without contacting the robot")
    args = parser.parse_args(argv)
    try:
        config = _config(Path(args.config))
        dye, water, mix_minimum = _check_settings(config)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    print(f"Single dilution: {dye:g} uL dye + {water:g} uL water -> {config['destination_well']}")
    print(f"Vials: water {config['water_source']}, dye {config['dye_source']}; first tip: {config['start_tip']}")
    print(f"Mix geometry requires at least {mix_minimum:.2f} uL in the destination well.")
    if args.preview:
        print("Preview only; no robot connection.")
        return 0
    if input("Confirm robot is physically ready for LIVE liquid handling (type LIVE): ").strip() != "LIVE":
        print("Cancelled before robot connection.")
        return 1

    from src.lab.robot_connection import connection_summary, resolve_host, verify_host
    from scripts import run_vial_print_robot as robot_runner

    with TemporaryDirectory(prefix="ot2_single_dilution_") as temp_dir:
        protocol_file = Path(temp_dir) / "single_dilution_c12_protocol.py"
        protocol_file.write_text(PROTOCOL_TEMPLATE.replace("__CONFIG__", pprint.pformat(config, sort_dicts=False)),
                                 encoding="utf-8")
        host = resolve_host()
        verify_host(host)
        print(connection_summary(host))
        protocol_id = robot_runner._upload_protocol(host, protocol_file)
        run_id = robot_runner._create_run(host, protocol_id, dry_run=False, do_dilution=True,
                                          do_print=False, send_runtime_parameters=False)
        started = False
        try:
            started = True  # an interrupt during play must stop the robot run too
            robot_runner._play_run(host, run_id)
            status = robot_runner._monitor(host, run_id, 5.0)
        except KeyboardInterrupt:
            if started:
                robot_runner._stop_run(host, run_id)
            raise
    print(f"OT-2 run status: {status}")
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
