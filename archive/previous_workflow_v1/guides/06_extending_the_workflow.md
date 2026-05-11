# Extending the Workflow

This project is designed to be modified. Here's where to make common
changes — no new files are needed in most cases, just edits to existing
ones.

## Add a new dilution strategy

**Scenario:** You want serial dilutions (each step dilutes the previous
well, not the stock) instead of parallel dilutions from stock.

**Where to change:**

- **`protocols/dilution_protocol.py`**, in the dilution stage loop
  (search for `# ── 6. Dilution stage`). Currently, every dilution
  aspirates from `source_well` (the stock tube in slot 1). To do serial
  dilution, change the source for each step to be the *previous*
  destination well instead.
- **`configs/SCHEMA.md`** — add a `dilution_mode` field (e.g.
  `"parallel"` or `"serial"`) and document it.
- **`tools/make_config.py`** — add a prompt or CLI flag for the new
  field.

The volume math changes too: in serial dilution, each factor is relative
to the *previous* concentration, not the stock.

## Swap labware

**Scenario:** You want to use a different plate (e.g. a 384-well plate)
or a different tube rack.

**Where to change:**

- **Config file** — change the `load_name` under the relevant slot in
  the `labware` section. You can do this manually in the YAML or
  generate a new config.
- **`tools/make_config.py`** — update the `DEFAULT_LABWARE` dict
  near the top of the file if you want the new labware to be the default
  going forward.
- **Well names** — if you switch to a plate with different wells (e.g.
  384-well), update `PLATE_96_WELLS` in `tools/make_config.py` or
  make the well-name list dynamic based on the labware.

**Finding valid labware names:** The Opentrons Labware Library at
[labware.opentrons.com](https://labware.opentrons.com) lists every
load name.

## Support a different pipette

**Scenario:** You want to use a P20 single-channel for low-volume
transfers.

**Where to change:**

- **Config file** — set `pipette.model` to `"p20_single_gen2"` and
  update `pipette.tip_rack_slot` if needed.
- **Tip rack labware** — change slot 11's `load_name` to
  `"opentrons_96_tiprack_20ul"`.
- **Pipette minimum** — in `protocols/dilution_protocol.py`, update
  `PIPETTE_MIN_VOLUME_UL` from `20.0` to the P20's minimum (~1 µL).
  Also update the same constant in `tools/make_config.py`.
- **Mix volume** — the `MIX_VOLUME_FRACTION` (0.80) in the protocol
  might push the mix volume above the P20's max (20 µL). Adjust as
  needed.

## Add a new tag to protocol comments

**Scenario:** You want to add a `[MIX]` tag so mixing steps show up
separately in the log.

**Where to change:**

- **`protocols/dilution_protocol.py`** — add
  `protocol.comment("[MIX] ...")` lines inside the mix loop. Currently
  mixing is done silently via `pipette.mix(...)`.
- **`tools/run_simulation.py`** — if the runner parses tags for
  filtering or special handling, add `[MIX]` to its recognized tags.
- **Guides** — update
  [guides/02_running_a_simulation.md](02_running_a_simulation.md) and
  [guides/04_inspecting_results.md](04_inspecting_results.md) to
  document the new tag.

## Change the output file layout

**Scenario:** You want logs in a date-based subdirectory (e.g.
`logs/2026-04-28/`).

**Where to change:**

- **`tools/make_config.py`** — in `build_config()`, change the
  `outputs` block to include the date in the directory path.
- **`tools/run_simulation.py`** — the runner already creates
  directories as needed, but verify it handles nested paths.

## Add a new config field

**Scenario:** You want to make mix repetitions configurable instead of
hardcoded.

**Where to change (in order):**

1. **`configs/SCHEMA.md`** — document the new field, its type, units,
   default, and any constraints.
2. **`configs/example_experiment.yaml`** — add the field with a
   reasonable default.
3. **`tools/make_config.py`** — add a prompt (interactive) and a CLI
   flag (non-interactive) for the field. Add it to `build_config()`.
4. **`protocols/dilution_protocol.py`** — read the new field from the
   config dict and use it in place of the hardcoded constant.

This order matters: schema first, then config, then generator, then
protocol. Each step downstream depends on the ones above it.

## Summary

| I want to… | Primary file(s) to edit |
|-----------|----------------------|
| Change dilution strategy | `protocols/dilution_protocol.py`, `configs/SCHEMA.md` |
| Swap labware | Config YAML, `tools/make_config.py` |
| Change pipette | Config YAML, `protocols/dilution_protocol.py` |
| Add a log tag | `protocols/dilution_protocol.py` |
| Change output paths | `tools/make_config.py` |
| Add a config field | Schema → example → generator → protocol |
