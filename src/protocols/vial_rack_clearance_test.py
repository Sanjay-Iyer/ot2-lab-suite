#!/usr/bin/env python3
"""
OT-2 Protocol: 3D-Print 20 mL Vial Rack — CLEARANCE / COLLISION TEST
====================================================================
Run this on the REAL robot BEFORE the dilution-print workflow to prove the custom
3D-printed vial rack (tuberack_3dprint_20ml_8vials_v2, slot 7) does not collide.

It reproduces the *exact* single-nozzle motion the dilution phase uses — partial
configuration on nozzle A1 (the BACK nozzle, so the 7 idle nozzles hang FORWARD,
away from the tall rack) — and walks the active tip to every vial and to the two
extreme plate wells, hovering then descending slowly so you can WATCH the clearance.

NO liquid is moved. One tip is picked up and returned. Keep a hand on the pause /
e-stop the first time. If anything looks like it will touch glass or the rack wall,
stop and re-check Labware Position Check / the slot layout.

RUN PATH (IMPORTANT): this uses apiLevel 2.28 partial-tip mode, which runs on the
new protocol engine. On this OT-2 (opentrons 9.0.0) run it from the **Opentrons App**
(import the labware JSONs + this file, then Run) so the engine gets a deck
configuration. Bare `opentrons_execute` over SSH fails with
`AreaNotInDeckConfigurationError` — that is expected. See skills/ot2-robot-profile.

DECK (must match the workflow):
  slot 7 = tuberack_3dprint_20ml_8vials_v2  (custom_beta v1)   ← the rack under test
  slot 4 = corning_96_wellplate_360ul_custom (custom_beta v1)  ← dilution plate
  slot 9 = opentrons_96_tiprack_300ul                          ← tips

Deploy the custom labware first (lab laptop):
  python -m scripts.deploy --labware labware/tuberack_3dprint_20ml_8vials_v2.json
  python -m scripts.deploy --labware labware/corning_96_wellplate_360ul_custom.json
Then:
  scp -O ... src/protocols/vial_rack_clearance_test.py root@<IP>:<remote>/
  ssh ... "opentrons_execute <remote>/vial_rack_clearance_test.py"
"""

from opentrons import protocol_api
from opentrons.protocol_api import SINGLE

metadata = {
    "protocolName": "3D-Print 20 mL Vial Rack — Clearance / Collision Test",
    "author": "ot2-lab-suite",
    "description": "Single-nozzle (A1) walk over every vial + plate extremes to "
                   "verify the custom vial rack clears with no collision. No liquid.",
    "apiLevel": "2.28",   # partial-nozzle config + return_tip()
}

# ── Must match the workflow YAML (configs/workflows/defaults/vial_dilution_print.yaml)
TUBERACK_SLOT   = 7
TUBERACK_NAME   = "tuberack_3dprint_20ml_8vials_v2"
PLATE_SLOT      = 4
PLATE_NAME      = "corning_96_wellplate_360ul_custom"
TIPRACK_SLOT    = 9
TIPRACK_NAME    = "opentrons_96_tiprack_300ul"
NAMESPACE       = "custom_beta"
VERSION         = 1

SINGLE_START    = "A1"     # back nozzle → idle nozzles hang forward, off the tall rack
PICKUP_TIP      = "A1"     # which tiprack well to take the single tip from

HOVER_MM        = 40.0     # safe hover above a well top before/after each approach
ENTER_DEPTH_MM  = 25.0     # how far ABOVE the vial floor to descend (vial is 55 mm deep)
PAUSE_S         = 2.0      # dwell so the operator can watch each position


def run(protocol: protocol_api.ProtocolContext):
    rack = protocol.load_labware(TUBERACK_NAME, TUBERACK_SLOT,
                                 namespace=NAMESPACE, version=VERSION)
    plate = protocol.load_labware(PLATE_NAME, PLATE_SLOT,
                                  namespace=NAMESPACE, version=VERSION)
    tiprack = protocol.load_labware(TIPRACK_NAME, TIPRACK_SLOT)
    pipette = protocol.load_instrument("p300_multi_gen2", "right", tip_racks=[tiprack])

    # Same partial configuration as the dilution phase.
    pipette.configure_nozzle_layout(style=SINGLE, start=SINGLE_START, tip_racks=[tiprack])
    protocol.comment(f"Single-nozzle layout: start={SINGLE_START} (back nozzle).")

    protocol.comment("Homing before the clearance walk...")
    protocol.home()

    # One tip on the active nozzle — this is what actually descends into the vials,
    # exactly like the real dilution. Idle nozzles are bare and hang forward.
    pipette.pick_up_tip(tiprack[PICKUP_TIP])
    protocol.comment(f"Picked up one tip from {TIPRACK_NAME}[{PICKUP_TIP}].")

    def walk(well, label, descend):
        protocol.comment(f"--- {label} ---")
        pipette.move_to(well.top(HOVER_MM))          # safe hover
        protocol.delay(seconds=PAUSE_S)
        pipette.move_to(well.top(0))                 # at the rim — check XY centering
        protocol.delay(seconds=PAUSE_S)
        if descend:
            pipette.move_to(well.bottom(ENTER_DEPTH_MM))  # inside the vial — check walls
            protocol.delay(seconds=PAUSE_S)
        pipette.move_to(well.top(HOVER_MM))          # retract
        protocol.delay(seconds=PAUSE_S)

    # 1) Every vial in the rack, column by column (A=back/top, B=front/bottom).
    protocol.comment("=== Walking all 8 vials in the rack (slot 7) ===")
    for col in rack.columns():
        for well in col:
            walk(well, f"vial {well.well_name}", descend=True)

    # 2) The two extreme dilution-plate wells (slot 4): A9 (back) and H9 (front).
    #    Confirms the single-nozzle dispense reach is clear at both ends of the column.
    protocol.comment("=== Hovering the plate column-9 extremes (slot 4) ===")
    for well_name in ("A9", "H9"):
        walk(plate[well_name], f"plate {well_name}", descend=False)

    protocol.comment("Returning tip and homing.")
    pipette.return_tip()
    protocol.home()
    protocol.comment("=== Vial-rack clearance test complete — no collision if you got here ===")
