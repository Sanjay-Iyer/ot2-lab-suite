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
│   └── utils/          # Preflight & Rate-limiting utilities
├── robot_data/         # Shared data mirror
│   ├── data/           # Mirrors /data on the OT-2 (Logs, Calibration)
│   └── deploy/         # Local staging for robot uploads
├── scripts/            # Deployment & Synchronization scripts
├── tests/              # Unit & Integration tests
└── docs/               # System & Agent guides
```

## 🛠️ Configuration

All system parameters are managed in **`src/core/config.py`**. 
To connect to your robot, create a `.env` file in the project root:

```bash
ROBOT_IP=169.254.46.57
GOOGLE_API_KEY=your_key_here
GEMINI_MODEL=gemini-1.5-flash
```

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
- [AI Agent Architecture](LangChain_ot2_agent_guide.md)
- [Preflight Validation Guide](../src/utils/README_preflight.md)
