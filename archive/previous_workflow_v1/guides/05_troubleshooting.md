# Troubleshooting

Common problems and how to fix them.

---

## Invalid config: "Dilution factor must be > 1"

**Symptom:**

```
Dilution factor #2 (1) must be > 1.
```

**Cause:** A dilution factor of 1 means "no dilution" — the final
concentration equals the stock concentration. This is disallowed to
prevent accidental no-ops.

**Fix:** Remove the factor of 1 from your dilutions list, or change it to
a value greater than 1 (e.g. 1.5 for a 1.5x dilution).

---

## Invalid config: "V_stock is below pipette minimum"

**Symptom:**

```
⚠ Factor #5 (10x): V_stock = 10.0 µL < pipette min (20.0 µL).
```

**Cause:** For high dilution factors, the stock volume per well becomes
very small. The P300 single-channel pipette can't reliably aspirate below
~20 µL.

**Fix (choose one):**

- Increase `final_volume_ul`. At 200 µL final volume, a 10x dilution
  gives V_stock = 20 µL — right at the threshold.
- Remove the offending factor from the list.
- Switch to a smaller pipette (e.g. P20). This requires changing the
  `pipette.model`, tip rack load name, and tip rack slot in the config.
  See [guides/06_extending_the_workflow.md](06_extending_the_workflow.md).

**Note:** The config generator treats this as a *warning*, not a hard
error — it will still write the config. However, the protocol's validator
will raise a hard error at simulation time.

---

## Invalid config: "Total stock exceeds available volume"

**Symptom:**

```
Total stock required (600 µL) exceeds available volume (500 µL).
```

**Cause:** The sum of V_stock across all dilutions is more than what's
available in the source tube.

**Fix:** Either increase `available_volume_ul` in the config (if your
tube actually contains more), or reduce the number of dilution factors.

---

## Missing dependency: "ModuleNotFoundError: opentrons"

**Symptom:**

```
ModuleNotFoundError: No module named 'opentrons'
```

**Cause:** The `opentrons` package isn't installed in your current Python
environment.

**Fix:**

```bash
source .venv/bin/activate     # make sure the virtualenv is active
pip install -r requirements.txt
```

If you see version conflicts, try creating a fresh virtualenv:

```bash
python -m venv .venv --clear
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Simulator installation issues

**Symptom:** `pip install opentrons` fails with compilation errors,
especially on macOS or ARM Linux.

**Cause:** The `opentrons` package has native dependencies that may need
a C compiler.

**Fix:**

- Make sure you're on Python 3.10+.
- On macOS, install Xcode command-line tools: `xcode-select --install`.
- On Linux, install build essentials: `sudo apt install build-essential`.
- If all else fails, use a Docker container or a virtual machine with
  x86_64 architecture.

---

## Config file not found

**Symptom:**

```
Config file not found: /absolute/path/to/configs/missing.yaml
```

**Cause:** The path you passed to `tools/run_simulation.py` doesn't
exist, or you have a typo.

**Fix:** Double-check the filename. List your configs:

```bash
ls configs/
```

**Note on OT_CONFIG_PATH:** The runner sets this environment variable
for you. Don't set it manually unless you're running the protocol
directly with `python -m opentrons.simulate` (rare).

---

## Filename collision in `configs/`

**Symptom (interactive):**

```
⚠  File already exists: configs/exp_2026-04-28_testsample_a.yaml
  [O]verwrite / [S]uffix (_v2, …) / [C]ancel
```

**Symptom (non-interactive):** The file is auto-suffixed and you get
`_v2`, `_v3`, etc.

**Cause:** You've already generated a config for this sample on this
date.

**Fix:** Choose overwrite if you want to replace it. Choose suffix to
keep both. The suffix is appended before `.yaml`, so
`exp_2026-04-28_testsample_a_v2.yaml`.

---

## Log file not appearing

**Symptom:** The run completed successfully (exit code 0) but there's no
file in `logs/`.

**Possible causes:**

1. **Permissions.** The runner can't create the `logs/` directory. Check
   that you have write access to the project root.
2. **Custom output path.** If you edited the config's `outputs.log_file`
   to point somewhere else, check that path.
3. **Wrong working directory.** The runner uses paths relative to the
   project root. Run from the root directory:

   ```bash
   cd /path/to/opentrons
   python tools/run_simulation.py configs/my_config.yaml
   ```

---

## Protocol error: "well does not exist on labware"

**Symptom:**

```
sample.source_well: well 'B7' does not exist on
'opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap' in slot 1.
```

**Cause:** The well name in the config doesn't match the labware's well
layout. The 24-tube rack has wells A1–D6, not the full A1–H12 of a
96-well plate.

**Fix:** Check the well name in your config against the labware's
available wells. Most users should keep the default `A1`.

---

## Still stuck?

1. Run with `--dry-validate` to check the config without starting the
   simulator.
2. Check the protocol's docstring at the top of
   `protocols/dilution_protocol.py` for the config path resolution order.
3. Review [configs/SCHEMA.md](../configs/SCHEMA.md) for the full field
   reference and validation rules.
