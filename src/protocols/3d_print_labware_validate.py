#!/usr/bin/env python3
"""
OT-2 Protocol: 3D-Print Labware Validate
========================================

Minimal liquid test for the custom 8-vial, 20 mL 3D-printed rack.

Goal:
  - Use the p300_multi_gen2 in SINGLE-nozzle mode.
  - Pick up exactly ONE tip.
  - Move 20 uL from rack A1 to A2 and mix.
  - Move 20 uL from rack A2 to A1 and mix.
  - Leave/dispense everything in A1, then return the one tip.

Physical layout:
  slot 7 = tuberack_3dprint_20ml_8vials_v2
  slot 9 = opentrons_96_tiprack_300ul
  right mount = p300_multi_gen2

Important tip-pick detail:
  The active nozzle is A1. Pick tip H1, not A1. With A1 active, picking from H1
  keeps the 7 idle nozzles hanging off the front edge of the tip rack instead of
  over the other tips in the column.
"""

from opentrons import protocol_api
from opentrons.protocol_api import SINGLE

metadata = {
    "protocolName": "3D Print Labware Validate - One Tip A1 A2 Mix",
    "author": "ot2-lab-suite",
    "description": "One-tip validation of the custom 20 mL 3D printed vial rack.",
    "apiLevel": "2.28",
}

TUBERACK_SLOT = 7
TUBERACK_NAME = "tuberack_3dprint_20ml_8vials_v2"
TIPRACK_SLOT = 9
TIPRACK_NAME = "opentrons_96_tiprack_300ul"
NAMESPACE = "custom_beta"
VERSION = 1

PIPETTE_NAME = "p300_multi_gen2"
PIPETTE_MOUNT = "right"
SINGLE_START = "A1"
PICKUP_TIP = "H1"

SOURCE_WATER = "A1"
SOURCE_DYE = "A2"
VOLUME_UL = 20.0
MIX_REPS = 2
ASPIRATE_HEIGHT_MM = 10.0
DISPENSE_HEIGHT_MM = 20.0


def run(protocol: protocol_api.ProtocolContext):
    rack = protocol.load_labware(
        TUBERACK_NAME,
        TUBERACK_SLOT,
        namespace=NAMESPACE,
        version=VERSION,
    )
    tiprack = protocol.load_labware(TIPRACK_NAME, TIPRACK_SLOT)
    pipette = protocol.load_instrument(PIPETTE_NAME, PIPETTE_MOUNT, tip_racks=[tiprack])

    pipette.configure_nozzle_layout(
        style=SINGLE,
        start=SINGLE_START,
        tip_racks=[tiprack],
    )
    protocol.comment(
        f"SINGLE-nozzle layout active: start={SINGLE_START}. "
        f"Will pick exactly one tip from {PICKUP_TIP}."
    )
    protocol.comment(
        "If more than one tip starts to engage, stop immediately and do not continue."
    )

    a1 = rack[SOURCE_WATER]
    a2 = rack[SOURCE_DYE]

    pipette.pick_up_tip(tiprack[PICKUP_TIP])
    protocol.comment(f"Picked up one tip from tiprack {PICKUP_TIP}.")

    protocol.comment(f"Move {VOLUME_UL} uL from vial A1 to vial A2.")
    pipette.aspirate(VOLUME_UL, a1.bottom(ASPIRATE_HEIGHT_MM))
    pipette.dispense(VOLUME_UL, a2.bottom(DISPENSE_HEIGHT_MM))

    protocol.comment(f"Mix vial A2: {MIX_REPS} x {VOLUME_UL} uL.")
    pipette.mix(MIX_REPS, VOLUME_UL, a2.bottom(ASPIRATE_HEIGHT_MM))

    protocol.comment(f"Move {VOLUME_UL} uL from vial A2 back to vial A1.")
    pipette.aspirate(VOLUME_UL, a2.bottom(ASPIRATE_HEIGHT_MM))
    pipette.dispense(VOLUME_UL, a1.bottom(DISPENSE_HEIGHT_MM))

    protocol.comment(f"Mix vial A1: {MIX_REPS} x {VOLUME_UL} uL.")
    pipette.mix(MIX_REPS, VOLUME_UL, a1.bottom(ASPIRATE_HEIGHT_MM))

    if pipette.current_volume > 0:
        protocol.comment(f"Final dispense of remaining {pipette.current_volume} uL into vial A1.")
        pipette.dispense(pipette.current_volume, a1.bottom(DISPENSE_HEIGHT_MM))

    protocol.comment("Returning the single tip.")
    pipette.return_tip()
    protocol.comment("=== 3D print labware one-tip validation complete ===")
