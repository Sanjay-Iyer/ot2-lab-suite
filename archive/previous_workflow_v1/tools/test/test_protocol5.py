from opentrons import protocol_api

metadata = {"apiLevel": "2.16"}

def run(protocol: protocol_api.ProtocolContext):
    # Home the gantry first — establishes a known position
    protocol.home()
    
    # Now load labware and pipettes
    tips = protocol.load_labware("opentrons_96_tiprack_300ul", 1)
    reservoir = protocol.load_labware("nest_12_reservoir_15ml", 2)
    plate = protocol.load_labware("nest_96_wellplate_200ul_flat", 3)
    
    pipette = protocol.load_instrument("p300_single_gen2", "left", tip_racks=[tips])
    
    # Your actual protocol commands
    pipette.transfer(100, reservoir["A1"], plate.wells())