"""
OT-2 Protocol: Automated Dilution
Generated: 2026-05-11
Config:
{
  "workflow": "dilution",
  "apiLevel": "2.15",
  "simulate": true,
  "metadata": {
    "protocolName": "Automated Dilution"
  },
  "deck": {
    "labware": [
      {
        "slot": 1,
        "type": "corning_96_wellplate_360ul_flat",
        "name": "source_plate"
      },
      {
        "slot": 2,
        "type": "corning_96_wellplate_360ul_flat",
        "name": "dilution_plate"
      },
      {
        "slot": 3,
        "type": "corning_96_wellplate_360ul_flat",
        "name": "destination_plate"
      },
      {
        "slot": 10,
        "type": "opentrons_96_tiprack_300ul",
        "name": "tip_rack"
      }
    ],
    "pipettes": [
      {
        "mount": "left",
        "type": "p300_single_gen2",
        "tip_rack_slots": [
          10
        ]
      }
    ],
    "modules": []
  },
  "dilution_steps": 2,
  "dilution_factors": [
    5.0,
    10.0
  ],
  "final_volume": 250.0,
  "replicates": 1
}
"""

import json
from opentrons import protocol_api

metadata = {
    "protocolName": "Automated Dilution",
    "author": "AI Agent",
    "apiLevel": "2.15"
}

def run(protocol: protocol_api.ProtocolContext):
    # 1. Labware & Pipettes
    deck_json = """{"labware": [{"slot": 1, "type": "corning_96_wellplate_360ul_flat", "name": "source_plate"}, {"slot": 2, "type": "corning_96_wellplate_360ul_flat", "name": "dilution_plate"}, {"slot": 3, "type": "corning_96_wellplate_360ul_flat", "name": "destination_plate"}, {"slot": 10, "type": "opentrons_96_tiprack_300ul", "name": "tip_rack"}], "pipettes": [{"mount": "left", "type": "p300_single_gen2", "tip_rack_slots": [10]}], "modules": []}"""
    deck = json.loads(deck_json)
    
    pipettes = {}
    for p_spec in deck['pipettes']:
        if not p_spec: continue
        tips = [protocol.load_labware(lw['type'], lw['slot']) 
                for lw in deck['labware'] if lw['slot'] in p_spec['tip_rack_slots']]
        pipettes[p_spec['mount']] = protocol.load_instrument(p_spec['type'], p_spec['mount'], tip_racks=tips)
        
    labware = {}
    for lw in deck['labware']:
        # Filter out tipracks from the main labware dict
        is_tiprack = any(p and lw['slot'] in p['tip_rack_slots'] for p in deck['pipettes'])
        if not is_tiprack:
            labware[lw['name']] = protocol.load_labware(lw['type'], lw['slot'])

    # 2. Dilution Logic
    protocol.comment(f"Starting dilution workflow: {len(deck['labware'])} items on deck")
    
    # Safely get a pipette
    pipette = pipettes.get('left') or pipettes.get('right')
    if not pipette:
        raise ValueError("No pipette loaded in protocol.")
    
    source = labware.get('source_plate')
    dilution = labware.get('dilution_plate')
    
    if source and dilution:
        protocol.comment("Transferring source to dilution plate")
        pipette.pick_up_tip()
        pipette.aspirate(100, source.wells()[0])
        pipette.dispense(100, dilution.wells()[0])
        pipette.drop_tip()
    else:
        protocol.comment("Skipping transfer: plates not found in labware map.")
