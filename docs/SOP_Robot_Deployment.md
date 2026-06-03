# SOP: OT-2 Robot Deployment Guide

This document walks you through everything required to connect to, deploy to, and execute protocols on the physical Opentrons OT-2 robot from your Windows laptop.

> **Prerequisite:** You must have already passed Phase 1 (manual simulation) and Phase 2 (AI simulation) from the [Simulation Testing SOP](SOP_Simulation_Testing.md) before proceeding.

---

## 1. Physical Setup

### 1a. Power On the OT-2
- Plug in the OT-2 power adapter.
- Wait for the status light on the front panel to turn **solid blue** (this takes ~60 seconds).
- If the light is blinking or red, the robot is still booting or has an error.

### 1b. Connect Ethernet
- Plug an Ethernet cable directly from your laptop to the OT-2's Ethernet port (on the back of the robot).
- **Do not use Wi-Fi.** The OT-2 uses a direct link-local Ethernet connection.
- Windows should auto-assign a `169.254.x.x` address to your Ethernet adapter within ~30 seconds.

### 1c. Verify Your Laptop Has a Link-Local IP
Open PowerShell and run:
```powershell
ipconfig
```
Look for your **Ethernet adapter** section. You should see an IP like `169.254.x.x`. If you see `Media disconnected`, the cable is not connected properly.

---

## 2. SSH Key Setup

The OT-2 authenticates over SSH using a private key (not a password). You need this key on your laptop.

### 2a. Check If You Already Have a Key
```powershell
dir C:\code\opentrons_home\ot2-lab-suite\keys\
```
If you see `ot2_automation_key` (no extension), you already have it. Skip to Step 3.

### 2b. Generate a New Key (If Missing)
```powershell
ssh-keygen -t rsa -b 4096 -f C:\code\opentrons_home\ot2-lab-suite\keys\ot2_automation_key -N ""
```
This creates two files:
- `ot2_automation_key` — your private key (keep this secret)
- `ot2_automation_key.pub` — your public key (this goes on the robot)

### 2c. Copy the Public Key to the Robot
You need to add your public key to the robot's authorized keys. If you have temporary password access or the Opentrons app:
```powershell
# Read your public key
type C:\code\opentrons_home\ot2-lab-suite\keys\ot2_automation_key.pub

# SSH into the robot (you may need the default password for first-time access)
ssh root@169.254.46.57

# On the robot, paste your public key:
echo "PASTE_YOUR_PUBLIC_KEY_HERE" >> ~/.ssh/authorized_keys
exit
```

Alternatively, use the **Opentrons Desktop App** to add your SSH key via the robot settings page.

---

## 3. Configure `.env`

Open `.env` in the project root and ensure these lines are set:
```env
# OT-2 Robot
ROBOT_IP=169.254.46.57
ROBOT_SSH_USER=root
ROBOT_SSH_KEY_PATH=C:\code\opentrons_home\ot2-lab-suite\keys\ot2_automation_key
```

> ⚠️ `ROBOT_SSH_KEY_PATH` must point to the **private** key (no `.pub` extension).
> If this is missing, the agent and all deployment scripts will refuse to connect.

---

## 4. Verify Connectivity

Run the built-in diagnostic from the project root:
```powershell
conda activate ai
cd C:\code\opentrons_home\ot2-lab-suite
python -m scripts.check_connectivity
```

### What Each Section Should Show

| Section | Expected Result | If It Fails |
|---|---|---|
| 1. Configuration Loaded | Your masked API key, robot IP, SSH key path | Check your `.env` file |
| 2. Gemini/API DNS Check | `PASS: DNS resolved...` | Run `ipconfig /flushdns`, check internet |
| 3. Gemini/API Request Check | `PASS: Successfully connected...` | Check `GOOGLE_API_KEY` in `.env` |
| 4. OT-2 IP/Socket Reachability | `PASS: Port 22 reachable...` | Check Ethernet cable, robot power, IP |
| 5. OT-2 SSH BatchMode Check | `PASS: SSH connected...` | Check SSH key path, key permissions |

**All 5 sections must show PASS before you deploy to the robot.**

---

## 5. Deploy and Run (Manual Method)

### 5a. Transfer the Protocol
```powershell
# Create a run folder on the robot
ssh -i C:\code\opentrons_home\ot2-lab-suite\keys\ot2_automation_key root@169.254.46.57 "mkdir -p /var/lib/opentrons/user_storage/ot2_runs/my_run"

# Copy the generated protocol to the robot
scp -i C:\code\opentrons_home\ot2-lab-suite\keys\ot2_automation_key src\protocols\generated\generated_printing.py root@169.254.46.57:/var/lib/opentrons/user_storage/ot2_runs/my_run/
```

### 5b. Deploy Custom Labware (only if your protocol uses it)

If the protocol calls `load_labware(name, namespace="custom_beta", version=...)`,
the definition must be in the robot's custom-labware store **first**, or analysis
fails with `Labware "<name>" not found ... namespace "custom_beta"`.

```powershell
# Reads namespace/loadName/version from the JSON, makes the nested dir, copies + verifies
python -m scripts.deploy --labware labware\tuberack_3dprint_20ml_8vials_v1.json

# Preview the destination only (no SSH/SCP)
python -m scripts.deploy --labware labware\tuberack_3dprint_20ml_8vials_v1.json --dry-run
```

Lands at `/data/labware/v2/custom_definitions/<namespace>/<loadName>/<version>.json`
— the on-robot filename is the **version** (e.g. `1.json`). The Opentrons App's
**Labware → Import** writes to the same place. (The bulk `deploy` and `sync_robot`
do **not** manage this path.)

### 5c. Execute the Protocol
```powershell
ssh -i C:\code\opentrons_home\ot2-lab-suite\keys\ot2_automation_key root@169.254.46.57 "opentrons_execute /var/lib/opentrons/user_storage/ot2_runs/my_run/generated_printing.py"
```

> ⚠️ **The robot will start moving immediately.** Make sure the deck is loaded correctly and you are watching the instrument.

---

## 6. Deploy and Run (AI Agent Method)

Let the AI handle connectivity checks, file transfer, and execution in one session:
```powershell
python -m src.agents.main "Check the robot connection, deploy the printing protocol, and execute it."
```

The agent will:
1. Run `check_robot_connection()` — verify SSH is reachable
2. Run `deploy_protocol_to_robot()` — SCP the file to a unique timestamped folder
3. Show you a summary (protocol hash, deck layout, pipettes) and ask for confirmation
4. Wait for you to type **`RUN ROBOT`** before executing
5. Run `execute_protocol_on_robot()` — trigger `opentrons_execute` over SSH

> **Safety:** The agent will refuse to deploy if:
> - The protocol has not passed simulation
> - `ROBOT_SSH_KEY_PATH` is missing
> - The robot is unreachable on port 22

---

## Troubleshooting

| Problem | Likely Cause | Fix |
|---|---|---|
| `Port 22 unreachable` | Robot off, cable unplugged, or wrong IP | Check physical setup (Step 1) |
| `ROBOT_SSH_KEY_PATH is missing` | `.env` not configured | Set the path in `.env` (Step 3) |
| `Permission denied (publickey)` | Key not on robot or wrong key file | Re-copy public key to robot (Step 2c) |
| `Network is unreachable` | No link-local IP on laptop | Unplug/replug Ethernet, wait 30s |
| `Host key verification failed` | Robot was reimaged or IP changed | Run `ssh-keygen -R 169.254.46.57` then retry |
