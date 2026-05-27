#!/usr/bin/env python3
"""
OT-2 Printing Demo — protocol_96.py  (96-Well Standard Plate Variant)
======================================================================
RECOMMENDED first physical test protocol.

Plate:   opentrons_96_wellplate_200ul_pcr_full_skirt
         Standard Opentrons labware — default namespace (NOT custom_beta).
         No custom labware JSON required on the robot.

Pipette: p300_multi_gen2 (8-channel, right mount)
         Compatible: each "A-row" well address aligns all 8 tips over that
         column (A1 → tips over A1-H1, A2 → tips over A2-H2, etc.).

First run:
  CONFIG["debug"]["dry_position_check"] = True
  → Moves to each well at top height only. No liquid transferred.
  → Verify tip clearance before enabling liquid transfer.

Generated from: configs/workflows/defaults/printing_96.yaml
Config target:  --config configs/workflows/defaults/printing_96.yaml
                --output src/protocols/generated/protocol_96.py
"""

from opentrons import protocol_api
from opentrons.types import Point
import os
import shutil
import subprocess
import time

metadata = {
    "protocolName": "OT-2 Printing Demo — 96-Well Standard Plate (SAFE TEST)",
    "author": "Antigravity AI Agent",
    "description": (
        "Standard Opentrons 96-well PCR plate. "
        "Recommended first physical test with p300_multi_gen2. "
        "No custom_beta namespace used."
    ),
    "apiLevel": "2.13",
}

# ── Safe Z travel constants ─────────────────────────────────────────────────
TRAVEL_CLEARANCE_MM = 15.0   # well.top() offset for safe lateral travel
ASPIRATE_HEIGHT_MM  = 3.0    # well.bottom() offset during aspiration
DISPENSE_HEIGHT_MM  = 5.0    # well.bottom() offset during dispense

CONFIG = {
    "camera": {
        "capture_after": True,
        "capture_after_each_dilution_step": True,
        "capture_after_each_print_step": True,
        "capture_before": True,
        "enabled": True,
        "image_filename_prefix": "printing_96",
        "robot_image_dir": "/data/vision/printing_demo_96",
    },
    "debug": {
        "dry_run_no_liquid": False,
        "dry_position_check": False,   # ← START WITH TRUE; set False for live run
        "enabled": True,
        "move_only": False,
        "verbose_protocol_comments": True,
    },
    "demo_mode": "dilution_print",
    "dilution": {
        "enabled": True,
        "steps": [
            {
                # Column 5 (A5-H5): 90 µL water + 10 µL dye = 100 µL total (50% of 200 µL max)
                "destination_well": "A5",
                "food_coloring_source_well": "A1",
                "mix_repetitions": 3,
                "mix_volume_ul": 50.0,
                "stock_volume_ul": 10.0,
                "water_source_well": "A3",
                "water_volume_ul": 90.0,
            },
            {
                # Column 6 (A6-H6): 75 µL water + 25 µL dye = 100 µL total (50% of 200 µL max)
                "destination_well": "A6",
                "food_coloring_source_well": "A2",
                "mix_repetitions": 3,
                "mix_volume_ul": 50.0,
                "stock_volume_ul": 25.0,
                "water_source_well": "A4",
                "water_volume_ul": 75.0,
            },
        ],
    },
    "layout": {
        # Column-based for 8-channel pipette: address "A<n>" to use column n.
        "dilution_destination_wells": ["A5", "A6"],
        "food_coloring_source_wells": ["A1", "A2"],
        "print_source_wells": ["A5", "A6"],
        "water_source_wells": ["A3", "A4"],
    },
    "output": {
        "create_image_manifest": True,
        "create_metadata": True,
        "local_run_root": "runs",
    },
    "pipette": {
        "mount": "right",
        "name": "p300_multi_gen2",
    },
    "plate": {
        # Standard Opentrons 96-well PCR plate.
        # namespace/version intentionally absent — uses Opentrons built-in namespace.
        # DO NOT add namespace="custom_beta" to this plate.
        "labware": "opentrons_96_wellplate_200ul_pcr_full_skirt",
        "slot": 2,
    },
    "printing": {
        # Printing Parameters:
        # - droplet_volume_ul: controls the spot size printed on the paper target.
        # - x_mm: controls left-right spacing between the printed 8-spot lines.
        # - y_mm: controls up-down movement of the whole 8-tip line.
        # - For this test, spacing is increased mostly through x_mm since the 8-channel
        #   pipette already spaces tips vertically in Y at 9 mm increments.
        "calibration_only": False,
        "dispense_height_mm": DISPENSE_HEIGHT_MM,
        "droplet_volume_ul": 20.0,
        "paper_slot": 3,
        "print_origin": {
            "x_mm": 5.0,
            "y_mm": 5.0,
        },
        "print_spacing": {
            "x_mm": 20.0,
            "y_mm": 10.0,
        },
        "print_positions": [
            {"label": "dilution_10_percent", "source_well": "A5", "x_index": 0, "y_index": 0},
            {"label": "dilution_25_percent", "source_well": "A6", "x_index": 1, "y_index": 0},
        ],
        "safe_z_mm": 20.0,
    },
    "tip_strategy": {
        "allowed_modes": [
            "new_tip_each_transfer",
            "reuse_low_to_high",
            "reuse_per_phase",
            "reuse_single_tip_for_demo",
        ],
        "drop_tip_at_end": False,
        "loaded_tip_count": 8,
        "mode": "reuse_single_tip_for_demo",
        "return_tip_at_end": True,
        "reuse_for_mixing": True,
        "reuse_for_printing": True,
        "reuse_for_stock": True,
        "reuse_for_water": True,
        "starting_tip": "A1",
    },
    "tiprack": {"labware": "opentrons_96_tiprack_300ul", "slot": 1},
}


