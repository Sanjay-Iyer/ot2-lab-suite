# Workflow 01 — LLM Guide (start here)

You are working on **Workflow 01: Vial Dilution → Paper Print**, the single active
production workflow. Everything in this family shares the `01_` number.

## Which file do I edit?
| Goal | Edit this |
|---|---|
| Change what/where is printed, volumes, pipettes, materials | a **config** `configs/printing/01_vial_dilution_paper_print.{p20_only,p300_only,mixed}.yaml` (copy the closest one) |
| Change motion logic / dispatch | the protocol `src/protocols/printing/01_vial_dilution_paper_print.py` (NOT the embedded `CONFIG` block, NOT generated files) |
| Change a validation rule | the ONE authoritative module in `src/core/` (`pipette_selection`, `print_groups`, `materials`, `workflow_config`) — never duplicate a rule |
| Add a labware definition | source YAML in `configs/labware/`, then `scripts/generate_labware.py` (keep filename == loadName) |

## Key facts an LLM needs
- **Materials → vials:** `materials:` maps a name → `{role, labware, vial, initial_volume_ul}`.
  Active rack loadName is `tuberack_3dprint_20ml_8vials_v2` (wells A1–A4, B1–B4). Water/
  ethanol/stocks are configured, never hardcoded.
- **Dilution wells:** `dilution_plan.series[].destination_column` sets which 96-well column
  each material's series fills; `factors` → rows A..H.
- **Print destinations:** `print_groups[].destination.paper_start_column` + `replicates`
  occupy consecutive paper columns (1..12); duplicates and out-of-bounds are rejected.
- **Pipette selection:** `pipette: auto` → ≤20 µL picks `p20_single_gen2`, >20 µL picks
  `p300_multi_gen2`; or name one explicitly. Resolved at build time.
- **single_spot vs column_8up:** P20 prints one droplet at a time (`single_spot`); P300
  prints 8 at once (`column_8up`). Never an 8-nozzle layout on the P20.
- **Tip reuse:** each group reuses its `tips.well` (P20) / `tips.block_column` (P300) and
  returns it to its own rack; P20 and P300 tip state is independent.
- **Imaging:** exactly one image before + one after (mocked in simulation).

## Build / validate / simulate
```bash
python scripts/build_vial_dilution_print.py --config configs/printing/01_vial_dilution_paper_print.mixed.yaml
python -m pytest tests/printing/
```
`SIMULATION OK` = pass. Failure interpretation + exact criteria:
`skills/printing_workflow/VALIDATION_AND_SIMULATION.md`.

## What runs at home vs needs the physical robot
- **Home computer (this environment):** editing, unit tests, build, Opentrons simulation,
  YAML/labware validation. NO instruments attached.
- **Work laptop + OT-2 (later):** real calibration, tip pickup/return, liquid handling,
  clearances/collisions, camera. Follow `docs/printing/WORK_LAPTOP_PHYSICAL_VALIDATION.md`.
  Never claim these were done from the home computer.

## Do-not-use / archived
`archive/**` (see `archive/ARCHIVE_MANIFEST.md`). Uncertain-but-active files:
`docs/printing/ARCHIVE_REVIEW_REQUIRED.md`.

Full skill index: `skills/printing_workflow/SKILL.md`.
