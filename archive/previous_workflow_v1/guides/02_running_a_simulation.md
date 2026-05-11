# Running a Simulation

This guide covers invoking `tools/run_simulation.py`, understanding
what it does, reading its output, and handling errors.

## Basic usage

```bash
python tools/run_simulation.py configs/exp_2026-04-28_testsample_a.yaml
```

The runner:

1. Loads and validates the YAML config.
2. **Converts the config to a temporary JSON file** (mimicking how the offline robot receives data).
3. Sets the `OT_CONFIG_PATH` environment variable to point to that JSON file.
4. Runs `protocols/dilution_protocol.py` through `opentrons.simulate`.
5. Parses tagged comments from the simulator output.
6. Writes a structured log to `logs/` and a raw transcript to `outputs/`.

## Command-line flags

| Flag | Default | Description |
|------|---------|-------------|
| *(positional)* | — | Path to the YAML config file (required) |
| `--protocol` | `protocols/dilution_protocol.py` | Override the protocol file |
| `--log-level` | `INFO` | Python log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--no-transcript` | *(off)* | Skip writing the raw transcript file |
| `--dry-validate` | *(off)* | Validate the config and exit without simulating |

### Dry validation

If you only want to check whether a config is valid:

```bash
python tools/run_simulation.py --dry-validate configs/my_config.yaml
```

This loads the config, runs all validation rules, and exits. No
simulator is started, no log files are written.

## What happens during a run

The protocol emits tagged `protocol.comment(...)` lines that the runner
captures. Each tag marks a phase of the workflow:

| Tag | Phase | What's happening |
|-----|-------|-----------------|
| `[LOAD]` | Setup | Labware loaded into slots, pipette attached |
| `[VALIDATE]` | Validation | Config checked against SCHEMA rules |
| `[DILUTE]` | Dilution | Stock + diluent transferred into slot-2 wells |
| `[PRINT]` | Print | Slot-2 well contents transferred to slot-3 wells |
| `[DONE]` | Finish | Protocol completed successfully |

### Sample log excerpt

```
2026-04-28T14:02:01 INFO  [LOAD] Config file: /home/user/opentrons/configs/exp_2026-04-28_testsample_a.yaml
2026-04-28T14:02:01 INFO  [LOAD] Experiment: TestSample_A_Dilution_Series  |  Date: 2026-04-28  |  Operator: Sanjay
2026-04-28T14:02:01 INFO  [LOAD] Slot 1: opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap (Sample tube rack)
2026-04-28T14:02:01 INFO  [LOAD] Slot 2: nest_96_wellplate_200ul_flat (Dilution destination plate)
2026-04-28T14:02:01 INFO  [LOAD] Pipette: p300_single_gen2 on right mount  |  Tip rack: slot 11
2026-04-28T14:02:01 INFO  [VALIDATE] Running config validation …
2026-04-28T14:02:01 INFO  [VALIDATE] Config OK — all checks passed.
2026-04-28T14:02:01 INFO  [DILUTE] Starting dilution series: 5 factor(s), final volume 100.0 µL per well.
2026-04-28T14:02:01 INFO  [DILUTE] Well A1 (1/5): 2x dilution  |  V_stock=50.0 µL, V_diluent=50.0 µL  |  [1000 → 500.0 µM]
2026-04-28T14:02:02 INFO  [DILUTE] Well B1 (2/5): 3x dilution  |  V_stock=33.3 µL, V_diluent=66.7 µL  |  [1000 → 333.33 µM]
...
2026-04-28T14:02:05 INFO  [PRINT] Transfer 1/5: slot 2 A1 → slot 3 A1  |  10.0 µL
...
2026-04-28T14:02:07 INFO  [DONE] Protocol finished: TestSample_A_Dilution_Series  |  5 dilutions, 5 prints.
```

**How to read each line:**

- `2026-04-28T14:02:01` — timestamp from the runner.
- `INFO` — log level (all protocol comments are INFO; runner errors are
  ERROR).
- `[DILUTE]` — which phase this line belongs to.
- Everything after the tag — human-readable detail.

## Exit codes

| Code | Meaning | What to do |
|------|---------|-----------|
| 0 | Success | Check the log and transcript. |
| 1 | Config invalid | Read the error message, fix the config, re-run. |
| 2 | Simulator failed | The protocol raised an exception. Check the traceback. |
| 3 | I/O failure | Couldn't read the config or write log/transcript. Check file paths and permissions. |

## Passing a custom protocol

If you've forked the protocol or created a variant:

```bash
python tools/run_simulation.py \
    --protocol protocols/my_custom_protocol.py \
    configs/my_config.yaml
```

The `OT_CONFIG_PATH` env var is still set for you.

## Next steps

- [guides/04_inspecting_results.md](04_inspecting_results.md) — how to
  read and compare logs.
- [guides/05_troubleshooting.md](05_troubleshooting.md) — common errors
  and fixes.
