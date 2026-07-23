# Workflow 01 — Labware Manifest

Custom labware JSONs live **flat** in `labware/` and are **not** renamed or moved into a
numbered directory, because Opentrons (and this repo's build/deploy path) require each
JSON's filename to equal its internal `parameters.loadName`. This manifest associates the
flat files with Workflow 01 instead of renaming them.

| loadName (== filename in `labware/`) | Role in Workflow 01 | Deck slot | Notes |
|---|---|---|---|
| `tuberack_3dprint_20ml_8vials_v2` | 20 mL vial rack (materials source) | 7 | Wells A1–A4, B1–B4; 20 000 µL each; depth 55 mm. The un-versioned name `tuberack_3dprint_20ml_8vials` does NOT exist on disk — use `_v2`. |
| `corning_96_wellplate_360ul_custom` | dilution plate | 4 | 96 wells; dilution series prepared here. |
| `corning_96_wellplate_360ul_custom` | paper coordinate proxy | 5 | Same definition, second load; paper is addressed as a 96-well grid (dispense_z controls the real standoff). |

Standard Opentrons tip racks (built-in, loaded by name — **not** files in `labware/`):
| loadName | Role | Deck slot |
|---|---|---|
| `opentrons_96_tiprack_300ul` | P300 tips | 9 |
| `opentrons_96_tiprack_20ul` | P20 tips (mixed/P20 runs) | 2 |

## Rules
- **Do not rename** the custom JSONs to add numeric prefixes — it would break Opentrons
  loading and `scripts/build_vial_dilution_print.py` (which resolves `labware/<loadName>.json`).
- Configs reference these by `load_name` in their `labware:` section; the simulator
  resolves them via `-L labware`.
- To regenerate a definition, edit its source YAML under `configs/labware/` and run
  `scripts/generate_labware.py` (the JSON filename stays == loadName).
