from opentrons import protocol_api

metadata = {
    "protocolName": "Simple OT-2 Simulation Test",
    "author": "Sanjay",
    "description": "Basic local simulation test",
    "apiLevel": "2.15"
}

def run(protocol: protocol_api.ProtocolContext):
    tiprack = protocol.load_labware("opentrons_96_tiprack_300ul", "1")
    plate = protocol.load_labware("corning_96_wellplate_360ul_flat", "2")
    reservoir = protocol.load_labware("nest_12_reservoir_15ml", "3")

    pipette = protocol.load_instrument(
        "p300_single_gen2",
        "right",
        tip_racks=[tiprack]
    )

    pipette.pick_up_tip()
    pipette.aspirate(100, reservoir["A1"])
    pipette.dispense(100, plate["A1"])
    pipette.blow_out()
    pipette.drop_tip()