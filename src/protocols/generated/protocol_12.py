#!/usr/bin/env python3
"""
OT-2 Printing Demo — protocol_12.py  (12-Well Custom Plate Variant)
====================================================================
EXPERIMENTAL / UNSAFE until custom labware geometry is manually validated.

Plate:   usascientific12well_12_wellplate_6000ul
         namespace=custom_beta  version=1
         Installed at:
         /data/labware/v2/custom_definitions/custom_beta/
         usascientific12well_12_wellplate_6000ul/1.json

WARNING:
  An 8-channel (multi) pipette is NOT recommended for this plate.
  The protocol will raise ValueError if p300_multi_gen2 is used unless
  CONFIG["pipette"]["allow_multi_on_12well"] is explicitly set to True.

  Use protocol_96.py for the first safe physical test.

Generated from: configs/workflows/defaults/printing_12.yaml
Config target:  --config configs/workflows/defaults/printing_12.yaml
                --output src/protocols/generated/protocol_12.py
"""

from opentrons import protocol_api
from opentrons.types import Point
import os
import shutil
import subprocess
import time

metadata = {
    "protocolName": "OT-2 Printing Demo — 12-Well Custom Plate (EXPERIMENTAL)",
    "author": "Antigravity AI Agent",
    "description": (
        "12-well USA Scientific CytoOne plate via custom_beta namespace. "
        "Requires manual geometry validation before physical use."
    ),
    "apiLevel": "2.13",
}

# ── Safe Z travel constants ─────────────────────────────────────────────────
TRAVEL_CLEARANCE_MM = 15.0   # well.top() offset for safe lateral travel
ASPIRATE_HEIGHT_MM  = 5.0    # well.bottom() offset during aspiration
DISPENSE_HEIGHT_MM  = 5.0    # well.bottom() offset during dispense

