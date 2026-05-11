"""
6-Sample 1:4 Dilution Protocol — OT-2

Deck layout:
  Slot 1  : source plate   (samples in A1-A6)
  Slot 2  : destination plate (empty, will receive diluted samples)
  Slot 3  : water reservoir (water in A1)
  Slot 10 : 300 uL tip rack
  Slot 12 : fixed trash (built-in)

Workflow:
  1. Home the gantry.
  2. Transfer 50 uL sample from slot 1 -> slot 2 (well-by-well, fresh tip each time).
  3. Add 150 uL water from slot 3 A1 to each destination well (one tip, top-dispense
     so the tip never touches the diluent and can be reused).
  4. Mix each destination well 3x with 100 uL (fresh tip per well).

Final volume per dest well: 200 uL  (50 uL sample + 150 uL water  =>  4x dilution)
"""

from opentrons import protocol_api

metadata = {
    "protocolName": "6-Sample 1:4 Dilution",
    "author": "Lab Automation",
    "description": "Transfer 6 samples from slot 1 to slot 2, dilute 1:4 with water from slot 3, mix.",
}

requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


def run(protocol: protocol_api.ProtocolContext) -> None:
    # ---------- Labware ----------
    tiprack = protocol.load_labware("opentrons_96_tiprack_300ul", location=10)
    source_plate = protocol.load_labware("nest_96_wellplate_200ul_flat", location=1)
    dest_plate = protocol.load_labware("nest_96_wellplate_200ul_flat", location=2)
    reservoir = protocol.load_labware("nest_1_reservoir_195ml", location=3)

    # ---------- Pipette ----------
    pipette = protocol.load_instrument(
        "p300_single_gen2", mount="right", tip_racks=[tiprack]
    )

    # ---------- Parameters ----------
    SAMPLE_VOLUME_UL = 50
    WATER_VOLUME_UL = 150
    MIX_VOLUME_UL = 100
    MIX_REPETITIONS = 3
    SAMPLE_WELLS = ["A1", "A2", "A3", "A4", "A5", "A6"]

    # ---------- Step 0: Home ----------
    protocol.comment("=== Step 0: Homing gantry ===")
    protocol.home()

    # ---------- Step 1: Transfer samples slot 1 -> slot 2 ----------
    # Fresh tip per sample to prevent cross-contamination.
    protocol.comment("=== Step 1: Transferring 6 samples from slot 1 to slot 2 ===")
    for well in SAMPLE_WELLS:
        pipette.pick_up_tip()
        pipette.aspirate(SAMPLE_VOLUME_UL, source_plate[well].bottom(z=1))
        pipette.touch_tip(source_plate[well])
        pipette.dispense(SAMPLE_VOLUME_UL, dest_plate[well].bottom(z=1))
        pipette.blow_out(dest_plate[well].top(z=-2))
        pipette.drop_tip()
        protocol.comment(f"  -> sample {well} transferred")

    # ---------- Step 2: Add water from slot 3 A1 to each dest well ----------
    # Single tip is safe here because we dispense from the well top (no contact
    # with the partially-diluted samples). This saves 5 tips.
    protocol.comment("=== Step 2: Adding 150 uL water to each diluted well ===")
    pipette.pick_up_tip()
    for well in SAMPLE_WELLS:
        pipette.aspirate(WATER_VOLUME_UL, reservoir["A1"])
        pipette.dispense(WATER_VOLUME_UL, dest_plate[well].top(z=-2))
        pipette.blow_out(dest_plate[well].top(z=-2))
        protocol.comment(f"  -> water added to {well}")
    pipette.drop_tip()

    # ---------- Step 3: Mix each diluted well ----------
    # Fresh tip per well so the mix step doesn't shuttle sample between wells.
    protocol.comment("=== Step 3: Mixing each diluted well ===")
    for well in SAMPLE_WELLS:
        pipette.pick_up_tip()
        pipette.mix(
            repetitions=MIX_REPETITIONS,
            volume=MIX_VOLUME_UL,
            location=dest_plate[well].bottom(z=2),
        )
        pipette.blow_out(dest_plate[well].top(z=-2))
        pipette.drop_tip()
        protocol.comment(f"  -> {well} mixed {MIX_REPETITIONS}x")

    protocol.comment("=== Protocol complete ===")