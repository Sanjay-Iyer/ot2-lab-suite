---
name: printing_workflow
description: The active OT-2 production workflow — aspirate materials from 20 mL vials, prepare/mix dilutions in a 96-well plate, and print droplets onto paper (96-well coordinate grid) with the P300 (8-up) and/or P20 (single-spot). Use for building, validating, simulating, or extending this workflow.
---

# Printing Workflow (vial → dilution → paper print)

This is the single active production workflow in this repository. An LLM agent should
treat the files below as tools.

## 1. Repository structure & entry points
Full map: [REPOSITORY_MAP.md](REPOSITORY_MAP.md) (mirrors `docs/printing/REPOSITORY_MAP.md`).

- **Primary protocol (entry point):** `src/protocols/printing/01_vial_dilution_paper_print.py`. It runs on
  the OT-2 from its embedded `CONFIG`. **Do not edit the `CONFIG` block by hand** — edit a
  YAML and rebuild. The Python IS safe to edit for motion/logic changes.
- **Generated (never edit):** `src/protocols/generated/vial_dilution_print_latest.py`.
- **Shared services (edit with care; one authoritative rule each):**
  `src/core/pipette_selection.py`, `src/core/print_groups.py`, `src/core/materials.py`,
  `src/core/workflow_config.py`.

## 2. Choosing / creating a configuration
Start from `configs/printing/01_vial_dilution_paper_print.<variant>.yaml` (see
`configs/printing/README.md`). Copy the
closest one and edit. The unified schema sections are visually distinct:
`deck / labware / pipettes / materials / dilution_plan / mixing_plan / print_groups /
imaging / tip_policy`. See [CONFIGURATION_GUIDE.md](CONFIGURATION_GUIDE.md).

## 3. Materials & 20 mL vials
`materials:` maps a name → `{role, labware, vial, initial_volume_ul, aspirate_height_mm?,
dead_volume_ul?}`. Steps reference materials by name; water/ethanol/stocks are **never
hardcoded in Python**. The active rack is `tuberack_3dprint_20ml_8vials_v2` (wells
A1–A4, B1–B4). Volume accounting is conservative (transfers × overage + dead volume).

## 4. Dilution & mixing locations
`dilution_plan:` sets `total_volume_ul`, `factors` (explicit/geometric/linear/log →
rows A..H), `water_material`, and `series[]` (each `{name, material, destination_column,
setup_tip}`). `mixing_plan:` sets `mix_reps`/`mix_volume_ul` (P300 8-up).

## 5. P20 vs P300 selection
`print_groups[].pipette` is `auto` or a pipette name. **auto** picks the smallest-capacity
mounted pipette that covers the volume: ≤20 µL → `p20_single_gen2`, >20 µL →
`p300_multi_gen2`. Ranges come from `configs/constraints/pipette_constraints.yaml`.
Selection runs at BUILD time (never inside the robot protocol).

## 6. single_spot vs column_8up
- `column_8up` → P300, 8 droplets at once (a plate column → a paper column).
- `single_spot` → P20, ONE droplet at a time (source wells map down paper rows;
  replicates sweep across paper columns). **Never** an 8-nozzle layout on the P20.

## 7. Tip reuse & return
Each group reuses its assigned tip (`tips.well` for P20) or 8-tip block
(`tips.block_column` for P300) and returns it to its own rack when `tips.return: true`.
P20 and P300 tip state is fully independent (separate instruments + separate racks:
300 µL rack for P300, 20 µL rack for P20).

## 8–12. Build, validate, simulate, interpret failures
See [VALIDATION_AND_SIMULATION.md](VALIDATION_AND_SIMULATION.md). Short version:
```bash
# build + validate + simulate (uses the `ai` conda env with the numpy.trapz shim)
python scripts/build_vial_dilution_print.py --config configs/printing/01_vial_dilution_paper_print.mixed.yaml
python -m pytest            # full unit suite (run from repo root)
```
Validation is authoritative in `src/core/*` (build script never re-implements a rule).
Simulation PASS = the build prints `SIMULATION OK`; FAIL = `SIMULATION FAILED` with the
matched error line. `opentrons.simulate` exits 0 even on runtime errors, so the verifier
scans output text (see the guide for the exact regex).

## 13. Beginning/ending images
`imaging: {capture_before, capture_after, robot_image_dir}` → one image before the run and
one after. On the robot they are captured via curl to the camera API; in simulation they
are mocked (`[SIMULATION] Mock photo: …`). CV is intentionally minimal (capture only).

## 14. Archived / do-not-use
See `archive/ARCHIVE_MANIFEST.md`. The old P20 bench tests are archived under
`archive/experiments/pipette_bringup/`. Family B (`printing_demo_*`) and legacy
generators are documented in `docs/printing/ARCHIVE_REVIEW_REQUIRED.md`.

## 15. Safe to edit by an LLM
YAML configs, `src/core/*` services (respecting the "one authoritative rule" principle),
tests, and the protocol's motion logic. Do NOT hand-edit generated files or the embedded
`CONFIG` block; do NOT rename labware JSONs (filename must equal `loadName`).

## 16. Requires physical OT-2 verification
See [SAFETY_AND_HARDWARE_CONSTRAINTS.md](SAFETY_AND_HARDWARE_CONSTRAINTS.md): any dispense
Z-height / paper standoff, real vial liquid levels, and any new deck slot placement must
be confirmed on the robot (Labware Position Check). Simulation validates motion planning
and collisions, NOT real-world calibration.
