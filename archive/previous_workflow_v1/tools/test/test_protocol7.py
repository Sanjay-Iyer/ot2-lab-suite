from opentrons import protocol_api

metadata = {
    "protocolName": "Flex Test Protocol",
    "author": "sanjay",
    "description": "Basic transfer test for Flex visualization",
}

requirements = {
    "robotType": "Flex",
    "apiLevel": "2.25",
}

def run(protocol: protocol_api.ProtocolContext):
    # Home the gantry first — establishes a known position
    protocol.home()
    
    # Load the trash bin (required on Flex, unlike OT-2 where it's fixed)
    trash = protocol.load_trash_bin("A3")
    
    # Load labware — Flex uses alphanumeric slot identifiers (A1-D4)
    tips = protocol.load_labware("opentrons_flex_96_tiprack_200ul", "D1")
    reservoir = protocol.load_labware("nest_12_reservoir_15ml", "D2")
    plate = protocol.load_labware("nest_96_wellplate_200ul_flat", "D3")
    
    # Load a Flex pipette (note: Flex uses different pipette names than OT-2)
    pipette = protocol.load_instrument(
        "flex_1channel_1000",
        "left",
        tip_racks=[tips],
    )
    
    # Transfer 100 µL from reservoir A1 to every well on the plate
    pipette.transfer(100, reservoir["A1"], plate.wells())