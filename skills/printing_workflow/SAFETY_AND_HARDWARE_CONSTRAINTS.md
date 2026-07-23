# Safety & Hardware Constraints

## Pipettes (fixed hardware)
- **Right mount:** `p300_multi_gen2` (8-channel, 20–300 µL). Does dilution transfers,
  8-up mixing, and `column_8up` printing.
- **Left mount:** `p20_single_gen2` (single-channel, 1–20 µL). Does `single_spot`
  printing only. **Never configure an 8-nozzle layout on the P20** (it is single-channel).

## Genuine hardware limitations (design implications)
- A single-channel P20 **cannot** perform the 200 µL dilution transfers → the P300 always
  prepares dilutions. "P20-only" means P20-only *printing*, with the P300 mounted for prep
  (or `dilution_plan.enabled: false` and a pre-filled plate).
- The P20 needs its **own 20 µL tip rack** (`opentrons_96_tiprack_20ul`) — it cannot use
  300 µL tips. Mixed/P20 runs therefore need two tip racks on the deck.
- The 20–30 µL band has no accurate pipette below the P300's 30 µL recommended minimum;
  `auto` will pick the P300 for 21–29 µL but emit a low-accuracy warning.

## Deck collision rule (verified in simulation)
The P300's single-nozzle (A1) dilution configuration extends its idle nozzles **forward**.
Any labware in the slot **directly in front** of a slot the P300 visits collides:
- slot 1 (in front of plate slot 4) and slot 6 (in front of tiprack slot 9) must stay clear.
- The 20 µL rack is placed in **slot 2** (verified collision-free; slots 3/8/11 also work).
Simulation catches this as `PartialTipMovementNotAllowedError` — always re-simulate after
changing any slot.

## Tip handling / contamination
- Setup tips (dilution) are unique H-row tips, one per material, never shared → no colour
  carryover. They must not sit in a print block column.
- Each print group reuses its own tip/block and returns it to its own rack. P20 and P300
  tip state is independent.

## What simulation DOES and does NOT verify
- **Does:** motion planning, deck collisions, labware/well existence, nozzle layouts,
  volume/tip-state legality, protocol structure.
- **Does NOT:** real-world calibration. The following require **physical OT-2 verification**
  (do not trust simulated values):
  - Dispense Z-height / paper standoff (`print_groups[].dispense.z_mm`) — confirm with
    Labware Position Check against the actual paper surface.
  - Real vial liquid levels and `aspirate_height_mm` vs. the meniscus.
  - Any NEW deck slot placement not already simulated here.
  - The paper fixture's real height (the paper is modelled as a 96-well plate for
    coordinates only).

## Do / Don't for automated edits
- **Do** edit YAML configs, `src/core/*` services, tests, and protocol motion logic.
- **Don't** hand-edit the embedded `CONFIG` block or generated files; **don't** rename
  labware JSONs (filename must equal `loadName`); **don't** claim physical validation that
  was only simulated.
