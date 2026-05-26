#!/usr/bin/env python3
"""
src/protocols/printing_demo_protocol.py
=======================================
OT-2 Printing Demo: Water printing or food coloring dilution printing on paper.

This script acts as both:
1. An OT-2 protocol (when run by the OT-2 execution engine).
2. A host-side orchestrator (when run from the laptop command line).

Host execution builds a self-contained copy of this file with the configuration
embedded at the top, simulates it, deploys it to the robot, triggers the run,
transfers JPEGs, runs image validation, and generates run manifests.
"""

import os
import re
import sys
import json
import yaml
import logging
import argparse
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

# --- NumPy 2.x Compatibility Patch for Opentrons ---
try:
    import numpy as np
    if not hasattr(np, "trapz"):
        np.trapz = getattr(np, "trapezoid", None)
except ImportError:
    pass

# Try importing Pydantic and Opentrons; handle gracefully if not present (e.g. on the robot)
try:
    from pydantic import BaseModel, Field, field_validator, model_validator
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False

try:
    from opentrons import protocol_api
    from opentrons.types import Point
    HAS_OPENTRONS = True
except ImportError:
    HAS_OPENTRONS = False

# Try importing vision config from the project (if run from repo root)
try:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
except NameError:
    PROJECT_ROOT = Path(".")

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Logger setup
logger = logging.getLogger("printing_demo")

# ──────────────────────────────────────────────────────────────────────
# CONFIGURATION PLACEHOLDER
# Do not remove the line below. The host runner will replace it with the
# actual configuration dictionary during deployment.
# ──────────────────────────────────────────────────────────────────────
CONFIG = { 'camera': { 'capture_after': True,
              'capture_after_each_dilution_step': True,
              'capture_after_each_print_step': True,
              'capture_before': True,
              'enabled': True,
              'image_filename_prefix': 'printing_demo',
              'robot_image_dir': '/data/vision/printing_demo'},
  'demo_mode': 'dilution_print',
  'dilution': { 'enabled': True,
                'steps': [ { 'destination_well': 'B7',
                             'food_coloring_source_well': 'A1',
                             'mix_repetitions': 3,
                             'mix_volume_ul': 100.0,
                             'stock_volume_ul': 20.0,
                             'water_source_well': 'A7',
                             'water_volume_ul': 180.0},
                           { 'destination_well': 'B8',
                             'food_coloring_source_well': 'A1',
                             'mix_repetitions': 3,
                             'mix_volume_ul': 100.0,
                             'stock_volume_ul': 50.0,
                             'water_source_well': 'A8',
                             'water_volume_ul': 150.0},
                           { 'destination_well': 'B9',
                             'food_coloring_source_well': 'A1',
                             'mix_repetitions': 3,
                             'mix_volume_ul': 100.0,
                             'stock_volume_ul': 100.0,
                             'water_source_well': 'A9',
                             'water_volume_ul': 100.0}]},
  'layout': { 'dilution_destination_wells': [ 'B7',
                                              'B8',
                                              'B9',
                                              'B10',
                                              'B11',
                                              'B12'],
              'food_coloring_source_wells': [ 'A1',
                                              'A2',
                                              'A3',
                                              'A4',
                                              'A5',
                                              'A6'],
              'print_source_wells': ['B7', 'B8', 'B9', 'B10', 'B11', 'B12'],
              'water_source_wells': ['A7', 'A8', 'A9', 'A10', 'A11', 'A12']},
  'output': { 'create_image_manifest': True,
              'create_metadata': True,
              'local_run_root': 'runs'},
  'pipette': {'mount': 'left', 'name': 'p300_single_gen2'},
  'plate': {'labware': 'corning_96_wellplate_360ul_flat', 'slot': 2},
  'printing': { 'calibration_only': False,
                'dispense_height_mm': 1.0,
                'droplet_volume_ul': 10.0,
                'paper_slot': 9,
                'print_positions': [ { 'label': 'dilution_1',
                                       'source_well': 'B7',
                                       'x_mm': 20.0,
                                       'y_mm': 20.0},
                                     { 'label': 'dilution_2',
                                       'source_well': 'B8',
                                       'x_mm': 30.0,
                                       'y_mm': 20.0},
                                     { 'label': 'dilution_3',
                                       'source_well': 'B9',
                                       'x_mm': 40.0,
                                       'y_mm': 20.0}],
                'safe_z_mm': 20.0},
  'tip_strategy': { 'allowed_modes': [ 'new_tip_each_transfer',
                                       'reuse_low_to_high',
                                       'reuse_per_phase'],
                    'mode': 'reuse_low_to_high'},
  'tiprack': {'labware': 'opentrons_96_tiprack_300ul', 'slot': 1}}

# ─── Well Validation Helpers ──────────────────────────────────────────

def is_valid_well(well: str) -> bool:
    """Check if a well name is a valid 96-well format (A1 to H12)."""
    return bool(re.match(r"^[A-H]([1-9]|1[0-2])$", well))

# ─── Pydantic Models for Config Validation (Host-Side Only) ──────────

