---
name: ot2-protocols
description: Build, validate, generate, and simulate OT-2 workflow protocols (dilution, printing, demos) from YAML configs. Use when the user wants to set up a liquid-handling workflow, generate an OT-2 Python protocol, validate a config against hardware constraints, or run a local Opentrons simulation. Generation and simulation work on any machine; deploying/running on hardware is a separate skill (ot2-robot-control).
---

# OT-2 Protocol Creation

Turn a workflow config into a validated, simulated OT-2 Python protocol.

## When to use this skill

- "Set up a dilution workflow with 8 steps"
- "Generate a printing protocol for the 96-well plate"
- "Validate this config against the hardware constraints"
- "Simulate the protocol before we run it"

Everything here is **safe on the dev laptop** — generation and simulation make no
robot connection. Only deployment/execution (see
[ot2-robot-control](../ot2-robot-control/SKILL.md)) needs the lab laptop.

## Key paths

| What | Path |
|------|------|
| Workflow defaults (YAML) | `configs/workflows/defaults/*.yaml` |
| User run configs | `configs/workflows/user/` |
| Hardware constraints | `configs/constraints/*.yaml` |
| Workflow registry | [`src/core/workflows/registry.py`](../../src/core/workflows/registry.py) |
| Generated protocols | `src/protocols/generated/generated_<workflow>.py` |
| Simulation records | `robot_data/data/simulations.json` |
| Agent tools | [`src/agents/tools.py`](../../src/agents/tools.py) |

## Registered workflows

| Workflow | Default config | Description |
|----------|----------------|-------------|
| `dilution` | `dilution.yaml` | Multi-step dilution series |
| `printing` | `printing.yaml` | Unified nanoparticle printing |
| `printing_demo` | `printing_demo.yaml` | Demo-scale printing |
| `printing_12` | `printing_12.yaml` | 12-well variant |
| `printing_96` | `printing_96.yaml` | 96-well variant |

## The protocol pipeline (always in this order)

```
1. list_available_workflows      → see what exists
2. load_workflow_defaults(type)  → load YAML into the working config
3. update_workflow_config(...)   → apply user changes (optional)
4. validate_current_workflow     → MUST pass (Pydantic + constraints)
5. generate_protocol             → write generated_<type>.py, get SHA256
6. simulate_protocol(path)       → MUST PASS before any hardware run
```

Steps 4 and 6 are **mandatory gates**. Never hand a protocol to the robot that
hasn't passed both validation and simulation.

## Path A — via the AI agent (recommended)

```bash
python -m src.agents.main
# add --rate-limit if you hit Gemini 429 errors
```

```
[USER]: Set up a dilution workflow
[AGENT]: (load_workflow_defaults) Loaded defaults. Use these or change parameters?
[USER]: Make it 6 dilution steps
[AGENT]: (update_workflow_config → validate_current_workflow) VALIDATION PASSED
[USER]: Generate and simulate it
[AGENT]: (generate_protocol → simulate_protocol) SIMULATION PASSED for hash a1b2c3d4
```

### Agent tools available (in `tools.py`)

| Tool | Purpose |
|------|---------|
| `list_available_workflows()` | List registered workflows |
| `load_workflow_defaults(type)` | Load defaults into working config |
| `update_workflow_config(updates)` | Merge user changes |
| `validate_current_workflow()` | Pydantic + hardware constraint validation |
| `validate_config(path)` | Validate an arbitrary YAML file |
| `show_full_config()` | Dump current working config as YAML |
| `generate_protocol()` | Render protocol → `.py`, return SHA256 |
| `simulate_protocol(path)` | Local `opentrons.simulate`, record PASS/FAIL |

## Path B — manual

Validate or simulate without the agent:

```bash
# Local simulation of a generated protocol
python -m opentrons.simulate src/protocols/generated/generated_dilution.py
```

Edit a default config directly under `configs/workflows/defaults/`, or copy it
into `configs/workflows/user/` for a one-off run, then simulate.

## Traceability — the SHA256 contract

- `generate_protocol` returns a **SHA256** of the protocol file.
- `simulate_protocol` records `{sha256: {status, path, timestamp, result}}` in
  `robot_data/data/simulations.json`.
- The robot-execution step (next skill) **refuses to run** unless that exact
  SHA256 has a `PASS` record. If you edit the protocol, the hash changes — you
  must re-simulate.

## Verify before moving to hardware

- Confirm `simulate_protocol` returned **PASS** for the current hash.
- Read the simulation output summary — check the deck layout, pipette, and
  transfer count match intent.
- If simulation fails, paste the error back to the agent (or read it) and adjust
  the config; do not deploy a failing protocol.

## Next step

Once a protocol has a passing simulation, deploy and run it on the live robot
with [ot2-robot-control](../ot2-robot-control/SKILL.md) (lab laptop only).
