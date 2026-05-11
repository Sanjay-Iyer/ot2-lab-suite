from opentrons import protocol_api

metadata = {
    "apiLevel": "2.15"
}

def run(protocol: protocol_api.ProtocolContext):
    # Define your labware and pipettes as usual
    tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', 1)
    plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 2)
    p300 = protocol.load_instrument('p300_single_gen2', 'left', tip_racks=[tiprack])

    # Commands
    p300.pick_up_tip()
    p300.aspirate(100, plate['A1'])
    p300.dispense(100, plate['B1'])
    p300.drop_tip()