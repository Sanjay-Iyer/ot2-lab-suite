"""P300-only dry-motion validation for an empty plate and paper holder."""
from opentrons import protocol_api


metadata = {
    "protocolName": "P300 GEN2 Dry-Motion Smoke Test",
    "author": "ot2-lab-suite",
    "description": "One P300 eight-tip column, empty labware, dry air strokes only.",
    "apiLevel": "2.15",
}

TIPRACK_LOAD_NAME = "opentrons_96_tiprack_300ul"
TIPRACK_SLOT = "8"
PLATE_LOAD_NAME = "corning_96_wellplate_360ul_custom"
PLATE_SLOT = "4"
PAPER_LOAD_NAME = "corning_96_wellplate_360ul_custom"
PAPER_SLOT = "5"
CUSTOM_NAMESPACE = "custom_beta"
CUSTOM_VERSION = 1
PIPETTE_NAME = "p300_multi_gen2"
PIPETTE_MOUNT = "right"
DRY_AIR_VOLUME_UL = 30.0
SAFE_PLATE_Z_MM = 2.0
SAFE_PAPER_Z_MM = 10.0
COMPARISON_DWELL_SECONDS = 5.0


def run(protocol: protocol_api.ProtocolContext) -> None:
    tiprack = protocol.load_labware(TIPRACK_LOAD_NAME, TIPRACK_SLOT)
    plate = protocol.load_labware(
        PLATE_LOAD_NAME,
        PLATE_SLOT,
        namespace=CUSTOM_NAMESPACE,
        version=CUSTOM_VERSION,
    )
    paper = protocol.load_labware(
        PAPER_LOAD_NAME,
        PAPER_SLOT,
        namespace=CUSTOM_NAMESPACE,
        version=CUSTOM_VERSION,
    )
    p300 = protocol.load_instrument(
        PIPETTE_NAME,
        PIPETTE_MOUNT,
        tip_racks=[tiprack],
    )

    # API 2.15-compatible conservative travel and plunger speeds.
    p300.default_speed = 40
    p300.flow_rate.aspirate = 20
    p300.flow_rate.dispense = 20

    plate_target = plate["A1"].top(z=SAFE_PLATE_Z_MM)
    paper_target = paper["A1"].top(z=SAFE_PAPER_Z_MM)

    protocol.comment(
        "P300-only dry test: no liquid; plate and paper must be empty."
    )
    # A multi-channel pickup at A1 engages the full first column, A1-H1.
    # No partial-nozzle configuration is used or required.
    p300.pick_up_tip(tiprack["A1"])
    protocol.comment("Picked up eight 300 uL tips from slot 8 A1-H1.")

    # Aspirate only air above empty plate column 1, then split the dry dispense
    # stroke between plate and paper column 1. Tips never enter or touch either.
    p300.move_to(plate_target)
    p300.aspirate(DRY_AIR_VOLUME_UL, plate_target)
    p300.dispense(DRY_AIR_VOLUME_UL / 2, plate_target)
    p300.move_to(paper_target)
    protocol.comment(
        "P300 comparison position: slot 5 A1, pausing for visual alignment check."
    )
    protocol.delay(seconds=COMPARISON_DWELL_SECONDS)
    p300.dispense(DRY_AIR_VOLUME_UL / 2, paper_target)

    p300.return_tip()
    protocol.comment(
        "P300 dry-motion smoke test complete; tips returned to slot 8 A1-H1."
    )
