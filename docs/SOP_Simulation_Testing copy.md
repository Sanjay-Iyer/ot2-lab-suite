# SOP: Opentrons Simulation and Execution Guide

This Standard Operating Procedure (SOP) outlines the exact, step-by-step commands required to simulate protocols and deploy them to the physical OT-2 robot.

You must run all commands from the project root inside your Conda environment:
```powershell
conda activate ai
cd C:\code\opentrons_home\ot2-lab-suite
```

---

## 1. Manual Simulation (No AI)
Verify that your local Opentrons installation is working by simulating a mock test protocol. This does not require an API key or a physical robot connection.

**Run the simulation:**
```powershell
python -m opentrons.simulate src\protocols\mock_test_protocol.py
```
*Expected Result: You should see the robot's steps printed out (e.g., "Simulation is working perfectly!").*

---

## 2. AI-Orchestrated Simulation (Requires API Key)
Use the AI Agent to configure a real workflow, generate the protocol file, and simulate it automatically.

**Ensure your API Key is set in `.env`:**
```env
GOOGLE_API_KEY=your_actual_api_key_here
```

**Command the AI to configure and simulate:**
```powershell
python -m src.agents.main "Configure a standard printing run and run a simulation."
```
*Expected Result: The AI will generate the protocol file, run the Opentrons simulator under the hood, and report whether the simulation passed or failed based on hardware constraints.*

---

## 3. Manual Deployment & Execution (Physical Robot)
If you generated a protocol and want to manually send it to the physical OT-2 and execute it without AI.

**Ensure your Robot config is set in `.env`:**
```env
ROBOT_IP=169.254.46.57
ROBOT_SSH_KEY_PATH=C:\code\opentrons_home\ot2-lab-suite\keys\ot2_automation_key
```

**Step A: Transfer the file via SCP**
*(Replace `run_123` with a unique folder name, and `generated_printing.py` with your actual file).*
```powershell
# Create a folder on the robot
ssh -i C:\code\opentrons_home\ot2-lab-suite\keys\ot2_automation_key root@169.254.46.57 "mkdir -p /var/lib/opentrons/user_storage/ot2_runs/run_123"

# Copy the file to the robot
scp -i C:\code\opentrons_home\ot2-lab-suite\keys\ot2_automation_key robot_data\deploy\generated_printing.py root@169.254.46.57:/var/lib/opentrons/user_storage/ot2_runs/run_123/
```

**Step B: Execute via SSH**
```powershell
ssh -i C:\code\opentrons_home\ot2-lab-suite\keys\ot2_automation_key root@169.254.46.57 "opentrons_execute /var/lib/opentrons/user_storage/ot2_runs/run_123/generated_printing.py"
```

---

## 4. AI-Orchestrated Deployment (Physical Robot)
Let the AI orchestrate the connection, staging, and execution on the physical OT-2.

*(Note: The AI will strictly refuse to deploy a protocol to the physical robot unless it has successfully passed a simulation hash-check first).*

**Command the AI to deploy and execute:**
```powershell
python -m src.agents.main "I want to deploy and run the protocol we just generated on the physical robot. Please check the connection, deploy the file, and execute it."
```

*Expected Result: The AI will use `check_robot_connection()` to ping the instrument, `deploy_protocol_to_robot()` to transfer the file via SCP, and `execute_protocol_on_robot()` to trigger it over SSH. It will stream the execution output back to you in the terminal.*
