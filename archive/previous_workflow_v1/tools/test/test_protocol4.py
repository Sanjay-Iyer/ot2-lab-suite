from opentrons import protocol_api

metadata = {
    "protocolName": "Serial Dilution Tutorial",
    "description": """This protocol is the outcome of following the
        Python Protocol API Tutorial. It takes a solution and progressively
        dilutes it by transferring it stepwise across a plate.""",
    "author": "New API User"
}

requirements = {
    "robotType": "OT-2",
    "apiLevel": "2.16"
}


def run(protocol: protocol_api.ProtocolContext):
    # Load labware
    tips = protocol.load_labware(
        "opentrons_96_tiprack_300ul",
        "1"
    )

    reservoir = protocol.load_labware(
        "nest_12_reservoir_15ml",
        "2"
    )

    plate = protocol.load_labware(
        "nest_96_wellplate_200ul_flat",
        "3"
    )

    # Load pipette
    left_pipette = protocol.load_instrument(
        "p300_single_gen2",
        "left",
        tip_racks=[tips]
    )

    # Add 100 µL diluent from reservoir column 1 to every well of the plate
    left_pipette.transfer(
        100,
        reservoir["A1"],
        plate.wells()
    )

    # For each row, add solution to column 1, then dilute across columns 1–12
    for i in range(1):
        row = plate.rows()[i]

        # Add 100 µL solution from reservoir column 2 to the first well of the row
        left_pipette.transfer(
            100,
            reservoir["A2"],
            row[0],
            mix_after=(3, 50)
        )

        # Serially transfer 100 µL from column to column across the row
        left_pipette.transfer(
            100,
            row[:11],
            row[1:],
            mix_after=(3, 50)
        )