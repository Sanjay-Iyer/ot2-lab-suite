# Workflow 01 — Vial Dilution → Paper Print

The active production workflow. This document is the human-facing entry point; the
machine/LLM-facing guides are under `skills/printing_workflow/`.

```
20 mL vial materials (custom rack)
        ↓  P300 single-nozzle transfers
96-well dilution + 8-up mixing
        ↓
P20 single-spot  and/or  P300 eight-up printing
        ↓
paper (96-well coordinate grid)
        ↓
one before image + one after image
```

## Files (all share the `01_` number)
| Kind | Path |
|---|---|
| Protocol (entry point) | `src/protocols/printing/01_vial_dilution_paper_print.py` |
| Configs | `configs/printing/01_vial_dilution_paper_print.{p20_only,p300_only,mixed}.yaml` |
| Legacy config (auto-migrated) | `configs/workflows/defaults/vial_dilution_print.yaml` |
| Builder | `scripts/build_vial_dilution_print.py` |
| Multi-mode validator | `scripts/validate_vial_print.py` |
| Shared validators | `src/core/{pipette_selection,print_groups,materials,workflow_config}.py` |
| Labware manifest | `labware/printing/01_vial_dilution_paper_print.labware.md` |
| Tests | `tests/printing/` |
| Skill | `skills/printing_workflow/01_WORKFLOW_GUIDE.md` |

## Quick start (home computer — simulation only)
```bash
# build + validate + simulate one of the three configs
python scripts/build_vial_dilution_print.py --config configs/printing/01_vial_dilution_paper_print.mixed.yaml
# unit tests
python -m pytest tests/printing/
```

## What each config demonstrates
- **p20_only** — 5 µL and 10 µL droplets printed one-at-a-time on the P20. (The P300 is
  still mounted to *prepare* the 200 µL dilutions — a single-channel P20 cannot make
  200 µL transfers; set `dilution_plan.enabled: false` to run with only a P20 and a
  pre-filled plate.)
- **p300_only** — 30 µL and 35 µL droplets, 8 at a time on the P300.
- **mixed** — both, in one run, with no paper-destination overlap (P300 → paper 1–4,
  P20 → paper 5–8) and independent tips per pipette.

## Pipette selection, printing, tips, imaging
See `skills/printing_workflow/CONFIGURATION_GUIDE.md` (schema),
`skills/printing_workflow/VALIDATION_AND_SIMULATION.md` (build/validate/simulate + failure
interpretation), and `skills/printing_workflow/SAFETY_AND_HARDWARE_CONSTRAINTS.md`
(hardware limits, deck collisions, what needs the physical robot).

## Physical validation
Nothing in this workflow has been run on a physical OT-2 from the home computer. Before any
real run, follow `docs/printing/WORK_LAPTOP_PHYSICAL_VALIDATION.md` on the instrument-connected
work laptop.
