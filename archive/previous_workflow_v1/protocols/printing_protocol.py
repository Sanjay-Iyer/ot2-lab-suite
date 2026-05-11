from opentrons import protocol_api
import json

metadata = {
    'protocolName': 'Dynamic AI-Agent Nanoparticle Printing',
    'author': 'Automated Lab Systems',
    'description': 'Agent-driven dilution and spotting matrix',
    'apiLevel': '2.13'
}

def run(protocol: protocol_api.ProtocolContext):
    # ==============================================================================
    # 0. Load AI Agent Configuration
    # ==============================================================================
    try:
        with open('agent_protocol_config.json', 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        protocol.pause("Configuration file not found. Ensure the AI Agent tool was run first.")
        return

    total_mix_volume = config.get('total_mix_volume', 200)
    gradient_targets = config.get('jobs', [])

    if not gradient_targets:
        protocol.comment("No print jobs found in configuration.")
        return

    # ==============================================================================
    # 1. Hardware & Labware Setup
    # ==============================================================================
    tips = protocol.load_labware('opentrons_96_tiprack_300ul', '1')
    source_reservoir = protocol.load_labware('nest_12_reservoir_15ml', '2') 
    mix_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', '3')
    paper_substrate = protocol.load_labware('corning_96_wellplate_360ul_flat', '4') 
    
    pipette = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tips])

    # ==============================================================================
    # 2. Location Definitions
    # ==============================================================================
    nano_stock = source_reservoir['A1']
    diluent = source_reservoir['A2']

    # ==============================================================================
    # 3. Dynamic Execution Sequence
    # ==============================================================================
    for target in gradient_targets:
        fraction = target['fraction']
        print_volume = target['print_volume']
        
        # Map the string (e.g., 'A1') to the actual labware well object
        mix_well = mix_plate[target['mix_well']]
        print_well = paper_substrate[target['print_well']]

        vol_nano = total_mix_volume * fraction
        vol_diluent = total_mix_volume - vol_nano
        
        protocol.comment(f"Agent Task: Preparing {fraction*100}% concentration...")

        # Step A: Transfer Diluent first 
        if vol_diluent > 0:
            pipette.transfer(
                vol_diluent, diluent, mix_well, 
                new_tip='once' 
            )
        
        # Step B: Transfer Nanoparticles and Mix
        if vol_nano > 0:
            pipette.transfer(
                vol_nano, nano_stock, mix_well, 
                mix_after=(3, total_mix_volume / 2), 
                new_tip='always' 
            )
        
        # Step C: Print/Spot onto the Paper
        protocol.comment(f"Agent Task: Printing {print_volume}uL to {target['print_well']}...")
        pipette.transfer(
            print_volume, mix_well, print_well,
            new_tip='always', 
            touch_tip=True    
        )

    protocol.comment("Dynamic printing protocol complete.")