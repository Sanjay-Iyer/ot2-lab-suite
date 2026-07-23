# Opentrons OT-2 Lab Suite

A professional, platform-agnostic suite for automating nanoparticle printing and optimization workflows on the Opentrons OT-2.

## 🚀 Key Features

- **Root-Aware Architecture**: Absolute path-independence via a centralized `Config` registry.
- **Preflight Validation**: Automated safety checks for Windows-to-Linux deployment leaks.
- **Resilient AI Agent**: LangChain-powered reasoning with built-in Gemini rate-limit protection.
- **High-Fidelity Simulation**: Mirroring the OT-2's internal `/data` structure locally for accurate testing.

## 📂 Project Structure

```text
ot2-lab-suite/
├── src/
│   ├── core/           # Config registry & base classes
│   ├── agents/         # LangChain logic & Tool library
│   ├── printing/       # Lab automation scripts (Protocols, Planners)
│   ├── protocols/      # OT-2 protocols (verify_tuberack.py, simulate_protocol.py, generated/)
│   └── utils/          # Preflight & Rate-limiting utilities
├── configs/
│   ├── labware/        # Labware YAML configs (→ generate_labware.py)
│   └── workflows/      # Workflow YAML configs
├── labware/            # Generated Opentrons labware JSON (deployed to the robot)
├── robot_data/         # Shared data mirror
│   ├── data/           # Mirrors /data on the OT-2 (Logs, Calibration)
│   └── deploy/         # Local staging for robot uploads
├── scripts/            # Deploy / sync / generate_labware / Workflow 01 validation
├── tests/              # Unit & Integration tests
└── docs/               # System & Agent guides
```

## 🛠️ Configuration

All system parameters are managed in **`src/core/config.py`**. 
To connect to your robot, create a `.env` file in the project root:

```bash
OT2_ROBOT_HOST=OT2CEP20220929R02.local
ROBOT_SSH_USER=root
ROBOT_SSH_KEY_PATH=C:\Users\<username>\.ssh\id_rsa_opentrons
ROBOT_SSH_IDENTITIES_ONLY=true
ROBOT_SSH_LEGACY_RSA=true
LLM_PROVIDER=vertexai
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-1.5-flash
```

Use `LLM_PROVIDER=api-key` and `GOOGLE_API_KEY` only for simulation-laptop
agent testing. See [OT-2 SSH compatibility](OT2_SSH_COMPATIBILITY.md).

## 🧪 Quick Start

### 1. Validate Your Environment
Ensure your code is clean and safe for the robot's Linux environment:
```bash
python -m src.utils.preflight src/
```

### 2. Run the AI Agent
Start the agent to plan and simulate an experiment:
```bash
python -m src.agents.main "Configure a 5-step dilution and run a mock simulation"
```

### 3. Sync from Robot
Pull the latest logs and calibration data from the physical OT-2:
```bash
python scripts/sync_robot.py
```

## 📖 Documentation

**Workflows & guides**
- [Tube Rack Verification Protocol](verify_tuberack.md) — simulate, deploy & physically run the custom-rack checker
- [AI Agent Architecture](LangChain_ot2_agent_guide.md)
- [Preflight Validation Guide](../src/utils/README_preflight.md)

**SOPs**
- [Simulation & Testing](SOP_Simulation_Testing.md) — Workflow 01 build/simulation and validation gates
- [Robot Deployment](SOP_Robot_Deployment.md) — connectivity, custom-labware deploy, execute

**Skills** (`skills/`)
- [ot2-labware](../skills/ot2-labware/SKILL.md) — create/regenerate labware definitions
- [ot2-protocols](../skills/ot2-protocols/SKILL.md) — build, validate & simulate protocols
- [ot2-robot-control](../skills/ot2-robot-control/SKILL.md) — deploy & run on hardware (lab laptop)
