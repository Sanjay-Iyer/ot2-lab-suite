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

✅ **If this passes**, your Opentrons Python environment is correctly installed. Move to Phase 2.

---

## 1b. Multi-Mode Validation (diagnostic protocols)

Some protocols have several run modes (e.g. the tube-rack verification —
see [verify_tuberack.md](verify_tuberack.md)). Validate **every mode at once**:

```powershell
python scripts\validate_protocol.py
```

It runs each flag combination through the simulator and **asserts on the output
text**, then prints `ALL CASES PASSED` / `SOME CASES FAILED`.

> ⚠️ **Exit code ≠ success.** `opentrons.simulate` returns **0 even when a
> protocol raises at runtime** (it prints the error but exits clean). So the
> `simulate_protocol.py` "SIMULATION SUCCESS" banner — keyed off the exit code —
> can mask a real error (this is how a removed-API call slipped through once).
> For anything non-trivial, use `validate_protocol.py` or read the simulator
> output for `Error`/`Exception`, rather than trusting the banner alone.

---

## 2. AI-Orchestrated Simulation (Requires API Key)
Use the AI Agent to configure a real workflow, generate the protocol file, and simulate it automatically.

**Ensure your API Key is set in `.env`:**
```env
GOOGLE_API_KEY=your_actual_api_key_here
```

**Command the AI to configure and simulate:**
```powershell
# Default (no rate limiting delay)
python -m src.agents.main "Configure a standard printing run and run a simulation."

# With rate limiting (adds a 5-second delay between API calls to avoid 429 errors)
python -m src.agents.main --rate-limit "Configure a standard printing run and run a simulation."
```
*Expected Result: The AI will generate the protocol file, run the Opentrons simulator under the hood, and report whether the simulation passed or failed based on hardware constraints.*

✅ **If the simulation passes**, your protocol is validated and safe for the physical robot. Move to Phase 3 or 4 to deploy.

⚠️ **Do NOT skip simulation.** The agent will refuse to deploy a protocol to the robot unless it has a passing simulation hash on record.

## Pre-Flight Checklist (Before Physical Robot)
Before running Phase 3 or 4, confirm **every item** on this list:

- [ ] **Phase 1 passed** — `python -m opentrons.simulate` ran without errors
- [ ] **Phase 2 passed** — AI agent reported `SIMULATION PASSED`
- [ ] **OT-2 is powered on** and the status light is solid blue
- [ ] **Ethernet cable** is connected between your laptop and the OT-2
- [ ] **Robot IP is set** in `.env`: `ROBOT_IP=169.254.46.57`
- [ ] **SSH private key exists** on disk at the path you configured
- [ ] **SSH key path is set** in `.env`: `ROBOT_SSH_KEY_PATH=<path to your private key>`
- [ ] **Connectivity check passes** — run this and confirm SSH is reachable:
  ```powershell
  python -m scripts.check_connectivity
  ```
  You should see `PASS` for both the socket check (port 22) and the SSH BatchMode check.
- [ ] **Correct labware is physically loaded** on the OT-2 deck in the slots matching your protocol configuration
- [ ] **Tip racks have tips** — do not run with empty or partially used racks unless intentional

---

## 3. Manual Deployment & Execution (Physical Robot)
If you generated a protocol and want to manually send it to the physical OT-2 and execute it without AI.

**Ensure your Robot config is set in `.env`:**
```env
ROBOT_IP=169.254.46.57
ROBOT_SSH_KEY_PATH=C:\Users\iyersn\.ssh\id_rsa_opentrons
```
> ⚠️ The `ROBOT_SSH_KEY_PATH` is **machine-specific**. Use the actual path to YOUR private key on THIS laptop. The commands below use `$KEY` as a shorthand — set it first.

**Step A: Set your key path variable in PowerShell**
```powershell
$KEY = "C:\Users\iyersn\.ssh\id_rsa_opentrons"
```

**Step B: Transfer the file via SCP**
*(Replace `run_123` with a unique folder name, and `generated_printing.py` with your actual file).*
```powershell
# Create a folder on the robot
ssh -i $KEY root@169.254.46.57 "mkdir -p /var/lib/opentrons/user_storage/ot2_runs/run_123"

# Copy the file to the robot (-O forces legacy SCP protocol; the OT-2 lacks sftp-server)
scp -O -i $KEY src\protocols\generated\generated_printing.py root@169.254.46.57:/var/lib/opentrons/user_storage/ot2_runs/run_123/
```

**Step C: Execute via SSH**
```powershell
ssh -i $KEY root@169.254.46.57 "opentrons_execute /var/lib/opentrons/user_storage/ot2_runs/run_123/generated_printing.py"
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
