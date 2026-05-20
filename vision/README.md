# OT-2 Machine Vision Module

> **Purpose** — Pull camera image files from an Opentrons OT-2 robot, store them locally in a structured directory tree, and perform basic validation in preparation for downstream nanoparticle printing quality analysis.

---

## Quick Start

```powershell
# 1. Install dependencies (from the project root)
pip install python-dotenv pyyaml pandas pillow

# 2. Configure connection (edit .env in the project root)
#    ROBOT_IP=169.254.46.57
#    ROBOT_SSH_KEY_PATH=C:\Users\<you>\.ssh\ot2_ssh_key

# 3. Dry run (no files are transferred)
python scripts/pull_ot2_images.py --dry-run

# 4. Full transfer
python scripts/pull_ot2_images.py
```

---

## Dependencies

| Package        | Purpose                                     |
| -------------- | ------------------------------------------- |
| `python-dotenv`| Load `.env` variables                       |
| `pyyaml`       | Parse `configs/vision.yaml`                 |
| `pandas`       | Inventory and validation CSV reports        |
| `pillow`       | Verify image file integrity (JPG, PNG)      |

> **Optional:** `opencv-python` may be added later for advanced image analysis.

---

## Configuration

### `.env` (project root)

```ini
ROBOT_IP=169.254.46.57
ROBOT_SSH_KEY_PATH=C:\Users\<you>\.ssh\ot2_ssh_key
```

### `configs/vision.yaml`

Adjustable parameters for remote directories, local paths, transfer behaviour, and validation thresholds.  See the file for inline comments.

---

## Usage

### Transfer images from the robot

```powershell
python scripts/pull_ot2_images.py
```

### Dry run (preview only)

```powershell
python scripts/pull_ot2_images.py --dry-run
```

### Overwrite existing local files

```powershell
python scripts/pull_ot2_images.py --overwrite
```

### Search a specific remote directory

```powershell
python scripts/pull_ot2_images.py --remote-dir /data/runs
```

### Custom local output directory

```powershell
python scripts/pull_ot2_images.py --output-dir C:\data\custom
```

---

## Where Files Are Saved

| Artifact                | Default Location                            |
| ----------------------- | ------------------------------------------- |
| Raw images              | `data/vision/raw/run_YYYYMMDD_HHMMSS/`     |
| Processed images        | `data/vision/processed/`  *(future use)*    |
| Transfer logs           | `data/vision/logs/vision_transfer_*.log`    |
| Image inventory CSV     | `data/vision/logs/image_inventory.csv`      |
| Validation report CSV   | `data/vision/logs/image_validation_report.csv` |

---

## Module Structure

```
vision/
├── __init__.py           # Package init
├── config.py             # Loads .env + vision.yaml, resolves paths
├── transfer_images.py    # SSH discovery + SCP file transfer
├── image_inventory.py    # Scan local files → CSV inventory
├── validate_images.py    # Integrity checks → CSV report
└── README.md             # This file
```

---

## How This Connects to Future Analysis

This module is **step 1** of a larger machine-vision pipeline:

1. **Image Acquisition** ← *this module*
2. **Pre-processing** — crop, normalise, denoise (future, in `data/vision/processed/`)
3. **Analysis** — nanoparticle dot detection, coverage metrics, quality scoring
4. **Reporting** — automated quality dashboards

The structured directory layout and CSV inventories are designed to feed directly into an image-analysis pipeline without manual file wrangling.
