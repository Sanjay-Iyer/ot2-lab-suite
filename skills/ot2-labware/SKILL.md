---
name: ot2-labware
description: Create, derive, list, validate, and generate custom Opentrons OT-2 labware definitions (well plates, printing substrates, reservoirs, tube racks). Use when the user wants to define new labware, copy or modify an existing labware definition, make a custom plate/rack/substrate, turn a labware spec into Opentrons JSON, or list/inspect/validate existing labware configs and definitions. Works on any machine (no robot connection needed).
---

# OT-2 Custom Labware Generation

Generate Opentrons **schemaVersion-2** labware definition JSON parametrically,
without the Labware Creator web UI.

The division of labour is the whole point:

```
user intent  ->  AI picks parameters  ->  Python computes every coordinate
                                      ->  layered validation
                                      ->  labware/<loadName>.json
```

**The model never writes well coordinates.** It chooses rows, columns, spacing,
origin and well geometry; `src/labware/geometry.py` calculates all 96 x/y pairs.

## When to use this skill

- "Create a custom 24-well plate with 20 mm deep wells"
- "Make another copy of my paper labware called paper_test_01"
- "Same as paper_print_96_flat but with 8.5 mm X spacing"
- "Is this labware JSON valid?"
- "List the labware configs / generated definitions we have"

No robot connection required — runs on the dev laptop or the lab laptop.

## Key paths

| What | Path |
|------|------|
| Generation engine | [`src/labware/`](../../src/labware/) |
| — parameter schema (pydantic) | [`schemas.py`](../../src/labware/schemas.py) |
| — coordinate math | [`geometry.py`](../../src/labware/geometry.py) |
| — schema-2 assembly | [`builder.py`](../../src/labware/builder.py) |
| — validation layers | [`validation.py`](../../src/labware/validation.py) |
| — template inheritance | [`templates.py`](../../src/labware/templates.py) |
| — family registry | [`families.py`](../../src/labware/families.py) |
| CLI | [`scripts/generate_labware.py`](../../scripts/generate_labware.py) |
| Agent tools | [`src/agents/labware_tools.py`](../../src/agents/labware_tools.py) |
| Specialized agent | [`src/agents/custom_labware_agent.py`](../../src/agents/custom_labware_agent.py) |
| YAML configs (inputs) | `configs/labware/*.yaml` |
| Generated JSON (outputs) | `labware/<load_name>.json` |
| Config template | `configs/labware/_template.yaml` |
| Full docs | [`docs/custom_labware_generation.md`](../../docs/custom_labware_generation.md) |

## Labware families

Only **implemented** families can be generated. Ask the code, never guess:

```bash
python -c "import sys; sys.path.insert(0,'.'); from src.labware import list_families; [print(f.name, '-', f.description) for f in list_families()]"
```

| Family | Covers |
|--------|--------|
| `rectangular_grid` | Evenly spaced rows x columns on one pitch — flat printing substrates, well plates, troughs, simple racks |

`tube_rack` is **not** implemented yet. `tuberack_3dprint_20ml_8vials_v2` is
currently generated as a 2x4 `rectangular_grid`, which reproduces it exactly.

## Path A — via the AI agent (recommended)

```bash
python -m src.agents.custom_labware_agent
```

```
[USER]: make another copy of my paper labware, call it paper_test_01
[AGENT]: (list_labware_families -> derive_custom_labware mode='copy')
         Wrote labware/paper_test_01.json — schema=PASS geometry=PASS json=PASS opentrons=PASS
```

The general agent (`python -m src.agents.main`) has the same labware tools.

### Agent tools

| Tool | Purpose |
|------|---------|
| `list_labware_families()` | Families that are actually implemented, and their required parameters |
| `list_labware_presets()` | 9 built-in starting layouts |
| `list_labware_configs()` | YAML configs in `configs/labware/` |
| `list_generated_labware()` | JSON definitions in `labware/` |
| `describe_labware_config(name)` | Human-readable summary of a config |
| `derive_custom_labware(params)` | Inherit an existing labware's geometry (**preferred** for "another one like X") |
| `generate_custom_labware(params)` | Full structured parameters -> validated JSON in one call |
| `create_labware_config(params)` | Write a new YAML config (preset + overrides) |
| `generate_labware_from_config(name)` | Render YAML config -> validated JSON |
| `validate_labware_definition(name)` | Re-run every validation layer on a JSON already on disk |

### Built-in presets

`6-well`, `12-well`, `24-well`, `48-well`, `96-well`, `384-well`,
`reservoir-12-well`, `tube-rack-15ml`, `tube-rack-1.5ml`

## Path B — manual CLI

```bash
python scripts/generate_labware.py configs/labware/my_plate.yaml
```

```bash
python scripts/generate_labware.py --dry-run configs/labware/my_plate.yaml
```

`--dry-run` builds and validates but writes nothing. `--overwrite` is required
to replace an existing definition whose content differs.

To make a new config: copy `configs/labware/_template.yaml`, set at minimum
`load_name` and `display_name`, plus grid (`rows`/`cols`), well geometry
(`shape`, `diameter` or `x_length`/`y_length`, `depth`, `total_liquid_volume`),
spacing (`x_offset`/`y_offset`/`x_spacing`/`y_spacing`) and the outer footprint
(`x_dimension`/`y_dimension`/`z_dimension`).

## Copy vs regenerate — keep these distinct

| Operation | Means | Geometry overrides |
|-----------|-------|--------------------|
| `mode='copy'` | Same physical object, new identity | **Rejected** |
| `mode='regenerate'` | Deliberately different object | Allowed; all coordinates recomputed and re-validated |

"Make another one called X" is a copy. "Like X but wider spacing" is a
regenerate. Treating the first as the second is how a calibrated definition
silently drifts.

