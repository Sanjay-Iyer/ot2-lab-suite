# Agentic Automation of Lab Workflows
## LangChain AI Agent Architecture & System Guide

**System Scope:** Autonomous formulation and optimization via Opentrons OT-2.
**Framework:** LangChain Tool-Calling Agent executing localized simulations and preflight-validated deployments.

---

## 1. Safety & Reliability Layers

To ensure autonomous operations are safe and cost-effective, the agent uses three core protection layers:

### A. Rate-Limit Protection (`RateLimitGuard`)
The agent routes all LLM calls through a conservative safety wrapper. This prevents `429 RESOURCE_EXHAUSTED` errors on the Gemini Free Tier by enforcing a 60-second rolling window and natural delays between reasoning steps.

### B. Path-Aware Configuration (`Config`)
All agent tools reference the centralized `Config` registry in `src/core/config.py`. This ensures that when the agent writes a protocol, it uses the correct relative paths for the current execution environment (Laptop vs. Robot).

### C. Preflight Validation (`preflight.py`)
Before any file is staged for deployment, it is audited for Windows path leaks (`C:\`), encoding issues, and syntax errors. This prevents "broken" code from reaching the hardware.

---

## 2. Core Tool Library

The agent has access to the following high-level capabilities:

### `configure_printing_parameters`
* **Purpose:** Sets experimental targets (concentration, volumes, variants).
* **Output:** Generates `experiment_config.json` in the `src/printing/configs/` directory.

### `run_mock_simulation`
* **Purpose:** Runs a local high-fidelity simulation using the OT-2's actual deck calibration.
* **Mechanism:** Uses `opentrons.simulate` module.
* **Self-Correction:** If simulation fails, the agent parses `STDERR` and adjusts parameters automatically.

---

## 3. Operational Workflow

1. **User Intent:** User provides a natural language goal.
2. **Planning:** The agent uses the `configure_printing_parameters` tool.
3. **Verification:** The agent triggers `run_mock_simulation`.
4. **Validation:** The agent uses internal logic (or the `preflight` tool) to verify file integrity.
5. **Human-in-the-Loop:** For live runs, the agent outputs the validated protocol for final human approval before deployment.

---

## 4. Troubleshooting the Agent

### Environment Variables
Ensure the following are set in your `.env`:
* `GOOGLE_API_KEY`: Allowed for simulation-laptop Gemini testing. Live robot laptop agent runs use Vertex AI / gcloud ADC (`LLM_PROVIDER=vertexai`, `GOOGLE_CLOUD_PROJECT`).
* `OT2_ROBOT_HOST`: Optional override; normally `configs/robot.yaml` and mDNS are used.

### Simulation Failures
If the agent fails to recover from a simulation error, check the `robot_data/data/logs/` directory for the raw execution trace. This usually indicates a physical collision or labware misconfiguration that requires manual intervention.
