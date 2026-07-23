# AI Agent Overview — OT-2 Lab Suite

> **Scope:** All AI/agent files, directories, tools, and workflows in this repo.
> **Target environment:** Lab laptop with live OT-2 connection. Not the dev/code laptop.

---

## Architecture Summary

This repo implements a **LangChain + LangGraph ReAct agent** that acts as a conversational automation engineer for the Opentrons OT-2 liquid-handling robot. The agent can:

- Configure lab workflows interactively from natural language
- Validate configs against hardware constraints
- Generate OT-2 Python protocols
- Simulate protocols locally before any physical run
- Deploy and execute protocols on the live robot via SSH

**LLM backend:** Google Gemini (`gemini-2.5-flash` or configured model via `GEMINI_MODEL` in `.env`)
**Framework:** LangChain tools + LangGraph `create_react_agent`

---

## Flagship: Vial-Dilution-Print Conversational Agent

`src/agents/vial_print_agent.py` is a **dedicated, standalone** agent for the 20 mL
vial → 96-well dilution → 8-channel paper-print demo. It is separate from the general
`main.py` agent because the vial demo uses the hardened **build-embed-CONFIG** pipeline
(`scripts/build_vial_dilution_print.py` + `validate_vial_print.py` +
`verify_print_droplets.py`), not the registry's render path.

Talk to it to set the three dynamic knobs; it edits a **user** YAML copy (never the
committed default), builds, validates, and CV-checks, then — on the lab laptop only —
deploys and runs behind the `RUN ROBOT` gate.

```bash
# Live on real robot laptop (requires Vertex AI / gcloud ADC)
python -m src.agents.vial_print_agent "set up 5 dilutions, 20 uL droplets, 3 replicates"
# Vertex/gcloud LLM test with simulation only; robot tools unavailable
python -m src.agents.vial_print_agent --simulation-only "build and validate the default orange and blue workflow"
# Offline, no LLM/API key — runs load→update→build→validate→CV directly
python -m src.agents.vial_print_agent --no-llm "5 dilutions, 20 uL droplets, 3 replicates"
```

| Knob | Tool arg | YAML key | Bounds |
|------|----------|----------|--------|
| number of dilutions | `num_dilutions` | first N `dilution.factors.explicit` + `cv.expected_droplets` | 1–8 |
| droplet volume | `droplet_volume_ul` | `printing.droplet_volume_ul` | 0 < v ≤ 300 |
| replicates | `num_replicates` | `printing.num_replicates` | ≥ 1 |
| anything else | `advanced_updates` | any documented key | per PARAMETERS.md |

**Tools** (`src/agents/vial_print_tools.py`): `load_vial_print_defaults`,
`update_vial_print_params`, `preview_dilution_plan`, `show_vial_print_config`,
`build_vial_print_protocol`, `validate_vial_print_matrix`, `verify_print_droplets_mock`;
robot deploy/execute are reused from `tools.py`. Ground truth for parameters and
mechanics: [`skills/vial-dilution-print/`](../skills/vial-dilution-print/SKILL.md).
Offline tests: `tests/test_vial_print_agent.py`.

---

## Directory Map — Agent-Related Files

