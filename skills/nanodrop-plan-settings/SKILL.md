---
name: nanodrop-plan-settings
description: The other settings Agent NanoDrop may change in conversation - deck slots, vials and names, tips, liquid handling switches - what is lab-owned, and how physical reports are recorded.
domain: dye_demo
agent: nanodrop
order: 4
scopes: [plan]
---

# Plan settings

## Fields
- deck.plate.slot, deck.paper.slot, deck.tuberack.slot, deck.tiprack.slot: a slot 1-11, or "OFF_DECK" when the
  labware is physically removed.
- materials.sample.vial, materials.solvent.vial: A1-B4 on the 8-vial rack; materials.sample.label,
  materials.solvent.label: display names.
- tips.start_tip (A1-H12), tips.return_tips, tips.policy: "single_tip" (one tip for the whole run), "per_liquid" (one
  tip per liquid), "new_tip_every_transfer".
- liquid_handling.air_gap_ul, liquid_handling.blow_out, liquid_handling.well_plate_shake.enabled.

Edit mode for these is modify (one value replaces another); there is nothing to add or replace.

## Lab-owned - never change
The pipette, flow rates, safety limits, every height, the print release cycle (air gap, push-out, blow-out, dwell),
transfer size and labware types are set on the lab laptop. Explain that, and change nothing.

## Values
Never invent a slot, vial, tip, well, row, column or volume. Return impossible values exactly as said (slot 12, well
Z11): Python refuses them and says what exists.

## Physical reports
Something already done on the robot (a fresh plate, paper or tip rack is in place; a well was refilled) is a
physical_report with "physical_actions". A plan to do it later is not a report.