# ── Labware loader helper ───────────────────────────────────────────────────

def load_plate(protocol, plate_cfg):
    """
    Load a plate labware.

    For standard Opentrons labware (no namespace/version in cfg): uses the
    default Opentrons namespace — do NOT pass custom_beta.

    For custom labware (namespace and version present in cfg): passes them
    explicitly so the robot looks in the right namespace.
    """
    labware_name = plate_cfg["labware"]
    slot = plate_cfg["slot"]
    namespace = plate_cfg.get("namespace")   # None for standard Opentrons plates
    version   = plate_cfg.get("version")     # None for standard Opentrons plates

    print(
        f"DEBUG: loading plate labware={labware_name} "
        f"namespace={namespace} version={version} slot={slot}"
    )

    if namespace and version is not None:
        # Custom labware: explicit namespace + version
        lw = protocol.load_labware(
            labware_name,
            slot,
            namespace=namespace,
            version=int(version),
        )
    elif namespace:
        lw = protocol.load_labware(labware_name, slot, namespace=namespace)
    else:
        # Standard Opentrons labware — default namespace, no custom_beta
        lw = protocol.load_labware(labware_name, slot)

    print(f"DEBUG: plate labware loaded successfully -> {labware_name} slot={slot}")
    return lw


def resolve_print_position(printing_cfg, pos_cfg):
    origin = printing_cfg.get("print_origin", {"x_mm": 5.0, "y_mm": 5.0})
    spacing = printing_cfg.get("print_spacing", {"x_mm": 20.0, "y_mm": 10.0})

    # Validation: print_spacing.x_mm must be greater than 0, print_spacing.y_mm must be greater than 0
    if float(spacing.get("x_mm", 0)) <= 0 or float(spacing.get("y_mm", 0)) <= 0:
        raise ValueError(
            f"Invalid print_spacing: x_mm={spacing.get('x_mm')}, y_mm={spacing.get('y_mm')}. "
            "Spacing must be greater than 0."
        )

    x_index = pos_cfg.get("x_index")
    y_index = pos_cfg.get("y_index")

    if x_index is not None or y_index is not None:
        x_index = int(x_index or 0)
        y_index = int(y_index or 0)
        x_mm = float(origin["x_mm"]) + x_index * float(spacing["x_mm"])
        y_mm = float(origin["y_mm"]) + y_index * float(spacing["y_mm"])
    else:
        # Backward compatibility for old configs:
        x_mm = float(pos_cfg["x_mm"])
        y_mm = float(pos_cfg["y_mm"])

    # Validation: resolved coordinates must be within safe ranges:
    # x_mm: 0 to 100 mm
    # y_mm: 0 to 80 mm
    if not (0.0 <= x_mm <= 100.0):
        raise ValueError(
            f"Resolved X coordinate {x_mm:.2f} mm is out of safe range (0 to 100 mm) "
            f"for print position '{pos_cfg.get('label')}'."
        )
    if not (0.0 <= y_mm <= 80.0):
        raise ValueError(
            f"Resolved Y coordinate {y_mm:.2f} mm is out of safe range (0 to 80 mm) "
            f"for print position '{pos_cfg.get('label')}'."
        )

    return x_mm, y_mm


