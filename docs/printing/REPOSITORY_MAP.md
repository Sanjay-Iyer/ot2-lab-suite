# Printing Workflow — Repository Map

The single active production workflow: **aspirate materials from 20 mL vials →
prepare/mix dilutions in a 96-well plate → print droplets onto paper (96-well
coordinate grid)**, using the right-mounted `p300_multi_gen2` (8-up) and/or the
left-mounted `p20_single_gen2` (single-spot), with one image before and one after.

## Active digital-automation layer

| Role | Path | Notes |
|---|---|---|
| **Protocol entry point** | [src/protocols/printing/01_vial_dilution_paper_print.py](../../src/protocols/printing/01_vial_dilution_paper_print.py) | The flagship. Reads its embedded `CONFIG`; executes dilution + group-based printing. Edit the YAML, not the generated copy. |
| **Build script** | [scripts/build_vial_dilution_print.py](../../scripts/build_vial_dilution_print.py) | YAML → validate (shared validators) → embed CONFIG → generate → simulate. |
| **Generated (do not edit)** | `src/protocols/generated/vial_dilution_print_latest.py` | Build output; regenerated each build. |
| **Pipette selection service** | [src/core/pipette_selection.py](../../src/core/pipette_selection.py) | ONE authoritative volume→pipette resolver (auto + explicit). |
| **Print-group schema + validator** | [src/core/print_groups.py](../../src/core/print_groups.py) | Group schema, layout/destination/duplicate/out-of-bounds checks, legacy migration. |
| **Materials schema + validator** | [src/core/materials.py](../../src/core/materials.py) | Vial materials, conservative volume accounting, filename==loadName check. |
| **Build-time normalizer/validator** | [src/core/workflow_config.py](../../src/core/workflow_config.py) | Maps the new config sections to internal CONFIG; runs every shared validator. |
| **Pipette constraints** | [configs/constraints/pipette_constraints.yaml](../../configs/constraints/pipette_constraints.yaml) | Canonical volume ranges (incl. `p300_multi_gen2`, `p20_single_gen2`). |
| **Example configs** | [configs/printing/](../../configs/printing/) | `01_vial_dilution_paper_print.p20_only`, `01_vial_dilution_paper_print.p300_only`, `01_vial_dilution_paper_print.mixed` (+ README index). |
| **Custom labware** | [labware/](../../labware/) | Filenames MUST equal internal `loadName` (Opentrons loader rule). |
| **Tests** | `tests/printing/test_pipette_selection.py`, `test_print_groups.py`, `test_materials.py`, `test_workflow_config.py` (all under `tests/printing/`) | Plus existing suite. Run with the `ai` conda env. |
| **Skills (LLM)** | [skills/printing_workflow/](../../skills/printing_workflow/) | SKILL, CONFIGURATION_GUIDE, VALIDATION_AND_SIMULATION, SAFETY_AND_HARDWARE_CONSTRAINTS. |

## Custom labware (filename == loadName — never rename to add numeric prefixes)

Custom JSONs in `labware/` (filename **must** equal internal `loadName`):

| File / loadName | Role | Key geometry |
|---|---|---|
| `tuberack_3dprint_20ml_8vials_v2` | 20 mL vial rack (active) | Wells A1–A4, B1–B4; 20 000 µL; depth 55 mm |
| `corning_96_wellplate_360ul_custom` | dilution plate + paper coordinate proxy | 96 wells |

Standard Opentrons tip racks (built-in — **not** files in `labware/`; loaded by name):
`opentrons_96_tiprack_300ul` (P300) and `opentrons_96_tiprack_20ul` (P20, mixed/P20 runs).

> Local labware is organized by an index (this table) — **not** by renaming files —
> because the Opentrons loader and the build script require `filename == loadName`.

## Deck layout (validated in simulation)

```
10  11  (trash)
 7:tuberack   8            9:tiprack_p300
 4:plate      5:paper      6  (keep clear — P300 partial-tip envelope)
 1 (keep clear)  2:tiprack_p20   3
```
The P300's single-nozzle (A1) dilution config extends idle nozzles forward, so the
slots directly in front of a visited slot (1 in front of 4, 6 in front of 9) must stay
clear. The 20 µL rack lives in slot 2 (verified collision-free).

## Data / history (do not rewrite)
- `configs/workflows/user/*.yaml` — per-run snapshots (immutable history).
- `src/protocols/generated/*_run_*.py` — timestamped build artifacts.

## Deferred (see final report / ARCHIVE_REVIEW_REQUIRED.md)
- Physical relocation of `src/protocols/` / `src/core/` into a `src/printing/` package
  and `protocols/printing/` — deferred because of the ~400-reference blast radius
  (build script, agent registries, `src/utils/paths.py`, tests, docs). The new
  organizational layer (numbered configs, skills, this map) is in place instead.
- AI-agent subsystem reorganization — explicitly out of scope for this phase.
