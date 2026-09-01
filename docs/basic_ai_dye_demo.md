# Basic AI Dye Dilution + Paper Print Demo

One command runs the conversational demo:

```powershell
python scripts\ai_dye_demo.py --simulate  # local only; no robot connection
python scripts\ai_dye_demo.py             # real OT-2 after confirmation
```

The AI edits a timestamped YAML under `configs/workflows/user/`; it never edits
robot Python. The builder validates and embeds the YAML into a deterministic
protocol, then simulates that exact artifact before physical execution.

## Default plan

| Slot | Item | Contents |
|---:|---|---|
| 7 | 20 mL vial rack | A1 water, A2 dye |
| 4 | 96-well plate | A9-D9: 1x, 2x, 5x, 10x; 200 uL each |
| 5 | paper proxy | four 30 uL drops at paper column 1 |
| 9 | 300 uL tip rack | setup and print tips |

The terminal resolves and displays the dye/water volume in every well, all deck
locations, paper columns, number of drops, and total liquid before confirmation.

## Work-laptop setup after `git pull`

From the repository root:

```powershell
conda activate llm
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_GCP_PROJECT_ID
```

Set these values in `.env` (do not commit credentials):

```text
LLM_PROVIDER=vertexai
GOOGLE_CLOUD_PROJECT=YOUR_GCP_PROJECT_ID
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-1.5-flash
```

Verify LLM authentication without robot motion:

```powershell
python -m src.agents.check_llm_auth
```

## Simulation-only rehearsal

```powershell
python scripts\ai_dye_demo.py --simulate
```

Example request:

```text
Use dye vial A3, make 6 dilutions in plate column 8, use 25 uL drops,
and print twice starting at paper column 3.
```

Type `show` for the full YAML or `run` for final review. Nothing executes until
you type exactly `RUN SIMULATION`. This performs a local build, Opentrons
simulation, validation matrix, and mock droplet-count CV. It never discovers or
contacts a robot.

## Real robot run

On the physically supervised robot laptop only:

```powershell
conda activate llm
python scripts\find_robot.py --check
python scripts\ai_dye_demo.py
```

The default robot IP is `169.254.46.57`. After reviewing the printed plan and
checking the physical deck, type exactly `RUN LIVE`. The live runner rebuilds,
simulates, validates, uploads, starts, monitors, and pulls enabled camera images.

Useful requests include:

- `Move the plate to slot 6 and the paper to slot 4.`
- `Use dye vial B2 and make the dilutions in plate column 11.`
- `Make 5 dilutions with factors 1, 2, 4, 8, and 16.`
- `Print 3 passes starting at paper column 2 with 30 uL drops.`
- `Use another installed 96-well plate definition.`

Invalid AI edits are rejected and the previous config remains unchanged.

## Logs

- `configs/workflows/user/ai_dye_demo_YYYYMMDD_HHMMSS.yaml`: edited config
- `runs/ai_dye_demo/YYYYMMDD_HHMMSS/session.log`: timestamped JSON-lines audit
- `runs/ai_dye_demo/YYYYMMDD_HHMMSS/executed_config.yaml`: executed snapshot
- `src/protocols/generated/vial_dilution_print_latest.py`: generated protocol
- existing robot runner logs and pulled images for live runs

On this simulation laptop use `conda activate ai` and only `--simulate`.
