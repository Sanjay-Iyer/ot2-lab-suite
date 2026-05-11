from opentrons import protocol_api

metadata = {
    'protocolName': 'Automated Nanoparticle Gradient Printing',
    'author': 'Automated Lab Systems',
    'description': 'Dilutes nanoparticle stock and spots onto a substrate matrix',
    'apiLevel': '2.14'
}

def run(protocol: protocol_api.ProtocolContext):
    # ==============================================================================
    # 1. Hardware & Labware Setup
    # ==============================================================================
    tips = protocol.load_labware('opentrons_96_tiprack_300ul', '1')
    
    # Reservoir holding your Nanoparticles (A1) and Diluent (A2)
    source_reservoir = protocol.load_labware('nest_12_reservoir_15ml', '2') 
    
    # Staging area to mix the dilutions before printing
    mix_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', '3')
    
    # The "Paper" target (modeled as a 96-well grid for precise XYZ coordinates)
    paper_substrate = protocol.load_labware('corning_96_wellplate_360ul_flat', '4') 
    
    # Define the pipette
    pipette = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tips])

    # ==============================================================================
    # 2. Location & Parameter Definitions
    # ==============================================================================
    nano_stock = source_reservoir['A1']
    diluent = source_reservoir['A2']
    
    print_volume = 10  # uL to spot onto the paper
    total_mix_volume = 200 # Total uL per dilution batch
    
    # Defining the gradient: 100%, 75%, 50%, 25%, 10%
    # This structure is easy to parameterize if you want an LLM agent to pass dynamic arrays
    gradient_targets = [
        {'fraction': 1.00, 'mix_well': mix_plate['A1'], 'print_well': paper_substrate['A1']},
        {'fraction': 0.75, 'mix_well': mix_plate['A2'], 'print_well': paper_substrate['A2']},
        {'fraction': 0.50, 'mix_well': mix_plate['A3'], 'print_well': paper_substrate['A3']},
        {'fraction': 0.25, 'mix_well': mix_plate['A4'], 'print_well': paper_substrate['A4']},
        {'fraction': 0.10, 'mix_well': mix_plate['A5'], 'print_well': paper_substrate['A5']},
    ]

    # ==============================================================================
    # 3. Execution Sequence
    # ==============================================================================
    for target in gradient_targets:
        vol_nano = total_mix_volume * target['fraction']
        vol_diluent = total_mix_volume - vol_nano
        
        protocol.comment(f"Preparing {target['fraction']*100}% concentration...")

        # Step A: Transfer Diluent first (prevents tip contamination with stock)
        if vol_diluent > 0:
            pipette.transfer(
                vol_diluent, 
                diluent, 
                target['mix_well'], 
                new_tip='once' # Re-use tip for diluent to save consumables
            )
        
        # Step B: Transfer Nanoparticles and Mix
        if vol_nano > 0:
            pipette.transfer(
                vol_nano, 
                nano_stock, 
                target['mix_well'], 
                mix_after=(3, total_mix_volume / 2), # Pipette up/down 3 times to homogenize
                new_tip='always' 
            )
        
        # Step C: Print/Spot onto the Paper
        protocol.comment(f"Printing {print_volume}uL to target location...")
        pipette.transfer(
            print_volume,
            target['mix_well'],
            target['print_well'],
            new_tip='always', # Crucial: ensures no cross-contamination between varying concentrations
            touch_tip=True    # Touches the edge/paper to ensure the droplet detaches cleanly
        )

    protocol.comment("Printing protocol complete.")