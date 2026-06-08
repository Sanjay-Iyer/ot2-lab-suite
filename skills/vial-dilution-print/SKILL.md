---
name: vial-dilution-print
description: Build, validate, simulate, deploy, and CV-verify the 20 mL vial → 96-well dilution → 8-channel paper-print demo. Use when the user works on vial_dilution_print (the YAML, the protocol, the build/validate/vision scripts) or asks about its parameters, tip routine, headspace, or droplet vision QC. Config/generate/simulate/CV work on any machine; deploying to hardware is the lab laptop only (see ot2-robot-control).
---

# Vial Dilution → 8-Channel Paper Print

The flagship demo pipeline: draw water + food colouring from two **20 mL
scintillation vials** in the custom v2 tube rack (slot 1), build an 8-step
dilution series down **one column** of a 96-well plate (slot 2), then pick up an
8-tip block and "print" that whole column onto paper (slot 3) as 8 simultaneous
droplets. Tips are **returned**, not trashed. The host then runs computer-vision
QC on the resulting droplet image.

This skill is the ground truth for that pipeline. For its sub-topics see:

| Reference | Covers |
|-----------|--------|
| [TOOLS.md](TOOLS.md) | Every CLI utility (builder, validator matrix, droplet CV) — args, inputs, outputs |
| [PROTOCOL_MECHANICS.md](PROTOCOL_MECHANICS.md) | Deck layout, dynamic single-tip routine, partial pickup, tip headspace, pre-flight gate |
| [PARAMETERS.md](PARAMETERS.md) | Dense dictionary of every `vial_dilution_print.yaml` key — type, units, deck impact, safe bounds |

## Source of truth

| Artifact | Path |
|----------|------|
| Workflow config (edit this) | [`configs/workflows/defaults/vial_dilution_print.yaml`](../../configs/workflows/defaults/vial_dilution_print.yaml) |
| Base protocol template | [`src/protocols/vial_dilution_print.py`](../../src/protocols/vial_dilution_print.py) |
| Generated, robot-ready copies | `src/protocols/generated/vial_dilution_print_latest.py` (+ timestamped) |
| Vial labware (explicit geometry) | [`labware/tuberack_3dprint_20ml_8vials_v2.json`](../../labware/tuberack_3dprint_20ml_8vials_v2.json) |
| Plate / paper labware | `labware/corning_96_wellplate_360ul_custom.json` |
| Builder | [`scripts/build_vial_dilution_print.py`](../../scripts/build_vial_dilution_print.py) |
| Run-mode matrix validator | [`scripts/validate_vial_print.py`](../../scripts/validate_vial_print.py) |
| Droplet CV verifier | [`vision_tests/scripts/verify_print_droplets.py`](../../vision_tests/scripts/verify_print_droplets.py) |
| Unit/regression tests | [`tests/test_vial_print.py`](../../tests/test_vial_print.py) |

## The pipeline (always this order)

```
1. Edit configs/workflows/defaults/vial_dilution_print.yaml      (or pass --config)
2. python scripts/build_vial_dilution_print.py                   build → embed CONFIG → simulate
3. python scripts/validate_vial_print.py                         5-case run-mode matrix gate
4. python vision_tests/scripts/verify_print_droplets.py --mock   CV sanity (8-droplet gradient)
5. (lab laptop) deploy + opentrons_execute                       see ot2-robot-control
```

Steps 2–4 run from the **conda `ai`** environment (has `opentrons`, `numpy`,
`cv2`, `pandas`, `yaml`). They make **no robot connection** and are safe on the
dev laptop. Only step 5 needs the lab laptop.

> **Why the YAML must be *built* in, not read at runtime:** the robot cannot see
> the repo. `build_vial_dilution_print.py` embeds the YAML as the `CONFIG` dict
> between the `# >>> CONFIG START >>>` / `# <<< CONFIG END <<<` markers and writes
> a self-contained file to `src/protocols/generated/`. That generated file is the
> only thing that ships. **Edit the YAML, never the generated file.**

## Hard safety invariants (verified by pre-flight + tests — do not break)

1. **Explicit vial geometry.** The tube rack is loaded with
   `namespace="custom_beta", version=1`, and pre-flight cross-checks the *loaded*
   well diameter (28 mm), depth (55 mm), row spacing (34 mm) and column spacing
   (31 mm) against `safety:` to ±0.5 mm **before any motion**. A geometry/identity
   mismatch aborts the run — this is what prevents the Z-axis from driving a tip
   into glass on fallback defaults.
2. **Tip-column separation.** `printing.print_block_column` must not appear in
   `dilution.single_tip_columns`, or the 8-tip print block would be clobbered by
   single-tip dilution pickups.
3. **Slot 6 tiprack.** The tip box must not sit directly behind the tuberack;
   single-nozzle idle nozzles collide. Slot 6 is verified clear.
4. **apiLevel 2.28.** Required for `return_tip()` in partial (single-nozzle) mode.
5. **No hardcoded rows/wells.** Row order is derived from the loaded labware
   (`rows_by_name()`), never the literal `"ABCDEFGH"`. A regression test enforces
   this.

## Verify before hardware

- `build_vial_dilution_print.py` prints `SIMULATION OK`.
- `validate_vial_print.py` prints `ALL CASES PASSED` (full_run, dry_run,
  dilution_only, print_only, **and** wrong_labware aborting on the identity check).
- A green simulator exit code alone is **not** proof — the simulator exits 0 even
  on a runtime raise. Trust the validator's text-scan verdict, not the exit code.
