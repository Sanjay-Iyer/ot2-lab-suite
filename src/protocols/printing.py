from src.core.models.config_models import PrintingWorkflowConfig
import json

def generate_printing_protocol(config: PrintingWorkflowConfig) -> str:
    """
    Generates an OT-2 protocol for the printing workflow.
    """
    # Convert nested config to base config for header/logic
    base_dict = config.to_base_config()
    config_json = json.dumps(base_dict, indent=2)
    
    protocol_template = f'''"""
OT-2 Protocol: Automated Printing
Generated: 2026-05-11
Config:
{config_json}
"""

import json
from opentrons import protocol_api

metadata = {{
    "protocolName": "Automated Printing",
    "author": "AI Agent",
    "apiLevel": "2.13"
}}

def run(protocol: protocol_api.ProtocolContext):
    # 1. Labware & Pipettes
    deck_json = """{json.dumps(base_dict['deck'])}"""
    deck = json.loads(deck_json)
    
    pipettes = {{}}
    for p_spec in deck['pipettes']:
        if not p_spec: continue
        tips = [protocol.load_labware(lw['type'], lw['slot']) 
                for lw in deck['labware'] if lw['slot'] in p_spec['tip_rack_slots']]
        pipettes[p_spec['mount']] = protocol.load_instrument(p_spec['type'], p_spec['mount'], tip_racks=tips)
        
    labware = {{}}
    for lw in deck['labware']:
        is_tiprack = any(p and lw['slot'] in p['tip_rack_slots'] for p in deck['pipettes'])
        if not is_tiprack:
            labware[lw['name']] = protocol.load_labware(lw['type'], lw['slot'])

    # 2. Printing Logic
    protocol.comment("Starting printing workflow: {base_dict['total_print_positions']} positions")
    
    # Safely get a pipette
    pipette = pipettes.get('left') or pipettes.get('right')
    if not pipette:
        raise ValueError("No pipette loaded in protocol.")
    
    reagent = labware.get('stock_reagent_rack')
    target = labware.get('printer_tray')
    
    if reagent and target:
        protocol.comment("Transferring reagent to printer tray")
        pipette.pick_up_tip()
        pipette.aspirate({base_dict['volume_per_print_ul']}, reagent.wells()[0])
        pipette.dispense({base_dict['volume_per_print_ul']}, target.wells()[0])
        pipette.drop_tip()
    else:
        protocol.comment("Skipping transfer: reagent or target tray not found in labware map.")
'''
    return protocol_template
