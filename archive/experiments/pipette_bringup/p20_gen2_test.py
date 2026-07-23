"""Small liquid-handling test for a newly installed P20 Single-Channel GEN2.

Physical setup (use water only):
  - Slot 1: Opentrons 20 uL tip rack (tip A1 present)
  - Slot 3: 12-channel reservoir (at least 1 mL water in A1)
  - Slot 4: Corning 96-well plate (empty wells A1-A3)
  - Left mount: P20 Single-Channel GEN2

The protocol picks up one tip and transfers 10 uL of water into each of A1-A3.
"""

from opentrons import protocol_api


metadata = {
    "protocolName": "P20 GEN2 Water Transfer Test",
    "author": "ot2-lab-suite",
    "description": "Test P20 GEN2 tip pickup and three 10 uL water transfers.",
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}

P20_MOUNT = "left"
TRANSFER_VOLUME_UL = 10


def run(protocol: protocol_api.ProtocolContext) -> None:
    tiprack = protocol.load_labware("opentrons_96_tiprack_20ul", "1")
    reservoir = protocol.load_labware("nest_12_reservoir_15ml", "3")
    plate = protocol.load_labware("corning_96_wellplate_360ul_flat", "4")
    p20 = protocol.load_instrument(
        "p20_single_gen2", P20_MOUNT, tip_racks=[tiprack]
    )

    source = reservoir["A1"]
    destinations = [plate[well_name] for well_name in ("A1", "A2", "A3")]

    protocol.comment("P20 test: transferring water from reservoir A1 to plate A1-A3.")
    p20.pick_up_tip()
    for destination in destinations:
        p20.aspirate(TRANSFER_VOLUME_UL, source.bottom(z=2))
        p20.dispense(TRANSFER_VOLUME_UL, destination.bottom(z=2))
        p20.blow_out(destination.top(z=-2))
    p20.drop_tip()
    protocol.comment("P20 GEN2 test complete: three 10 uL transfers performed.")
