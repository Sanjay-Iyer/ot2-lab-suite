# SOP: Automated Printing Workflow on OT-2

This document provides the standard operating procedure for running the **Nanoparticle Printing Workflow** using the AI Automation Agent.

## 1. Prerequisites

### Environment Setup
- Open a terminal and ensure you are in the project root: `c:\code\opentrons_home\ot2-lab-suite`.
- Activate the dedicated AI environment:
  ```powershell
  conda activate ai
  ```

### Robot Connectivity
- Connect the OT-2 via Ethernet.
- Ensure the `.env` file contains the correct `ROBOT_IP=169.254.46.57`.
- Verify you can ping the robot: `ping 169.254.46.57`.

---

## 2. Phase 1: Planning & Simulation (Local)

Always perform a simulation before touching the physical robot.

1.  **Launch the Agent**:
    Start a session with a printing-specific request:
    ```powershell
    python -m src.agents.main "Configure a standard printing run and run a simulation"
    ```

2.  **Review Configuration**:
    The agent will load `configs/workflows/defaults/printing.yaml`. Review the parameters it presents (e.g., `volume_per_print_ul`, `total_print_positions`).

3.  **Run Simulation**:
    Ask the agent: *"Proceed with local simulation."*
    - The agent will call `generate_protocol()` followed by `simulate_protocol()`.
    - **Verification**: Ensure the agent reports `SIMULATION PASSED` and provides a SHA256 hash (e.g., `hash f5f5f5f5`).
    - The record is saved in `robot_data/data/simulations.json`.

---

## 3. Phase 2: Robot Pre-Run Checks

Once simulation passes, verify the hardware.

1.  **Deck Setup**:
    Physically load the robot according to the summary provided by the agent:
    - **Slot 1**: Stock Reagent Rack.
    - **Slot 2**: Printer Tray.
    - **Slot 11**: Tip Rack (compatible with your pipette).

2.  **Connectivity Preflight**:
    Ask the agent: *"Check robot connection."*
    - The agent will run `check_robot_connection()`.
    - **Success Criteria**: It must report `Connectivity PASSED: Instrument is READY`.

---

## 4. Phase 3: Physical Execution

Physical execution requires a strict confirmation protocol.

1.  **Request Execution**:
    Ask the agent: *"I am ready to run this on the physical robot."*

2.  **Review Pre-Run Summary**:
    The agent will present a final summary:
    - **Protocol**: `generated_printing.py`
    - **Hash**: Matches the simulation.
    - **Deck**: Verified slots.
    - **Pipette**: Right/Left mount verification.

3.  **MANDATORY CONFIRMATION**:
    You must type exactly the following phrase when prompted:
    ```text
    RUN ROBOT
    ```

4.  **Monitor Progress**:
    The agent will call `deploy_protocol_to_robot()` (transferring files via SCP) and then `execute_protocol_on_robot()`.
    - Files are staged in `robot_data/deploy/run_YYYYMMDD_HHMMSS/`.
    - Live logs will stream to your terminal.

---

## 5. Phase 4: Post-Run & Data Recovery

1.  **Sync Logs**:
    Pull the physical run logs and calibration data back to your PC for analysis:
    ```powershell
    python scripts/sync_robot.py
    ```

2.  **Cleanup**:
    The staging folder in `robot_data/deploy/` can be archived or deleted once the run is confirmed successful in the logs.

---

## Troubleshooting

- **"Hash Mismatch"**: If you manually edit the generated Python file after simulation, the robot will refuse to run. You must re-simulate the modified file.
- **"SSH Timeout"**: Ensure no other application (like the Opentrons App) is hogging the connection if bandwidth is limited, and check your Ethernet cable.
- **"BatchMode Failed"**: Ensure your SSH public key is added to the robot's `authorized_keys` file.