def run(protocol: protocol_api.ProtocolContext) -> None:
    """Main entry point called by the Opentrons execution engine on the robot."""

    # ── Debug flags ───────────────────────────────────────────────────────
    debug_cfg         = CONFIG.get("debug", {})
    debug_enabled     = debug_cfg.get("enabled", False)
    verbose_comments  = debug_cfg.get("verbose_protocol_comments", False)
    dry_run_no_liquid = debug_cfg.get("dry_run_no_liquid", False)
    dry_pos_check     = debug_cfg.get("dry_position_check", False)

    def dbg(msg: str) -> None:
        if debug_enabled and verbose_comments:
            protocol.comment(f"DEBUG: {msg}")

    dbg("entered run() — protocol_96.py (96-well standard plate)")
    dbg("config loaded")

    # ── Config extraction ─────────────────────────────────────────────────
    plate_cfg    = CONFIG["plate"]
    tiprack_cfg  = CONFIG["tiprack"]
    pipette_cfg  = CONFIG["pipette"]
    printing_cfg = CONFIG["printing"]
    camera_cfg   = CONFIG["camera"]

    # ── 1. Deck-slot collision guard ──────────────────────────────────────
    slots = [plate_cfg["slot"], tiprack_cfg["slot"], printing_cfg["paper_slot"]]
    if len(slots) != len(set(slots)):
        raise ValueError(
            f"Duplicate deck slot assignments: "
            f"Plate={plate_cfg['slot']}, Tiprack={tiprack_cfg['slot']}, "
            f"Paper={printing_cfg['paper_slot']}"
        )

    # ── 2. Labware name guard — reject custom_beta on 96-well protocol ────
    _STD_96WELL = "opentrons_96_wellplate_200ul_pcr_full_skirt"
    if plate_cfg["labware"] != _STD_96WELL:
        raise ValueError(
            f"protocol_96.py expects labware='{_STD_96WELL}'. "
            f"Got: '{plate_cfg['labware']}'. "
            "Use protocol_12.py for custom 12-well plates."
        )
    if plate_cfg.get("namespace") == "custom_beta":
        raise ValueError(
            "protocol_96.py must NOT use namespace=custom_beta for the standard "
            "96-well plate. Remove namespace from the plate config."
        )

    # ── 3. Load labware ───────────────────────────────────────────────────
    dbg("labware loading started")
    plate = load_plate(protocol, plate_cfg)
    dbg("plate loaded")

    tiprack = protocol.load_labware(tiprack_cfg["labware"], tiprack_cfg["slot"])
    dbg("tiprack loaded")

    # paper_ref uses the same standard 96-well plate as a geometry reference
    paper_ref_cfg = {"labware": _STD_96WELL, "slot": printing_cfg["paper_slot"]}
    print(
        f"DEBUG: loading paper_ref labware={_STD_96WELL} "
        f"namespace=None version=None "
        f"slot={printing_cfg['paper_slot']}"
    )
    paper_ref = load_plate(protocol, paper_ref_cfg)
    print("DEBUG: paper_ref labware loaded successfully")

    pipette = protocol.load_instrument(
        pipette_cfg["name"],
        pipette_cfg["mount"],
        tip_racks=[tiprack],
    )
    dbg("pipette loaded")

    if CONFIG.get("demo_mode") == "dry_run" or dry_run_no_liquid:
        protocol.comment("robot dry protocol reached run function")
        protocol.comment("Dry run check complete. Labware and pipettes loaded successfully.")
        dbg("protocol completed")
        return

    # ── 4. Tip strategy ───────────────────────────────────────────────────
    strategy_cfg     = CONFIG.get("tip_strategy", {})
    strategy_mode    = strategy_cfg.get("mode", "new_tip_each_transfer")
    starting_tip     = strategy_cfg.get("starting_tip", "A1")
    loaded_tip_count = strategy_cfg.get("loaded_tip_count", 96) or 96

    dbg(f"tip strategy = {strategy_mode}")
    if strategy_mode == "reuse_single_tip_for_demo":
        protocol.comment(
            "WARNING: reuse_single_tip_for_demo mode — demonstration only, may cause carryover."
        )

    all_wells = tiprack.wells()
    try:
        start_idx = all_wells.index(tiprack.wells_by_name()[starting_tip])
    except Exception:
        start_idx = 0
    allowed_wells = all_wells[start_idx : start_idx + loaded_tip_count]
    tip_index = 0

    def ensure_tip(phase: str) -> None:
        if pipette.has_tip:
            return
        nonlocal tip_index
        if tip_index >= len(allowed_wells):
            raise RuntimeError(
                f"Exceeded loaded_tip_count={loaded_tip_count}. "
                f"Cannot pick up another tip for phase: {phase}."
            )
        target = allowed_wells[tip_index]
        dbg(f"picking up tip from {target.well_name} (phase={phase})")
        pipette.pick_up_tip(target)
        if strategy_mode != "reuse_single_tip_for_demo":
            tip_index += 1

    def finish_tip() -> None:
        if not pipette.has_tip:
            return
        if strategy_mode == "reuse_single_tip_for_demo":
            if strategy_cfg.get("return_tip_at_end", True):
                dbg("returning tip at end")
                pipette.return_tip()
            elif strategy_cfg.get("drop_tip_at_end", False):
                dbg("dropping tip at end")
                pipette.drop_tip()
            else:
                protocol.comment("WARNING: Protocol completed but tip remains attached.")
        else:
            dbg("dropping tip at end")
            pipette.drop_tip()

    # ── 5. Camera helper ─────────────────────────────────────────────────
    def capture_image(filename: str) -> None:
        if protocol.is_simulating():
            protocol.comment(f"[SIMULATION] Mock photo: {filename}")
            return
        protocol.comment(f"--- DIAGNOSTIC CAPTURE START: {filename} ---")
        remote_dir = camera_cfg.get("robot_image_dir", "/data/vision/printing_demo_96")
        protocol.comment(f"Debug: target remote_dir = {remote_dir}")
        try:
            os.makedirs(remote_dir, exist_ok=True)
            test_path = os.path.join(remote_dir, ".write_test")
            with open(test_path, "w") as _f:
                _f.write("test")
            os.remove(test_path)
            protocol.comment(f"Debug: write permissions verified for {remote_dir}")
        except Exception as _e:
            protocol.comment(f"Warning: Directory/write test failed: {_e}")

        output_path = os.path.join(remote_dir, filename)
        cmd = [
            "curl", "-s", "-X", "POST",
            "-H", "opentrons-version: *",
            "--max-time", "5",
            "http://localhost:31950/camera/picture",
            "--output", output_path,
        ]
        protocol.comment(f"Debug: Running curl cmd: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            protocol.comment(f"Debug: curl returned code {result.returncode}")
            if result.stdout:
                protocol.comment(f"Debug: curl stdout = {result.stdout[:200]}")
            if result.stderr:
                protocol.comment(f"Debug: curl stderr = {result.stderr[:200]}")
            if os.path.exists(output_path):
                sz = os.path.getsize(output_path)
                protocol.comment(f"Debug: image file size = {sz} bytes")
                if sz < 1000:
                    protocol.comment(f"Warning: captured file suspicious (too small: {sz} bytes)")
            else:
                protocol.comment(f"Warning: File {output_path} was NOT created.")
        except FileNotFoundError:
            protocol.comment(f"Warning: 'curl' not found on robot. Cannot capture {filename}.")
        except Exception as _e:
            protocol.comment(f"Warning: Camera capture error for {filename}: {_e}")
        protocol.comment(f"--- DIAGNOSTIC CAPTURE END: {filename} ---")

    # ── Phase 1: Before overview ──────────────────────────────────────────
    if camera_cfg["enabled"] and camera_cfg["capture_before"]:
        capture_image("before_deck.jpg")
        capture_image("before_wellplate.jpg")

    # ── Dry position-check mode ───────────────────────────────────────────
    # When dry_position_check=True: move to each well at top+TRAVEL_CLEARANCE_MM.
    # No aspiration or dispensing. Use this for first physical test.
    if dry_pos_check:
        protocol.comment(
            "DRY POSITION CHECK MODE — no liquid will be transferred. "
            f"TRAVEL_CLEARANCE_MM={TRAVEL_CLEARANCE_MM}"
        )
        ensure_tip("dry_check")
        all_check_wells = (
            CONFIG["layout"]["food_coloring_source_wells"]
            + CONFIG["layout"]["water_source_wells"]
            + CONFIG["layout"]["dilution_destination_wells"]
        )
        for well_name in all_check_wells:
            well = plate.wells_by_name()[well_name]
            print(f"DEBUG: dry_position_check moving to well={well_name} "
                  f"at top({TRAVEL_CLEARANCE_MM} mm)")
            protocol.comment(
                f"Dry check: moving above {well_name} at top+{TRAVEL_CLEARANCE_MM} mm "
                f"(no liquid)"
            )
            pipette.move_to(well.top(TRAVEL_CLEARANCE_MM))
        finish_tip()
        dbg("dry position check completed — set dry_position_check=False for live run")
        return

    # ── Phase 2: Dilution ─────────────────────────────────────────────────
    if (
        CONFIG["demo_mode"] == "dilution_print"
        and CONFIG["dilution"]["enabled"]
        and not printing_cfg.get("calibration_only", False)
    ):
        dil_steps = list(CONFIG["dilution"]["steps"])

        if strategy_mode == "reuse_low_to_high":
            def _conc_ratio(s):
                tot = s["water_volume_ul"] + s["stock_volume_ul"]
                return s["stock_volume_ul"] / tot if tot > 0.0 else 0.0
            dil_steps = sorted(dil_steps, key=_conc_ratio)
            protocol.comment("Sorted dilution steps by concentration for tip reuse.")

        # Water distribution
        water_steps = [s for s in dil_steps if s["water_volume_ul"] > 0]
        if water_steps:
            ensure_tip("water")
            for step in water_steps:
                src_well  = plate.wells_by_name()[step["water_source_well"]]
                dest_well = plate.wells_by_name()[step["destination_well"]]
                vol = step["water_volume_ul"]
                print(f"DEBUG: aspirating {vol} uL water from {step['water_source_well']} "
                      f"at bottom({ASPIRATE_HEIGHT_MM} mm)")
                protocol.comment(
                    f"Pipetting {vol} uL water from {step['water_source_well']} "
                    f"to {step['destination_well']}"
                )
                pipette.aspirate(vol, src_well.bottom(ASPIRATE_HEIGHT_MM))
                print(f"DEBUG: dispensing {vol} uL water into {step['destination_well']} "
                      f"at bottom({DISPENSE_HEIGHT_MM} mm)")
                pipette.dispense(vol, dest_well.bottom(DISPENSE_HEIGHT_MM))
            if strategy_mode != "reuse_single_tip_for_demo":
                pipette.drop_tip()

        # Stock + mix
        reusing_tip = False
        if strategy_mode in ("reuse_low_to_high", "reuse_per_phase", "reuse_single_tip_for_demo"):
            ensure_tip("stock")
            reusing_tip = True

        for idx, step in enumerate(dil_steps):
            if not reusing_tip:
                ensure_tip("stock")
            src_well  = plate.wells_by_name()[step["food_coloring_source_well"]]
            dest_well = plate.wells_by_name()[step["destination_well"]]
            stock_vol = step["stock_volume_ul"]
            if stock_vol > 0:
                print(f"DEBUG: aspirating {stock_vol} uL stock from "
                      f"{step['food_coloring_source_well']} at bottom({ASPIRATE_HEIGHT_MM} mm)")
                protocol.comment(
                    f"Pipetting {stock_vol} uL stock from {step['food_coloring_source_well']} "
                    f"to {step['destination_well']}"
                )
                pipette.aspirate(stock_vol, src_well.bottom(ASPIRATE_HEIGHT_MM))
                print(f"DEBUG: dispensing {stock_vol} uL stock into "
                      f"{step['destination_well']} at bottom({DISPENSE_HEIGHT_MM} mm)")
                pipette.dispense(stock_vol, dest_well.bottom(DISPENSE_HEIGHT_MM))

            mix_vol  = step.get("mix_volume_ul", 0.0)
            mix_reps = step.get("mix_repetitions", 0)
            if mix_reps > 0 and mix_vol > 0.0:
                protocol.comment(
                    f"Mixing well {step['destination_well']} ({mix_reps} x {mix_vol} uL)"
                )
                pipette.mix(mix_reps, mix_vol, dest_well.bottom(ASPIRATE_HEIGHT_MM))

            if not reusing_tip:
                pipette.drop_tip()
            if camera_cfg["enabled"] and camera_cfg["capture_after_each_dilution_step"]:
                capture_image(
                    f"wellplate_step_{idx + 1:03d}_well_{step['destination_well']}.jpg"
                )

        if reusing_tip and strategy_mode != "reuse_single_tip_for_demo":
            pipette.drop_tip()

    # ── Phase 3: Printing / Calibration ──────────────────────────────────
    print_positions = printing_cfg["print_positions"]
    paper_a1    = paper_ref.wells()[0]
    droplet_vol = printing_cfg["droplet_volume_ul"]
    dispense_h  = printing_cfg.get("dispense_height_mm", DISPENSE_HEIGHT_MM)
    is_calibration = printing_cfg.get("calibration_only", False)

    if is_calibration:
        protocol.comment("Calibration Mode: verifying coordinates above paper.")
        ensure_tip("printing")
        for idx, pos in enumerate(print_positions):
            x_mm, y_mm = resolve_print_position(printing_cfg, pos)
            print(f"DEBUG: print position label={pos.get('label')} source_well={pos.get('source_well')} "
                  f"x_index={pos.get('x_index')} y_index={pos.get('y_index')} "
                  f"resolved_x_mm={x_mm} resolved_y_mm={y_mm}")
            dest_loc = paper_a1.bottom(dispense_h).move(
                Point(x=x_mm, y=y_mm, z=0)
            )
            print(f"DEBUG: moving to paper X={x_mm} Y={y_mm} "
                  f"Z(bottom+{dispense_h})")
            protocol.comment(
                f"Moving tip above paper X={x_mm} mm, "
                f"Y={y_mm} mm, Z(bottom+{dispense_h}) mm"
            )
            pipette.move_to(dest_loc)
            if protocol.is_simulating():
                protocol.delay(seconds=2)
            else:
                protocol.pause(
                    f"Calibration Check - Point: {pos['label']} "
                    f"(Source: {pos['source_well']}). Resume to continue."
                )
            if camera_cfg["enabled"] and camera_cfg["capture_after_each_print_step"]:
                capture_image(f"paper_print_{idx + 1:03d}_well_{pos['source_well']}.jpg")
        if strategy_mode != "reuse_single_tip_for_demo":
            pipette.drop_tip()
    else:
        reusing_print_tip = (strategy_mode == "reuse_single_tip_for_demo")
        if reusing_print_tip:
            ensure_tip("printing")

        for idx, pos in enumerate(print_positions):
            x_mm, y_mm = resolve_print_position(printing_cfg, pos)
            print(f"DEBUG: print position label={pos.get('label')} source_well={pos.get('source_well')} "
                  f"x_index={pos.get('x_index')} y_index={pos.get('y_index')} "
                  f"resolved_x_mm={x_mm} resolved_y_mm={y_mm}")
            if not reusing_print_tip:
                ensure_tip("printing")
            src_well = plate.wells_by_name()[pos["source_well"]]
            print(f"DEBUG: aspirating {droplet_vol} uL from {pos['source_well']} "
                  f"at bottom({ASPIRATE_HEIGHT_MM} mm)")
            protocol.comment(
                f"Aspirating {droplet_vol} uL from {pos['source_well']} for printing"
            )
            pipette.aspirate(droplet_vol, src_well.bottom(ASPIRATE_HEIGHT_MM))

            dest_loc = paper_a1.bottom(dispense_h).move(
                Point(x=x_mm, y=y_mm, z=0)
            )
            print(f"DEBUG: dispensing at paper X={x_mm} Y={y_mm} "
                  f"Z(bottom+{dispense_h})")
            protocol.comment(
                f"Printing droplet on paper at X={x_mm} mm, Y={y_mm} mm"
            )
            pipette.dispense(droplet_vol, dest_loc)

            if not reusing_print_tip:
                pipette.drop_tip()
            if camera_cfg["enabled"] and camera_cfg["capture_after_each_print_step"]:
                capture_image(f"paper_print_{idx + 1:03d}_well_{pos['source_well']}.jpg")

    # ── Phase 4: After overview ───────────────────────────────────────────
    if camera_cfg["enabled"] and camera_cfg["capture_after"]:
        capture_image("after_deck.jpg")
        capture_image("after_wellplate.jpg")

    finish_tip()
    dbg("protocol completed — protocol_96.py")
