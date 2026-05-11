from opentrons import protocol_api

metadata = {
    'protocolName': 'Four Corners Calibration Test',
    'author': 'Your Name',
    'apiLevel': '2.14'
}

def run(protocol: protocol_api.ProtocolContext):
    # 1. Load your pipette. Matches the physical p300_multi_v2.1 hardware!
    pipette = protocol.load_instrument('p300_multi_gen2', 'right')

    # 2. Load "dummy" labware in the 4 corners of the deck (Slots 1, 3, 10, 11)
    corner_1 = protocol.load_labware('corning_96_wellplate_360ul_flat', '1')
    corner_3 = protocol.load_labware('corning_96_wellplate_360ul_flat', '3')
    corner_10 = protocol.load_labware('corning_96_wellplate_360ul_flat', '10')
    corner_11 = protocol.load_labware('corning_96_wellplate_360ul_flat', '11')

    print("Homing the robot...")
    protocol.home()

    # 3. Create a list of the locations to loop through
    corners = [
        ("Front-Left (Slot 1)", corner_1),
        ("Front-Right (Slot 3)", corner_3),
        ("Back-Left (Slot 10)", corner_10),
        ("Back-Right (Slot 11)", corner_11)
    ]

    # 4. Move to each corner safely
    for name, labware in corners:
        print(f"Moving to {name}...")
        
        # We tell the pipette to move to well A1 of the dummy plate, 
        # hovering 50mm safely above the top of the well.
        pipette.move_to(labware.wells_by_name()['A1'].top(50))
        
        # Pause for 2 seconds so you can see it arrive
        protocol.delay(seconds=2)

    print("Test complete! Returning home...")
    protocol.home()