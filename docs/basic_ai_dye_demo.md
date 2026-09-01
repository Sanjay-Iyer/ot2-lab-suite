# AI Agent Demo: Dilutions and Paper Printing

One conversational command shows the OT-2 doing the two things this lab does most:
make a dilution series, then print those dilutions onto paper.

```powershell
python scripts\ai_dye_demo.py --simulate   # local only; never contacts a robot
python scripts\ai_dye_demo.py              # real OT-2, after you type run
```

The agent introduces itself, asks what you want, and edits a timestamped YAML copy
of the standard plan. It never writes robot Python. Every edit is validated against
the P20's real limits before it is accepted, and the deterministic builder embeds
the YAML into protocol v19
(`src/protocols/printing/13_ai_agent_dilution_print_demo.py`) and simulates that
exact artifact before anything physical happens.

## What it runs

Everything is on the single-channel **P20** — the same instrument, tip logic and
liquid handling as the v6 dilution/print workflow, with the print-release cycle
from the recent printing scripts.

1. **Dilutions** — water into every well of one plate column, then dye on top of
   it, one fold factor per plate row, mixed before use.
2. **Printing** — each dilution prints on its own paper row; each droplet volume
   and replicate takes its own paper column. A fresh tip per dilution, so no two
   concentrations ever share a tip.

### The default plan (the "I don't know" answer)

| | |
|---|---|
| Dilutions | 8 — 1x, 2x, 3x, 4x, 6x, 8x, 12x, 16x, 150 µL each, plate column 11 |
| Print | one **5 µL** drop of each, paper column 1 |
| Deck | vial rack 7, plate 4, paper 5, 20 µL tip rack 9 |
| Liquids | water in vial A1, dye in vial A2 |
| Tips | 10, from A1 — one water, one dye, one per printed dilution |

The series stops at 16x so every dye transfer is 9 µL or more, where the P20 is
accurate. Ask for a steeper series (2-fold out to 128x) if range matters more than
precision — 150 µL total supports up to 150x before the 1 µL floor bites.

## What you can say

The agent opens with a greeting and a list of examples. Anything in this space
works:

- `make 8 dilutions in column 11 and print them at 5 uL`
- `4 dilutions, 10 uL drops, start printing at paper column 3`
- `move the plate to slot 6 and use dye vial A3`
- `print at 5 and 10 uL side by side, two columns each`
- `three drops stacked on each spot`
- `start the series at row D`
- `only do the dilutions, skip the printing`
- `use 200 uL per dilution and start from tip C2`

If you have no idea what to ask for, say **"I don't know"** — the agent prints the
standard example above and invites you to adjust it.

Typed commands: `plan`, `show` (raw YAML), `help`, `quit`.

## Starting the run

Every plan ends by naming the exact word that starts it, so it is never something
you have to already know:

```text
  >>>  TO RUN THE SIMULATION NOW, TYPE:   run
       Or keep talking to change the plan first.
```

That word runs it — there is no second confirmation prompt. A plain go-ahead works
too (`go`, `go ahead`, `this is good run it`); anything carrying a number or a piece
of labware is treated as an edit instead, so "run 8 dilutions in column 3" changes
the plan rather than starting it.

**It is the same word on the real instrument** — nothing new to remember at the
moment it matters. Live mode says which one you are in three times over:

```text
Mode        : LIVE - the real OT-2 will move
...
  >>>  TO START THE REAL ROBOT NOW, TYPE: run
       The OT-2 starts moving as soon as you do.
       Or keep talking to change the plan first.
```

and typing it prints the deck it is about to work on before handing off to the
runner:

```text
==============================================================================
STARTING THE REAL OT-2 - the robot is about to move.
  vial rack slot 7, plate slot 4, paper slot 5, tips slot 9
  8 dilutions, then 8 drops on paper
  Ctrl-C now if the deck does not match.
==============================================================================
```

## What the agent will not do

Rejected edits leave the config exactly as it was, and the agent says why:

- the pipette, the safety limits, the flow rates and the run modes are fixed;
- the calibrated print geometry — dispense height, air gap, push-out, blow-out,
  dwell — is laboratory-owned and comes from
  `configs/machines/ot2_standard_printing_p20_v1.yaml`;
- physically impossible plans are refused with the reason: a 25 µL drop on a P20,
  two things in one deck slot, 9 dilutions, a series that runs past row H, a
  1000x dilution that would need 0.15 µL of dye, a print that runs off the paper,
  a starting tip that leaves too few tips.

## Simulation rehearsal

```powershell
python scripts\ai_dye_demo.py --simulate
```

Nothing runs until you type `run`. That builds the protocol from your YAML and
simulates every movement locally. It never discovers or contacts a robot. On this
simulation laptop use `conda activate ai` and only `--simulate`.

## Real robot run

On the physically supervised robot laptop, after checking the deck, tips, liquids,
labware, pipette and paper:

```powershell
conda activate llm
python scripts\find_robot.py --check
python scripts\ai_dye_demo.py
```

Review the printed plan, then type `run` — the plan's own footer says so, and the
header above it reads `LIVE - the real OT-2 will move`. The runner rebuilds,
simulates, uploads over the HTTP API, starts the run and monitors it. The robot host
comes from `configs\robot.yaml`; pass `--robot-host` only to override it.

## Work-laptop setup after a pull

```powershell
conda activate llm
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_GCP_PROJECT_ID
python -m src.agents.check_llm_auth
```

`.env` (do not commit credentials):

```text
LLM_PROVIDER=vertexai
GOOGLE_CLOUD_PROJECT=YOUR_GCP_PROJECT_ID
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-2.5-flash
```

## Files

- `configs/workflows/defaults/ai_agent_dilution_print_demo.yaml` — the standard
  plan the agent starts from; every knob is commented
- `configs/workflows/user/ai_dye_demo_YYYYMMDD_HHMMSS.yaml` — the edited copy
- `runs/ai_dye_demo/YYYYMMDD_HHMMSS/session.log` — JSON-lines audit of the
  conversation, every accepted and rejected edit, and the confirmation
- `runs/ai_dye_demo/YYYYMMDD_HHMMSS/executed_config.yaml` — what actually ran
- `src/protocols/generated/ai_agent_dilution_print_demo_latest.py` — the uploaded
  protocol
