# Vial-Dilution-Print — CLI Tool Reference

Every utility in the pipeline, its exact arguments, preconditions, and outputs.
All run from the repo root in the conda **`ai`** environment unless noted. None of
these make a robot connection (deployment lives in
[ot2-robot-control](../ot2-robot-control/SKILL.md)).

---

## 1. `scripts/build_vial_dilution_print.py` — the builder/compiler

Turns the workflow YAML into a robot-ready protocol and simulates it.

```bash
python scripts/build_vial_dilution_print.py
python scripts/build_vial_dilution_print.py --config configs/workflows/user/my_run.yaml
python scripts/build_vial_dilution_print.py --no-sim
```

| Argument | Type | Default | Meaning |
|----------|------|---------|---------|
| `--config` | path | `configs/workflows/defaults/vial_dilution_print.yaml` | YAML config to compile. |
| `--no-sim` | flag | off | Generate only; skip the simulation step. |

**Preconditions**
- `ai` env active (`opentrons`, `numpy`, `pyyaml`).
- Base template present at `src/protocols/printing/01_vial_dilution_paper_print.py` with the
  `# >>> CONFIG START >>>` / `# <<< CONFIG END <<<` markers intact.
- Custom labware JSON present under `labware/` (passed to the simulator via `-L`).

**What it does** (in order)
1. Loads the YAML; pops `run_modes` (these are *not* part of `CONFIG`).
2. `validate(config)` — mirrors the protocol's resolvers so config errors surface
   at build time, not on the robot. Pulls physical bounds from the **plate/tiprack
   labware JSON** when available (max well volume, rows-per-column, column count),
   falling back to the `safety:` block only when the JSON is absent.
3. Rewrites the `DEFAULT_DRY_RUN / DEFAULT_DO_DILUTION / DEFAULT_DO_PRINT` flags
   from `run_modes`.
4. Embeds `CONFIG` (pretty-printed, key order preserved) between the markers.
5. Writes **two** files and simulates the timestamped one.

**Outputs**
- `src/protocols/generated/vial_dilution_print_run_<YYYYMMDD_HHMMSS>.py` (timestamp
  is local time with UTC offset applied, for log correlation).
- `src/protocols/generated/vial_dilution_print_latest.py` (the deploy artifact).
- Console: `Config validation passed.`, the two generated paths, a key-line
  simulation digest, and `SIMULATION OK` / `SIMULATION FAILED`.

**Exit codes** `0` = built (and simulated, unless `--no-sim`) cleanly; `1` =
config-not-found, validation failure, or simulation failure.

> **Simulation caveat:** `opentrons.simulate` returns 0 even when a protocol raises
> at runtime. The builder guards against this by scanning the simulator's output
> text for genuine error markers (`Traceback`, `RuntimeError`,
> `ProtocolCommandFailedError`, `KeyError`, …). Both the exit code **and** a clean
> text scan are required for `SIMULATION OK`.

---

## 2. `scripts/validate_vial_print.py` — the run-mode matrix gate

Simulates the **generated** protocol (falls back to the base template) under five
run modes and asserts each behaves correctly. This is the real pass/fail gate.

```bash
python scripts/validate_vial_print.py
python scripts/validate_vial_print.py --config configs/workflows/user/my_run.yaml
```

| Argument | Type | Default | Meaning |
|----------|------|---------|---------|
| `--config` | path | the committed default YAML | YAML the must-contain assertions are **derived** from. Pass the *same* user config used to build the generated protocol so a custom factor list / `destination_column` validates against its own expectations, not the default's. |

**Target file priority:**
`src/protocols/generated/vial_dilution_print_latest.py` →
`src/protocols/printing/01_vial_dilution_paper_print.py`.

> When the AI agent (below) builds from a user config, it threads that same path
> into `--config` automatically. Hand-runs of the bare command still validate the
> default.

**The five cases** (must-contain / must-not-contain strings are generated *from the
YAML*, so changing `destination_column` or the factor list never silently breaks
the gate):

| Case | Flags | Expectation |
|------|-------|-------------|
| `full_run` | dilution + print | Pre-flight passes; top & bottom wells diluted; SINGLE nozzle layout for dilution and print; single-tip droplets printed; tips returned; demo completes. |
| `dry_run` | dry | Pre-flight passes; `DRY RUN`; completes with **no** dilution/print motion. |
| `dilution_only` | dilution | Dilutes + series complete; **no** printing. |
| `print_only` | print | Single-tip paper print; **no** dilution. |
| `wrong_labware` | dilution + print, **bad rack name injected** | Pre-flight **FAILS** on the identity/geometry mismatch; demo never completes. |

The `wrong_labware` case desyncs `expected_tuberack_load_name` from the loaded rack
to prove the pre-flight identity check actually fires — a live negative test of the
glass-protection gate.

**Preconditions** `ai` env; the workflow YAML present (source of the dynamic
assertions); `labware/` present.

**Outputs** Per-case `[PASS]/[FAIL]` lines, then `ALL CASES PASSED` (exit 0) or
`SOME CASES FAILED` (exit 1).

---

## 3. `vision_tests/scripts/verify_print_droplets.py` — droplet CV verifier

Host-side computer-vision QC of the printed paper image: did all 8 droplets land,
are they round, and does the colour gradient run light→dark down the column?