```
ot2-lab-suite/
│
├── src/
│   └── agents/                          ← CORE AGENT CODE
│       ├── main.py                      ← General agent entry point, REPL, session logging
│       ├── vial_print_agent.py          ← FLAGSHIP: conversational driver for the 20 mL vial demo
│       ├── vial_print_tools.py          ← Tools wrapping the vial-dilution-print CLI pipeline
│       ├── tools.py                     ← 12 LangChain @tool functions
│       ├── check_models.py              ← Lists available Gemini models (debug utility)
│       ├── run_stress_tests.py          ← 10-test benchmark suite for agent validation
│       ├── test_simulation_agent.py     ← Integration test: simulate full workflow
│       ├── logs/                        ← Timestamped session logs (agent_session_*.log)
│       └── archive/                     ← Historical agent implementations (do not use)
│           ├── main_0508_oldlang.py
│           ├── main_0508_245pm.py
│           ├── main_0508_313pm.py
│           └── run_stress_tests_0508_oldlang.py
│
├── src/core/
│   ├── config.py                        ← Config singleton: LLM factory, robot IP, SSH paths
│   ├── config_loader.py                 ← load/merge/validate/save workflow configs
│   └── workflows/
│       └── registry.py                  ← Workflow registry (dilution, printing + schemas)
│
├── src/utils/
│   ├── paths.py                         ← All canonical project paths (PROJECT_ROOT, dirs)
│   ├── limits_per_minute.py             ← RateLimitGuard (60-second rolling window)
│   ├── hashing.py                       ← SHA256 file hashing for protocol traceability
│   └── preflight.py                     ← Pre-deploy code safety scanner
│
├── configs/
│   ├── workflows/
│   │   ├── defaults/                    ← YAML workflow defaults (agent loads these)
│   │   │   ├── dilution.yaml
│   │   │   ├── printing.yaml
│   │   │   ├── printing_demo.yaml
│   │   │   ├── printing_12.yaml
│   │   │   └── printing_96.yaml
│   │   └── user/                        ← Agent writes run configs here at execution time
│   └── constraints/
│       ├── deck_constraints.yaml        ← Deck slot limits
│       ├── pipette_constraints.yaml     ← Pipette hardware limits
│       ├── labware_constraints.yaml     ← Volume/geometry rules
│       └── workflow_constraints.yaml    ← Workflow-level safety rules
│
├── runs/
│   ├── generated/                       ← Agent-generated .py protocol files
│   ├── simulations.json                 ← SHA256-keyed simulation pass/fail records
│   └── deploy/                          ← Local staging directories before SCP to robot
│
├── .env                                 ← LIVE credentials (lab laptop only, not in git)
├── .env.template                        ← Template with placeholder values
│
└── docs/
    ├── AI_Agent_Overview.md             ← THIS FILE
    ├── SOP_AI_Agent_Lab_Laptop.md       ← How to use the agent on the real robot
    └── LangChain_ot2_agent_guide.md     ← Original architecture reference
```

---

## The 12 Agent Tools (`src/agents/tools.py`)

Tools are LangChain `@tool` functions. The agent calls these autonomously; the user never invokes them directly.

### Configuration Tools

| Tool | What it does |
|------|-------------|
| `list_available_workflows()` | Lists registered workflow types and descriptions |
| `load_workflow_defaults(workflow_type)` | Reads YAML from `configs/workflows/defaults/`, sets global `_WORKING_CONFIG` |
| `update_workflow_config(updates)` | Deep-merges user changes into `_WORKING_CONFIG` |
| `validate_current_workflow()` | Pydantic schema + hardware constraint validation on `_WORKING_CONFIG` |
| `validate_config(config_path)` | Same validation on an arbitrary YAML file path |
| `show_full_config()` | Dumps current `_WORKING_CONFIG` as YAML (debug/review) |

### Protocol Generation & Simulation Tools

| Tool | What it does |
|------|-------------|
| `generate_protocol()` | Renders `_WORKING_CONFIG` → OT-2 Python protocol, saves to `runs/generated/generated_{workflow}.py`, returns SHA256 |
| `simulate_protocol(protocol_path)` | Runs `python -m opentrons.simulate` locally, saves PASS/FAIL + SHA256 to `runs/simulations.json` |

### Robot Execution Tools (live SSH — lab laptop only)

| Tool | What it does |
|------|-------------|
| `check_robot_connection()` | SSH BatchMode ping to `ROBOT_IP`, checks `opentrons_execute` exists |
| `get_robot_hardware_status()` | SSHs to robot, reads `/var/lib/opentrons/attached_instruments.json` to verify actual pipettes |
| `deploy_protocol_to_robot(protocol_path, config_paths)` | Creates timestamped run folder, SCPs protocol + manifest to robot at `REMOTE_USER_STORAGE/ot2_runs/run_*` |
| `execute_protocol_on_robot(remote_protocol_path, protocol_hash)` | Verifies SHA256 has a PASS simulation record, then runs `opentrons_execute <path>` on robot via SSH |

---

## Agent Workflow — Step-by-Step Logic

The agent follows this enforced sequence (hardcoded in its system prompt):

```
1. list_available_workflows()           → Identify what's available
2. load_workflow_defaults(type)         → Load YAML defaults into working config
3. [update_workflow_config(updates)]    → Apply any user customizations (optional)
4. validate_current_workflow()          → MUST pass before proceeding
5. generate_protocol()                  → Render .py protocol file + get SHA256
6. simulate_protocol(path)              → MUST PASS before any physical run
7. check_robot_connection()             → Confirm robot is reachable
8. get_robot_hardware_status()          → Confirm actual pipettes match config
9. [AGENT presents PRE-RUN SUMMARY]     → Shows: protocol name, SHA256, robot IP, 
                                           deck layout, pipettes, transfer count
10. [USER types: RUN ROBOT]             → Mandatory confirmation gate
11. deploy_protocol_to_robot(path)      → SCP files to robot
12. execute_protocol_on_robot(path, hash) → Run via opentrons_execute
```

