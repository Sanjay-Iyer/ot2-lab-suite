"""
OT-2 Size Mixing Protocol — Single Tip / 8-Channel Workflow
==========================================================

Uses a single 8-channel pipette and 1 tip to distribute 
size pools into final samples.
"""

import json
import os
from opentrons import protocol_api

metadata = {
    "apiLevel": "2.13",
    "protocolName": "Nanoparticle Size Mixing (Single Tip)",
}

CONFIG_DIR = "configs"
SAMPLE_MATRIX_FILE = os.path.join(CONFIG_DIR, "sample_matrix.json")
DECK_MAP_FILE = os.path.join(CONFIG_DIR, "deck_map.json")

def run(protocol: protocol_api.ProtocolContext):
    # 1. Load configs
    try:
        with open(SAMPLE_MATRIX_FILE, "r") as f:
            sample_matrix = json.load(f)
        with open(DECK_MAP_FILE, "r") as f:
            deck_map = json.load(f)
    except FileNotFoundError as e:
        protocol.pause(f"Missing config: {e}")
        return

    # 2. Load labware
    labware_objs = {}
    for slot, info in deck_map["slots"].items():
        labware_objs[info["reagent_name"]] = protocol.load_labware(info["labware_type"], int(slot))

    # 3. Load pipette
    # Using multi-channel on right mount as default
    pipette = protocol.load_instrument("p300_multi_gen2", "right", tip_racks=[labware_objs["tips_main"]])

    # 4. Execute
    pipette.pick_up_tip()
    
    for sample in sample_matrix:
        # Source size pool
        size = sample["size_variant"]
        if sample["source_size_pool_slot"] == 4: source_labware = labware_objs["size_pool_A"]
        elif sample["source_size_pool_slot"] == 5: source_labware = labware_objs["size_pool_B"]
        else: source_labware = labware_objs["size_pool_C"]
        
        source_well = source_labware["A1"]

        # Destination
        dest_rack = labware_objs["output_rack_1"] if sample["destination_slot"] == 7 else labware_objs["output_rack_2"]
        dest_well = dest_rack[sample["destination_well"]]

        protocol.comment(f"[MIX] {size} NS -> {sample['sample_id']}")

        # Transfer 100uL
        pipette.aspirate(100, source_well)
        pipette.dispense(100, dest_well)
        pipette.mix(2, 100, dest_well)

    pipette.drop_tip()
    protocol.comment("[DONE] Size mixing complete.")
