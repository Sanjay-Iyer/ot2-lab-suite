"""Alternating liquid-handling test for P20 single and P300 multi GEN2.

Physical setup (use water only):
  - Slot 1: Opentrons 20 uL tip rack (tips A1 and B1 present)
  - Slot 2: Opentrons 300 uL tip rack (full tip columns 1 and 2 present)
  - Slot 3: 12-channel reservoir (at least 1 mL water in A1)
  - Slot 4: Corning 96-well plate
      - At least 250 uL water in every well of column 1 (A1-H1)
      - Empty wells in columns 2 and 4, plus empty wells A3 and A5
  - Left mount: P20 Single-Channel GEN2
  - Right mount: P300 8-Channel GEN2

The command order deliberately switches mounts:
  P20 -> 10 uL into A3
  P300 -> 100 uL per well from column 1 to column 2
  P20 -> 10 uL into A5
  P300 -> 100 uL per well from column 1 to column 4
"""

from opentrons import protocol_api


metadata = {
    "protocolName": "P20 Single and P300 Multi GEN2 Alternating Test",
    "author": "ot2-lab-suite",
    "description": "Alternate twice between P20 single and P300 multi transfers.",
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}

P20_MOUNT = "left"
P300_MOUNT = "right"


def _transfer_with_fresh_tips(pipette, volume_ul: float, source, destination) -> None:
    """Use a fresh single tip or eight-tip column for one transfer."""
    pipette.pick_up_tip()
    pipette.aspirate(volume_ul, source.bottom(z=2))
    pipette.dispense(volume_ul, destination.bottom(z=2))
    pipette.blow_out(destination.top(z=-2))
    pipette.drop_tip()


def run(protocol: protocol_api.ProtocolContext) -> None:
    p20_tiprack = protocol.load_labware("opentrons_96_tiprack_20ul", "1")
    p300_tiprack = protocol.load_labware("opentrons_96_tiprack_300ul", "2")
    reservoir = protocol.load_labware("nest_12_reservoir_15ml", "3")
    plate = protocol.load_labware("corning_96_wellplate_360ul_flat", "4")

    p20 = protocol.load_instrument(
        "p20_single_gen2", P20_MOUNT, tip_racks=[p20_tiprack]
    )
    p300 = protocol.load_instrument(
        "p300_multi_gen2", P300_MOUNT, tip_racks=[p300_tiprack]
    )

    p20_source = reservoir["A1"]
    p300_source = plate["A1"]  # A1 anchor means the full A1-H1 column.
    steps = (
        ("P20", p20, 10, p20_source, plate["A3"], "well A3"),
        ("P300", p300, 100, p300_source, plate["A2"], "column 2 (A2-H2)"),
        ("P20", p20, 10, p20_source, plate["A5"], "well A5"),
        ("P300", p300, 100, p300_source, plate["A4"], "column 4 (A4-H4)"),
    )

    protocol.comment("Starting alternating P20/P300 GEN2 test using water.")
    for step_number, step in enumerate(steps, 1):
        name, pipette, volume_ul, source, destination, target_name = step
        protocol.comment(
            f"Step {step_number}/4: {name} transferring {volume_ul} uL "
            f"to {target_name}."
        )
        _transfer_with_fresh_tips(pipette, volume_ul, source, destination)

    protocol.comment("Alternating test complete: P20 -> P300 -> P20 -> P300.")
