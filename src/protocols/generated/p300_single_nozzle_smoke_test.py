"""API 2.28 dry-motion test for one nozzle of an OT-2 P300 multi."""
from opentrons import protocol_api
from opentrons.protocol_api import SINGLE


metadata = {
    "protocolName": "P300 Multi Single-Nozzle Dry-Motion Test",
    "author": "ot2-lab-suite",
    "description": (
        "Pick one P300 tip, visit all eight vial positions plus plate and paper, "
        "perform dry-air strokes, and return the tip."
    ),
    "apiLevel": "2.28",
}

TUBERACK_LOAD_NAME = "tuberack_3dprint_20ml_8vials_v2"
TUBERACK_SLOT = "7"
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
SINGLE_START = "A1"
PICKUP_TIP = "H1"

VIAL_WELLS = ("A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4")
PLATE_WELLS = ("A1", "A12", "H1", "H12")
PAPER_WELLS = ("A1", "A12", "H1", "H12")
VIAL_CLEARANCE_MM = 10.0
PLATE_CLEARANCE_MM = 5.0
PAPER_CLEARANCE_MM = 10.0
VISIT_DWELL_SECONDS = 1.0
DRY_AIR_VOLUME_UL = 30.0


def run(protocol: protocol_api.ProtocolContext) -> None:
    tuberack = protocol.load_labware(
        TUBERACK_LOAD_NAME,
        TUBERACK_SLOT,
        namespace=CUSTOM_NAMESPACE,
        version=CUSTOM_VERSION,
    )
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

    p300.configure_nozzle_layout(
        style=SINGLE,
        start=SINGLE_START,
        tip_racks=[tiprack],
    )
    p300.default_speed = 40
    p300.flow_rate.aspirate = 20
    p300.flow_rate.dispense = 20

    protocol.comment(
        f"P300 SINGLE-nozzle dry test: start={SINGLE_START}; "
        f"one tip from slot {TIPRACK_SLOT} {PICKUP_TIP}."
    )
    protocol.comment(
        "No liquid. Stop immediately if more than one tip engages or any nozzle "
        "approaches labware unexpectedly."
    )
    p300.pick_up_tip(tiprack[PICKUP_TIP])

    for well_name in VIAL_WELLS:
        target = tuberack[well_name].top(z=VIAL_CLEARANCE_MM)
        protocol.comment(
            f"Vial alignment check: slot {TUBERACK_SLOT} {well_name}, "
            f"{VIAL_CLEARANCE_MM} mm above the vial top."
        )
        p300.move_to(target)
        protocol.delay(seconds=VISIT_DWELL_SECONDS)

    for well_name in PLATE_WELLS:
        target = plate[well_name].top(z=PLATE_CLEARANCE_MM)
        protocol.comment(
            f"Plate alignment check: slot {PLATE_SLOT} {well_name}, "
            f"{PLATE_CLEARANCE_MM} mm above the well top."
        )
        p300.move_to(target)
        protocol.delay(seconds=VISIT_DWELL_SECONDS)

    # Aspirate only air above an empty plate well, then split the dry dispense
    # stroke between the plate and paper. The active tip never enters labware.
    plate_air_target = plate["A1"].top(z=PLATE_CLEARANCE_MM)
    p300.aspirate(DRY_AIR_VOLUME_UL, plate_air_target)
    p300.dispense(DRY_AIR_VOLUME_UL / 2, plate_air_target)

    for well_name in PAPER_WELLS:
        target = paper[well_name].top(z=PAPER_CLEARANCE_MM)
        protocol.comment(
            f"Paper alignment check: slot {PAPER_SLOT} {well_name}, "
            f"{PAPER_CLEARANCE_MM} mm above the paper reference."
        )
        p300.move_to(target)
        protocol.delay(seconds=VISIT_DWELL_SECONDS)

    p300.dispense(
        DRY_AIR_VOLUME_UL / 2,
        paper["A1"].top(z=PAPER_CLEARANCE_MM),
    )
    protocol.comment("Returning the one tip while still in SINGLE-nozzle mode.")
    p300.return_tip()
    protocol.comment(
        "P300 single-nozzle dry-motion smoke test complete; no liquid moved."
    )
