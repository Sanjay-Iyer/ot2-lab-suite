#!/usr/bin/env python3
"""One live, one-well dye dilution using the existing OT-2 HTTP runner helpers.

Run only on the real robot laptop after checking the physical deck:
    conda activate llm
    python scripts/test_single_dilution_c12.py

This host script creates a self-contained API 2.15 protocol in a temporary file,
uploads it with the repo's custom labware definitions, and monitors the run.
It never imports Agent NanoDrop or uses its GUI, LLM, or YAML workflow.
"""
from __future__ import annotations

import json
import math
import pprint
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


# =============================================
# QUICK TEST SETTINGS — EDIT THESE IF NEEDED
# =============================================
DESTINATION_WELL = "C12"       # 96-well plate in slot 4
FINAL_VOLUME_UL = 100.0
DILUTION_FACTOR = 2.0          # dye = final / factor; water = remainder

ASPIRATE_HEIGHT_MM = 0.3      # plate aspiration while mixing; vial heights stay at 4 mm
DISPENSE_HEIGHT_MM = 0.3      # plate destination, above well bottom
MIX_HEIGHT_MM = 0.3           # plate mix dispense position
AIR_GAP_UL = 1.0               # after every vial aspiration; counts toward P20 capacity
BLOW_OUT = True
TOUCH_TIP = True
MIX_VOLUME_UL = 15.0
MIX_REPETITIONS = 2
ASPIRATE_RATE = 3.0            # existing dye-demo P20 rate, uL/s
DISPENSE_RATE = 3.0
RETURN_TIP = True             # one A1 tip for water, dye, and mix; may carry liquid between sources

WATER_SOURCE_WELL = "A1"      # slot-7 vial rack; existing dye-demo source
DYE_SOURCE_WELL = "A2"        # slot-7 vial rack; existing dye-demo source
WATER_VIAL_HEIGHT_MM = 4.0   # keep vial geometry separate from plate test heights
DYE_VIAL_HEIGHT_MM = 4.0
START_TIP = "A1"              # existing dye-demo P20 tip rack in slot 9

P20_MAX_UL = 20.0
P20_MIN_UL = 1.0
MAX_WELL_FILL_UL = 340.0     # existing dye-demo safety limit


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _config() -> dict:
    return {
        "deck": {
            "plate": {"slot": 4, "load_name": "corning_96_wellplate_360ul_custom",
                      "namespace": "custom_beta", "version": 1},
            "tuberack": {"slot": 7, "load_name": "tuberack_3dprint_20ml_8vials_v2",
                         "namespace": "custom_beta", "version": 1},
            "tiprack": {"slot": 9, "load_name": "opentrons_96_tiprack_20ul"},
        },
        "pipette": {"name": "p20_single_gen2", "mount": "left"},
        "destination_well": DESTINATION_WELL,
        "final_volume_ul": FINAL_VOLUME_UL,
        "dilution_factor": DILUTION_FACTOR,
        "water_source": WATER_SOURCE_WELL,
        "dye_source": DYE_SOURCE_WELL,
        "water_vial_height_mm": WATER_VIAL_HEIGHT_MM,
        "dye_vial_height_mm": DYE_VIAL_HEIGHT_MM,
        "aspirate_height_mm": ASPIRATE_HEIGHT_MM,
        "dispense_height_mm": DISPENSE_HEIGHT_MM,
        "mix_height_mm": MIX_HEIGHT_MM,
        "air_gap_ul": AIR_GAP_UL,
        "blow_out": BLOW_OUT,
        "touch_tip": TOUCH_TIP,
        "mix_volume_ul": MIX_VOLUME_UL,
        "mix_repetitions": MIX_REPETITIONS,
        "aspirate_rate": ASPIRATE_RATE,
        "dispense_rate": DISPENSE_RATE,
        "return_tip": RETURN_TIP,
        "start_tip": START_TIP,
        "p20_max_ul": P20_MAX_UL,
        "p20_min_ul": P20_MIN_UL,
        "max_well_fill_ul": MAX_WELL_FILL_UL,
    }


def _check_settings(config: dict) -> tuple[float, float, float]:
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
    if not 0 < config["final_volume_ul"] <= MAX_WELL_FILL_UL:
        raise ValueError("final volume exceeds the existing dye-demo well-fill limit")
    if DESTINATION_WELL not in {f"{row}{column}" for row in "ABCDEFGH" for column in range(1, 13)}:
        raise ValueError("destination must be a 96-well plate position")
    plate_def = json.loads((REPO / "labware" / "corning_96_wellplate_360ul_custom.json").read_text(encoding="utf-8"))
    well = plate_def["wells"][DESTINATION_WELL]
    depth = float(well["depth"])
    for height in (ASPIRATE_HEIGHT_MM, DISPENSE_HEIGHT_MM, MIX_HEIGHT_MM):
        if not 0 < height < depth:
            raise ValueError("plate test heights must be above 0 and below the well depth")
    area = math.pi * (float(well["diameter"]) / 2) ** 2
    minimum_for_mix = MIX_VOLUME_UL + max(ASPIRATE_HEIGHT_MM, MIX_HEIGHT_MM) * area
    if FINAL_VOLUME_UL < minimum_for_mix:
        raise ValueError(f"mix would draw air: this geometry needs {minimum_for_mix:.2f} uL")
    return dye, water, minimum_for_mix


PROTOCOL_TEMPLATE = '''\
"""Generated one-well dye dilution; edit scripts/test_single_dilution_c12.py on the work laptop."""
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


def main() -> int:
    config = _config()
    dye, water, mix_minimum = _check_settings(config)
    print(f"Single dilution: {dye:g} uL dye + {water:g} uL water -> slot 4 {DESTINATION_WELL}")
    print(f"Slot 7 vials: water {WATER_SOURCE_WELL}, dye {DYE_SOURCE_WELL}; slot 9 P20 tips: {START_TIP}")
    print(f"Mix geometry requires at least {mix_minimum:.2f} uL in the destination well.")
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
