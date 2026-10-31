"""
Vial dilution -> paper print, protocol v3.

This protocol is intentionally limited to the OT-2 Python Protocol API 2.15.
The P20 single-channel pipette performs every vial transfer and every paper
print. The P300 multi-channel pipette is used only with a complete column of
eight tips to mix the eight wells in dilution-plate column 11.

The CONFIG block is replaced by scripts/build_vial_dilution_print.py. Edit the
workflow YAML, not a generated protocol.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess

from opentrons import protocol_api


metadata = {
    "protocolName": "Vial Dilution to Paper Print v3 (OT-2 API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Prepare eight BP dilutions with a P20, mix the plate column with a "
        "full-column P300 multi-channel pickup, and print four P20 spot volumes."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN = True
DEFAULT_DO_DILUTION = True
DEFAULT_DO_PRINT = True


# >>> CONFIG START >>>
CONFIG = {
    "deck": {
        "tuberack": {
            "slot": 7,
            "load_name": "tuberack_3dprint_20ml_8vials_v2",
            "namespace": "custom_beta",
            "version": 1,
        },
        "plate": {
            "slot": 4,
            "load_name": "corning_96_wellplate_360ul_custom",
            "namespace": "custom_beta",
            "version": 1,
        },
        "paper": {
            "slot": 5,
            "load_name": "corning_96_wellplate_360ul_custom",
            "namespace": "custom_beta",
            "version": 1,
        },
        "tiprack": {"slot": 8, "load_name": "opentrons_96_tiprack_300ul"},
        "tiprack_p20": {"slot": 9, "load_name": "opentrons_96_tiprack_20ul"},
    },
    "pipettes": [
        {"name": "p300_multi_gen2", "mount": "right"},
        {"name": "p20_single_gen2", "mount": "left"},
    ],
    "sources": {
        "water_vial": "A1",
        "bp_dye_vial": "A2",
        "vial_aspirate_height_mm": 4.0,
    },
    "dilution": {
        "enabled": True,
        "destination_column": "11",
        "total_volume_ul": 150.0,
        "factors": {"mode": "explicit", "explicit": [1, 2, 5, 10, 15, 20, 25, 50]},
        "mix_reps": 5,
        "mix_volume_ul": 99.0,
        "transfer_pipette": "p20_single_gen2",
        "max_transfer_ul": 20.0,
        "dead_volume_ul": 30.0,
        "stock_tip_policy": "single",
        "water_dispense_from_top_mm": -2.0,
        "stock_dispense_from_top_mm": -1.0,
    },
    "color_series": [
        {
            "name": "bp",
            "dye_vial": "A2",
            "destination_column": "11",
        }
    ],
    "print_groups": [
        {
            "name": "col1_20ul_p20",
            "volume_ul": 20.0,
            "pipette": "p20_single_gen2",
            "layout": "single_spot",
            "source": {"plate_column": "11"},
            "replicates": 1,
            "droplets_per_spot": 1,
            "mix_before": False,
            "destination": {"paper_start_column": 1},
            "dispense": {"z_mm": 1.0, "blow_out": True, "post_dispense_delay_s": 0.3},
            "tips": {"strategy": "per_source_row", "map_ref": "print_by_row"},
        }
    ],
    "tips": {
        "return_tips": False,
        "p20": {
            "water": "A1",
            "stock": "A2",
            "print_by_row": {
                "A": "A3",
                "B": "A4",
                "C": "A5",
                "D": "A6",
                "E": "A7",
                "F": "A8",
                "G": "A9",
                "H": "A10",
            },
        },
        "p300": {"mix_block_column": 1},
    },
    "camera": {
        "enabled": False,
        "capture_before": False,
        "capture_after": False,
        "robot_image_dir": "/data/vision/vial_dilution_print",
        "robot_api_url": "http://localhost:31950/camera/picture",
        "capture_timeout_s": 5,
    },
    "flow_rates": {
        "p20": {"aspirate": 3.0, "dispense": 3.0},
        "p300": {"aspirate": 50.0, "dispense": 100.0},
    },
    "safety": {
        "expected_tuberack_load_name": "tuberack_3dprint_20ml_8vials_v2",
        "expected_well_count": 8,
        "expected_depth_mm": 55.0,
        "expected_tuberack_z_dimension_mm": 60.0,
        "p300_travel_clearance_mm": 5.0,
        "max_well_fill_ul": 340.0,
        "p20_max_volume_ul": 20.0,
    },
}
# <<< CONFIG END <<<


ROWS = tuple("ABCDEFGH")
EPSILON_UL = 0.01


def _load_labware(protocol, spec):
    kwargs = {}
    if spec.get("namespace"):
        kwargs["namespace"] = spec["namespace"]
    if spec.get("version") is not None:
        kwargs["version"] = int(spec["version"])
    return protocol.load_labware(spec["load_name"], str(spec["slot"]), **kwargs)


def _resolve_factors(dilution):
    factors = dilution["factors"]
    mode = factors.get("mode", "explicit")
    if mode == "explicit":
        return [float(value) for value in factors["explicit"]]
    count = int(factors.get("count", 8))
    start = float(factors.get("start", 1))
    if mode == "geometric":
        step = float(factors.get("step_factor", 2))
        return [round(start * (step ** index), 4) for index in range(count)]
    end = float(factors.get("end", 50))
    if count == 1:
        return [start]
    if mode == "linear":
        return [
            round(start + (end - start) * index / (count - 1), 4)
            for index in range(count)
        ]
    if mode == "log":
        low, high = math.log(start), math.log(end)
        return [
            round(math.exp(low + (high - low) * index / (count - 1)), 4)
            for index in range(count)
        ]
    raise RuntimeError("Unsupported dilution factor mode: {!r}".format(mode))


def _split_volume(total_ul, max_transfer_ul):
    """Split a volume into positive P20-sized transfers without rounding drift."""
    remaining = float(total_ul)
    chunks = []
    while remaining > EPSILON_UL:
        chunk = min(float(max_transfer_ul), remaining)
        chunks.append(round(chunk, 2))
        remaining = round(remaining - chunk, 6)
    return chunks


def _release_tip(pipette, return_tips):
    if not pipette.has_tip:
        return
    if return_tips:
        pipette.return_tip()
    else:
        pipette.drop_tip()


def _capture_image(protocol, filename):
    camera = CONFIG.get("camera", {})
    if not camera.get("enabled"):
        return
    if protocol.is_simulating():
        protocol.comment("Camera capture skipped during simulation: {}".format(filename))
        return
    output_dir = str(camera.get("robot_image_dir", "/data/vision/vial_dilution_print"))
    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as exc:
        protocol.comment("WARNING: could not create {}: {}".format(output_dir, exc))
        return
    if shutil.which("curl") is None:
        protocol.comment("WARNING: curl is unavailable; camera capture skipped.")
        return
    output_path = os.path.join(output_dir, filename)
    command = [
        "curl",
        "-s",
        "-X",
        "POST",
        "-H",
        "opentrons-version: *",
        "--max-time",
        str(camera.get("capture_timeout_s", 5)),
        str(camera.get("robot_api_url", "http://localhost:31950/camera/picture")),
        "--output",
        output_path,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except Exception as exc:
        protocol.comment("WARNING: camera capture {} failed: {}".format(filename, exc))
        return
    if result.returncode or not os.path.exists(output_path):
        protocol.comment(
            "WARNING: camera capture {} failed (curl rc={}): {}".format(
                filename, result.returncode, result.stderr.strip()
            )
        )
        return
    protocol.comment(
        "Captured image: {} ({} bytes).".format(
            output_path, os.path.getsize(output_path)
        )
    )


def _preflight(protocol, labware, p20, p300):
    errors = []
    deck = CONFIG["deck"]
    dilution = CONFIG["dilution"]
    safety = CONFIG["safety"]
    tips = CONFIG["tips"]

    required_slots = {
        "tuberack": 7,
        "plate": 4,
        "paper": 5,
        "tiprack": 8,
        "tiprack_p20": 9,
    }
    for role, expected_slot in required_slots.items():
        actual = int(deck[role]["slot"])
        if actual != expected_slot:
            errors.append(
                "deck.{} must be slot {}, got {}".format(role, expected_slot, actual)
            )

    if requirements != {"robotType": "OT-2", "apiLevel": "2.15"}:
        errors.append("protocol requirements must be OT-2 / API 2.15")

    if p20.name != "p20_single_gen2":
        errors.append("left pipette must be p20_single_gen2")
    if p300.name != "p300_multi_gen2":
        errors.append("right pipette must be p300_multi_gen2")

    tuberack = labware["tuberack"]
    expected_name = safety["expected_tuberack_load_name"]
    if tuberack.load_name != expected_name:
        errors.append(
            "tuberack load name is {!r}; expected {!r}".format(
                tuberack.load_name, expected_name
            )
        )
    if len(tuberack.wells()) != int(safety["expected_well_count"]):
        errors.append(
            "tuberack has {} wells; expected {}".format(
                len(tuberack.wells()), safety["expected_well_count"]
            )
        )
    for vial in (CONFIG["sources"]["water_vial"], CONFIG["color_series"][0]["dye_vial"]):
        if vial not in tuberack.wells_by_name():
            errors.append("source vial {} is absent from the rack".format(vial))

    expected_rack_height = float(safety["expected_tuberack_z_dimension_mm"])
    actual_rack_height = float(tuberack.highest_z)
    if abs(actual_rack_height - expected_rack_height) > 0.5:
        errors.append(
            "tuberack highest_z {:.2f} mm does not match declared {:.2f} mm".format(
                actual_rack_height, expected_rack_height
            )
        )
    clearance = float(safety["p300_travel_clearance_mm"])
    safe_travel_z = expected_rack_height + clearance
    if safe_travel_z <= actual_rack_height:
        errors.append(
            "P300 safe travel Z {:.2f} mm does not clear rack height {:.2f} mm".format(
                safe_travel_z, actual_rack_height
            )
        )

    factors = _resolve_factors(dilution)
    if len(factors) != len(ROWS):
        errors.append("v3 requires exactly eight dilution factors")
    total = float(dilution["total_volume_ul"])
    dead = float(dilution["dead_volume_ul"])
    print_consumption = sum(float(group["volume_ul"]) for group in CONFIG["print_groups"])
    if total > float(safety["max_well_fill_ul"]):
        errors.append(
            "total volume {:.2f} uL exceeds safe well fill {:.2f} uL".format(
                total, float(safety["max_well_fill_ul"])
            )
        )
    if total + EPSILON_UL < print_consumption + dead:
        errors.append(
            "total volume {:.2f} uL is below print consumption + dead volume "
            "({:.2f} uL)".format(total, print_consumption + dead)
        )

    max_transfer = float(dilution["max_transfer_ul"])
    if max_transfer > float(safety["p20_max_volume_ul"]):
        errors.append("P20 transfer limit exceeds 20 uL")
    for factor in factors:
        if factor <= 0:
            errors.append("dilution factors must be positive")
            continue
        stock = total / factor
        water = total - stock
        if abs((stock + water) - total) > 0.1:
            errors.append("stock + water does not equal total for {}x".format(factor))
        if any(chunk > 20.0 for chunk in _split_volume(stock, max_transfer)):
            errors.append("stock transfer chunk exceeds 20 uL")
        if any(chunk > 20.0 for chunk in _split_volume(water, max_transfer)):
            errors.append("water transfer chunk exceeds 20 uL")

    p20_tips = tips["p20"]
    print_tip_map = p20_tips["print_by_row"]
    if set(print_tip_map) != set(ROWS):
        errors.append("tips.p20.print_by_row must map rows A through H")
    assigned = [p20_tips["water"], p20_tips["stock"]]
    assigned.extend(print_tip_map[row] for row in ROWS)
    if len(assigned) != len(set(assigned)):
        errors.append("P20 tip assignments must be unique")
    for tip_name in assigned:
        if tip_name not in labware["tiprack_p20"].wells_by_name():
            errors.append("P20 tip {} is outside the slot-9 tip rack".format(tip_name))

    mix_block = int(tips["p300"]["mix_block_column"])
    if not 1 <= mix_block <= 12:
        errors.append("P300 mix block column must be 1..12")
    mix_volume = float(dilution["mix_volume_ul"])
    if not (20.0 <= mix_volume < total):
        errors.append(
            "P300 mix volume must be >= 20 uL and below V; got {:.2f} for V {:.2f}".format(
                mix_volume, total
            )
        )

    expected_columns = [1, 2, 3, 4]
    actual_columns = [
        int(group["destination"]["paper_start_column"]) for group in CONFIG["print_groups"]
    ]
    actual_volumes = [float(group["volume_ul"]) for group in CONFIG["print_groups"]]
    if actual_columns != expected_columns or actual_volumes != [20.0, 15.0, 10.0, 5.0]:
        errors.append("v3 paper plan must be columns 1..4 at 20/15/10/5 uL")
    if any(group.get("pipette") != "p20_single_gen2" for group in CONFIG["print_groups"]):
        errors.append("all v3 print groups must use p20_single_gen2")

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    protocol.comment("Pre-flight validation passed: config + labware geometry OK.")
    protocol.comment(
        "P300 safe travel envelope: rack {:.1f} mm + {:.1f} mm clearance = "
        "{:.1f} mm deck Z.".format(actual_rack_height, clearance, safe_travel_z)
    )
    return factors, safe_travel_z


def _set_flow_rates(p20, p300):
    rates = CONFIG.get("flow_rates", {})
    p20_rates = rates.get("p20", {})
    p300_rates = rates.get("p300", {})
    if p20_rates.get("aspirate"):
        p20.flow_rate.aspirate = float(p20_rates["aspirate"])
    if p20_rates.get("dispense"):
        p20.flow_rate.dispense = float(p20_rates["dispense"])
    if p300_rates.get("aspirate"):
        p300.flow_rate.aspirate = float(p300_rates["aspirate"])
    if p300_rates.get("dispense"):
        p300.flow_rate.dispense = float(p300_rates["dispense"])


def _p20_transfer(protocol, p20, volume_ul, source, destination, *, label, blow_out):
    max_transfer = float(CONFIG["dilution"]["max_transfer_ul"])
    chunks = _split_volume(volume_ul, max_transfer)
    for index, chunk in enumerate(chunks, start=1):
        if chunk > 20.0:
            raise RuntimeError("P20 transfer exceeds 20 uL: {}".format(chunk))
        protocol.comment(
            "P20 transfer: {:.2f} uL [{} {}/{}].".format(
                chunk, label, index, len(chunks)
            )
        )
        p20.aspirate(chunk, source)
        p20.dispense(chunk, destination)
        if blow_out:
            p20.blow_out(destination)


def _prepare_dilutions(protocol, labware, p20, p300, factors, safe_travel_z):
    dilution = CONFIG["dilution"]
    total = float(dilution["total_volume_ul"])
    column = str(dilution["destination_column"])
    wells = [labware["plate"]["{}{}".format(row, column)] for row in ROWS]
    sources = CONFIG["sources"]
    water_vial = labware["tuberack"][sources["water_vial"]]
    stock_vial = labware["tuberack"][CONFIG["color_series"][0]["dye_vial"]]
    height = float(sources["vial_aspirate_height_mm"])
    tip_map = CONFIG["tips"]["p20"]
    return_tips = bool(CONFIG["tips"].get("return_tips", False))

    p20.pick_up_tip(labware["tiprack_p20"][tip_map["water"]])
    protocol.comment("P20 water setup tip {} picked.".format(tip_map["water"]))
    for well, factor in zip(wells, factors):
        stock = total / factor
        water = total - stock
        if water <= EPSILON_UL:
            continue
        _p20_transfer(
            protocol,
            p20,
            water,
            water_vial.bottom(height),
            well.top(float(dilution["water_dispense_from_top_mm"])),
            label="water -> {}".format(well.well_name),
            blow_out=False,
        )
    _release_tip(p20, return_tips)
    protocol.comment("Water setup transfers done.")

    stock_policy = dilution.get("stock_tip_policy", "single")
    if stock_policy not in ("single", "per_well"):
        raise RuntimeError("stock_tip_policy must be 'single' or 'per_well'")
    if stock_policy == "single":
        p20.pick_up_tip(labware["tiprack_p20"][tip_map["stock"]])
        protocol.comment("P20 stock setup tip {} picked.".format(tip_map["stock"]))
    for row, well, factor in zip(ROWS, wells, factors):
        if stock_policy == "per_well":
            stock_by_row = tip_map.get("stock_by_row", {})
            p20.pick_up_tip(labware["tiprack_p20"][stock_by_row[row]])
        stock = total / factor
        protocol.comment(
            "Diluting well {} to {:g}x (stock {:.2f} uL).".format(
                well.well_name, factor, stock
            )
        )
        _p20_transfer(
            protocol,
            p20,
            stock,
            stock_vial.bottom(height),
            well.top(float(dilution["stock_dispense_from_top_mm"])),
            label="bp stock -> {}".format(well.well_name),
            blow_out=True,
        )
        if stock_policy == "per_well":
            _release_tip(p20, return_tips)
    if stock_policy == "single":
        _release_tip(p20, return_tips)
    protocol.comment("bp stock transfers done.")

    mix_column = int(CONFIG["tips"]["p300"]["mix_block_column"])
    p300.pick_up_tip(labware["tiprack"]["A{}".format(mix_column)])
    plate = labware["plate"]
    clearance_above_plate = safe_travel_z - float(plate.highest_z)
    if clearance_above_plate < 0:
        raise RuntimeError("safe P300 travel waypoint is below the plate top")
    p300.move_to(plate["A{}".format(column)].top(clearance_above_plate))
    p300.mix(
        int(dilution["mix_reps"]),
        float(dilution["mix_volume_ul"]),
        plate["A{}".format(column)],
    )
    p300.move_to(plate["A{}".format(column)].top(clearance_above_plate))
    p300.drop_tip()
    protocol.comment(
        "P300 mixed column {} with eight tips: {} x {:.2f} uL.".format(
            column, dilution["mix_reps"], float(dilution["mix_volume_ul"])
        )
    )
    protocol.comment("Dilution series complete: bp={}. ".format(column))


def _print_paper(protocol, labware, p20):
    dilution = CONFIG["dilution"]
    column = str(dilution["destination_column"])
    tip_map = CONFIG["tips"]["p20"]["print_by_row"]
    return_tips = bool(CONFIG["tips"].get("return_tips", False))
    groups = sorted(
        CONFIG["print_groups"],
        key=lambda group: int(group["destination"]["paper_start_column"]),
    )

    for row in ROWS:
        tip_name = tip_map[row]
        p20.pick_up_tip(labware["tiprack_p20"][tip_name])
        protocol.comment("P20 print row {} tip {} picked.".format(row, tip_name))
        source = labware["plate"]["{}{}".format(row, column)]
        for group in groups:
            volume = float(group["volume_ul"])
            if volume > 20.0:
                raise RuntimeError("P20 print volume exceeds 20 uL: {}".format(volume))
            paper_column = int(group["destination"]["paper_start_column"])
            paper_well = labware["paper"]["{}{}".format(row, paper_column)]
            dispense = group.get("dispense", {})
            destination = paper_well.bottom(float(dispense.get("z_mm", 1.0)))
            protocol.comment(
                "P20 print: row {}, {:.2f} uL -> paper column {}.".format(
                    row, volume, paper_column
                )
            )
            p20.aspirate(volume, source)
            p20.dispense(volume, destination)
            if dispense.get("blow_out", True):
                p20.blow_out(destination)
            dwell = float(dispense.get("post_dispense_delay_s", 0.0))
            if dwell > 0:
                protocol.delay(seconds=dwell)
        _release_tip(p20, return_tips)
    protocol.comment("Paper print complete: 32 spots across columns 1-4.")


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    labware = {
        role: _load_labware(protocol, deck[role])
        for role in ("tuberack", "plate", "paper", "tiprack", "tiprack_p20")
    }
    pipettes = {entry["name"]: entry for entry in CONFIG["pipettes"]}
    p20_cfg = pipettes["p20_single_gen2"]
    p300_cfg = pipettes["p300_multi_gen2"]
    p20 = protocol.load_instrument(
        p20_cfg["name"], p20_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )
    p300 = protocol.load_instrument(
        p300_cfg["name"], p300_cfg["mount"], tip_racks=[labware["tiprack"]]
    )

    factors, safe_travel_z = _preflight(protocol, labware, p20, p300)
    protocol.comment("=== Vial Dilution -> Paper Print V3 Started ===")
    protocol.comment(
        "Flags: dry_run={}, do_dilution={}, do_print={}".format(
            DEFAULT_DRY_RUN, DEFAULT_DO_DILUTION, DEFAULT_DO_PRINT
        )
    )
    protocol.comment(
        "Series: bp vial A2 -> plate column 11 -> paper columns 1-4."
    )
    protocol.comment(
        "Tip map: P20 water {}; stock {}; print {}. P300 mix block column {}.".format(
            CONFIG["tips"]["p20"]["water"],
            CONFIG["tips"]["p20"]["stock"],
            ", ".join(
                "{}={}".format(row, CONFIG["tips"]["p20"]["print_by_row"][row])
                for row in ROWS
            ),
            CONFIG["tips"]["p300"]["mix_block_column"],
        )
    )

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: pre-flight only; no robot motion or liquid handling.")
        protocol.comment("=== Vial Dilution -> Paper Print V3 Completed (dry run) ===")
        return

    _set_flow_rates(p20, p300)
    if CONFIG.get("camera", {}).get("capture_before"):
        _capture_image(protocol, "before_deck.jpg")
    if DEFAULT_DO_DILUTION and CONFIG["dilution"].get("enabled", True):
        _prepare_dilutions(protocol, labware, p20, p300, factors, safe_travel_z)
    if DEFAULT_DO_PRINT and CONFIG.get("print_groups"):
        _print_paper(protocol, labware, p20)
    if CONFIG.get("camera", {}).get("capture_after"):
        _capture_image(protocol, "after_print.jpg")
    protocol.comment("=== Vial Dilution -> Paper Print V3 Completed ===")
