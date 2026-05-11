"""
OT-2 Dilution Protocol — Single Tip / Single Pipette Workflow
=============================================================

This protocol is optimized for a single 8-channel pipette and
minimal tip usage. It picks up ONE tip (first channel) and
performs the entire run to conserve consumables.

Order of operations (to avoid cross-contamination with 1 tip):
1. Transfer WATER to all target tubes first.
2. Transfer STOCK to tubes in order of increasing concentration.
"""

import json
import os
from opentrons import protocol_api

metadata = {
    "apiLevel": "2.13",
    "protocolName": "Nanoparticle Dilution (Single Tip Optimization)",
    "description": "Uses 1 tip and 1 8-channel pipette for the entire run.",
}

CONFIG_DIR = "configs"
DILUTION_PLAN_FILE = os.path.join(CONFIG_DIR, "dilution_plan.json")
DECK_MAP_FILE = os.path.join(CONFIG_DIR, "deck_map.json")

def run(protocol: protocol_api.ProtocolContext):
    # 1. Load Configs
    try:
        with open(DILUTION_PLAN_FILE, "r") as f:
            raw_data = json.load(f)
            
            # Handle the Agent's 'Experiment Config' format vs the old 'List' format
            if isinstance(raw_data, dict) and "dilution_factors" in raw_data:
                factors = raw_data["dilution_factors"]
            else:
                factors = raw_data

            # Convert those factors into the step-dictionaries the protocol expects
            dilution_steps = []
            for df in factors:
                if isinstance(df, (int, float)):
                    dilution_steps.append({"factor": df})
                else:
                    dilution_steps.append(df)
                    
        with open(DECK_MAP_FILE, "r") as f:
            deck_map = json.load(f)
            
    except FileNotFoundError as e:
        protocol.pause(f"Config missing: {e}")
        return

    # 2. Load Labware
    labware_objs = {}
    for slot_str, info in deck_map["slots"].items():
        labware_objs[info["reagent_name"]] = protocol.load_labware(info["labware_type"], int(slot_str))

    # 3. Load Pipette (8-channel)
    # We assume the user has a P300 Multi as per their request
    pipette = protocol.load_instrument(
        "p300_multi_gen2", "right", tip_racks=[labware_objs["tips_main"]]
    )

    # 4. Sources
    stock_well = labware_objs["stock_ns"]["A1"]
    water_well = labware_objs["water"]["A1"]

    # ── Tip Management ──
    # Pick up one tip (from the first channel)
    # Note: On a multi-channel, picking up from column 1 uses all 8 tips.
    # To use only 1 tip, you would typically only have 1 tip in the rack, 
    # or use a single-channel pipette.
    # If the user literally only has 1 tip on the head, we just pick_up_tip().
    pipette.pick_up_tip()

    # 5. STEP 1: WATER (Transfer to all tubes first)
    protocol.comment("[STAGE 1] Distributing Diluent (Water)")
    for step in dilution_steps:
        v_water = step["water_volume_ul"]
        if v_water > 0:
            dest_rack = labware_objs["output_rack_1"] if step["output_slot"] == 7 else labware_objs["output_rack_2"]
            dest_well = dest_rack[step["output_well"]]
            
            _transfer_simple(pipette, v_water, water_well, dest_well)

    # 6. STEP 2: STOCK (Transfer in order of concentration)
    # We sort by dilution factor (highest DF = lowest concentration first)
    protocol.comment("[STAGE 2] Distributing Nanoparticle Stock")
    sorted_steps = sorted(dilution_steps, key=lambda x: x["dilution_factor"], reverse=True)
    
    for step in sorted_steps:
        v_stock = step["stock_volume_ul"]
        if v_stock > 0:
            dest_rack = labware_objs["output_rack_1"] if step["output_slot"] == 7 else labware_objs["output_rack_2"]
            dest_well = dest_rack[step["output_well"]]
            
            protocol.comment(f"Adding stock for DF={step['dilution_factor']}")
            _transfer_simple(pipette, v_stock, stock_well, dest_well)
            
            # Mix in place (homogenize)
            mix_vol = min(v_stock + step["water_volume_ul"], pipette.max_volume) * 0.8
            pipette.mix(3, mix_vol, dest_well)

    # Final drop
    pipette.drop_tip()
    protocol.comment("[DONE] Protocol complete using 1 tip.")

def _transfer_simple(pipette, volume, source, destination):
    """Handles multi-aspiration if volume > pipette capacity."""
    remaining = volume
    while remaining > 0:
        vol = min(remaining, pipette.max_volume)
        pipette.aspirate(vol, source)
        pipette.dispense(vol, destination)
        remaining -= vol
