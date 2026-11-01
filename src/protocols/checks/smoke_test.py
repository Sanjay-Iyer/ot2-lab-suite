"""P20-only dry-motion validation for an empty plate and paper holder."""
from opentrons import protocol_api


metadata = {
    "protocolName": "P20 GEN2 Dry-Motion Smoke Test",
    "author": "ot2-lab-suite",
    "description": "One P20 tip, empty labware, dry air strokes only.",
    "apiLevel": "2.15",
}

TIPRACK_LOAD_NAME = "opentrons_96_tiprack_20ul"
TIPRACK_SLOT = "9"
PLATE_LOAD_NAME = "corning_96_wellplate_360ul_custom"
PLATE_SLOT = "4"
PAPER_LOAD_NAME = "corning_96_wellplate_360ul_custom"
PAPER_SLOT = "5"
CUSTOM_NAMESPACE = "custom_beta"
CUSTOM_VERSION = 1
PIPETTE_NAME = "p20_single_gen2"
PIPETTE_MOUNT = "left"
DRY_AIR_VOLUME_UL = 5.0
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
    p20 = protocol.load_instrument(
        PIPETTE_NAME,
        PIPETTE_MOUNT,
        tip_racks=[tiprack],
    )

    # Conservative travel and plunger speeds for the first physical check.
    p20.default_speed = 40
    p20.flow_rate.aspirate = 5
    p20.flow_rate.dispense = 5

    plate_target = plate["A1"].top(z=SAFE_PLATE_Z_MM)
    paper_target = paper["A1"].top(z=SAFE_PAPER_Z_MM)

    protocol.comment("P20-only dry test: no liquid; plate and paper must be empty.")
    p20.pick_up_tip(tiprack["A1"])
    protocol.comment("Picked up one 20 uL tip from slot 9 A1.")

    # Aspirate only air above empty plate A1, then split the dry dispense stroke
    # between the plate and paper positions. The tip never enters or touches either.
    p20.move_to(plate_target)
    p20.aspirate(DRY_AIR_VOLUME_UL, plate_target)
    p20.dispense(DRY_AIR_VOLUME_UL / 2, plate_target)
    p20.move_to(paper_target)
    protocol.comment(
        "P20 comparison position: slot 5 A1, pausing for visual alignment check."
    )
    protocol.delay(seconds=COMPARISON_DWELL_SECONDS)
    p20.dispense(DRY_AIR_VOLUME_UL / 2, paper_target)

    p20.return_tip()
    protocol.comment("P20 dry-motion smoke test complete; tip returned to slot 9 A1.")
