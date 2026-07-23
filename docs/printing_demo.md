# OT-2 Paper Printing Demo User Guide

This user guide describes how to configure, simulate, run, and troubleshoot the new **OT-2 Paper Printing Demo Workflow** in `ot2-lab-suite`.

The workflow allows you to pipette plain water or a food-coloring dilution series from a single 96-well plate onto paper located in a target deck slot, while capturing step-by-step camera snaps of the deck, well plate, and printed droplets.

---

## Architecture Overview

To keep the pipeline simple and easy to maintain, the workflow is built using an intentionally minimal architecture:
1. **One YAML Config File**: `configs/workflows/defaults/printing_demo.yaml`
2. **One Python Protocol/Runner File**: `src/protocols/printing_demo_protocol.py`

When run on your laptop, the protocol runner reads your YAML config, validates it using Pydantic, compiles a self-contained protocol file `src/protocols/generated/printing_demo_run.py` (with the parsed configuration dictionary embedded at the top), simulates it locally using the Opentrons simulator, and optionally deploys and triggers it on the physical OT-2.

---

## Configuration Settings

You can adjust all details of the experiment by editing `configs/workflows/defaults/printing_demo.yaml`. Key sections include:

- `demo_mode`: Set to either `water_print` or `dilution_print`.
- `plate`: The physical slot (e.g. 2) and labware type (e.g. `corning_96_wellplate_360ul_flat`).
- `tiprack`: The tiprack slot (e.g. 1) and type (e.g. `opentrons_96_tiprack_300ul`).
- `pipette`: Pipette name (e.g. `p300_single_gen2`) and mount (`left` or `right`).
- `layout`: Dict defining the physical role of wells on the plate:
  - `food_coloring_source_wells`: Pre-filled stock wells.
  - `water_source_wells`: Pre-filled water source wells.
  - `dilution_destination_wells`: Destination wells where dilution gradients are prepared.
  - `print_source_wells`: Wells from which printing onto paper is performed.
- `dilution`: Dilution steps (water volume, stock volume, mix volume, mix repetitions).
- `printing`: The target paper deck slot (e.g., 9), droplet volume, dispense height, and specific coordinate list (X/Y relative to the bottom center of well A1 on the paper slot).
- `tip_strategy`: Support for `reuse_low_to_high` (sorts steps from lowest to highest concentration to reuse a single tip without cross-contamination), `new_tip_each_transfer`, and `reuse_per_phase`.
- `camera`: Options to enable/disable camera snaps and select specific timing triggers.
- `output`: Configures run folder roots and output formats.

### Config Validation Rules
Before any physical execution, the runner validates the layout using Pydantic:
- Well coordinates must exist and match format (A1 to H12).
- Volumes and coordinates must be positive.
- Well overlap is forbidden: food coloring stock wells, water source wells, and dilution destination wells must not overlap.
- Destination wells cannot be used as aspirations *before* they have been prepared in a previous dilution step.
- Dispensing back into source wells is forbidden.

---

## Run Folder Organization

Every demo execution creates a timestamped run folder at the output path (e.g., `runs/dilution_print_YYYYMMDD_HHMMSS/`):

```
runs/<demo_mode>_YYYYMMDD_HHMMSS/
├── before_after/
│   ├── before_deck.jpg          # Overall deck snapshot before pipetting
│   ├── before_wellplate.jpg     # Plate snapshot before pipetting
│   ├── after_deck.jpg           # Overall deck snapshot after completion
│   └── after_wellplate.jpg      # Plate snapshot after completion
├── wellplate/
│   ├── wellplate_step_001_well_B7.jpg   # Snapshot after dilution step 1
│   └── wellplate_step_002_well_B8.jpg   # Snapshot after dilution step 2
├── paper/
│   ├── paper_print_001_well_B7.jpg      # Snapshot after printing droplet 1
│   └── paper_print_002_well_B8.jpg      # Snapshot after printing droplet 2
├── errors/                              # Folder for failed captures
│   ├── wellplate_step_003_well_B9.json  # Failure description log
│   └── wellplate_step_003_well_B9.partial.jpg  # Corrupt image (if any)
├── printer_manifest.csv                 # CSV mapping printed wells to coordinates
├── printer_manifest.json                # JSON mapping printed wells to coordinates
└── run_metadata.json                    # Overall step logs and visual plate map
```

