---
name: ot2-labware
description: Create, list, and generate custom Opentrons OT-2 labware definitions (well plates, reservoirs, tube racks). Use when the user wants to define new labware, make a custom plate/rack/reservoir, turn a labware spec into Opentrons JSON, or list/inspect existing labware configs. Works on any machine (no robot connection needed).
---

# OT-2 Labware Creation

Generate Opentrons **schemaVersion-2** labware definition JSON for grid plates,
reservoirs, and tube racks — parametrically, without the Labware Creator web UI.

## When to use this skill

- "Create a custom 24-well plate with 20 mm deep wells"
- "Define a 15 mL tube rack for the OT-2"
- "Turn this labware spec into Opentrons JSON"
- "List the labware configs / generated definitions we have"

This skill needs **no robot connection** — it runs fully on the dev laptop or the lab laptop.

## Key paths

| What | Path |
|------|------|
| YAML configs (inputs) | `configs/labware/*.yaml` |
| Generated JSON (outputs) | `labware/<load_name>.json` |
| Generator engine | [`scripts/generate_labware.py`](../../scripts/generate_labware.py) |
| Agent tools | [`src/agents/labware_tools.py`](../../src/agents/labware_tools.py) |
| Config template | `configs/labware/_template.yaml` |

## Path A — via the AI agent (recommended)

Start the agent and describe the labware in plain language:

```bash
python -m src.agents.main
```

```
[USER]: Create a custom 24-well plate, 20mm deep wells, 4000ul each, call it my_deep_24well
[AGENT]: (list_labware_presets → create_labware_config → describe_labware_config)
         Here is the config summary... does this look correct?
[USER]: yes generate it
[AGENT]: (generate_labware_from_config) Wrote labware/my_deep_24well.json
```

### Agent tools available (in `labware_tools.py`)

| Tool | Purpose |
|------|---------|
| `list_labware_presets()` | Show 9 built-in starting layouts |
| `list_labware_configs()` | List YAML configs in `configs/labware/` |
| `list_generated_labware()` | List JSON definitions in `labware/` |
| `describe_labware_config(name)` | Human-readable summary of a config |
| `create_labware_config(params)` | Write a new YAML config (preset + overrides) |
| `generate_labware_from_config(name)` | Render YAML → Opentrons JSON |

### Built-in presets

`6-well`, `12-well`, `24-well`, `48-well`, `96-well`, `384-well`,
`reservoir-12-well`, `tube-rack-15ml`, `tube-rack-1.5ml`

## Path B — manual CLI

1. Copy the template and edit it:
   ```bash
   copy configs\labware\_template.yaml configs\labware\my_plate.yaml
   ```
2. Edit `my_plate.yaml` — set at minimum `load_name` and `display_name`, plus
   grid (`rows`/`cols`), well geometry (`shape`, `diameter` or `x_length`/`y_length`,
   `depth`, `total_liquid_volume`), and spacing (`x_offset`/`y_offset`/`x_spacing`/`y_spacing`).
3. Generate the JSON:
   ```bash
   python scripts/generate_labware.py configs/labware/my_plate.yaml
   ```
   Output → `labware/my_plate.json`

## Naming rules (enforced — generation fails otherwise)

- `load_name` and `namespace`: **lowercase letters, digits, `.`, `_` only** —
  no spaces, hyphens, or capitals.
- `display_name`: any human-readable string.
- Custom labware keeps `namespace: custom_beta`.

## Coordinate convention (mm)

- Origin = front-left-bottom of the labware. x → right, y → back, z → up.
- Row **A is the back-most row** (highest y); wells auto-fill back-to-front.
- All x/y/z must be **≥ 0** — the generator errors if a grid goes negative
  (increase `y_offset` or reduce `rows`/`y_spacing`).
- `well_z` = center-bottom of the well above the labware floor. Leave it `null`
  to auto-compute as `z_dimension - depth`.
- Tip racks (`is_tiprack: true`) **require** `tip_length`.

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
  See [ot2-robot-control](../ot2-robot-control/SKILL.md). If you revise a
  definition, bump `version` in the JSON (and in the protocol's `load_labware`).

## Verify before shipping

- Re-run `describe_labware_config(<name>)` (or open the JSON) and confirm well
  count = rows × cols, volumes, and depth match the user's intent.
- For multichannel access, plate_format must be `96Standard`/`384Standard`;
  `irregular` disables multichannel column addressing.
