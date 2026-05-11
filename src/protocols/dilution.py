from src.core.models.config_models import DilutionWorkflowConfig
import json

def generate_dilution_protocol(config: DilutionWorkflowConfig) -> str:
    """
    Generates an OT-2 protocol for the dilution workflow.
    """
    # Convert nested config to base config for header
    base_dict = config.to_base_config()
    config_json = json.dumps(base_dict, indent=2)
    
    protocol_template = f'''"""
OT-2 Protocol: Automated Dilution
Generated: 2026-05-11
Config:
{config_json}
"""

import json
from opentrons import protocol_api

metadata = {{
    "protocolName": "Automated Dilution",
    "author": "AI Agent",
    "apiLevel": "2.15"
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
        # Filter out tipracks from the main labware dict
        is_tiprack = any(p and lw['slot'] in p['tip_rack_slots'] for p in deck['pipettes'])
        if not is_tiprack:
            labware[lw['name']] = protocol.load_labware(lw['type'], lw['slot'])

    # 2. Dilution Logic
    protocol.comment(f"Starting dilution workflow: {{len(deck['labware'])}} items on deck")
    
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
'''
    return protocol_template