```bash
# Synthesize an 8-droplet gradient (no camera needed — full pipeline in sim)
python vision_tests/scripts/verify_print_droplets.py --mock --expect 8

# FAIL path: synthesize only 4 droplets to exercise the count mismatch
python vision_tests/scripts/verify_print_droplets.py --mock --expect 8 --inject-missing 4

# Analyse a real run folder (resolves images via robot_image_dir from the YAML)
python vision_tests/scripts/verify_print_droplets.py --run-dir runs/<run_id>

# Analyse one specific image
python vision_tests/scripts/verify_print_droplets.py --image path/to/paper.jpg
```

| Argument | Type | Default | Meaning |
|----------|------|---------|---------|
| `--image` | path | — | A single paper-print image. |
| `--run-dir` | path | — | Run root; images resolved at `<run-dir>/<robot_image_dir basename>/` (fallback `<run-dir>/images/paper/`). |
| `--mock` | flag | off | Synthesize a white-paper image with an N-droplet blue gradient. |
| `--inject-missing` | int | — | With `--mock`, draw only this many droplets (drives the FAIL path). |
| `--expect` | int | `len(FOLD_ORDER)` (8) | Expected droplet count; derived from the YAML factor count. |
| `--out` | path | `vision_tests/outputs/` | Output directory. |

**Image source precedence:** `--image` → `--run-dir` → `--mock` → `vision_tests/raw/*.jpg`.

**Detection config precedence (highest wins):** `vision_tests/configs/vision_config.yaml`
`droplet_detection:` → workflow YAML `cv.detection:` → in-code fallbacks.

**Well/fold labels** (`WELL_ORDER`, `FOLD_ORDER`) and `min_circularity_ok` are read
from the workflow YAML, so droplet annotations track config changes automatically.

**Outputs** (under `--out`)
- `cv_results.csv` — one row per detected droplet (centroid, area, circularity,
  RGB/HSV, brightness, well, fold).
- `cv_summary.json` — machine-readable verdict + per-image stats.
- `cv_report.txt` — human-readable report (the thing to skim).
- `annotated/<stem>_droplets.jpg` — droplets circled + labelled.

**Exit codes** `0` = overall PASS (every image's found count == expected);
`1` = FAIL. Note the verdict gates on **count**; circularity and gradient are
reported as quality signals.

---

## 4. `tests/test_vial_print.py` — unit & regression suite

```bash
pytest tests/test_vial_print.py -q
```

Covers the resolvers (`resolve_factors`, `resolve_dilution_wells`,
`resolve_single_tips`, `dilution_volumes`), the builder's `validate()` rejecting
out-of-bounds configs, and **structural regression guards** (e.g. the protocol
source must not contain `_ROWS = "ABCDEFGH"` — rows must be derived from labware).
Add new behaviour here as invariant assertions, not hardcoded expected strings.

---

## 5. `src/agents/vial_print_agent.py` — conversational driver

A standalone LangChain + LangGraph (Gemini) agent that runs the whole pipeline from
natural language. It wraps the three tools above — it does **not** replace them, so
every safety gate still fires.

```bash
# Live/simulation agent:
# - simulation laptop: LLM_PROVIDER=api-key + GOOGLE_API_KEY is allowed for testing
# - real robot laptop: LLM_PROVIDER=vertexai + GOOGLE_CLOUD_PROJECT is required
python -m src.agents.vial_print_agent
python -m src.agents.vial_print_agent "set up 5 dilutions, 20 uL droplets, 3 replicates"

# Deterministic offline pipeline — no LLM / API key (load->update->build->validate->CV)
python -m src.agents.vial_print_agent --no-llm "5 dilutions, 20 uL droplets, 3 replicates"
```

**The three conversational knobs → YAML** (see [PARAMETERS.md](PARAMETERS.md)):

| Say | YAML key set | Notes |
|-----|--------------|-------|
| "N dilutions" | first N canonical `dilution.factors.explicit` **+** `cv.expected_droplets=N` | N = droplets per print; `1 ≤ N ≤ 8` |
| "V uL droplets" | `printing.droplet_volume_ul` | `0 < V ≤ 300`; keep small |
| "R replicates" | `printing.num_replicates` | `R ≥ 1` |

Anything else (fold strengths, total volume, mix reps, columns, slots) goes through
`update_vial_print_params(advanced_updates={...})`.

**Tools** (`src/agents/vial_print_tools.py`): `load_vial_print_defaults`,
`update_vial_print_params`, `preview_dilution_plan`, `show_vial_print_config`,
`build_vial_print_protocol` (→ writes a user YAML under `configs/workflows/user/`,
runs the builder, records a PASS in `simulations.json`), `validate_vial_print_matrix`
(runs the matrix with `--config`), `verify_print_droplets_mock`. Robot deploy/execute
are reused unchanged from `src/agents/tools.py` (lab laptop only, behind `RUN ROBOT`).
For live vial-print runs, `scripts/run_vial_print_robot.py` pulls robot-side camera
images from `/data/vision/vial_dilution_print` into
`vision_runs/vial_dilution_print/run_YYYYMMDD_HHMMSS/` automatically after the run
reaches a terminal status. Use `--no-pull-images` only when you explicitly want to
leave images on the robot.

The agent **edits the YAML, never the generated file**, and never touches the
committed default — each run leaves a timestamped user YAML for traceability.
Offline tests: `tests/test_vial_print_agent.py`.

## Quick recipes

```bash
# Full local gate, dev laptop
python scripts/build_vial_dilution_print.py        \
  && python scripts/validate_vial_print.py          \
  && python vision_tests/scripts/verify_print_droplets.py --mock --expect 8

# Rebuild from a one-off user config without simulating
python scripts/build_vial_dilution_print.py --config configs/workflows/user/run42.yaml --no-sim
```
