# OT-2 Machine Vision Module

> **Purpose** — Capture camera images on the Opentrons OT-2 robot, transfer them to the local laptop, and prepare for future nanoparticle printing quality analysis.

---

## Quick Start

```powershell
# Take 2 pictures on the OT-2 and transfer them to the laptop
python vision/ot2_camera_workflow.py

# Dry run — see what commands would execute
python vision/ot2_camera_workflow.py --dry-run
```

---

## Dependencies

| Package   | Purpose                |
| --------- | ---------------------- |
| `pyyaml`  | Parse `configs/vision.yaml` |

All other operations use Python builtins (`subprocess`, `pathlib`, `argparse`).

```powershell
pip install pyyaml
```

---

## Configuration

### `configs/vision.yaml`

All connection and workflow settings are in one file — nothing is hardcoded in the scripts.

```yaml
robot:
  ip: 169.254.46.57          # Your OT-2's USB/Ethernet IP
  user: root                  # OT-2 SSH user (always root)
  host_env_var: ROBOT_IP
  ssh_user_env_var: ROBOT_SSH_USER
  ssh_key_env_var: ROBOT_SSH_KEY_PATH
  ssh_identities_only_env_var: ROBOT_SSH_IDENTITIES_ONLY
  ssh_legacy_rsa_env_var: ROBOT_SSH_LEGACY_RSA

remote:
  vision_dir: /data/vision    # Where images are saved on the robot
  camera_endpoint: http://localhost:31950/camera/picture
  opentrons_version_header: "*"

local:
  raw_dir: vision/raw         # Where images are saved on the laptop

capture:
  default_count: 2            # Number of images per capture
  default_delay_seconds: 3    # Seconds between captures
  filename_prefix: ot2_image

transfer:
  use_legacy_scp: true        # Must be true (OT-2 lacks sftp-server)
  overwrite_existing: true
```

---

## Usage

### Capture images on the OT-2

```powershell
# Take 2 pictures (default)
python vision/capture_ot2_images.py

# Take 5 pictures with 2-second delay
python vision/capture_ot2_images.py --count 5 --delay 2

# Dry run
python vision/capture_ot2_images.py --dry-run
```

### Transfer images to the laptop

```powershell
# Copy all images from the robot
python vision/transfer_ot2_images.py

# Dry run
python vision/transfer_ot2_images.py --dry-run

# Custom directories
python vision/transfer_ot2_images.py --remote-dir /data/vision --local-dir vision/raw
```

### Full workflow (capture + transfer)

```powershell
# Take pictures and transfer in one step
python vision/ot2_camera_workflow.py --count 2 --delay 3

# Dry run
python vision/ot2_camera_workflow.py --dry-run
```

---

## Where Images Are Saved

| Location | Path |
| -------- | ---- |
| **On the OT-2** | `/data/vision/ot2_image_YYYYMMDD_HHMMSS.jpg` |
| **On the laptop** | `vision/raw/ot2_image_YYYYMMDD_HHMMSS.jpg` |

---

## Why `scp -O` Is Required

The OT-2 does not have `/usr/libexec/sftp-server` installed.
Normal SCP (which uses SFTP internally) fails with:

```
sh: /usr/libexec/sftp-server: not found
```

The `-O` flag (capital O) forces the **legacy SCP protocol**, which works.

> **Warning:** Do not confuse `-O` (capital, legacy SCP) with `-o` (lowercase, SSH option).

---

## Module Structure

```
vision/
├── __init__.py               # Package init
├── capture_ot2_images.py     # SSH + curl to take pictures on the robot
├── transfer_ot2_images.py    # SCP -O to copy images to the laptop
├── ot2_camera_workflow.py    # Full workflow: capture → transfer
├── raw/                      # Local image storage (git-ignored)
├── config.py                 # Advanced config loader (env-var based)
├── transfer_images.py        # Advanced transfer module (SSH find)
├── image_inventory.py        # CSV inventory generator
├── validate_images.py        # Image integrity checker
└── README.md                 # This file
```

---

## Future: Machine Vision Analysis

This module handles **Step 1 — image acquisition**.
Future steps will add:

1. **Pre-processing** — crop, normalise, denoise
2. **Analysis** — nanoparticle dot detection, coverage, quality scoring
3. **Reporting** — automated quality dashboards
