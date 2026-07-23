# Workflow 01 — Work-Laptop Physical Validation Checklist

Run this **on the instrument-connected work laptop**, after pulling the branch. None of
these steps were (or can be) done on the home computer — the home computer has **no**
OT-2, camera, or fixtures. Do not mark any step complete from the home computer.

> Home computer did: edit → unit tests → build → **simulation** → audit → commit → push.
> Work laptop does: pull → inspect → **simulate again** → **physical OT-2 tests** (below).

## 0. Get the exact code
1. **Pull the branch** (this work was committed on branch **`bp`**, tracking `origin/bp`):
   ```
   git fetch origin
   git checkout bp
   git pull --ff-only origin bp
   ```
2. **Confirm the commit hash** matches the one in the delivered report: `git rev-parse HEAD`.
3. **Confirm a clean working tree:** `git status` shows nothing to commit.

## 1. Re-validate in software (still no liquids)
4. **Verify custom labware loads:** build + simulate all three configs and confirm
   `SIMULATION OK`:
   ```
   python scripts/build_vial_dilution_print.py --config configs/printing/01_vial_dilution_paper_print.p20_only.yaml
   python scripts/build_vial_dilution_print.py --config configs/printing/01_vial_dilution_paper_print.p300_only.yaml
   python scripts/build_vial_dilution_print.py --config configs/printing/01_vial_dilution_paper_print.mixed.yaml
   ```
5. **Verify deck slots** match the physical setup: tuberack=7, plate=4, paper=5,
   tiprack_p300=9, tiprack_p20=2. Slots 1 and 6 **must be empty** (P300 partial-tip envelope).

## 2. Instrument + calibration checks (Opentrons App)
6. **Verify left mount = `p20_single_gen2`.**
7. **Verify right mount = `p300_multi_gen2`.**
8. **Verify pipette offsets and deck calibration** are current in the App (re-run if stale).

## 3. Dry / conservative physical checks (start here — smallest blast radius)
9. **Dry run without liquids** where safe (App runtime `dry_run = true`): confirm motion
   planning, no unexpected contact.
10. **Test P20 tip pickup and return** (20 µL rack, slot 2): one pick + return; confirm the
    tip seats and returns to the same well.
11. **Test P300 tip pickup and return** (300 µL rack, slot 9): one 8-tip block pick + return.
12. **Test one conservative vial aspiration** (small volume, high `aspirate_height_mm`):
    confirm the tip does not bottom out or hit the rack.
13. **Test one dilution transfer** (one well): confirm volume and no splashing.
14. **Test one P20 paper spot** (single_spot): confirm standoff `dispense.z_mm` places the
    tip just above the paper, no scraping.
15. **Test one P300 eight-up print** (column_8up): confirm all 8 nozzles clear the paper
    fixture and the walls.
16. **Confirm beginning and ending images** are captured and stored (one `before`, one
    `after`) and that the camera does not block gantry motion.

## 4. Limited then full
17. **Run a limited mixed workflow** (few dilutions, few prints) end-to-end.
18. **Record** collisions, offsets, splashing, dripping, and source-depth issues in a run log.
19. **Run the full workflow only after** the limited test passes cleanly.

## STOP immediately (power off / E-stop) if you observe:
- **Unexpected contact** between any tip/nozzle and labware, rack, paper, or deck.
- **Tip-rack misalignment** (P20 20 µL or P300 300 µL) — tips not seating squarely.
- **Vial-rack misalignment** — tip not centered over a vial, or contacting the rim.
- **Paper-fixture contact** — a tip scraping or pressing the paper/fixture.
- **Pipette offset errors** — moves land off-target vs. the App calibration.
- **Liquid aspiration failures** — air-only aspiration, or the tip below the meniscus/bottom.
- **Tip return failures** — a tip not returning to its origin well, or dropping.
- **Camera blocking robot motion** — the camera or its mount in the gantry path.

## Things simulation already checked (so you can focus on the physical)
Motion planning, deck collisions (incl. the P300 partial-tip envelope), labware/well
existence, nozzle layouts, volume/tip-state legality, and protocol structure. Simulation
does **not** verify real calibration, liquid behavior, or fixture geometry — that is what
this checklist is for.