if HAS_PYDANTIC:
    class PlateConfig(BaseModel):
        slot: int = Field(..., ge=1, le=11)
        labware: str

    class TiprackConfig(BaseModel):
        slot: int = Field(..., ge=1, le=11)
        labware: str

    class PipetteConfig(BaseModel):
        name: str
        mount: str

        @field_validator("mount")
        @classmethod
        def validate_mount(cls, v: str) -> str:
            if v not in ("left", "right"):
                raise ValueError("Pipette mount must be 'left' or 'right'")
            return v

    class LayoutConfig(BaseModel):
        food_coloring_source_wells: List[str]
        water_source_wells: List[str]
        dilution_destination_wells: List[str]
        print_source_wells: List[str]

        @field_validator("food_coloring_source_wells", "water_source_wells", "dilution_destination_wells", "print_source_wells")
        @classmethod
        def validate_wells(cls, v: List[str]) -> List[str]:
            for well in v:
                if not is_valid_well(well):
                    raise ValueError(f"Invalid well format: {well}. Must be A1-H12.")
            return v

    class DilutionStep(BaseModel):
        destination_well: str
        water_source_well: str
        food_coloring_source_well: str
        water_volume_ul: float = Field(..., ge=0)
        stock_volume_ul: float = Field(..., ge=0)
        mix_volume_ul: float = Field(..., ge=0)
        mix_repetitions: int = Field(..., ge=0)

        @model_validator(mode="after")
        def validate_step(self) -> 'DilutionStep':
            if not is_valid_well(self.destination_well):
                raise ValueError(f"Invalid destination well: {self.destination_well}")
            if not is_valid_well(self.water_source_well):
                raise ValueError(f"Invalid water source well: {self.water_source_well}")
            if not is_valid_well(self.food_coloring_source_well):
                raise ValueError(f"Invalid food coloring source well: {self.food_coloring_source_well}")
            if self.water_volume_ul == 0 and self.stock_volume_ul == 0:
                raise ValueError("Both water volume and stock volume cannot be zero.")
            return self

    class DilutionConfig(BaseModel):
        enabled: bool
        steps: List[DilutionStep]

    class PrintPosition(BaseModel):
        source_well: str
        label: str
        x_mm: float
        y_mm: float

        @model_validator(mode="after")
        def validate_pos(self) -> 'PrintPosition':
            if not is_valid_well(self.source_well):
                raise ValueError(f"Invalid print source well: {self.source_well}")
            return self

    class PrintingConfig(BaseModel):
        calibration_only: bool = False
        paper_slot: int = Field(..., ge=1, le=11)
        droplet_volume_ul: float = Field(..., gt=0)
        dispense_height_mm: float = Field(..., ge=0)
        safe_z_mm: float = Field(..., ge=0)
        print_positions: List[PrintPosition]

    class TipStrategyConfig(BaseModel):
        mode: str
        allowed_modes: List[str]

        @model_validator(mode="after")
        def validate_strategy(self) -> 'TipStrategyConfig':
            if self.mode not in self.allowed_modes:
                raise ValueError(f"Tip mode '{self.mode}' not in allowed modes: {self.allowed_modes}")
            return self

    class CameraConfig(BaseModel):
        enabled: bool
        capture_before: bool
        capture_after: bool
        capture_after_each_dilution_step: bool
        capture_after_each_print_step: bool
        robot_image_dir: str
        image_filename_prefix: str

    class OutputConfig(BaseModel):
        local_run_root: str
        create_metadata: bool
        create_image_manifest: bool

    class PrintingDemoConfig(BaseModel):
        demo_mode: str
        plate: PlateConfig
        tiprack: TiprackConfig
        pipette: PipetteConfig
        layout: LayoutConfig
        dilution: DilutionConfig
        printing: PrintingConfig
        tip_strategy: TipStrategyConfig
        camera: CameraConfig
        output: OutputConfig

        @model_validator(mode="after")
        def validate_experiment_rules(self) -> 'PrintingDemoConfig':
            if self.demo_mode not in ("water_print", "dilution_print"):
                raise ValueError(f"Invalid demo_mode: {self.demo_mode}. Must be water_print or dilution_print.")

            fc_wells = set(self.layout.food_coloring_source_wells)
            w_wells = set(self.layout.water_source_wells)
            d_wells = set(self.layout.dilution_destination_wells)

            # 1. Overlaps checks
            if fc_wells & w_wells:
                raise ValueError(f"Overlap detected: food coloring and water source wells overlap at {fc_wells & w_wells}")
            if fc_wells & d_wells:
                raise ValueError(f"Overlap detected: food coloring and dilution destination wells overlap at {fc_wells & d_wells}")
            if w_wells & d_wells:
                raise ValueError(f"Overlap detected: water source and dilution destination wells overlap at {w_wells & d_wells}")

            # 2. Generalization rules validation
            if self.demo_mode == "dilution_print":
                prepared_wells = set()
                for step in self.dilution.steps:
                    # Do not aspirate from a destination well before it is prepared
                    if step.water_source_well in d_wells and step.water_source_well not in prepared_wells:
                        raise ValueError(f"Attempted to aspirate water from dilution destination {step.water_source_well} before it was prepared.")
                    if step.food_coloring_source_well in d_wells and step.food_coloring_source_well not in prepared_wells:
                        raise ValueError(f"Attempted to aspirate stock from dilution destination {step.food_coloring_source_well} before it was prepared.")

                    # Do not dispense into a source well
                    if step.destination_well in fc_wells:
                        raise ValueError(f"Attempted to dispense dilution step into food coloring source well: {step.destination_well}")
                    if step.destination_well in w_wells:
                        raise ValueError(f"Attempted to dispense dilution step into water source well: {step.destination_well}")
                    if step.destination_well not in d_wells:
                        raise ValueError(f"Dilution destination well {step.destination_well} is not defined in layout.dilution_destination_wells")

                    prepared_wells.add(step.destination_well)

                # Validate print sources
                for pos in self.printing.print_positions:
                    if pos.source_well not in prepared_wells and pos.source_well not in self.layout.print_source_wells:
                        raise ValueError(f"Print source well {pos.source_well} has not been prepared in dilution steps.")
            else:
                # water_print mode
                allowed_sources = w_wells | set(self.layout.print_source_wells)
                for pos in self.printing.print_positions:
                    if pos.source_well not in allowed_sources:
                        raise ValueError(f"Water print source well {pos.source_well} is not in layout.water_source_wells or layout.print_source_wells.")

            return self

