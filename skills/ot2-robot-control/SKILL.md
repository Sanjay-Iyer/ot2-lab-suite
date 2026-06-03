---
name: ot2-robot-control
description: Interact with the physical OT-2 robot from the lab laptop — check connectivity, SCP/deploy protocol files over, SSH in, and run workflows on the robot with opentrons_execute. Use when the user wants to connect to the real OT-2, copy files to the robot, run a protocol on hardware, or troubleshoot the robot link. LAB LAPTOP ONLY (requires the live .env, SSH key, and a connected robot).
---

# OT-2 Robot Control (Lab Laptop Only)

Deploy and run workflows on the **physical OT-2** over SSH/SCP.

> ⚠️ **This skill only works on the lab laptop** — the machine with the OT-2
> connected, the live `.env`, and the SSH private key. On the dev/code laptop
> the robot is unreachable; stop at simulation (see
> [ot2-protocols](../ot2-protocols/SKILL.md)).

## When to use this skill

- "Connect to the OT-2 / check the robot is online"
- "Copy the protocol over to the robot"
- "Run the dilution workflow on the real robot"
- "SSH into the robot and check calibration / pipettes"
- "The robot link isn't working — diagnose it"

## Connection settings (from `.env`)

| Variable | Meaning | Typical value |
|----------|---------|---------------|
| `ROBOT_IP` | OT-2 link-local IP | `169.254.46.57` |
| `ROBOT_SSH_USER` | SSH user (always root on OT-2) | `root` |
| `ROBOT_SSH_KEY_PATH` | Path to the private key | `C:\Users\<you>\.ssh\id_rsa_opentrons` |
| `ROBOT_REMOTE_RUN_DIR` | Remote run directory | `/var/lib/opentrons/user_storage/ot2_runs` |

Resolved in [`src/core/config.py`](../../src/core/config.py) as
`Config.ROBOT_IP`, `Config.ROBOT_SSH_USER`, `Config.ROBOT_SSH_KEY_PATH`,
`Config.REMOTE_USER_STORAGE`.

## Step 0 — Always check connectivity first

```bash
python -m scripts.check_connectivity
```

This runs a 6-part diagnostic: config loaded, Gemini DNS/auth, **OT-2 reachable
on port 22**, **non-interactive SSH (BatchMode)**, and **robot calibration paths**.
Fix any FAIL here before deploying.

Quick manual checks:

```powershell
# Is the robot reachable?
ping 169.254.46.57

# Does passwordless SSH work? (BatchMode = no interactive prompts)
ssh -i "$env:ROBOT_SSH_KEY_PATH" -o BatchMode=yes root@169.254.46.57 "echo SSH_OK"
```

## Path A — via the AI agent (recommended, with safety gates)

```bash
python -m src.agents.main
```

The agent enforces the full safety sequence before any physical run:

```
A. simulate_protocol passed for the current SHA256
B. get_robot_hardware_status   → actual pipettes vs config
C. check_robot_connection      → SSH/BatchMode + opentrons_execute present
D. PRE-RUN SUMMARY             → protocol, hash, IP, deck, pipettes, transfers
E. USER types exactly: RUN ROBOT
F. deploy_protocol_to_robot → execute_protocol_on_robot
```

```
[USER]: Run the dilution protocol on the robot
[AGENT]: (check_robot_connection, get_robot_hardware_status) ... PRE-RUN SUMMARY ...
         Reply with exactly: RUN ROBOT
[USER]: RUN ROBOT
[AGENT]: (deploy_protocol_to_robot → execute_protocol_on_robot) Execution COMPLETE.
```

### Agent tools (in `tools.py`)

| Tool | Purpose |
|------|---------|
| `check_robot_connection()` | SSH BatchMode reachability + `opentrons_execute` present |
| `get_robot_hardware_status()` | Read attached pipettes from the robot via SSH |
| `deploy_protocol_to_robot(path, configs)` | Stage + SCP to a timestamped run folder + manifest |
| `execute_protocol_on_robot(remote_path, hash)` | Verify PASS sim, then `opentrons_execute` |

`execute_protocol_on_robot` **refuses to run** unless the protocol's SHA256 has a
`PASS` record in `robot_data/data/simulations.json`.

## Path B — manual SCP + SSH + execute

All commands use **non-interactive BatchMode** SSH with the key. Remote paths are
**always forward-slash POSIX paths** (the robot is Linux).

