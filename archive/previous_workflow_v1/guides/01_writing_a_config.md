# Writing a Config

Every simulation starts with a YAML config file. You can create one
interactively or from the command line.

## Interactive mode (recommended for first-timers)

```bash
python tools/make_config.py
```

The script walks you through each field. Defaults are shown in brackets —
press Enter to accept them.

### Worked example

Below is a full interactive session for a sample called "TestSample_A"
at 1000 µM, with dilutions 2x, 3x, 4x, 5x, and 10x:

```
============================================================
  Opentrons Dilution Workflow — Config Generator
============================================================

── Experiment Metadata ─────────────────────────────────
Operator name: Sanjay
Experiment notes (optional) []: First test run

── Sample ──────────────────────────────────────────────
Sample name [TestSample_A]:
Stock concentration (numeric value) [1000.0]:
Concentration units [µM]:
Available stock volume (µL) [500.0]:

── Dilutions ───────────────────────────────────────────
Final volume per well (µL) [100.0]:
Dilution factors (comma-separated, e.g. 2,5,10) [2,3,4,5,10]:
  ⚠ Factor #5 (10x): V_stock = 10.0 µL < pipette min (20.0 µL).

── Diluent ─────────────────────────────────────────────
Diluent name [Nuclease-Free Water]:

── Paper-Print Stage ───────────────────────────────────
Print volume per well (µL) [10.0]:

── Labware (default deck layout) ───────────────────────
  Slot  1: opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap
           Sample tube rack
  Slot  2: nest_96_wellplate_200ul_flat
           Dilution destination plate
  Slot  3: nest_96_wellplate_200ul_flat
           Paper-proxy plate (position-matched output)
  Slot  5: nest_12_reservoir_15ml
           Diluent reservoir
  Slot 11: opentrons_96_tiprack_300ul
           Tip rack for P300
  (To customise labware, edit the YAML after generation.)

────────────────────────────────────────────────────────
  SUMMARY
────────────────────────────────────────────────────────
  Experiment : TestSample_A_Dilution_Series
  Date       : 2026-04-28
  Operator   : Sanjay
  ...
────────────────────────────────────────────────────────

Write this config? [Y/n]: Y
✓ Config written to: configs/exp_2026-04-28_testsample_a.yaml
```

### What each section means

The resulting YAML has these top-level blocks. For full field definitions
including types, units, and constraints, see
[configs/SCHEMA.md](../configs/SCHEMA.md).

| YAML block | What it holds |
|------------|--------------|
| `experiment` | Name, date, operator, notes |
| `sample` | Sample name, source slot/well, stock concentration, available volume |
| `dilutions` | List of Nx factors and the per-well final volume |
| `diluent` | Name, source slot/well |
| `labware` | Slot-number → labware-load-name mapping |
| `pipette` | Model, mount, tip rack slot |
| `paper_print` | Print volume, source/dest slots, well-by-well mapping |
| `outputs` | Paths where the runner writes logs and transcripts |

### Auto-generated fields

You don't need to type these — the generator fills them in:

- **`experiment.date`** — today's date in ISO format.
- **`paper_print.well_map`** — built automatically from the number of
  dilution factors, using Opentrons column-major well order (A1, B1,
  C1, …).
- **`outputs`** — log and transcript paths derived from the sample name
  and date.

## Non-interactive mode

For scripting or CI, use `--from-defaults` with any overrides:

```bash
python tools/make_config.py --from-defaults \
    --sample-name TestSample_A \
    --stock-conc 1000 \
    --dilutions 2,3,4,5,10 \
    --final-volume 100 \
    --operator Sanjay \
    --notes "Automated batch run"
```

All available flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--sample-name` | `TestSample_A` | Sample identifier |
| `--stock-conc` | `1000.0` | Stock concentration (numeric) |
| `--stock-units` | `µM` | Concentration units |
| `--available-volume` | `500.0` | Stock volume in µL |
| `--dilutions` | `2,3,4,5,10` | Comma-separated Nx factors |
| `--final-volume` | `100.0` | Final volume per well in µL |
| `--operator` | `auto` | Operator name |
| `--notes` | *(empty)* | Free-text notes |
| `--diluent-name` | `Nuclease-Free Water` | Diluent identifier |
| `--print-volume` | `10.0` | Print volume in µL |

### Output filename

Configs are written to `configs/exp_<YYYY-MM-DD>_<sanitized_name>.yaml`.
If a file with that name already exists:

- **Interactive mode** — you're asked to overwrite, auto-suffix, or cancel.
- **Non-interactive mode** — auto-suffixed to `_v2`, `_v3`, etc.

## Validation rules

The generator validates your input before writing. Hard errors block the
config from being written. Warnings are displayed but don't prevent
generation.

### Hard errors (config will not be written)

| Rule | Plain-English meaning | Example error message |
|------|----------------------|----------------------|
| Factor > 1 | Every dilution factor N must be strictly greater than 1. | `Dilution factor #2 (1) must be > 1.` |
| Total stock ≤ available | Sum of all V_stock values can't exceed what's in the tube. | `Total stock required (600 µL) exceeds available volume (500 µL).` |
| ≤ 96 dilutions | You can't have more factors than wells in a 96-well plate. | `Too many dilutions (97); max is 96.` |

### Warnings (config is written, but review carefully)

| Rule | Plain-English meaning | Example warning |
|------|----------------------|----------------|
| V_stock ≥ pipette min | The P300 can't reliably pipette below ~20 µL. | `Factor #5 (10x): V_stock = 10.0 µL < pipette min (20.0 µL).` |

If a warning fires in interactive mode, you'll see a ⚠ message but can
still proceed. In non-interactive mode, the warning is printed to stderr.

## Next steps

Once you have a config, run it through the simulator:
[guides/02_running_a_simulation.md](02_running_a_simulation.md).