# ─── OT-2 PROTOCOL LOGIC ──────────────────────────────────────────────

metadata = {
    "protocolName": "OT-2 Paper Printing Demo with Vision Integration",
    "author": "Antigravity AI Agent",
    "description": "Pipetting water or dilution series onto paper with step-by-step camera snaps",
    "apiLevel": "2.13"
}

def run(protocol: 'protocol_api.ProtocolContext') -> None:
    """The main entry point called by the Opentrons execution engine on the robot."""
    global CONFIG
    if CONFIG is None:
        # Fallback local loading for standalone simulations
        config_path = os.environ.get("OT2_PRINTING_DEMO_CONFIG", "configs/workflows/defaults/printing_demo.yaml")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                CONFIG = yaml.safe_load(f)
            protocol.comment(f"Loaded config from environment path: {config_path}")
        except Exception as e:
            protocol.comment(f"Warning: Could not load configuration: {e}")
            raise ValueError(f"Failed to resolve CONFIG: {e}")

    protocol.comment(f"Starting Demo Mode: {CONFIG['demo_mode']}")

    # 1. Deck validations
    plate_cfg = CONFIG["plate"]
    tiprack_cfg = CONFIG["tiprack"]
    pipette_cfg = CONFIG["pipette"]
    printing_cfg = CONFIG["printing"]
    camera_cfg = CONFIG["camera"]

    slots = [plate_cfg["slot"], tiprack_cfg["slot"], printing_cfg["paper_slot"]]
    if len(slots) != len(set(slots)):
        raise ValueError(f"Duplicate deck slot assignments: Plate={plate_cfg['slot']}, Tiprack={tiprack_cfg['slot']}, Paper={printing_cfg['paper_slot']}")

    # 2. Load labware
    plate = protocol.load_labware(plate_cfg["labware"], plate_cfg["slot"])
    tiprack = protocol.load_labware(tiprack_cfg["labware"], tiprack_cfg["slot"])
    paper_ref = protocol.load_labware(plate_cfg["labware"], printing_cfg["paper_slot"]) # flat block reference

    pipette = protocol.load_instrument(
        pipette_cfg["name"],
        pipette_cfg["mount"],
        tip_racks=[tiprack]
    )

    # 3. Camera capture function
    def capture_image(filename: str) -> None:
        if protocol.is_simulating():
            protocol.comment(f"[SIMULATION] Mock photo: {filename}")
            return

        import subprocess
        remote_dir = camera_cfg.get("robot_image_dir", "/data/vision/printing_demo")
        subprocess.run(["mkdir", "-p", remote_dir], check=False)
        output_path = f"{remote_dir}/{filename}"

        endpoint = "http://localhost:31950/camera/picture"
        cmd = [
            "curl", "-s", "-X", "POST",
            "-H", "opentrons-version: *",
            endpoint,
            "--output", output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            protocol.comment(f"Captured photo: {filename}")
        else:
            protocol.comment(f"Camera capture failed for {filename}: {result.stderr}")

    # ── Phase 1: Before Overview ──
    if camera_cfg["enabled"] and camera_cfg["capture_before"]:
        capture_image("before_deck.jpg")
        capture_image("before_wellplate.jpg")

    # ── Phase 2: Dilution ──
    if CONFIG["demo_mode"] == "dilution_print" and CONFIG["dilution"]["enabled"] and not printing_cfg.get("calibration_only", False):
        dil_steps = CONFIG["dilution"]["steps"]
        strategy_mode = CONFIG.get("tip_strategy", {}).get("mode", "new_tip_each_transfer")

        # Sort steps by increasing concentration (decreasing water_volume / increasing stock_volume)
        if strategy_mode == "reuse_low_to_high":
            def get_concentration_ratio(s: Dict[str, Any]) -> float:
                tot = s["water_volume_ul"] + s["stock_volume_ul"]
                return s["stock_volume_ul"] / tot if tot > 0.0 else 0.0
            dil_steps = sorted(dil_steps, key=get_concentration_ratio)
            protocol.comment("Sorted dilution steps by concentration for tip reuse.")

        # Distribute water first (efficiency)
        water_steps = [s for s in dil_steps if s["water_volume_ul"] > 0]
        if water_steps:
            pipette.pick_up_tip()
            for step in water_steps:
                src_well = plate.wells_by_name()[step["water_source_well"]]
                dest_well = plate.wells_by_name()[step["destination_well"]]
                vol = step["water_volume_ul"]
                protocol.comment(f"Pipetting {vol} uL water from {step['water_source_well']} to {step['destination_well']}")
                pipette.aspirate(vol, src_well)
                pipette.dispense(vol, dest_well)
            pipette.drop_tip()

        # Transfer food coloring and mix
        reusing_tip = False
        if strategy_mode in ("reuse_low_to_high", "reuse_per_phase"):
            pipette.pick_up_tip()
            reusing_tip = True

        for idx, step in enumerate(dil_steps):
            if not reusing_tip:
                pipette.pick_up_tip()

            src_well = plate.wells_by_name()[step["food_coloring_source_well"]]
            dest_well = plate.wells_by_name()[step["destination_well"]]
            stock_vol = step["stock_volume_ul"]

            if stock_vol > 0:
                protocol.comment(f"Pipetting {stock_vol} uL stock from {step['food_coloring_source_well']} to {step['destination_well']}")
                pipette.aspirate(stock_vol, src_well)
                pipette.dispense(stock_vol, dest_well)

            # Active mixing
            mix_vol = step.get("mix_volume_ul", 0.0)
            mix_reps = step.get("mix_repetitions", 0)
            if mix_reps > 0 and mix_vol > 0.0:
                protocol.comment(f"Mixing well {step['destination_well']} ({mix_reps} x {mix_vol} uL)")
                pipette.mix(mix_reps, mix_vol, dest_well)

            if not reusing_tip:
                pipette.drop_tip()

            if camera_cfg["enabled"] and camera_cfg["capture_after_each_dilution_step"]:
                capture_image(f"wellplate_step_{idx+1:03d}_well_{step['destination_well']}.jpg")

        if reusing_tip:
            pipette.drop_tip()

    # ── Phase 3: Printing / Calibration ──
    print_positions = printing_cfg["print_positions"]
    paper_a1 = paper_ref.wells()[0]
    droplet_vol = printing_cfg["droplet_volume_ul"]
    dispense_h = printing_cfg.get("dispense_height_mm", 1.0)
    is_calibration = printing_cfg.get("calibration_only", False)

    if is_calibration:
        protocol.comment("Executing Calibration Mode: verifying coordinates above paper.")
        pipette.pick_up_tip()
        for idx, pos in enumerate(print_positions):
            dest_loc = paper_a1.bottom().move(Point(x=pos["x_mm"], y=pos["y_mm"], z=dispense_h))
            protocol.comment(f"Moving tip above paper coordinate X={pos['x_mm']} mm, Y={pos['y_mm']} mm, Z={dispense_h} mm")
            pipette.move_to(dest_loc)
            if protocol.is_simulating():
                protocol.delay(seconds=2)
            else:
                protocol.pause(f"Calibration Check - Point: {pos['label']} (Source well: {pos['source_well']}). Verify alignment. Resume to continue.")
            
            if camera_cfg["enabled"] and camera_cfg["capture_after_each_print_step"]:
                capture_image(f"paper_print_{idx+1:03d}_well_{pos['source_well']}.jpg")
        pipette.drop_tip()
    else:
        for idx, pos in enumerate(print_positions):
            pipette.pick_up_tip()
            src_well = plate.wells_by_name()[pos["source_well"]]

            protocol.comment(f"Aspirating {droplet_vol} uL from {pos['source_well']} for printing")
            pipette.aspirate(droplet_vol, src_well)

            # Move with Point offsets from A1 bottom center
            dest_loc = paper_a1.bottom().move(Point(x=pos["x_mm"], y=pos["y_mm"], z=dispense_h))
            protocol.comment(f"Printing droplet on paper at X={pos['x_mm']} mm, Y={pos['y_mm']} mm")
            pipette.dispense(droplet_vol, dest_loc)
            pipette.drop_tip()

            if camera_cfg["enabled"] and camera_cfg["capture_after_each_print_step"]:
                capture_image(f"paper_print_{idx+1:03d}_well_{pos['source_well']}.jpg")

    # ── Phase 4: After Overview ──
    if camera_cfg["enabled"] and camera_cfg["capture_after"]:
        capture_image("after_deck.jpg")
        capture_image("after_wellplate.jpg")

# ─── HOST-SIDE CLI AND RUNNER ORCHESTRATION ───────────────────────────

def generate_plate_map(config: Dict[str, Any]) -> Tuple[Dict[str, str], str]:
    """Generate visual ASCII plate grid and role dictionary."""
    plate_map = {}
    rows = list("ABCDEFGH")
    cols = list(range(1, 13))
    for r in rows:
        for c in cols:
            plate_map[f"{r}{c}"] = "Empty"

    layout = config.get("layout", {})
    for w in layout.get("food_coloring_source_wells", []):
        plate_map[w] = "Food Coloring Stock"
    for w in layout.get("water_source_wells", []):
        plate_map[w] = "Water Source"
    for w in layout.get("dilution_destination_wells", []):
        plate_map[w] = "Dilution Destination"
    for w in layout.get("print_source_wells", []):
        if plate_map[w] == "Empty":
            plate_map[w] = "Print Source"

    lines = ["Col:  " + "  ".join(f"{c:2d}" for c in cols)]
    for r in rows:
        row_str = f"{r}:   "
        for c in cols:
            role = plate_map[f"{r}{c}"]
            if role == "Food Coloring Stock":
                char = "[S]"
            elif role == "Water Source":
                char = "[W]"
            elif role == "Dilution Destination":
                char = "[D]"
            elif role == "Print Source":
                char = "[P]"
            else:
                char = "[ ]"
            row_str += char + " "
        lines.append(row_str)

    return plate_map, "\n".join(lines)

def run_local_simulation(run_path: Path) -> str:
    """Run `opentrons.simulate` on the compiled script to verify syntax/safety."""
    cmd = [
        sys.executable, "-c",
        "import numpy as np; np.trapz = getattr(np, 'trapezoid', None); from opentrons.simulate import main; main()",
        run_path.name
    ]
    logger.info(f"Simulating protocol: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(run_path.parent), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error(f"Simulation FAILED:\n{result.stderr}")
        raise ValueError(f"Simulation failed with returncode {result.returncode}")
    logger.info("Simulation PASSED successfully.")
    return result.stdout

def build_flat_step_log(config_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = []
    camera_cfg = config_dict.get("camera", {})
    demo_mode = config_dict.get("demo_mode")
    dilution_cfg = config_dict.get("dilution", {})
    printing_cfg = config_dict.get("printing", {})
    is_calibration = printing_cfg.get("calibration_only", False)
    
    step_idx = 1
    
    # 1. Before snaps
    if camera_cfg.get("enabled") and camera_cfg.get("capture_before"):
        steps.append({
            "step_index": step_idx,
            "phase": "before",
            "action": "capture_before_deck",
            "source_well": "",
            "destination_well": "",
            "volume_ul": 0.0,
            "print_label": "",
            "x_mm": 0.0,
            "y_mm": 0.0,
            "image_expected": True,
            "image_name": "before_deck.jpg",
            "status": "pending"
        })
        step_idx += 1
        steps.append({
            "step_index": step_idx,
            "phase": "before",
            "action": "capture_before_wellplate",
            "source_well": "",
            "destination_well": "",
            "volume_ul": 0.0,
            "print_label": "",
            "x_mm": 0.0,
            "y_mm": 0.0,
            "image_expected": True,
            "image_name": "before_wellplate.jpg",
            "status": "pending"
        })
        step_idx += 1
        
    # 2. Dilution steps
    if demo_mode == "dilution_print" and dilution_cfg.get("enabled") and not is_calibration:
        dil_steps = dilution_cfg.get("steps", [])
        
        # Sort steps by increasing concentration if reuse_low_to_high
        strategy_mode = config_dict.get("tip_strategy", {}).get("mode", "new_tip_each_transfer")
        if strategy_mode == "reuse_low_to_high":
            def get_concentration_ratio(s: Dict[str, Any]) -> float:
                tot = s["water_volume_ul"] + s["stock_volume_ul"]
                return s["stock_volume_ul"] / tot if tot > 0.0 else 0.0
            dil_steps = sorted(dil_steps, key=get_concentration_ratio)

        # Distribute water first
        water_steps = [s for s in dil_steps if s["water_volume_ul"] > 0]
        for step in water_steps:
            steps.append({
                "step_index": step_idx,
                "phase": "dilution",
                "action": "pipette_water",
                "source_well": step["water_source_well"],
                "destination_well": step["destination_well"],
                "volume_ul": step["water_volume_ul"],
                "print_label": "",
                "x_mm": 0.0,
                "y_mm": 0.0,
                "image_expected": False,
                "image_name": "",
                "status": "pending"
            })
            step_idx += 1

        # Transfer stock, mix, and snap
        for i, step in enumerate(dil_steps):
            if step["stock_volume_ul"] > 0:
                steps.append({
                    "step_index": step_idx,
                    "phase": "dilution",
                    "action": "pipette_stock",
                    "source_well": step["food_coloring_source_well"],
                    "destination_well": step["destination_well"],
                    "volume_ul": step["stock_volume_ul"],
                    "print_label": "",
                    "x_mm": 0.0,
                    "y_mm": 0.0,
                    "image_expected": False,
                    "image_name": "",
                    "status": "pending"
                })
                step_idx += 1
                
            mix_vol = step.get("mix_volume_ul", 0.0)
            mix_reps = step.get("mix_repetitions", 0)
            if mix_reps > 0 and mix_vol > 0.0:
                steps.append({
                    "step_index": step_idx,
                    "phase": "dilution",
                    "action": f"mix_well_{mix_reps}x",
                    "source_well": "",
                    "destination_well": step["destination_well"],
                    "volume_ul": mix_vol,
                    "print_label": "",
                    "x_mm": 0.0,
                    "y_mm": 0.0,
                    "image_expected": False,
                    "image_name": "",
                    "status": "pending"
                })
                step_idx += 1

            if camera_cfg.get("enabled") and camera_cfg.get("capture_after_each_dilution_step"):
                dest = step["destination_well"]
                steps.append({
                    "step_index": step_idx,
                    "phase": "dilution",
                    "action": "capture_dilution_step",
                    "source_well": "",
                    "destination_well": dest,
                    "volume_ul": 0.0,
                    "print_label": "",
                    "x_mm": 0.0,
                    "y_mm": 0.0,
                    "image_expected": True,
                    "image_name": f"wellplate_step_{i+1:03d}_well_{dest}.jpg",
                    "status": "pending"
                })
                step_idx += 1
                
    # 3. Printing steps
    print_positions = printing_cfg.get("print_positions", [])
    droplet_vol = printing_cfg.get("droplet_volume_ul", 0.0)
    for i, pos in enumerate(print_positions):
        if is_calibration:
            steps.append({
                "step_index": step_idx,
                "phase": "printing",
                "action": "calibrate_coordinate",
                "source_well": pos["source_well"],
                "destination_well": "",
                "volume_ul": 0.0,
                "print_label": pos["label"],
                "x_mm": pos["x_mm"],
                "y_mm": pos["y_mm"],
                "image_expected": False,
                "image_name": "",
                "status": "pending"
            })
            step_idx += 1
        else:
            steps.append({
                "step_index": step_idx,
                "phase": "printing",
                "action": "aspirate_print_source",
                "source_well": pos["source_well"],
                "destination_well": "",
                "volume_ul": droplet_vol,
                "print_label": "",
                "x_mm": 0.0,
                "y_mm": 0.0,
                "image_expected": False,
                "image_name": "",
                "status": "pending"
            })
            step_idx += 1
            steps.append({
                "step_index": step_idx,
                "phase": "printing",
                "action": "print_droplet",
                "source_well": "",
                "destination_well": "",
                "volume_ul": droplet_vol,
                "print_label": pos["label"],
                "x_mm": pos["x_mm"],
                "y_mm": pos["y_mm"],
                "image_expected": False,
                "image_name": "",
                "status": "pending"
            })
            step_idx += 1

        if camera_cfg.get("enabled") and camera_cfg.get("capture_after_each_print_step"):
            src = pos["source_well"]
            steps.append({
                "step_index": step_idx,
                "phase": "printing",
                "action": "capture_print_step",
                "source_well": src,
                "destination_well": "",
                "volume_ul": 0.0,
                "print_label": pos["label"],
                "x_mm": pos["x_mm"],
                "y_mm": pos["y_mm"],
                "image_expected": True,
                "image_name": f"paper_print_{i+1:03d}_well_{src}.jpg",
                "status": "pending"
            })
            step_idx += 1

    # 4. After snaps
    if camera_cfg.get("enabled") and camera_cfg.get("capture_after"):
        steps.append({
            "step_index": step_idx,
            "phase": "after",
            "action": "capture_after_deck",
            "source_well": "",
            "destination_well": "",
            "volume_ul": 0.0,
            "print_label": "",
            "x_mm": 0.0,
            "y_mm": 0.0,
            "image_expected": True,
            "image_name": "after_deck.jpg",
            "status": "pending"
        })
        step_idx += 1
        steps.append({
            "step_index": step_idx,
            "phase": "after",
            "action": "capture_after_wellplate",
            "source_well": "",
            "destination_well": "",
            "volume_ul": 0.0,
            "print_label": "",
            "x_mm": 0.0,
            "y_mm": 0.0,
            "image_expected": True,
            "image_name": "after_wellplate.jpg",
            "status": "pending"
        })
        step_idx += 1

    return steps

def create_mock_images(local_dir: Path, expected_images: List[Dict[str, Any]]) -> None:
    """Generate dummy colorful images with file text to simulate camera capture."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("Pillow not installed; skipping mock image creation.")
        return

    logger.info("Generating mock images for local/mock run.")
    for step in expected_images:
        phase = step["phase"]
        filename = step["filename"]
        label = step.get("print_label") or step.get("filename")
        # Assign colors based on category
        if phase == "before" or phase == "after":
            color = (70, 130, 180)  # Steel blue
        elif phase == "dilution":
            color = (46, 139, 87)   # Sea green
        else:
            color = (220, 20, 60)   # Crimson

        img = Image.new("RGB", (640, 480), color=color)
        draw = ImageDraw.Draw(img)
        text = f"OT-2 printing_demo mock capture\nFile: {filename}\nLabel: {label}\nTimestamp: {datetime.utcnow().isoformat()}Z"
        draw.text((20, 20), text, fill=(255, 255, 255))

        # Decide output subdirectory based on phase
        if phase in ("before", "after"):
            dest_dir = local_dir / "images" / "before_after"
        elif phase == "dilution":
            dest_dir = local_dir / "images" / "plate"
        else:
            dest_dir = local_dir / "images" / "paper"

        dest_dir.mkdir(parents=True, exist_ok=True)
        img.save(dest_dir / filename)
        logger.debug(f"Saved mock image {dest_dir / filename}")

def host_run(args: argparse.Namespace) -> None:
    """The host-side execution pipeline loader."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    logger.info(f"Loading YAML config: {args.config}")

    # 1. Parse and validate YAML
    with open(args.config, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)

    if HAS_PYDANTIC:
        try:
            validated = PrintingDemoConfig(**config_data)
            config_dict = validated.model_dump()
            logger.info("Pydantic config validation PASSED.")
        except Exception as e:
            logger.error(f"Pydantic config validation FAILED: {e}")
            sys.exit(1)
    else:
        logger.warning("Pydantic not available; skipping schema checks, using raw YAML dictionary.")
        config_dict = config_data

    # Generate timestamp
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # 2. Inject configuration into script template to build self-contained execution script
    template_path = Path(__file__).resolve()
    with open(template_path, "r", encoding="utf-8") as f:
        code = f.read()

    import pprint
    config_str = pprint.pformat(config_dict, indent=2)
    injected_code = code.replace("CONFIG" + " = None", f"CONFIG = {config_str}", 1)

    generated_dir = PROJECT_ROOT / "src" / "protocols" / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    
    # Write timestamped protocol
    run_script_path = generated_dir / f"printing_demo_run_{timestamp}.py"
    with open(run_script_path, "w", encoding="utf-8") as f:
        f.write(injected_code)
    logger.info(f"Compiled run protocol written to: {run_script_path}")

    # Write latest reference protocol
    latest_script_path = generated_dir / "printing_demo_latest.py"
    with open(latest_script_path, "w", encoding="utf-8") as f:
        f.write(injected_code)
    logger.info(f"Compiled run protocol updated latest: {latest_script_path}")

    # 3. Simulate compiled script
    sim_stdout = run_local_simulation(run_script_path)

    # 4. Folder allocation
    run_name = f"{config_dict['demo_mode']}_{timestamp}"
    local_root = args.output_dir or config_dict.get("output", {}).get("local_run_root", "runs")
    local_run_dir = Path(local_root) / run_name
    local_run_dir.mkdir(parents=True, exist_ok=True)

    # Subdirectories setup
    (local_run_dir / "metadata").mkdir(exist_ok=True)
    (local_run_dir / "images").mkdir(exist_ok=True)
    (local_run_dir / "images" / "before_after").mkdir(parents=True, exist_ok=True)
    (local_run_dir / "images" / "plate").mkdir(parents=True, exist_ok=True)
    (local_run_dir / "images" / "paper").mkdir(parents=True, exist_ok=True)
    (local_run_dir / "errors").mkdir(exist_ok=True)
    (local_run_dir / "protocols").mkdir(exist_ok=True)
    (local_run_dir / "logs").mkdir(exist_ok=True)

    # Copy the exact generated protocol into the run folder for traceability
    shutil.copy2(run_script_path, local_run_dir / "protocols" / f"printing_demo_run_{timestamp}.py")
    logger.info(f"Copied protocol into run folder: {local_run_dir / 'protocols' / f'printing_demo_run_{timestamp}.py'}")

    # Build the full step log template
    flat_step_log = build_flat_step_log(config_dict)

    # Compile the expected image list
    camera_cfg = config_dict["camera"]
    expected_images = []
    if camera_cfg["enabled"]:
        for step in flat_step_log:
            if step["image_expected"]:
                # Map keys to match the create_mock_images expected structure
                expected_images.append({
                    "phase": step["phase"],
                    "filename": step["image_name"],
                    "step_index": step["step_index"],
                    "source_well": step["source_well"],
                    "destination_well": step["destination_well"],
                    "print_label": step["print_label"],
                    "expected_path": f"images/before_after/{step['image_name']}" if step["phase"] in ("before", "after") else 
                                     (f"images/plate/{step['image_name']}" if step["phase"] == "dilution" else f"images/paper/{step['image_name']}")
                })

    image_manifest_data = []

    # 5. Execute run (Mock vs Real Robot)
    is_mock = args.mock or (not args.robot_ip and config_dict.get("robot_ip") is None)
    run_ok = True
    start_time = datetime.utcnow().isoformat() + "Z"

    if is_mock:
        logger.info("Executing MOCK run pipeline.")
        create_mock_images(local_run_dir, expected_images)
        
        # Update flat_step_log to completed
        for step in flat_step_log:
            step["status"] = "ok"
            if step["image_expected"]:
                fn = step["image_name"]
                phase = step["phase"]
                expected_path = f"images/before_after/{fn}" if phase in ("before", "after") else \
                                (f"images/plate/{fn}" if phase == "dilution" else f"images/paper/{fn}")

                image_manifest_data.append({
                    "image_name": fn,
                    "phase": phase,
                    "step_index": step["step_index"],
                    "source_well": step["source_well"],
                    "destination_well": step["destination_well"],
                    "print_label": step["print_label"],
                    "expected_path": expected_path,
                    "status": "valid",
                    "validation_result": "passed",
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                })
    else:
        # Real robot run
        robot_ip = args.robot_ip or config_dict.get("robot_ip")
        logger.info(f"Connecting to physical OT-2 robot: {robot_ip}")

        # Check existing project path utility config or load default SSH path
        from src.core.config import Config
        ssh_key = Config.ROBOT_SSH_KEY_PATH
        ssh_user = Config.ROBOT_SSH_USER

        if not ssh_key:
            logger.error("ROBOT_SSH_KEY_PATH is missing. Check .env configuration.")
            sys.exit(1)

        ssh_opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=no", "-i", ssh_key]

        # SCP run script to robot
        remote_dest_dir = "/var/lib/opentrons/user_storage/ot2_runs"
        remote_script_path = f"{remote_dest_dir}/printing_demo_run.py"
        logger.info(f"Deploying protocol to robot...")
        deploy_cmd = ["scp", "-O"] + ssh_opts + [str(run_script_path), f"{ssh_user}@{robot_ip}:{remote_script_path}"]
        
        dep_res = subprocess.run(deploy_cmd, capture_output=True, text=True, check=False)
        if dep_res.returncode != 0:
            logger.error(f"Protocol deployment FAILED: {dep_res.stderr}")
            sys.exit(1)
        logger.info("Protocol deployed successfully.")

        # Execute protocol remotely
        logger.info("Triggering protocol run on OT-2 robot...")
        exec_cmd = ["ssh"] + ssh_opts + [f"{ssh_user}@{robot_ip}", f"opentrons_execute {remote_script_path}"]
        
        run_res = subprocess.run(exec_cmd, capture_output=True, text=True, check=False)
        
        # Write robot execute log
        with open(local_run_dir / "logs" / "run.log", "w", encoding="utf-8") as lf:
            lf.write(run_res.stdout)
            lf.write(run_res.stderr)
            
        if run_res.returncode != 0:
            logger.error(f"Remote protocol execution FAILED! Check logs/run.log")
            run_ok = False
        else:
            logger.info("Remote execution completed.")

        # 6. SCP images back and reconcile
        if camera_cfg["enabled"]:
            logger.info("Retrieving captured images from OT-2...")
            temp_transfer_dir = local_run_dir / "temp_raw"
            temp_transfer_dir.mkdir(exist_ok=True)

            remote_image_dir = camera_cfg.get("robot_image_dir", "/data/vision/printing_demo")
            scp_cmd = ["scp", "-O"] + ssh_opts + ["-r", f"{ssh_user}@{robot_ip}:{remote_image_dir}/*", str(temp_transfer_dir)]
            
            scp_res = subprocess.run(scp_cmd, capture_output=True, text=True, check=False)
            if scp_res.returncode != 0:
                logger.warning(f"Could not download remote images: {scp_res.stderr}")

            # Reconcile expected captures against files retrieved
            try:
                from PIL import Image
            except ImportError:
                Image = None

            for step in expected_images:
                fn = step["filename"]
                phase = step["phase"]
                step_index = step["step_index"]
                source_well = step["source_well"]
                destination_well = step["destination_well"]
                print_label = step["print_label"]
                expected_path = step["expected_path"]
                
                temp_file = temp_transfer_dir / fn
                validation_passed = False
                failure_reason = "OK"

                if not temp_file.exists():
                    failure_reason = "Expected image not found on robot"
                else:
                    # Validate image
                    if temp_file.stat().st_size < 1000:
                        failure_reason = f"File too small ({temp_file.stat().st_size} bytes)"
                    elif Image:
                        try:
                            with Image.open(temp_file) as img:
                                img.verify()
                            validation_passed = True
                        except Exception as e:
                            failure_reason = f"Corrupt image block: {e}"
                    else:
                        validation_passed = True  # Fallback if PIL not present

                # Route validation output
                if validation_passed:
                    # Move to valid folder
                    if phase in ("before", "after"):
                        dest_dir = local_run_dir / "images" / "before_after"
                    elif phase == "dilution":
                        dest_dir = local_run_dir / "images" / "plate"
                    else:
                        dest_dir = local_run_dir / "images" / "paper"
                    
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(temp_file), str(dest_dir / fn))
                    
                    # Update status in flat_step_log
                    for s in flat_step_log:
                        if s["step_index"] == step_index and s["image_name"] == fn:
                            s["status"] = "ok"

                    image_manifest_data.append({
                        "image_name": fn,
                        "phase": phase,
                        "step_index": step_index,
                        "source_well": source_well,
                        "destination_well": destination_well,
                        "print_label": print_label,
                        "expected_path": expected_path,
                        "status": "valid",
                        "validation_result": "passed",
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    })
                else:
                    # Write failure log
                    logger.warning(f"Capture reconciliation failure for {fn}: {failure_reason}")
                    err_json_path = local_run_dir / "errors" / f"{Path(fn).stem}.json"
                    error_data = {
                        "step_index": step_index,
                        "action": phase,
                        "source_well": source_well,
                        "destination_well": destination_well,
                        "attempted_image_name": fn,
                        "failure_reason": failure_reason,
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                    with open(err_json_path, "w", encoding="utf-8") as ef:
                        json.dump(error_data, ef, indent=2)

                    if temp_file.exists():
                        shutil.move(str(temp_file), str(local_run_dir / "errors" / f"{Path(fn).stem}.partial.jpg"))

                    # Update status in flat_step_log
                    for s in flat_step_log:
                        if s["step_index"] == step_index and s["image_name"] == fn:
                            s["status"] = "failed"

                    image_manifest_data.append({
                        "image_name": fn,
                        "phase": phase,
                        "step_index": step_index,
                        "source_well": source_well,
                        "destination_well": destination_well,
                        "print_label": print_label,
                        "expected_path": expected_path,
                        "status": "failed",
                        "validation_result": failure_reason,
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    })

            # Cleanup remote directories on OT-2
            logger.info("Cleaning up remote JPEGs on robot...")
            cleanup_cmd = ["ssh"] + ssh_opts + [f"{ssh_user}@{robot_ip}", f"rm -rf {remote_image_dir}/*"]
            subprocess.run(cleanup_cmd, check=False)
            
            # Clean local temp_raw
            if temp_transfer_dir.exists():
                shutil.rmtree(temp_transfer_dir)

        # Mark non-image steps based on run success
        for s in flat_step_log:
            if not s["image_expected"]:
                s["status"] = "ok" if run_ok else "failed"

    # 7. Write Manifests and Metadata
    end_time = datetime.utcnow().isoformat() + "Z"
    plate_map_dict, ascii_map = generate_plate_map(config_dict)

    # Write run_metadata.json
    metadata_path = local_run_dir / "metadata" / "run_metadata.json"
    run_metadata = {
        "demo_name": config_dict["demo_mode"],
        "start_utc_timestamp": start_time,
        "end_utc_timestamp": end_time,
        "robot_ip": config_dict.get("robot_ip") or (None if is_mock else robot_ip),
        "plate_map_ascii": ascii_map,
        "plate_map_roles": plate_map_dict,
        "config_parameters": config_dict,
        "execution_steps": flat_step_log,
        "image_manifest": image_manifest_data,
        "simulation_output": sim_stdout,
        "overall_status": "SUCCESS" if run_ok else "FAILED"
    }
    with open(metadata_path, "w", encoding="utf-8") as mf:
        json.dump(run_metadata, mf, indent=2)
    logger.info(f"Saved run metadata to: {metadata_path}")

    # Write printer manifest (mapping wells to coordinates)
    manifest_data = []
    for pos in config_dict["printing"]["print_positions"]:
        manifest_data.append({
            "source_well": pos["source_well"],
            "target_label": pos["label"],
            "x_mm": pos["x_mm"],
            "y_mm": pos["y_mm"],
            "volume_ul": config_dict["printing"]["droplet_volume_ul"],
            "concentration": "Water" if config_dict["demo_mode"] == "water_print" else "Diluted"
        })

    # Save printer manifest JSON
    pm_json = local_run_dir / "metadata" / "printer_manifest.json"
    with open(pm_json, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    # Save printer manifest CSV
    import csv
    pm_csv = local_run_dir / "metadata" / "printer_manifest.csv"
    with open(pm_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["source_well", "target_label", "x_mm", "y_mm", "volume_ul", "concentration"])
        writer.writeheader()
        writer.writerows(manifest_data)
    logger.info(f"Saved printer manifests (JSON and CSV) to: {local_run_dir / 'metadata'}")

    # Save image_manifest.csv
    image_manifest_csv = local_run_dir / "metadata" / "image_manifest.csv"
    with open(image_manifest_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "image_name", "phase", "step_index", "source_well", "destination_well", 
            "print_label", "expected_path", "status", "validation_result", "timestamp"
        ])
        writer.writeheader()
        writer.writerows(image_manifest_data)
    logger.info(f"Saved image manifest to: {image_manifest_csv}")

    # Save step_log.csv
    step_log_csv = local_run_dir / "metadata" / "step_log.csv"
    with open(step_log_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "step_index", "phase", "action", "source_well", "destination_well",
            "volume_ul", "print_label", "x_mm", "y_mm", "image_expected", "image_name", "status"
        ])
        writer.writeheader()
        writer.writerows(flat_step_log)
    logger.info(f"Saved step log to: {step_log_csv}")

    logger.info("Done execution run.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Compile and orchestrate the OT-2 Printing Demo.")
    parser.add_argument("--config", type=str, default="configs/workflows/defaults/printing_demo.yaml", help="Path to config YAML.")
    parser.add_argument("--mock", action="store_true", help="Run local simulation only + mock camera snaps.")
    parser.add_argument("--output-dir", type=str, default=None, help="Override output runs directory.")
    parser.add_argument("--robot-ip", type=str, default=None, help="IP of physical robot.")
    host_args = parser.parse_args()

    host_run(host_args)