```powershell
# Set once for the session (PowerShell)
$IP  = "169.254.46.57"
$KEY = "$env:USERPROFILE\.ssh\id_rsa_opentrons"
$REMOTE = "/var/lib/opentrons/user_storage/ot2_runs"
$OPTS = @("-i", $KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
```

```powershell
# 1. Create the remote run directory
ssh @OPTS root@$IP "mkdir -p $REMOTE"

# 2. SCP the protocol over  (NOTE the -O flag: legacy SCP protocol, required for OT-2)
scp -O @OPTS "src/protocols/generated/generated_dilution.py" "root@${IP}:$REMOTE/generated_dilution.py"

# 3. Verify it landed
ssh @OPTS root@$IP "ls -lah $REMOTE/generated_dilution.py"

# 4. Run it on the robot
ssh @OPTS root@$IP "opentrons_execute $REMOTE/generated_dilution.py"
```

> **Why `scp -O`:** newer OpenSSH defaults to the SFTP protocol, which the OT-2's
> dropbear SSH server does not support. The `-O` flag forces the legacy SCP
> protocol. Every SCP to the robot needs it.

### Helper scripts (laptop-side)

```bash
# End-to-end smoke test: SSH check → upload → verify → opentrons_execute
python scripts/run_smoke_test.py --robot-ip 169.254.46.57 --ssh-key "C:\path\to\key"

# Bulk-deploy the staging directory contents to the robot
python -m scripts.deploy

# Deploy ONE custom labware JSON to the robot's custom_definitions store
# (reads namespace/loadName/version from the JSON; --dry-run previews the path)
python -m scripts.deploy --labware labware/<load_name>.json

# Pull config / labware / pipettes / logs back FROM the robot
python -m scripts.sync_robot
```

> **Custom labware path.** `opentrons_execute` resolves
> `load_labware(name, namespace=, version=)` from
> `/data/labware/v2/custom_definitions/<namespace>/<loadName>/<version>.json`
> (the on-robot filename is the **version**, e.g. `1.json`). `scripts.deploy
> --labware` puts it there; the Opentrons App's Labware Import writes to the same
> place. Neither the bulk `deploy` nor `sync_robot` manage this path.

## Common errors & fixes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `Connection timed out` / port 22 unreachable | Robot off, cable unplugged, wrong IP | Power-cycle, check cable, verify `ROBOT_IP` |
| `Permission denied (publickey)` | Wrong/missing key, key not on robot | Check `ROBOT_SSH_KEY_PATH`; confirm key is in robot `authorized_keys` |
| SCP hangs or `subsystem request failed` | Missing `-O` flag | Add `-O` to the `scp` command |
| `ROBOT_SSH_KEY_PATH is missing` | `.env` blank | Set the key path in `.env` |
| `opentrons_execute: not found` | Robot software issue | SSH in and check `which opentrons_execute` |
| `AreaNotInDeckConfigurationError` | apiLevel mismatch (Flex API on OT-2) | Fix `apiLevel` in the protocol/config |
| `instrument was requested, but no instrument is present` | Pipette/mount mismatch | Run `get_robot_hardware_status`, align config to actual hardware |
| Robot traffic routed through proxy | `ROBOT_IP` not in `NO_PROXY` | Add the IP to `NO_PROXY` (config.py does this automatically) |

## Safety rules (do not bypass)

1. **Never run a protocol on hardware without a PASS simulation** for its exact SHA256.
2. **Confirm hardware matches config** (`get_robot_hardware_status`) before running.
3. Use **BatchMode SSH** — if it prompts for a password, the key auth is broken; fix it, don't type a password into automation.
4. The agent gates physical runs behind an explicit **`RUN ROBOT`** confirmation. When running manually, apply the same discipline: review the deck layout and pipettes before executing.

## What gets created on a run

| Location | Contents |
|----------|----------|
| `robot_data/deploy/run_<timestamp>/` | Local staging: protocol + `manifest.json` (run_id, hash, IP) |
| Robot `:/var/lib/opentrons/user_storage/ot2_runs/run_<timestamp>/` | The deployed files |
| `robot_data/data/logs/agents/` | Agent session logs |

## Related

- [ot2-protocols](../ot2-protocols/SKILL.md) — must produce a PASS simulation first
- [ot2-labware](../ot2-labware/SKILL.md) — custom labware the protocol may need
- [docs/SOP_Robot_Deployment.md](../../docs/SOP_Robot_Deployment.md) — deployment SOP
