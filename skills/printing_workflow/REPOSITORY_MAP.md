# Printing Workflow — Repository Map (skill copy)

Authoritative version: [docs/printing/REPOSITORY_MAP.md](../../docs/printing/REPOSITORY_MAP.md).
Quick reference for an LLM working inside `skills/`:

## Entry points & services
- **Protocol:** `src/protocols/printing/01_vial_dilution_paper_print.py` (embedded CONFIG; robot entry point)
- **Build:** `scripts/build_vial_dilution_print.py` (YAML → validate → embed → simulate)
- **Selection:** `src/core/pipette_selection.py` (volume → pipette)
- **Groups:** `src/core/print_groups.py` (schema + layout/destination validation + migration)
- **Materials:** `src/core/materials.py` (vials + volume accounting + filename==loadName)
- **Normalizer:** `src/core/workflow_config.py` (new sections → internal CONFIG + all validators)

## Configs & labware
- Examples: `configs/printing/01_vial_dilution_paper_print.p20_only.yaml`, `02_p300_only.yaml`, `03_mixed_p20_p300.yaml`
- Constraints: `configs/constraints/pipette_constraints.yaml`
- Labware (filename == loadName): `labware/tuberack_3dprint_20ml_8vials_v2.json`,
  `corning_96_wellplate_360ul_custom.json`, standard `opentrons_96_tiprack_{20,300}ul`

## Tests
`tests/printing/test_pipette_selection.py`, `test_print_groups.py`, `test_materials.py`,
`test_workflow_config.py` (+ existing suite). Run: `python -m pytest` from repo root
using the `ai` conda env.

## File classification
| Kind | Examples | Edit? |
|---|---|---|
| Entry point | `vial_dilution_print.py` (logic, not CONFIG) | Yes (logic) |
| Library module | `src/core/*.py` | Yes (carefully) |
| Config | `configs/printing/*.yaml`, `configs/constraints/*.yaml` | Yes |
| Generated | `src/protocols/generated/*` | No |
| Labware | `labware/*.json` | Never rename |
| Test | `tests/*.py` | Yes |
| Archived | `archive/**` | No (restore only) |
