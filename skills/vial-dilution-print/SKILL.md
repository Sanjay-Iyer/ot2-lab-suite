---
name: vial-dilution-print
description: Build, validate, simulate, deploy, and CV-verify the 20 mL vial → 96-well dilution → single-tip paper-print demo. Use when the user works on vial_dilution_print (the YAML, the protocol, the build/validate/vision scripts) or asks about its parameters, tip routine, headspace, or droplet vision QC. Config/generate/simulate/CV work on any machine; deploying to hardware is the lab laptop only (see ot2-robot-control).
---

# Vial Dilution → Single-Tip Paper Print

The flagship demo pipeline: draw water + food colouring from two **20 mL
scintillation vials** in the custom v2 tube rack (slot 7), build an 8-step
dilution series down **one column** of a 96-well plate (slot 4), then use
SINGLE-nozzle mode to pick up **one tip at a time** and print those wells onto
paper (slot 5) as sequential droplets. Tips are **returned**, not trashed. The host then runs computer-vision
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
5. (lab laptop) python scripts/run_vial_print_robot.py --live     HTTP API terminal run
```

Steps 2–4 run from the **conda `ai`** environment (has `opentrons`, `numpy`,
`cv2`, `pandas`, `yaml`). They make **no robot connection** and are safe on the
dev laptop. Only step 5 needs the lab laptop.

### Or drive it conversationally (AI agent)

`src/agents/vial_print_agent.py` is a LangChain + LangGraph (Gemini) agent that runs
this exact pipeline from natural language — adjust the number of dilutions, droplet
volume, and replicates by talking to it. It **wraps** the CLI tools (it does not
bypass any gate) and edits a *user* YAML copy, never the committed default.

LLM auth follows the laptop role. On the simulation laptop, the agent may use the
regular Gemini API-key path (`LLM_PROVIDER=api-key`, `GOOGLE_API_KEY`) for testing.
On the real robot laptop, live OT-2 agent interactions must use Vertex AI / gcloud
ADC (`LLM_PROVIDER=vertexai`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`);
do not use `GOOGLE_API_KEY` for live robot runs.

```bash
python -m src.agents.vial_print_agent "set up 5 dilutions, 20 uL droplets, 3 replicates"
python -m src.agents.vial_print_agent --no-llm "5 dilutions, 20 uL droplets"   # offline, no API key
```

See [TOOLS.md](TOOLS.md) §5 for the tool list and the knob→YAML mapping.

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
2. **Tip-column separation.** `printing.single_tip_columns` must not overlap
   `dilution.single_tip_columns`; dilution and print tips are both picked one at
   a time.
3. **Middle-row layout for single-nozzle labware.** The vial rack, tip rack, and
   plate must stay in the middle deck rows (4-5-6 / 7-8-9) — off the front row (1-2-3)
   and back row (10-11-12) — so partial-tip idle nozzles stay in robot bounds, and
   `single_start` must point those idle nozzles off the tall vial rack. Verified
   layout: rack 7, plate 4, paper 5, tips 9, `single_start: A1`. See
   [ot2-robot-profile](../ot2-robot-profile/SKILL.md) for the general rule.
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