**Safety rules enforced by the agent:**
- Will not call `execute_protocol_on_robot` if `simulations.json` has no PASS record for that SHA256
- Will not proceed past step 9 without explicit `RUN ROBOT` from user
- Re-reads pipette config from YAML at generate time to prevent LLM hallucination
- Uses BatchMode SSH (no interactive prompts) — fails loudly if keys are missing

---

## Configuration: `.env` Variables

All runtime configuration lives in `.env` at the project root (lab laptop only):

```bash
# Robot
ROBOT_IP=169.254.46.57          # OT-2 link-local IP
ROBOT_SSH_USER=root             # SSH user (always root on OT-2)
ROBOT_SSH_KEY_PATH=/path/to/ot2_ssh_key   # Private key for passwordless SSH
ROBOT_SSH_IDENTITIES_ONLY=true  # Use only the configured key
ROBOT_SSH_LEGACY_RSA=true       # Required by older OT-2 SSH servers

# LLM auth by laptop role:
# Simulation laptop testing may use LLM_PROVIDER=api-key + GOOGLE_API_KEY.
# Real robot laptop live interactions must use Vertex AI / gcloud ADC.
LLM_PROVIDER=vertexai
GOOGLE_API_KEY=                         # Simulation laptop testing only
GOOGLE_CLOUD_PROJECT=your-project-id    # Required for Vertex AI / gcloud ADC
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-2.5-flash           # Or any supported Gemini model

# Optional
GEMINI_BASE_URL=                # Leave blank unless using a proxy
REMOTE_USER_STORAGE=/var/lib/jupyter/notebooks   # Robot filesystem path
```

---

## Available Workflows (Registered in `src/core/workflows/registry.py`)

| Workflow Type | Config File | Description |
|---------------|-------------|-------------|
| `dilution` | `configs/workflows/defaults/dilution.yaml` | Multi-step dilution series with P300 single |
| `printing` | `configs/workflows/defaults/printing.yaml` | Unified nanoparticle printing (dilution + mixing + printing) |
| `printing_demo` | `configs/workflows/defaults/printing_demo.yaml` | Demo-scale printing |
| `printing_12` | `configs/workflows/defaults/printing_12.yaml` | 12-well plate variant |
| `printing_96` | `configs/workflows/defaults/printing_96.yaml` | 96-well plate variant |

> **Note:** the flagship `vial_dilution_print` demo is **not** a registry workflow — it
> has its own build-embed-CONFIG pipeline and its own agent
> (`src/agents/vial_print_agent.py`, see above), driven from
> `configs/workflows/defaults/vial_dilution_print.yaml`.

---

## Session Logging

Every agent session automatically logs to:
```
src/agents/logs/agent_session_YYYYMMDD_HHMMSS.log
```

Each log entry contains:
- User input
- Agent text response
- Full LangGraph debug trace (tool calls, tool outputs, intermediate reasoning)

---

## Rate Limiting

The `RateLimitGuard` (`src/utils/limits_per_minute.py`) enforces a 60-second rolling window on Gemini API calls. It is **off by default** and activated with `--rate-limit`.

Use `--rate-limit` if you are on Gemini Free Tier and hitting `429 RESOURCE_EXHAUSTED` errors.

---

## Stress Testing & Validation

```bash
# Run the 10-test benchmark suite against the real agent
python -m src.agents.run_stress_tests

# Run the simulation integration test
python -m src.agents.test_simulation_agent

# Check available Gemini models
python -m src.agents.check_models
```

---

## Related Documentation

| File | Contents |
|------|---------|
| [SOP_AI_Agent_Lab_Laptop.md](SOP_AI_Agent_Lab_Laptop.md) | Step-by-step SOP for running on the real robot |
| [LangChain_ot2_agent_guide.md](LangChain_ot2_agent_guide.md) | Original architecture deep-dive |
| [SOP_Robot_Deployment.md](SOP_Robot_Deployment.md) | Manual robot deployment procedures |
| [SOP_Simulation_Testing.md](SOP_Simulation_Testing.md) | Simulation testing protocol |
