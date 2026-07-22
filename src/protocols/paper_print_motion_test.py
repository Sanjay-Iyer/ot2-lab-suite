"""
src/protocols/paper_print_motion_test.py
=========================================
MOTION-ONLY dry test for the flat paper print surface (paper_print_96_flat).

WHAT IT DOES (no liquid is ever aspirated or dispensed):
  1. Loads the 300 uL tiprack in slot 8 and the paper surface in slot 5.
  2. Picks up ONE column of 8 tips with the p300 multi.
  3. Walks all 12 columns of the paper, descending to the print height at each
     one so you can watch the tips and confirm they clear the 14 mm outer walls
     and stop cleanly at ~7 mm above the paper.
  4. Returns the 8 tips to their original spots in the rack.

The point is to prove the geometry/heights are safe BEFORE any real printing.

Deck:
  slot 8 = opentrons_96_tiprack_300ul
  slot 5 = paper_print_96_flat   (custom_beta v1 — deploy it first with
           `python -m scripts.deploy --labware labware/paper_print_96_flat.json`)

Pipette: p300_multi_gen2, right mount (fixed hardware).
"""
from opentrons import protocol_api

metadata = {
    "protocolName": "Paper Print Motion Test (dry, 8-channel)",
    "author": "ot2-lab-suite",
    "description": "Pick up 8 tips, visit all 12 paper columns at print height, return tips. No liquid.",
}
requirements = {"robotType": "OT-2", "apiLevel": "2.16"}

# ── Deck layout ──────────────────────────────────────────────────────────────
TIPRACK_LOADNAME = "opentrons_96_tiprack_300ul"
TIPRACK_SLOT = "8"

PAPER_LOADNAME = "paper_print_96_flat"
PAPER_NAMESPACE = "custom_beta"
PAPER_VERSION = 1
PAPER_SLOT = "5"

PIPETTE_NAME = "p300_multi_gen2"
PIPETTE_MOUNT = "right"

# ── Motion tuning ────────────────────────────────────────────────────────────
# Paper surface is well bottom (z = 6.0 mm in the labware def). bottom(z=PRINT_Z_MM)
# is the real dispense standoff. PRINT_Z_MM = 1.0 -> 7 mm above the deck, i.e. 1 mm
# above the paper and well clear of the 14 mm walls (which the pipette only crosses
# while arcing between columns, above zDimension).
PRINT_Z_MM = 1.0
DWELL_SECONDS = 1.0  # pause at each column so you can eyeball the height


def run(protocol: protocol_api.ProtocolContext) -> None:
    # Guard against overlapping deck slots.
    if TIPRACK_SLOT == PAPER_SLOT:
        raise ValueError(f"Tiprack and paper share slot {TIPRACK_SLOT}.")

    tiprack = protocol.load_labware(TIPRACK_LOADNAME, TIPRACK_SLOT)
    paper = protocol.load_labware(
        PAPER_LOADNAME, PAPER_SLOT, namespace=PAPER_NAMESPACE, version=PAPER_VERSION,
    )

    protocol.comment(
        f"Loaded paper '{PAPER_LOADNAME}' in slot {PAPER_SLOT} "
        f"(namespace={PAPER_NAMESPACE}, version={PAPER_VERSION})."
    )

    pipette = protocol.load_instrument(PIPETTE_NAME, PIPETTE_MOUNT, tip_racks=[tiprack])

    protocol.comment("=== MOTION-ONLY TEST — no liquid will be handled ===")

    # 1) Pick up one column of 8 tips.
    pipette.pick_up_tip()
    protocol.comment("Picked up 8 tips from the 300 uL rack (slot 8).")

    # 2) Visit every paper column at print height. columns()[i][0] = the row-A well;
    #    the multi's 8 nozzles span A..H automatically. move_to arcs up over the
    #    14 mm walls between columns, then descends into the open cavity.
    for i, column in enumerate(paper.columns()):
        target = column[0].bottom(z=PRINT_Z_MM)
        pipette.move_to(target)
        protocol.comment(
            f"Column {i + 1}/12 at {column[0]} — tips at {PRINT_Z_MM} mm above paper (~7 mm above deck)."
        )
        if DWELL_SECONDS > 0:
            protocol.delay(seconds=DWELL_SECONDS)

    # 3) Return the 8 tips to the rack (not the trash) so nothing is consumed.
    pipette.return_tip()
    protocol.comment("Returned 8 tips to the rack. Motion test complete.")