### Image Validation and Errors
If a camera capture fails or retrieves a corrupt/incomplete file, the host runner:
1. Logs a warning and continues (it does **not** abort the run).
2. Marks the step status as `"failed"` in `run_metadata.json`.
3. Creates a JSON descriptor under `errors/` containing the step index, action, source, target, and the failure reason (e.g., `File too small` or `Corrupt image block`).
4. Moves the partial file (if any) to `errors/<filename>.partial.jpg`.

---

## Execution Commands

Always run these commands from the project root using your `AI` conda environment:

### 1. Run local simulation (Local check only)
You can run a local simulation to check for code correctness and command syntax:
```bash
conda run -n AI python -m opentrons.simulate src/protocols/printing_demo_protocol.py
```

### 2. Run local mock execution (Highly Recommended for Testing)
The mock execution runs local simulation, generates colorful mock JPEGs mimicking the camera capture, and writes out the complete `runs/` folder layout and manifests:
```bash
conda run -n AI python src/protocols/printing_demo_protocol.py --config configs/workflows/defaults/printing_demo.yaml --mock
```

### 3. Run physical robot execution
To deploy the compiled protocol to the physical OT-2 robot, execute it, retrieve JPEGs, and clean up remote folders:
```bash
conda run -n AI python src/protocols/printing_demo_protocol.py --config configs/workflows/defaults/printing_demo.yaml --robot-ip <ROBOT_IP>
```

### 4. Run camera diagnostics
To test the host-to-robot connection and camera status without running a full experiment, run:
```bash
conda run -n AI python scripts/test_ot2_camera_capture.py
```
Or run a mock test locally:
```bash
conda run -n AI python scripts/test_ot2_camera_capture.py --mock
```

---

## Troubleshooting Guide

### 1. Camera Diagnostic Failures
If `test_ot2_camera_capture.py` fails:
- Check that the OT-2 robot is powered on and connected to the same network as your laptop.
- Ping the robot: `ping <ROBOT_IP>`.
- Verify your SSH credentials in configs. The private key must match the key on the robot.

### 2. SCP transfer fails with legacy error
The OT-2 does not run a standard SFTP server. To transfer files, the legacy SCP protocol is required.
- The Python script runs `scp -O` (capital O) to force legacy SCP. Ensure your command-line environment has a standard OpenSSH `scp` command that supports the `-O` flag.

### 3. Verification Fails on Transferred Files
If transferred images are placed in the `errors/` directory:
- Verify that your camera server on the OT-2 is running. The onboard server exposes `http://localhost:31950/camera/picture`.
- Log into the robot directly to check if pictures can be captured manually:
  ```bash
  ssh -o IdentitiesOnly=yes -o PubkeyAcceptedAlgorithms=+ssh-rsa \
    -i <key_path> root@<ROBOT_IP> "curl -s -X POST -H 'opentrons-version: *' http://localhost:31950/camera/picture --output /data/vision/manual_test.jpg"
  ```

### 4. Calibration file locations
opentrons_execute may report a warning message:
```
/data/deck_calibration.json not found. Loading defaults
```
This warning message refers to a legacy filepath. On modern OT-2 software versions, the active calibration data is stored at:
`/data/robot/deck_calibration.json`

Therefore, a warning about `/data/deck_calibration.json` being missing should not be treated as proof that the robot has no calibration. As long as `/data/robot/deck_calibration.json` is present on the robot's filesystem, the calibration settings are active. To inspect this, run the connectivity diagnostics script:
```bash
conda run -n AI python -m scripts.check_connectivity
```
It will run checks for legacy and current calibration paths and report their timestamps and directory contents.
