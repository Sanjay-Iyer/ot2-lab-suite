"""
OT-2 Protocol: Automated Printing
Generated: 2026-05-11
Config:
{
  "workflow": "printing",
  "apiLevel": "2.15",
  "simulate": true,
  "metadata": {
    "protocolName": "Automated Printing"
  },
  "deck": {
    "labware": [
      {
        "slot": 1,
        "type": "opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap",
        "name": "stock_reagent_rack"
      },
      {
        "slot": 2,
        "type": "nest_12_reservoir_15ml",
        "name": "solvent_reservoir"
      },
      {
        "slot": 9,
        "type": "nest_96_wellplate_200ul_flat",
        "name": "printer_tray"
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
  "reagent_source": "stock_reagent_rack",
  "total_print_positions": 24,
  "target_final_volume_ul": 200.0,
  "volume_per_print_ul": 5.0
}
"""

import json
from opentrons import protocol_api

metadata = {
    "protocolName": "Automated Printing",
    "author": "AI Agent",
    "apiLevel": "2.15"
}

def run(protocol: protocol_api.ProtocolContext):
    # 1. Labware & Pipettes
    deck_json = """{"labware": [{"slot": 1, "type": "opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap", "name": "stock_reagent_rack"}, {"slot": 2, "type": "nest_12_reservoir_15ml", "name": "solvent_reservoir"}, {"slot": 9, "type": "nest_96_wellplate_200ul_flat", "name": "printer_tray"}, {"slot": 10, "type": "opentrons_96_tiprack_300ul", "name": "tip_rack"}], "pipettes": [{"mount": "left", "type": "p300_single_gen2", "tip_rack_slots": [10]}], "modules": []}"""
    deck = json.loads(deck_json)
    
    pipettes = {}
    for p_spec in deck['pipettes']:
        if not p_spec: continue
        tips = [protocol.load_labware(lw['type'], lw['slot']) 
                for lw in deck['labware'] if lw['slot'] in p_spec['tip_rack_slots']]
        pipettes[p_spec['mount']] = protocol.load_instrument(p_spec['type'], p_spec['mount'], tip_racks=tips)
        
    labware = {}
    for lw in deck['labware']:
        is_tiprack = any(p and lw['slot'] in p['tip_rack_slots'] for p in deck['pipettes'])
        if not is_tiprack:
            labware[lw['name']] = protocol.load_labware(lw['type'], lw['slot'])

    # 2. Printing Logic
    protocol.comment("Starting printing workflow: 24 positions")
    
    # Safely get a pipette
    pipette = pipettes.get('left') or pipettes.get('right')
    if not pipette:
        raise ValueError("No pipette loaded in protocol.")
    
    reagent = labware.get('stock_reagent_rack')
    target = labware.get('printer_tray')
    
    if reagent and target:
        protocol.comment("Transferring reagent to printer tray")
        pipette.pick_up_tip()
        pipette.aspirate(5.0, reagent.wells()[0])
        pipette.dispense(5.0, target.wells()[0])
        pipette.drop_tip()
    else:
        protocol.comment("Skipping transfer: reagent or target tray not found in labware map.")