CONFIG = {
    "camera": {
        "capture_after": True,
        "capture_after_each_dilution_step": True,
        "capture_after_each_print_step": True,
        "capture_before": True,
        "enabled": True,
        "image_filename_prefix": "printing_12",
        "robot_image_dir": "/data/vision/printing_demo_12",
    },
    "debug": {
        "dry_run_no_liquid": False,
        "dry_position_check": False,
        "enabled": True,
        "move_only": False,
        "verbose_protocol_comments": True,
    },
    "demo_mode": "dilution_print",
    "dilution": {
        "enabled": True,
        "steps": [
            {
                "destination_well": "C1",
                "food_coloring_source_well": "A1",
                "mix_repetitions": 3,
                "mix_volume_ul": 100.0,
                "stock_volume_ul": 20.0,
                "water_source_well": "B1",
                "water_volume_ul": 180.0,
            },
            {
                "destination_well": "C2",
                "food_coloring_source_well": "A1",
                "mix_repetitions": 3,
                "mix_volume_ul": 100.0,
                "stock_volume_ul": 50.0,
                "water_source_well": "B2",
                "water_volume_ul": 150.0,
            },
        ],
    },
    "layout": {
        "dilution_destination_wells": ["C1", "C2"],
        "food_coloring_source_wells": ["A1", "A2"],
        "print_source_wells": ["C1", "C2"],
        "water_source_wells": ["B1", "B2"],
    },
    "output": {
        "create_image_manifest": True,
        "create_metadata": True,
        "local_run_root": "runs",
    },
    "pipette": {
        "mount": "right",
        "name": "p300_multi_gen2",
        "allow_multi_on_12well": False,  # set True to override safety guard
    },
    "plate": {
        "labware": "usascientific12well_12_wellplate_6000ul",
        "namespace": "custom_beta",
        "slot": 2,
        "version": 1,
    },
    "printing": {
        "calibration_only": False,
        "dispense_height_mm": DISPENSE_HEIGHT_MM,
        "droplet_volume_ul": 10.0,
        "paper_slot": 3,
        "print_positions": [
            {"label": "dilution_1", "source_well": "C1", "x_mm": 5.0, "y_mm": 5.0},
            {"label": "dilution_2", "source_well": "C2", "x_mm": 10.0, "y_mm": 10.0},
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
    """Load a plate using namespace/version when present (custom labware)."""
    labware_name = plate_cfg["labware"]
    slot = plate_cfg["slot"]
    namespace = plate_cfg.get("namespace")
    version = plate_cfg.get("version")

    print(
        f"DEBUG: loading plate labware={labware_name} "
        f"namespace={namespace} version={version} slot={slot}"
    )

    if namespace and version is not None:
        lw = protocol.load_labware(
            labware_name,
            slot,
            namespace=namespace,
            version=int(version),
        )
    elif namespace:
        lw = protocol.load_labware(labware_name, slot, namespace=namespace)
    else:
        lw = protocol.load_labware(labware_name, slot)

    print(f"DEBUG: plate labware loaded successfully -> {labware_name} slot={slot}")
    return lw


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

    dbg("entered run() — protocol_12.py (12-well custom plate)")
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

    # ── 2. Multi-channel safety guard ─────────────────────────────────────
    _CUSTOM_12WELL = "usascientific12well_12_wellplate_6000ul"
    if (
        "multi" in pipette_cfg["name"].lower()
        and plate_cfg["labware"] == _CUSTOM_12WELL
        and not pipette_cfg.get("allow_multi_on_12well", False)
    ):
        raise ValueError(
            f"Unsafe configuration: '{pipette_cfg['name']}' is not recommended "
            f"with the custom 12-well plate '{_CUSTOM_12WELL}'. "
            "Use protocol_96.py for the first physical test, or set "
            "CONFIG['pipette']['allow_multi_on_12well'] = True to override."
        )

    # ── 3. Labware name validation ────────────────────────────────────────
    if plate_cfg["labware"] == "usa_scientific_12well_12_wellplate_6000ul":
        raise ValueError(
            "Old incorrect labware name detected. Use "
            "usascientific12well_12_wellplate_6000ul with "
            "namespace custom_beta and version 1."
        )
    if plate_cfg["labware"] != _CUSTOM_12WELL:
        raise ValueError(
            f"protocol_12.py expects labware='{_CUSTOM_12WELL}'. "
            f"Got: '{plate_cfg['labware']}'. Use protocol_96.py for other plates."
        )
    if plate_cfg.get("namespace") != "custom_beta":
        raise ValueError(
            f"Unexpected plate namespace: {plate_cfg.get('namespace')}. "
            "Expected: custom_beta"
        )
    if int(plate_cfg.get("version", 1)) != 1:
        raise ValueError(f"Unexpected plate version: {plate_cfg.get('version')}")

    # ── 4. Load labware ───────────────────────────────────────────────────
    dbg("labware loading started")
    plate = load_plate(protocol, plate_cfg)
    dbg("plate loaded")

    tiprack = protocol.load_labware(tiprack_cfg["labware"], tiprack_cfg["slot"])
    dbg("tiprack loaded")

    # paper_ref also uses the same custom plate labware (geometry reference only)
    paper_ref_cfg = dict(plate_cfg)
    paper_ref_cfg["slot"] = printing_cfg["paper_slot"]
    print(
        f"DEBUG: loading paper_ref labware={plate_cfg['labware']} "
        f"namespace={plate_cfg.get('namespace')} "
        f"version={plate_cfg.get('version')} "
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

    # ── 5. Tip strategy ───────────────────────────────────────────────────
    strategy_cfg   = CONFIG.get("tip_strategy", {})
    strategy_mode  = strategy_cfg.get("mode", "new_tip_each_transfer")
    starting_tip   = strategy_cfg.get("starting_tip", "A1")
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

    # ── 6. Camera helper ─────────────────────────────────────────────────
    def capture_image(filename: str) -> None:
        if protocol.is_simulating():
            protocol.comment(f"[SIMULATION] Mock photo: {filename}")
            return
        protocol.comment(f"--- DIAGNOSTIC CAPTURE START: {filename} ---")
        remote_dir = camera_cfg.get("robot_image_dir", "/data/vision/printing_demo_12")
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
    if dry_pos_check:
        protocol.comment("DRY POSITION CHECK MODE — no liquid will be transferred.")
        ensure_tip("dry_check")
        all_check_wells = (
            CONFIG["layout"]["food_coloring_source_wells"]
            + CONFIG["layout"]["water_source_wells"]
            + CONFIG["layout"]["dilution_destination_wells"]
        )
        for well_name in all_check_wells:
            well = plate.wells_by_name()[well_name]
            print(f"DEBUG: moving to well={well_name} at top({TRAVEL_CLEARANCE_MM} mm)")
            protocol.comment(f"Dry check: moving above {well_name} at top+{TRAVEL_CLEARANCE_MM} mm")
            pipette.move_to(well.top(TRAVEL_CLEARANCE_MM))
        finish_tip()
        dbg("dry position check completed")
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
    paper_a1 = paper_ref.wells()[0]
    droplet_vol = printing_cfg["droplet_volume_ul"]
    dispense_h  = printing_cfg.get("dispense_height_mm", DISPENSE_HEIGHT_MM)
    is_calibration = printing_cfg.get("calibration_only", False)

    if is_calibration:
        protocol.comment("Calibration Mode: verifying coordinates above paper.")
        ensure_tip("printing")
        for idx, pos in enumerate(print_positions):
            dest_loc = paper_a1.bottom(dispense_h).move(
                Point(x=pos["x_mm"], y=pos["y_mm"], z=0)
            )
            print(f"DEBUG: moving to paper X={pos['x_mm']} Y={pos['y_mm']} Z(bottom+{dispense_h})")
            protocol.comment(
                f"Moving tip above paper X={pos['x_mm']} mm, "
                f"Y={pos['y_mm']} mm, Z(bottom+{dispense_h}) mm"
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
                Point(x=pos["x_mm"], y=pos["y_mm"], z=0)
            )
            print(f"DEBUG: dispensing at paper X={pos['x_mm']} Y={pos['y_mm']} "
                  f"Z(bottom+{dispense_h})")
            protocol.comment(
                f"Printing droplet on paper at X={pos['x_mm']} mm, Y={pos['y_mm']} mm"
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
    dbg("protocol completed — protocol_12.py")
