"""
OT-2 Protocol: Standard Nanoparticle Printing
Generated: 2026-05-11
Config:
{
  "workflow": "printing",
  "apiLevel": "2.15",
  "simulate": true,
  "metadata": {
    "protocolName": "Standard Nanoparticle Printing",
    "author": "Sanjay",
    "description": "Unified dilution, mixing, and printing suite"
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
        "slot": 7,
        "type": "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap",
        "name": "output_rack_1"
      },
      {
        "slot": 9,
        "type": "nest_96_wellplate_200ul_flat",
        "name": "printer_tray"
      },
      {
        "slot": 10,
        "type": "opentrons_96_tiprack_300ul",
        "name": "tips_300"
      }
    ],
    "pipettes": [
      {
        "mount": "left",
        "type": "p300_single_gen2",
        "tip_rack_slots": [
          10,
          11
        ]
      }
    ],
    "modules": []
  },
  "reagent_source": "stock_reagent_rack",
  "reagent_concentration": 100.0,
  "concentration_unit": "ug/mL",
  "dilution_factors": [
    1,
    10,
    50,
    100
  ],
  "target_final_volume_ul": 200.0,
  "mix_after": true,
  "mix_volume_ul": 50.0,
  "mix_count": 3,
  "print_targets": [
    {
      "labware_name": "printer_tray",
      "starting_well": "A1"
    }
  ],
  "total_print_positions": 24,
  "volume_per_print_ul": 5.0,
  "replicates_per_position": 1
}
"""

from opentrons import protocol_api

metadata = {
    "protocolName": "Standard Nanoparticle Printing",
    "author": "Sanjay",
    "apiLevel": "2.15"
}

def run(protocol: protocol_api.ProtocolContext):
    # 1. Labware & Pipettes
    deck = {"labware":[{"slot":1,"type":"opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap","name":"stock_reagent_rack"},{"slot":2,"type":"nest_12_reservoir_15ml","name":"solvent_reservoir"},{"slot":7,"type":"opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap","name":"output_rack_1"},{"slot":9,"type":"nest_96_wellplate_200ul_flat","name":"printer_tray"},{"slot":10,"type":"opentrons_96_tiprack_300ul","name":"tips_300"}],"pipettes":[{"mount":"left","type":"p300_single_gen2","tip_rack_slots":[10,11]}],"modules":[]}
    
    pipettes = {}
    for p_spec in deck['pipettes']:
        tips = []
        for s in p_spec['tip_rack_slots']:
            # Find labware at this slot
            lw_entry = next((lw for lw in deck['labware'] if lw['slot'] == s), None)
            if lw_entry:
                tips.append(protocol.load_labware(lw_entry['type'], s))
            else:
                protocol.comment(f"WARNING: Tip rack slot {s} defined in pipette but missing on deck.")
        
        if not tips:
            # Fallback if no tips loaded (Opentrons will error anyway, but we handle it)
            protocol.comment(f"ERROR: No tip racks loaded for pipette on {p_spec['mount']}")
            
        pipettes[p_spec['mount']] = protocol.load_instrument(p_spec['type'], p_spec['mount'], tip_racks=tips)
        
    labware = {}
    for lw in deck['labware']:
        if any(lw['slot'] in p['tip_rack_slots'] for p in deck['pipettes']):
            continue # Already loaded as tip rack
        labware[lw['name']] = protocol.load_labware(lw['type'], lw['slot'])

    # 2. Logic (Simulated for this refactor)
    protocol.comment("Starting printing workflow")
    protocol.comment("Target print positions: 24")
    
    # Simple move to demonstrate it works
    pipette = pipettes[deck['pipettes'][0]['mount']]
    reagent = labware['stock_reagent_rack']
    target = labware['printer_tray']
    
    pipette.pick_up_tip()
    pipette.aspirate(5.0, reagent.wells()[0])
    pipette.dispense(5.0, target.wells()[0])
    pipette.drop_tip()