Deriving always requires a **new** `load_name`, and the template file is never
modified.

## Never invent a dimension

Physical geometry has **no defaults** in the schema. `rows`, `cols`, offsets,
spacings, `depth`, `total_liquid_volume` and the outer footprint are all
required. If the user has not supplied a value and no template or preset
provides it, generation fails and names what is missing — **ask the user**, do
not choose a plausible number. A wrong dimension is a tip driven into glass.

Legitimate ways to fill a gap: an explicit template the user named, a built-in
preset, or a published Opentrons definition (`opentrons_shared_data.labware.
load_definition(...)`). Not: recall, or a number that looks about right.

## Naming rules (enforced — generation fails otherwise)

- `load_name` and `namespace`: **lowercase letters, digits, `.`, `_` only** —
  no spaces, hyphens, or capitals.
- `display_name`: any human-readable string.
- Custom labware keeps `namespace: custom_beta`.
- The output filename is always `<load_name>.json`, flat in `labware/`.

Three distinct concepts: `display_name` (shown in the app), `load_name` (the
identity protocols load by), and the filename (must equal `load_name`).

## Versioning

`schemaVersion` is fixed at 2 by the format. `namespace` stays `custom_beta`.
`version` identifies a **revision of the same loadName** — bump it when you
change a definition that keeps its name, and update the protocol's
`load_labware(..., version=)` to match.

A derived labware is a *new identity*, not a revision: it gets a new
`load_name` and `version` restarts at 1. Never leave a changed definition
carrying the old name and version.

## Coordinate convention (mm)

- Origin = front-left-bottom of the labware. x → right, y → back, z → up.
- Row **A is the back-most row** (highest y); y decreases A → H.
- Column 1 is leftmost; x increases 1 → 12.
- `x = x_offset + column_index * x_spacing`, `y = y_offset - row_index * y_spacing`.
- All x/y/z must be **≥ 0** — generation errors if a grid goes negative.
- `well_z` = centre-bottom of the well above the labware floor. Leave it `null`
  to auto-compute as `z_dimension - depth`; set it explicitly only when you have
  a measurement that differs (`paper_print_96_flat` pins 6.0, not 13.9).
- Tip racks (`is_tiprack: true`) **require** `tip_length`.

## Validation — four layers, four different questions

| Layer | Answers |
|-------|---------|
| `schema` | Are the parameters well-formed and self-consistent? (pydantic) |
| `geometry` | Does the object make physical sense? Unique names, right count, wells inside the footprint **accounting for well radius**, well floor inside the body |
| `json` | Valid round-trippable document, required keys, `ordering` ↔ `wells` integrity, filename matches `loadName` |
| `opentrons` | jsonschema against shipped labware schema 2, `LabwareDefinition2` model, and `opentrons.protocols.labware.verify_definition` |

Every layer must pass **before** anything is written — a failing definition
never lands on disk. `opentrons` reports `NOT_AVAILABLE` (not `PASS`) when the
tooling is not importable, so an unchecked definition never looks checked.

**None of these is physical verification.** A definition that passes all four
can still be wrong about the real object. Only a measured check on the OT-2
settles that — never claim a definition is hardware-verified when it is not.

## Output safety

Generation **refuses** to replace an existing `labware/*.json` whose content
differs. Re-running a config that produces identical output is a silent no-op
(`unchanged`). To replace a definition on purpose, pass `--overwrite` (CLI) or
`overwrite=True` (tools). `labware/paper_print_96_flat.json` and
`labware/tuberack_3dprint_20ml_8vials_v2.json` are the known-working references
and the regression baselines — do not overwrite them.

## Using the result

- **In a protocol:** reference by `load_name`, `namespace` (`custom_beta`), and `version`.
- **Opentrons App:** Labware → Import → select `labware/<load_name>.json`.
- **Headless robot (`opentrons_execute`):** the JSON must live in the robot's
  custom-labware store at
  `/data/labware/v2/custom_definitions/<namespace>/<load_name>/<version>.json`.
  Deploy it there with the helper (reads namespace/loadName/version from the JSON,
  creates the nested dir, copies to `<version>.json`, and verifies):
  ```bash
  python -m scripts.deploy --labware labware/<load_name>.json            # lab laptop
  python -m scripts.deploy --labware labware/<load_name>.json --dry-run  # preview path
  ```
  See [ot2-robot-control](../ot2-robot-control/SKILL.md).
- **Workflow validation:** register new labware in
  `configs/constraints/labware_constraints.yaml` or the workflow validator will
  not recognise it.

## Adding a new labware family

1. Subclass `CommonLabwareSpec` in `schemas.py` with only the new fields.
2. Write the geometry generator (`geometry.py` or a new module).
3. Write `build_<family>_labware(spec)` in `builder.py`.
4. Register it in `LABWARE_FAMILIES` in `families.py`.
5. Add a baseline config under `configs/labware/` and a reproduction test.

The agent, the tools and the validation layers need no changes — they read the
registry. Full procedure in
[`docs/custom_labware_generation.md`](../../docs/custom_labware_generation.md).

## Verify before shipping

```bash
pytest tests/test_labware_geometry.py tests/test_labware_schema.py tests/test_labware_validation.py tests/test_labware_baseline.py tests/test_labware_tools.py
```

`tests/test_labware_baseline.py` regenerates `paper_print_96_flat`,
`corning_96_wellplate_360ul_custom` and `tuberack_3dprint_20ml_8vials_v2` from
their configs and asserts an exact match against the known-working files. If it
fails, the generator has drifted — fix the generator, not the golden file.

Then confirm well count = rows × cols, and that volumes and depth match the
user's intent.
