# Active Workflows

The current production path in this repository. A `NN_` prefix identifies a complete
workflow family (protocol + configs + tests + docs share the number). `01_` is reserved
for the active vial→dilution→paper-print workflow; `02_`+ are for genuinely different
future workflows.

---

## Workflow 01 — Vial Dilution and Paper Printing
**Status: Active production workflow.**

Aspirate materials from custom 20 mL vials → prepare/mix dilutions in a 96-well plate →
print droplets onto paper (96-well coordinate grid) with the P300 (8-up) and/or the P20
(single-spot) → capture one image before and one after.

### Protocol (entry point)
`src/protocols/printing/01_vial_dilution_paper_print.py` — robot entry point; runs from its
embedded `CONFIG`. Do **not** hand-edit the `CONFIG` block; edit a YAML and rebuild.

### Configurations (the three primary examples)
- `configs/printing/01_vial_dilution_paper_print.p20_only.yaml`
- `configs/printing/01_vial_dilution_paper_print.p300_only.yaml`
- `configs/printing/01_vial_dilution_paper_print.mixed.yaml`
- Legacy flat config (still supported, auto-migrated): `configs/workflows/defaults/vial_dilution_print.yaml`

### Protocol versions
A config selects its base protocol with `protocol_version:` (default 1). Each version
generates under its own basename, so the two never overwrite each other.

| version | base protocol | generated artifact | dilution |
|---|---|---|---|
| 1 (default) | `src/protocols/printing/01_vial_dilution_paper_print.py` | `generated/vial_dilution_print_latest.py` | every vial→plate transfer on the P300 |
| 2 | `src/protocols/printing/02_vial_dilution_paper_print_p20_dilution.py` | `generated/vial_dilution_print_v2_latest.py` | transfers ≤ `dilution_plan.small_volume.threshold_ul` on the P20 |

v2 also adds a **pipette check** bring-up mode (`--pipette-check`): it picks up and
returns every tip the config uses on both mounts, visiting each labware at its working
height. Real motion, no liquid — it sits between the dry run (which moves nothing) and a
live run (which commits material), and is the step that proves tip pickup/return, labware
offsets, and the P20's reach into the 55 mm vials.

Printing behaviour is identical in both. v2 exists because the P300 is inaccurate below
~20 µL, which biases the most dilute points of a series. `scripts/validate_vial_print.py`
and `scripts/run_vial_print_robot.py` both read `protocol_version` from `--config` and
target the matching artifact.

### Experiment configs
- `configs/printing/bp_20260723.yaml` — BP (Au/Ag) nanoparticle stock, 8 dilutions
  (1/2/5/10/15/20/25/50x) in plate column 11 → paper columns 1-5 at 30 (P300), 20, 10, 5
  and 5 µL ×3 stacked droplets (P20). Deck: tiprack_p300=8, tiprack_p20=9, tuberack=7,
  plate=4, paper=5.
- `configs/printing/bp_20260723_v2.yaml` — the same experiment on protocol v2; the
  15/20/25/50x stock volumes (13.33/10/8/4 µL) run on the P20 instead of the P300.

### Custom labware (filenames must equal internal `loadName` — never renamed)
- `labware/tuberack_3dprint_20ml_8vials_v2.json` (active 20 mL vial rack; the un-versioned
  `tuberack_3dprint_20ml_8vials` name does **not** exist on disk)
- `labware/corning_96_wellplate_360ul_custom.json` (dilution plate + paper coordinate proxy)
- Standard, loaded by name: `opentrons_96_tiprack_300ul` (P300), `opentrons_96_tiprack_20ul` (P20)
- Association manifest: `labware/printing/01_vial_dilution_paper_print.labware.md`

### Builder / validator / simulation
```bash
# build (validate → embed → generate → simulate) — pick any of the three configs
python scripts/build_vial_dilution_print.py --config configs/printing/01_vial_dilution_paper_print.mixed.yaml
# validate + generate only (no simulation)
python scripts/build_vial_dilution_print.py --config configs/printing/01_vial_dilution_paper_print.mixed.yaml --no-sim
# multi-mode matrix over the generated protocol
python scripts/validate_vial_print.py
```
- Shared validators (one authoritative rule each): `src/core/pipette_selection.py`,
  `src/core/print_groups.py`, `src/core/materials.py`, `src/core/workflow_config.py`.

### Tests
`tests/printing/` (`test_pipette_selection.py`, `test_print_groups.py`, `test_materials.py`,
`test_workflow_config.py`). Run from repo root with the `ai` conda env:
```bash
python -m pytest tests/printing/     # workflow-01 unit tests
python -m pytest                     # full suite
```

### Documentation & skills
- `docs/printing/01_vial_dilution_paper_print.md` (workflow doc)
- `docs/printing/REPOSITORY_MAP.md`, `docs/printing/WORK_LAPTOP_PHYSICAL_VALIDATION.md`
- `skills/printing_workflow/` (`01_WORKFLOW_GUIDE.md`, `SKILL.md`, `CONFIGURATION_GUIDE.md`,
  `VALIDATION_AND_SIMULATION.md`, `SAFETY_AND_HARDWARE_CONSTRAINTS.md`)

### Archive
Legacy/experimental files: `archive/` (see `archive/ARCHIVE_MANIFEST.md`). Uncertain
items kept active: `docs/printing/ARCHIVE_REVIEW_REQUIRED.md`.

### Hardware assumptions
- Right mount: `p300_multi_gen2` (8-channel, 20–300 µL). Left mount: `p20_single_gen2`
  (single-channel, 1–20 µL). Deck: tuberack=7, plate=4, paper=5, tiprack_p300=9,
  tiprack_p20=2 (slots 1 and 6 kept clear of the P300 partial-tip envelope).

### Validation status
- **On this home computer (no instruments):** edit, unit tests, build, Opentrons
  **simulation**, YAML/labware validation, and audits are complete and passing.
- **Requires the work laptop + physical OT-2 (NOT done here):** real pipette/deck
  calibration, tip pickup/return, liquid handling, clearances/collisions, camera capture,
  and fixture verification. See `docs/printing/WORK_LAPTOP_PHYSICAL_VALIDATION.md`.

---

## Deferred (not this pass)
- Physical relocation of `src/core` / `src/protocols` into a `src/printing/` package
  (large reference blast radius) — the numbered SOURCE files + this index provide
  discoverability without that churn.
- AI-agent subsystem reorganization (`src/agents/`) — only path-break fixes were applied.
- Retiring/ migrating Family B (`printing_demo_*`) onto the unified schema.
