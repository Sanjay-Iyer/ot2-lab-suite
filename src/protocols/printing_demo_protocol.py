#!/usr/bin/env python3
"""
src/protocols/printing_demo_protocol.py
========================================
OT-2 Printing Demo - HOST-SIDE orchestrator only.

This file is the HOST-SIDE builder and runner. It:
  1. Loads and Pydantic-validates the YAML config (host-only).
  2. Generates a self-contained robot protocol from an explicit template.
  3. Runs local Opentrons simulation on the generated file.
  4. Deploys and executes on the robot (real run) or runs mock (--mock).

The GENERATED robot protocol is deliberately minimal. It contains ONLY:
  - from opentrons import protocol_api
  - standard-library imports (os, shutil, subprocess, time)
  - metadata dict
  - embedded CONFIG dict (plain Python literal, no YAML file access)
  - capture_image() helper
  - def run(protocol)

Forbidden from the generated robot protocol:
  import yaml / from yaml / pydantic / PIL / pandas / numpy /
  project-local imports / argparse / manifest writing / SCP / SSH
"""

import os
import re
import sys
import csv
import json
import shutil
import logging
import pprint
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

# ── Conditional imports (host has these; robot may not) ───────────────
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

# Logger
logger = logging.getLogger("printing_demo")

# ─── Well Validation Helpers ──────────────────────────────────────────

def is_valid_well(well: str) -> bool:
    """Check if a well name is a valid 96-well format (A1 to H12)."""
    return bool(re.match(r"^[A-H]([1-9]|1[0-2])$", well))

def get_well_bounds_for_labware(labware: str) -> Tuple[str, int]:
    """Return the maximum row letter and column number for the given labware name."""
    lw_lower = labware.lower()
    if "96_wellplate" in lw_lower or "96_tiprack" in lw_lower:
        return "H", 12
    elif "24_wellplate" in lw_lower or "24_tuberack" in lw_lower:
        return "D", 6
    elif "12_wellplate" in lw_lower or "12well" in lw_lower:
        return "C", 4
    # Default fallback to 96-well
    return "H", 12


def is_valid_well_for_labware(well: str, labware: str) -> bool:
    """Validate that a well name is structurally correct and lies within the boundaries of the labware."""
    match = re.match(r"^([A-Z])([0-9]+)$", well)
    if not match:
        return False
    row, col_str = match.groups()
    col = int(col_str)
    max_row, max_col = get_well_bounds_for_labware(labware)
    if not ("A" <= row <= max_row):
        return False
    if not (1 <= col <= max_col):
        return False
    return True


# ─── Pydantic Models for Config Validation (Host-Side Only) ──────────

if HAS_PYDANTIC:
    class PlateConfig(BaseModel):
        slot: int = Field(..., ge=1, le=11)
        labware: str
        namespace: Optional[str] = None
        version: Optional[int] = None


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

        @field_validator(
            "food_coloring_source_wells",
            "water_source_wells",
            "dilution_destination_wells",
            "print_source_wells",
        )
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
        def validate_step(self) -> "DilutionStep":
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
        x_mm: Optional[float] = None
        y_mm: Optional[float] = None
        x_index: Optional[int] = None
        y_index: Optional[int] = None

        @model_validator(mode="after")
        def validate_pos(self) -> "PrintPosition":
            if not is_valid_well(self.source_well):
                raise ValueError(f"Invalid print source well: {self.source_well}")
            has_indices = self.x_index is not None and self.y_index is not None
            has_coords = self.x_mm is not None and self.y_mm is not None
            if not (has_indices or has_coords):
                raise ValueError(
                    f"PrintPosition '{self.label}' must have either x_index and y_index, or x_mm and y_mm."
                )
            return self

    class PrintOriginConfig(BaseModel):
        x_mm: float
        y_mm: float

    class PrintSpacingConfig(BaseModel):
        x_mm: float
        y_mm: float

        @field_validator("x_mm", "y_mm")
        @classmethod
        def validate_spacing(cls, v: float) -> float:
            if v <= 0:
                raise ValueError("Spacing must be greater than 0.")
            return v

    class PrintingConfig(BaseModel):
        calibration_only: bool = False
        paper_slot: int = Field(..., ge=1, le=11)
        droplet_volume_ul: float = Field(..., gt=0)
        dispense_height_mm: float = Field(..., ge=0)
        safe_z_mm: float = Field(..., ge=0)
        print_origin: Optional[PrintOriginConfig] = None
        print_spacing: Optional[PrintSpacingConfig] = None
        print_positions: List[PrintPosition]

    class TipStrategyConfig(BaseModel):
        mode: str
        allowed_modes: List[str]
        loaded_tip_count: Optional[int] = Field(None, ge=1)
        starting_tip: Optional[str] = None
        return_tip_at_end: Optional[bool] = None
        drop_tip_at_end: Optional[bool] = None
        reuse_for_water: Optional[bool] = None
        reuse_for_stock: Optional[bool] = None
        reuse_for_mixing: Optional[bool] = None
        reuse_for_printing: Optional[bool] = None

        @model_validator(mode="after")
        def validate_strategy(self) -> "TipStrategyConfig":
            if self.mode not in self.allowed_modes:
                raise ValueError(
                    f"Tip mode '{self.mode}' not in allowed modes: {self.allowed_modes}"
                )
            if self.starting_tip is not None and not is_valid_well(self.starting_tip):
                raise ValueError(
                    f"starting_tip must be a valid well (A1-H12). Got: {self.starting_tip}"
                )
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

    class DebugConfig(BaseModel):
        enabled: bool = True
        verbose_protocol_comments: bool = True
        dry_run_no_liquid: bool = False
        move_only: bool = False

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
        debug: DebugConfig = DebugConfig()
        output: OutputConfig

        @model_validator(mode="after")
        def validate_experiment_rules(self) -> "PrintingDemoConfig":
            if self.demo_mode not in ("water_print", "dilution_print", "dry_run"):
                raise ValueError(
                    f"Invalid demo_mode: {self.demo_mode}. Must be water_print, dilution_print, or dry_run."
                )

            if self.demo_mode == "dry_run":
                return self

            # Safety check: Detect old labware name usage
            if self.plate.labware == "usa_scientific_12well_12_wellplate_6000ul":
                raise ValueError(
                    "Incorrect old labware name detected: usa_scientific_12well_12_wellplate_6000ul. "
                    "Use usascientific12well_12_wellplate_6000ul with namespace custom_beta and version 1."
                )

            # Safety check: Enforce correct namespace and version for the custom labware
            if self.plate.labware == "usascientific12well_12_wellplate_6000ul":
                if self.plate.namespace != "custom_beta" or self.plate.version != 1:
                    raise ValueError(
                        f"Custom labware '{self.plate.labware}' must be loaded from namespace 'custom_beta' "
                        f"with version 1. (Got namespace={self.plate.namespace}, version={self.plate.version})"
                    )

            # Out-of-bounds X safety validation (OT-2 gantry limits)
            origin = self.printing.print_origin
            spacing = self.printing.print_spacing
            for pos in self.printing.print_positions:
                if pos.x_index is not None and pos.y_index is not None and origin and spacing:
                    x_mm = origin.x_mm + pos.x_index * spacing.x_mm
                else:
                    x_mm = pos.x_mm or 0.0
                if x_mm > 418.0:
                    raise ValueError(
                        f"Print position '{pos.label}' X coordinate {x_mm} mm exceeds physical OT-2 limit of 418.0 mm."
                    )

            # Verify that all configured wells exist within the plate's boundaries
            plate_labware = self.plate.labware

            for name, wells in [
                ("food_coloring_source_wells", self.layout.food_coloring_source_wells),
                ("water_source_wells", self.layout.water_source_wells),
                ("dilution_destination_wells", self.layout.dilution_destination_wells),
                ("print_source_wells", self.layout.print_source_wells),
            ]:
                for well in wells:
                    if not is_valid_well_for_labware(well, plate_labware):
                        max_row, max_col = get_well_bounds_for_labware(plate_labware)
                        raise ValueError(
                            f"Well '{well}' in layout.{name} is invalid for plate '{plate_labware}' "
                            f"(valid bounds: A1 to {max_row}{max_col})."
                        )

            if self.demo_mode == "dilution_print" and self.dilution.enabled:
                for idx, step in enumerate(self.dilution.steps):
                    for field, well in [
                        ("destination_well", step.destination_well),
                        ("water_source_well", step.water_source_well),
                        ("food_coloring_source_well", step.food_coloring_source_well),
                    ]:
                        if not is_valid_well_for_labware(well, plate_labware):
                            max_row, max_col = get_well_bounds_for_labware(plate_labware)
                            raise ValueError(
                                f"Well '{well}' in dilution step {idx+1} ({field}) is invalid for plate '{plate_labware}' "
                                f"(valid bounds: A1 to {max_row}{max_col})."
                            )

            fc_wells = set(self.layout.food_coloring_source_wells)

            w_wells = set(self.layout.water_source_wells)
            d_wells = set(self.layout.dilution_destination_wells)

            if fc_wells & w_wells:
                raise ValueError(
                    f"Overlap: food coloring and water source wells overlap at {fc_wells & w_wells}"
                )
            if fc_wells & d_wells:
                raise ValueError(
                    f"Overlap: food coloring and dilution destination wells overlap at {fc_wells & d_wells}"
                )
            if w_wells & d_wells:
                raise ValueError(
                    f"Overlap: water source and dilution destination wells overlap at {w_wells & d_wells}"
                )

            if self.demo_mode == "dilution_print":
                prepared_wells = set()
                for step in self.dilution.steps:
                    if step.water_source_well in d_wells and step.water_source_well not in prepared_wells:
                        raise ValueError(
                            f"Aspirating water from unprepared dilution destination {step.water_source_well}."
                        )
                    if step.food_coloring_source_well in d_wells and step.food_coloring_source_well not in prepared_wells:
                        raise ValueError(
                            f"Aspirating stock from unprepared dilution destination {step.food_coloring_source_well}."
                        )
                    if step.destination_well in fc_wells:
                        raise ValueError(
                            f"Dispense into food coloring source well: {step.destination_well}"
                        )
                    if step.destination_well in w_wells:
                        raise ValueError(
                            f"Dispense into water source well: {step.destination_well}"
                        )
                    if step.destination_well not in d_wells:
                        raise ValueError(
                            f"Dilution destination {step.destination_well} not in layout.dilution_destination_wells"
                        )
                    prepared_wells.add(step.destination_well)

                for pos in self.printing.print_positions:
                    if (
                        pos.source_well not in prepared_wells
                        and pos.source_well not in self.layout.print_source_wells
                    ):
                        raise ValueError(
                            f"Print source well {pos.source_well} not prepared in dilution steps."
                        )
            else:
                allowed_sources = w_wells | set(self.layout.print_source_wells)
                for pos in self.printing.print_positions:
                    if pos.source_well not in allowed_sources:
                        raise ValueError(
                            f"Water print source well {pos.source_well} not in water_source_wells or print_source_wells."
                        )

            return self


# ─── OT-2 metadata (kept for host-side compatibility) ────────────────

metadata = {
    "protocolName": "OT-2 Paper Printing Demo with Vision Integration",
    "author": "Antigravity AI Agent",
    "description": "Pipetting water or dilution series onto paper with step-by-step camera snaps",
    "apiLevel": "2.13",
}


# ══════════════════════════════════════════════════════════════════════
# ROBOT-ONLY SCRIPT TEMPLATE
# These two string constants are concatenated around the embedded CONFIG
# dict to produce the generated robot protocol.
#
# Rules:
#   - MUST NOT contain: yaml, pydantic, PIL, pandas, numpy, argparse,
#     project-local imports, manifest writing, SCP/SSH, mock image code.
#   - MUST contain: from opentrons import protocol_api, CONFIG =,
#     def run(, protocol.is_simulating()
# ══════════════════════════════════════════════════════════════════════

_ROBOT_SCRIPT_PRE_CONFIG = '''\
#!/usr/bin/env python3
"""
OT-2 Printing Demo - Robot Protocol (Auto-generated)
Generated by: src/protocols/printing_demo_protocol.py host-side builder.

DO NOT EDIT this file manually. Regenerate it by running the host script:
  python src/protocols/printing_demo_protocol.py --config <yaml> --mock

This file contains ONLY:
  - Opentrons API imports
  - Standard-library imports (os, shutil, subprocess, time)
  - metadata dict
  - Embedded CONFIG dict (plain Python literal, no YAML file access)
  - capture_image() camera helper
  - def run(protocol)
"""

from opentrons import protocol_api
from opentrons.types import Point
import os
import shutil
import subprocess
import time

metadata = {
    "protocolName": "OT-2 Paper Printing Demo with Vision Integration",
    "author": "Antigravity AI Agent",
    "description": "Pipetting water or dilution series onto paper with step-by-step camera snaps",
    "apiLevel": "2.13",
}

CONFIG = \
'''

_ROBOT_SCRIPT_POST_CONFIG = '''

def resolve_print_position(printing_cfg, pos_cfg):
    origin = printing_cfg.get("print_origin", {"x_mm": 5.0, "y_mm": 5.0})
    spacing = printing_cfg.get("print_spacing", {"x_mm": 20.0, "y_mm": 10.0})

    if float(spacing.get("x_mm", 0)) <= 0 or float(spacing.get("y_mm", 0)) <= 0:
        raise ValueError(
            f"Invalid print_spacing: x_mm={spacing.get('x_mm')}, y_mm={spacing.get('y_mm')}. "
            "Spacing must be greater than 0."
        )

    x_index = pos_cfg.get("x_index")
    y_index = pos_cfg.get("y_index")

    if x_index is not None or y_index is not None:
        x_index = int(x_index or 0)
        y_index = int(y_index or 0)
        x_mm = float(origin["x_mm"]) + x_index * float(spacing["x_mm"])
        y_mm = float(origin["y_mm"]) + y_index * float(spacing["y_mm"])
    else:
        x_mm = float(pos_cfg["x_mm"])
        y_mm = float(pos_cfg["y_mm"])

    if not (0.0 <= x_mm <= 100.0):
        raise ValueError(
            f"Resolved X coordinate {x_mm:.2f} mm is out of safe range (0 to 100 mm) "
            f"for print position '{pos_cfg.get('label')}'."
        )
    if not (0.0 <= y_mm <= 80.0):
        raise ValueError(
            f"Resolved Y coordinate {y_mm:.2f} mm is out of safe range (0 to 80 mm) "
            f"for print position '{pos_cfg.get('label')}'."
        )

    return x_mm, y_mm


def run(protocol: protocol_api.ProtocolContext) -> None:
    """Main entry point called by the Opentrons execution engine on the robot."""

    # ── Debug flags ──────────────────────────────────────────────────
    debug_cfg = CONFIG.get("debug", {})
    debug_enabled = debug_cfg.get("enabled", False)
    verbose_comments = debug_cfg.get("verbose_protocol_comments", False)
    dry_run_no_liquid = debug_cfg.get("dry_run_no_liquid", False)
    move_only = debug_cfg.get("move_only", False)

    def dbg_comment(msg: str) -> None:
        if debug_enabled and verbose_comments:
            protocol.comment(f"DEBUG: {msg}")

    dbg_comment("entered run()")
    dbg_comment("config loaded")

    # ── 1. Deck slot collision check ─────────────────────────────────
    plate_cfg = CONFIG["plate"]
    tiprack_cfg = CONFIG["tiprack"]
    pipette_cfg = CONFIG["pipette"]
    printing_cfg = CONFIG["printing"]
    camera_cfg = CONFIG["camera"]

    slots = [plate_cfg["slot"], tiprack_cfg["slot"], printing_cfg["paper_slot"]]
    if len(slots) != len(set(slots)):
        raise ValueError(
            f"Duplicate deck slot assignments: "
            f"Plate={plate_cfg['slot']}, Tiprack={tiprack_cfg['slot']}, "
            f"Paper={printing_cfg['paper_slot']}"
        )

    # ── 2. Load labware and instrument ───────────────────────────────
    dbg_comment("labware loading started")
    
    # Safety Check: Detect old labware name usage
    if plate_cfg.get("labware") == "usa_scientific_12well_12_wellplate_6000ul":
        raise ValueError(
            "Incorrect old labware name detected: usa_scientific_12well_12_wellplate_6000ul. "
            "Use usascientific12well_12_wellplate_6000ul with namespace custom_beta and version 1."
        )

    # Safety Check: Validate custom labware properties
    if plate_cfg.get("labware") == "usascientific12well_12_wellplate_6000ul":
        if plate_cfg.get("namespace") != "custom_beta" or int(plate_cfg.get("version", 1)) != 1:
            raise ValueError(
                f"Custom labware '{plate_cfg['labware']}' must be loaded from namespace 'custom_beta' "
                f"with version 1. (Got namespace={plate_cfg.get('namespace')}, version={plate_cfg.get('version')})"
            )

    protocol.comment(
        f"DEBUG: loading plate labware={plate_cfg.get('labware')} "
        f"namespace={plate_cfg.get('namespace', 'custom_beta')} "
        f"version={plate_cfg.get('version', 1)} slot={plate_cfg.get('slot')}"
    )
    plate = protocol.load_labware(
        plate_cfg["labware"],
        plate_cfg["slot"],
        namespace=plate_cfg.get("namespace", "custom_beta"),
        version=plate_cfg.get("version", 1),
    )
    protocol.comment("DEBUG: plate labware loaded successfully")
    
    tiprack = protocol.load_labware(tiprack_cfg["labware"], tiprack_cfg["slot"])
    dbg_comment("tiprack loaded")
    
    paper_ref = protocol.load_labware(
        plate_cfg["labware"],
        printing_cfg["paper_slot"],
        namespace=plate_cfg.get("namespace", "custom_beta"),
        version=plate_cfg.get("version", 1),
    )


    pipette = protocol.load_instrument(
        pipette_cfg["name"],
        pipette_cfg["mount"],
        tip_racks=[tiprack],
    )
    dbg_comment("pipette loaded")

    if CONFIG.get("demo_mode") == "dry_run" or dry_run_no_liquid:
        protocol.comment("robot dry protocol reached run function")
        protocol.comment("Dry run check complete. Labware and pipettes loaded successfully.")
        dbg_comment("protocol completed")
        return

    # ── 2.5 Tip Strategy Setup and Helpers ───────────────────────────
    strategy_cfg = CONFIG.get("tip_strategy", {})
    strategy_mode = strategy_cfg.get("mode", "new_tip_each_transfer")
    starting_tip = strategy_cfg.get("starting_tip", "A1")
    loaded_tip_count = strategy_cfg.get("loaded_tip_count", 96) or 96

    dbg_comment(f"tip strategy = {strategy_mode}")

    if strategy_mode == "reuse_single_tip_for_demo":
        protocol.comment("WARNING: reuse_single_tip_for_demo mode is enabled. This is for demonstration only and may cause carryover.")

    # Build list of allowed wells based on starting tip and loaded count
    all_wells = tiprack.wells()
    try:
        start_idx = all_wells.index(tiprack.wells_by_name()[starting_tip])
    except Exception:
        start_idx = 0

    allowed_wells = all_wells[start_idx : start_idx + loaded_tip_count]
    tip_index = 0

    def ensure_tip(phase: str) -> None:
        """Ensure the pipette has a tip, picking one up if needed."""
        if pipette.has_tip:
            return

        nonlocal tip_index
        if tip_index >= len(allowed_wells):
            raise RuntimeError(
                f"Exceeded loaded_tip_count limit of {loaded_tip_count} tips. "
                f"Cannot pick up another tip for phase: {phase}."
            )

        target_well = allowed_wells[tip_index]
        if strategy_mode == "reuse_single_tip_for_demo":
            dbg_comment(f"picking up demo tip {target_well.well_name}")
        else:
            dbg_comment(f"picking up tip from {target_well.well_name}")

        pipette.pick_up_tip(target_well)

        if strategy_mode != "reuse_single_tip_for_demo":
            tip_index += 1

    def finish_tip() -> None:
        """Handle end-of-run tip disposal."""
        if not pipette.has_tip:
            return

        if strategy_mode == "reuse_single_tip_for_demo":
            if strategy_cfg.get("return_tip_at_end", True):
                dbg_comment("returning tip at end")
                pipette.return_tip()
            elif strategy_cfg.get("drop_tip_at_end", False):
                dbg_comment("dropping tip at end")
                pipette.drop_tip()
            else:
                protocol.comment("WARNING: Protocol completed but tip remains attached to pipette.")
        else:
            dbg_comment("dropping tip at end")
            pipette.drop_tip()

    # ── 3. Camera capture helper ─────────────────────────────────────
    def capture_image(filename: str) -> None:
        """Capture a JPEG from the robot camera via the HTTP API."""
        if protocol.is_simulating():
            protocol.comment(f"[SIMULATION] Mock photo: {filename}")
            return

        protocol.comment(f"--- DIAGNOSTIC CAPTURE START: {filename} ---")

        remote_dir = camera_cfg.get("robot_image_dir", "/data/vision/printing_demo")
        protocol.comment(f"Debug: target remote_dir = {remote_dir}")

        try:
            os.makedirs(remote_dir, exist_ok=True)
            test_file_path = os.path.join(remote_dir, ".write_test")
            with open(test_file_path, "w") as _f:
                _f.write("test")
            os.remove(test_file_path)
            protocol.comment(f"Debug: write permissions verified for {remote_dir}")
        except Exception as _e:
            protocol.comment(f"Warning: Directory/write test failed for {remote_dir}: {_e}")

        output_path = os.path.join(remote_dir, filename)
        endpoint = "http://localhost:31950/camera/picture"

        curl_path = shutil.which("curl")
        protocol.comment(f"Debug: shutil.which('curl') = {curl_path}")

        cmd = [
            "curl", "-s", "-X", "POST",
            "-H", "opentrons-version: *",
            "--max-time", "5",
            endpoint,
            "--output", output_path,
        ]
        protocol.comment(f"Debug: Running curl cmd: {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            protocol.comment(f"Debug: curl returned code {result.returncode}")
            if result.stdout:
                protocol.comment(f"Debug: curl stdout = {result.stdout[:200]}")
            if result.stderr:
                protocol.comment(f"Debug: curl stderr = {result.stderr[:200]}")

            if os.path.exists(output_path):
                sz = os.path.getsize(output_path)
                protocol.comment(f"Debug: image file size = {sz} bytes")
                if sz < 1000:
                    protocol.comment(f"Warning: captured file suspicious (too small: {sz} bytes)")
                    try:
                        with open(output_path, "r", encoding="utf-8", errors="ignore") as _tf:
                            protocol.comment(f"Debug: file preview: {_tf.read(200)}")
                    except Exception as _pe:
                        protocol.comment(f"Debug: preview read failed: {_pe}")
            else:
                protocol.comment(f"Warning: File {output_path} was NOT created.")

        except FileNotFoundError:
            protocol.comment(f"Warning: 'curl' not found on robot. Cannot capture {filename}.")
        except Exception as _e:
            protocol.comment(f"Warning: Camera capture error for {filename}: {_e}")

        protocol.comment(f"--- DIAGNOSTIC CAPTURE END: {filename} ---")

    # ── Phase 1: Before overview ─────────────────────────────────────
    if camera_cfg["enabled"] and camera_cfg["capture_before"]:
        capture_image("before_deck.jpg")
        capture_image("before_wellplate.jpg")

    # ── Phase 2: Dilution ────────────────────────────────────────────
    if (
        CONFIG["demo_mode"] == "dilution_print"
        and CONFIG["dilution"]["enabled"]
        and not printing_cfg.get("calibration_only", False)
    ):
        dil_steps = list(CONFIG["dilution"]["steps"])

        if strategy_mode == "reuse_low_to_high":
            def _get_concentration_ratio(s):
                tot = s["water_volume_ul"] + s["stock_volume_ul"]
                return s["stock_volume_ul"] / tot if tot > 0.0 else 0.0
            dil_steps = sorted(dil_steps, key=_get_concentration_ratio)
            protocol.comment("Sorted dilution steps by concentration for tip reuse.")

        # Distribute water first (single tip, efficient)
        water_steps = [s for s in dil_steps if s["water_volume_ul"] > 0]
        if water_steps:
            ensure_tip("water")
            if strategy_mode == "reuse_single_tip_for_demo":
                dbg_comment("reusing same tip for water transfer")
            for step in water_steps:
                src_well = plate.wells_by_name()[step["water_source_well"]]
                dest_well = plate.wells_by_name()[step["destination_well"]]
                vol = step["water_volume_ul"]
                protocol.comment(
                    f"Pipetting {vol} uL water from {step['water_source_well']} "
                    f"to {step['destination_well']}"
                )
                pipette.aspirate(vol, src_well)
                pipette.dispense(vol, dest_well)
            
            if strategy_mode != "reuse_single_tip_for_demo":
                pipette.drop_tip()

        # Transfer food coloring and mix
        reusing_tip = False
        if strategy_mode in ("reuse_low_to_high", "reuse_per_phase", "reuse_single_tip_for_demo"):
            ensure_tip("stock")
            reusing_tip = True

        for idx, step in enumerate(dil_steps):
            if not reusing_tip:
                ensure_tip("stock")

            if strategy_mode == "reuse_single_tip_for_demo":
                dbg_comment("reusing same tip for stock transfer")

            src_well = plate.wells_by_name()[step["food_coloring_source_well"]]
            dest_well = plate.wells_by_name()[step["destination_well"]]
            stock_vol = step["stock_volume_ul"]

            if stock_vol > 0:
                protocol.comment(
                    f"Pipetting {stock_vol} uL stock from {step['food_coloring_source_well']} "
                    f"to {step['destination_well']}"
                )
                pipette.aspirate(stock_vol, src_well)
                pipette.dispense(stock_vol, dest_well)

            mix_vol = step.get("mix_volume_ul", 0.0)
            mix_reps = step.get("mix_repetitions", 0)
            if mix_reps > 0 and mix_vol > 0.0:
                if strategy_mode == "reuse_single_tip_for_demo":
                    dbg_comment("reusing same tip for mixing")
                protocol.comment(
                    f"Mixing well {step['destination_well']} ({mix_reps} x {mix_vol} uL)"
                )
                pipette.mix(mix_reps, mix_vol, dest_well)

            if not reusing_tip:
                pipette.drop_tip()

            if camera_cfg["enabled"] and camera_cfg["capture_after_each_dilution_step"]:
                capture_image(
                    f"wellplate_step_{idx + 1:03d}_well_{step['destination_well']}.jpg"
                )

        if reusing_tip and strategy_mode != "reuse_single_tip_for_demo":
            pipette.drop_tip()

    # ── Phase 3: Printing / Calibration ─────────────────────────────
    print_positions = printing_cfg["print_positions"]
    paper_a1 = paper_ref.wells()[0]
    droplet_vol = printing_cfg["droplet_volume_ul"]
    dispense_h = printing_cfg.get("dispense_height_mm", 1.0)
    is_calibration = printing_cfg.get("calibration_only", False)

    if is_calibration:
        protocol.comment("Executing Calibration Mode: verifying coordinates above paper.")
        ensure_tip("printing")
        for idx, pos in enumerate(print_positions):
            x_mm, y_mm = resolve_print_position(printing_cfg, pos)
            print(f"DEBUG: print position label={pos.get('label')} source_well={pos.get('source_well')} "
                  f"x_index={pos.get('x_index')} y_index={pos.get('y_index')} "
                  f"resolved_x_mm={x_mm} resolved_y_mm={y_mm}")
            dest_loc = paper_a1.bottom().move(
                Point(x=x_mm, y=y_mm, z=dispense_h)
            )
            # Safety check: Prevent gantry out of bounds move on the X axis
            if dest_loc.point.x > 418.0:
                raise ValueError(
                    f"Out of bounds move detected: X={dest_loc.point.x:.3f} mm is too high for physical limit of 418.0 mm. "
                    f"Check coordinates for print position '{pos['label']}'."
                )
            protocol.comment(
                f"Moving tip above paper X={x_mm} mm, "
                f"Y={y_mm} mm, Z={dispense_h} mm"
            )
            pipette.move_to(dest_loc)
            if protocol.is_simulating():
                protocol.delay(seconds=2)
            else:
                protocol.pause(
                    f"Calibration Check - Point: {pos['label']} "
                    f"(Source: {pos['source_well']}). Resume to continue."
                )
            if camera_cfg["enabled"] and camera_cfg["capture_after_each_print_step"]:
                capture_image(f"paper_print_{idx + 1:03d}_well_{pos['source_well']}.jpg")
        
        if strategy_mode != "reuse_single_tip_for_demo":
            pipette.drop_tip()
    else:
        reusing_print_tip = (strategy_mode == "reuse_single_tip_for_demo")
        if reusing_print_tip:
            ensure_tip("printing")

        for idx, pos in enumerate(print_positions):
            x_mm, y_mm = resolve_print_position(printing_cfg, pos)
            print(f"DEBUG: print position label={pos.get('label')} source_well={pos.get('source_well')} "
                  f"x_index={pos.get('x_index')} y_index={pos.get('y_index')} "
                  f"resolved_x_mm={x_mm} resolved_y_mm={y_mm}")
            if not reusing_print_tip:
                ensure_tip("printing")
            
            if strategy_mode == "reuse_single_tip_for_demo":
                dbg_comment("reusing same tip for printing")

            src_well = plate.wells_by_name()[pos["source_well"]]

            protocol.comment(
                f"Aspirating {droplet_vol} uL from {pos['source_well']} for printing"
            )
            pipette.aspirate(droplet_vol, src_well)

            dest_loc = paper_a1.bottom().move(
                Point(x=x_mm, y=y_mm, z=dispense_h)
            )
            # Safety check: Prevent gantry out of bounds move on the X axis
            if dest_loc.point.x > 418.0:
                raise ValueError(
                    f"Out of bounds move detected: X={dest_loc.point.x:.3f} mm is too high for physical limit of 418.0 mm. "
                    f"Check coordinates for print position '{pos['label']}'."
                )
            protocol.comment(
                f"Printing droplet on paper at X={x_mm} mm, Y={y_mm} mm"
            )
            pipette.dispense(droplet_vol, dest_loc)
            
            if not reusing_print_tip:
                pipette.drop_tip()

            if camera_cfg["enabled"] and camera_cfg["capture_after_each_print_step"]:
                capture_image(f"paper_print_{idx + 1:03d}_well_{pos['source_well']}.jpg")

    # ── Phase 4: After overview ──────────────────────────────────────
    if camera_cfg["enabled"] and camera_cfg["capture_after"]:
        capture_image("after_deck.jpg")
        capture_image("after_wellplate.jpg")

    finish_tip()
    dbg_comment("protocol completed")
'''

# ─── Forbidden / Required patterns for generated protocol safety check ─

_FORBIDDEN_PATTERNS: List[str] = [
    "import yaml",
    "from yaml",
    "pydantic",
    "from pydantic",
    "import pandas",
    "import numpy",
    "from pil",
    "import pil",
    "from PIL",
    "import PIL",
    "import argparse",
    "from argparse",
]

_REQUIRED_PATTERNS: List[str] = [
    "CONFIG =",
    "def run(",
    "protocol.is_simulating()",
]

# Conservative OT-2 motor limit margins for host-side bounds warning
_X_WARN_LIMIT_MM = 380.0
_Y_WARN_LIMIT_MM = 320.0


# ─── Robot Script Builder ─────────────────────────────────────────────

def _build_robot_script(config_dict: Dict[str, Any]) -> str:
    """
    Assemble the minimal robot-only protocol script with CONFIG embedded
    as a plain Python literal.  Never copies the host file.
    """
    config_repr = pprint.pformat(config_dict, indent=2)
    return _ROBOT_SCRIPT_PRE_CONFIG + config_repr + _ROBOT_SCRIPT_POST_CONFIG


def _check_generated_script(script_text: str, script_path: Path) -> None:
    """
    Scan the generated robot protocol for forbidden imports and required
    patterns. Raises RuntimeError immediately if any check fails.
    Case-insensitive scan for forbidden strings.
    """
    script_lower = script_text.lower()
    errors: List[str] = []

    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.lower() in script_lower:
            errors.append(f"Forbidden import/pattern found: '{pattern}'")

    for pattern in _REQUIRED_PATTERNS:
        if pattern not in script_text:
            errors.append(f"Required pattern missing: '{pattern}'")

    if errors:
        raise RuntimeError(
            f"Generated robot protocol FAILED safety check: {script_path}\n"
            + "\n".join(f"  ✗ {e}" for e in errors)
        )

    logger.info(
        f"Generated protocol passed safety check ({len(_FORBIDDEN_PATTERNS)} forbidden, "
        f"{len(_REQUIRED_PATTERNS)} required patterns verified): {script_path}"
    )


# ─── Host-side Print-Position Bounds Warning ──────────────────────────

def validate_print_positions_bounds(config_dict: Dict[str, Any]) -> None:
    """
    Warn if any print position offset is close to OT-2 motor limits.
    The robot deck A1 corner is not at (0,0) so this is a rough guard only;
    Pydantic's coordinate simulation check (when Opentrons is installed) is
    more accurate.
    """
    print_positions = config_dict.get("printing", {}).get("print_positions", [])
    printing_cfg = config_dict.get("printing", {})
    origin = printing_cfg.get("print_origin")
    spacing = printing_cfg.get("print_spacing")
    for pos in print_positions:
        x_index = pos.get("x_index")
        y_index = pos.get("y_index")
        if x_index is not None and y_index is not None and origin and spacing:
            x_mm = float(origin.get("x_mm", 5.0)) + int(x_index) * float(spacing.get("x_mm", 20.0))
            y_mm = float(origin.get("y_mm", 5.0)) + int(y_index) * float(spacing.get("y_mm", 10.0))
        else:
            x_mm = float(pos.get("x_mm", 0.0))
            y_mm = float(pos.get("y_mm", 0.0))
        if x_mm > _X_WARN_LIMIT_MM:
            logger.warning(
                f"Print position '{pos.get('label')}' x_mm={x_mm} exceeds conservative "
                f"limit {_X_WARN_LIMIT_MM}. Robot may hit X motor boundary (418.0 mm)."
            )
        if y_mm > _Y_WARN_LIMIT_MM:
            logger.warning(
                f"Print position '{pos.get('label')}' y_mm={y_mm} exceeds conservative "
                f"limit {_Y_WARN_LIMIT_MM}. Robot may hit Y motor boundary (353.0 mm)."
            )


# ─── Host-Side Utilities ──────────────────────────────────────────────

def generate_plate_map(config: Dict[str, Any]) -> Tuple[Dict[str, str], str]:
    """Generate visual ASCII plate grid and role dictionary."""
    plate_map: Dict[str, str] = {}
    labware = config.get("plate", {}).get("labware", "corning_96_wellplate_360ul_flat")
    max_row_char, max_col = get_well_bounds_for_labware(labware)
    
    rows = [chr(r) for r in range(ord("A"), ord(max_row_char) + 1)]
    cols = list(range(1, max_col + 1))
    
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
    """Run opentrons.simulate on the *generated* script to verify syntax/safety."""
    cmd = [
        sys.executable,
        "-c",
        (
            "import numpy as np; "
            "np.trapz = getattr(np, 'trapezoid', np.trapz if hasattr(np, 'trapz') else None); "
            "from opentrons.simulate import main; main()"
        ),
        run_path.name,
    ]
    logger.info(f"Simulating generated protocol: {run_path.name}")
    result = subprocess.run(
        cmd, cwd=str(run_path.parent), capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        logger.error(f"Simulation FAILED:\n{result.stderr}")
        raise ValueError(f"Simulation failed with returncode {result.returncode}")
    logger.info("Simulation PASSED successfully.")
    return result.stdout


def build_flat_step_log(config_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the ordered step log for run manifest tracking."""
    steps: List[Dict[str, Any]] = []
    camera_cfg = config_dict.get("camera", {})
    demo_mode = config_dict.get("demo_mode")
    dilution_cfg = config_dict.get("dilution", {})
    printing_cfg = config_dict.get("printing", {})
    is_calibration = printing_cfg.get("calibration_only", False)
    step_idx = 1

    if demo_mode == "dry_run":
        steps.append({
            "step_index": step_idx, "phase": "dry_run", "action": "dry_run_check",
            "source_well": "", "destination_well": "", "volume_ul": 0.0,
            "print_label": "", "x_mm": 0.0, "y_mm": 0.0,
            "image_expected": False, "image_name": "", "status": "pending",
        })
        return steps

    if camera_cfg.get("enabled") and camera_cfg.get("capture_before"):
        for fname in ("before_deck.jpg", "before_wellplate.jpg"):
            steps.append({
                "step_index": step_idx, "phase": "before",
                "action": f"capture_{fname.replace('.jpg', '')}",
                "source_well": "", "destination_well": "", "volume_ul": 0.0,
                "print_label": "", "x_mm": 0.0, "y_mm": 0.0,
                "image_expected": True, "image_name": fname, "status": "pending",
            })
            step_idx += 1

    if demo_mode == "dilution_print" and dilution_cfg.get("enabled") and not is_calibration:
        dil_steps = dilution_cfg.get("steps", [])
        strategy_mode = config_dict.get("tip_strategy", {}).get("mode", "new_tip_each_transfer")
        if strategy_mode == "reuse_low_to_high":
            def _ratio(s: Dict[str, Any]) -> float:
                tot = s["water_volume_ul"] + s["stock_volume_ul"]
                return s["stock_volume_ul"] / tot if tot > 0.0 else 0.0
            dil_steps = sorted(dil_steps, key=_ratio)

        for step in [s for s in dil_steps if s["water_volume_ul"] > 0]:
            steps.append({
                "step_index": step_idx, "phase": "dilution", "action": "pipette_water",
                "source_well": step["water_source_well"],
                "destination_well": step["destination_well"],
                "volume_ul": step["water_volume_ul"],
                "print_label": "", "x_mm": 0.0, "y_mm": 0.0,
                "image_expected": False, "image_name": "", "status": "pending",
            })
            step_idx += 1

        for i, step in enumerate(dil_steps):
            if step["stock_volume_ul"] > 0:
                steps.append({
                    "step_index": step_idx, "phase": "dilution", "action": "pipette_stock",
                    "source_well": step["food_coloring_source_well"],
                    "destination_well": step["destination_well"],
                    "volume_ul": step["stock_volume_ul"],
                    "print_label": "", "x_mm": 0.0, "y_mm": 0.0,
                    "image_expected": False, "image_name": "", "status": "pending",
                })
                step_idx += 1

            mix_vol = step.get("mix_volume_ul", 0.0)
            mix_reps = step.get("mix_repetitions", 0)
            if mix_reps > 0 and mix_vol > 0.0:
                steps.append({
                    "step_index": step_idx, "phase": "dilution",
                    "action": f"mix_well_{mix_reps}x",
                    "source_well": "", "destination_well": step["destination_well"],
                    "volume_ul": mix_vol,
                    "print_label": "", "x_mm": 0.0, "y_mm": 0.0,
                    "image_expected": False, "image_name": "", "status": "pending",
                })
                step_idx += 1

            if camera_cfg.get("enabled") and camera_cfg.get("capture_after_each_dilution_step"):
                dest = step["destination_well"]
                steps.append({
                    "step_index": step_idx, "phase": "dilution",
                    "action": "capture_dilution_step",
                    "source_well": "", "destination_well": dest, "volume_ul": 0.0,
                    "print_label": "", "x_mm": 0.0, "y_mm": 0.0,
                    "image_expected": True,
                    "image_name": f"wellplate_step_{i + 1:03d}_well_{dest}.jpg",
                    "status": "pending",
                })
                step_idx += 1

    print_positions = printing_cfg.get("print_positions", [])
    droplet_vol = printing_cfg.get("droplet_volume_ul", 0.0)
    origin = printing_cfg.get("print_origin")
    spacing = printing_cfg.get("print_spacing")
    for i, pos in enumerate(print_positions):
        x_index = pos.get("x_index")
        y_index = pos.get("y_index")
        if x_index is not None and y_index is not None and origin and spacing:
            x_mm = float(origin.get("x_mm", 5.0)) + int(x_index) * float(spacing.get("x_mm", 20.0))
            y_mm = float(origin.get("y_mm", 5.0)) + int(y_index) * float(spacing.get("y_mm", 10.0))
        else:
            x_mm = float(pos.get("x_mm", 0.0))
            y_mm = float(pos.get("y_mm", 0.0))

        if is_calibration:
            steps.append({
                "step_index": step_idx, "phase": "printing",
                "action": "calibrate_coordinate",
                "source_well": pos["source_well"], "destination_well": "",
                "volume_ul": 0.0, "print_label": pos["label"],
                "x_mm": x_mm, "y_mm": y_mm,
                "image_expected": False, "image_name": "", "status": "pending",
            })
            step_idx += 1
        else:
            steps.append({
                "step_index": step_idx, "phase": "printing",
                "action": "aspirate_print_source",
                "source_well": pos["source_well"], "destination_well": "",
                "volume_ul": droplet_vol,
                "print_label": "", "x_mm": 0.0, "y_mm": 0.0,
                "image_expected": False, "image_name": "", "status": "pending",
            })
            step_idx += 1
            steps.append({
                "step_index": step_idx, "phase": "printing", "action": "print_droplet",
                "source_well": "", "destination_well": "",
                "volume_ul": droplet_vol, "print_label": pos["label"],
                "x_mm": x_mm, "y_mm": y_mm,
                "image_expected": False, "image_name": "", "status": "pending",
            })
            step_idx += 1

        if camera_cfg.get("enabled") and camera_cfg.get("capture_after_each_print_step"):
            src = pos["source_well"]
            steps.append({
                "step_index": step_idx, "phase": "printing",
                "action": "capture_print_step",
                "source_well": src, "destination_well": "",
                "volume_ul": 0.0, "print_label": pos["label"],
                "x_mm": x_mm, "y_mm": y_mm,
                "image_expected": True,
                "image_name": f"paper_print_{i + 1:03d}_well_{src}.jpg",
                "status": "pending",
                })
            step_idx += 1

    if camera_cfg.get("enabled") and camera_cfg.get("capture_after"):
        for fname in ("after_deck.jpg", "after_wellplate.jpg"):
            steps.append({
                "step_index": step_idx, "phase": "after",
                "action": f"capture_{fname.replace('.jpg', '')}",
                "source_well": "", "destination_well": "", "volume_ul": 0.0,
                "print_label": "", "x_mm": 0.0, "y_mm": 0.0,
                "image_expected": True, "image_name": fname, "status": "pending",
            })
            step_idx += 1

    return steps


def create_mock_images(local_dir: Path, expected_images: List[Dict[str, Any]]) -> None:
    """Generate dummy colorful images to simulate camera capture (host-only)."""
    try:
        from PIL import Image, ImageDraw  # PIL is host-only, never in robot script
    except ImportError:
        logger.warning("Pillow not installed; skipping mock image creation.")
        return

    logger.info("Generating mock images for local/mock run.")
    for step in expected_images:
        phase = step["phase"]
        filename = step["filename"]
        label = step.get("print_label") or step.get("filename", "")
        if phase in ("before", "after"):
            color = (70, 130, 180)
        elif phase == "dilution":
            color = (46, 139, 87)
        else:
            color = (220, 20, 60)

        img = Image.new("RGB", (640, 480), color=color)
        draw = ImageDraw.Draw(img)
        text = (
            f"OT-2 printing_demo mock capture\n"
            f"File: {filename}\nLabel: {label}\n"
            f"Timestamp: {datetime.utcnow().isoformat()}Z"
        )
        draw.text((20, 20), text, fill=(255, 255, 255))

        if phase in ("before", "after"):
            dest_dir = local_dir / "images" / "before_after"
        elif phase == "dilution":
            dest_dir = local_dir / "images" / "plate"
        else:
            dest_dir = local_dir / "images" / "paper"

        dest_dir.mkdir(parents=True, exist_ok=True)
        img.save(dest_dir / filename)
        logger.debug(f"Saved mock image: {dest_dir / filename}")


# ─── Host-Side Main Pipeline ──────────────────────────────────────────

def host_run(args: Any) -> None:
    """The host-side execution pipeline: validate → generate → simulate → run."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    # ── 1. Load and validate YAML config (host-only) ──────────────────
    import yaml  # host-only; never reaches the generated robot protocol

    logger.info(f"Loading YAML config: {args.config}")
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
        logger.warning("Pydantic not available; using raw YAML dict (no schema checks).")
        config_dict = config_data

    # ── 1b. Host-side bounds warning ──────────────────────────────────
    validate_print_positions_bounds(config_dict)

    # ── 2. Generate robot protocol from explicit template ─────────────
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    robot_script_text = _build_robot_script(config_dict)

    generated_dir = Path(__file__).resolve().parent / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    run_script_path = generated_dir / f"printing_demo_run_{timestamp}.py"
    latest_script_path = generated_dir / "printing_demo_latest.py"

    for dest in (run_script_path, latest_script_path):
        with open(dest, "w", encoding="utf-8") as f:
            f.write(robot_script_text)
    logger.info(f"Robot protocol written to: {run_script_path}")
    logger.info(f"Robot protocol updated latest: {latest_script_path}")

    # ── 3. Safety check: scan generated file for forbidden imports ────
    _check_generated_script(robot_script_text, run_script_path)

    # ── 4. Simulate generated protocol locally ────────────────────────
    sim_stdout = run_local_simulation(run_script_path)

    # ── 5. Folder allocation ──────────────────────────────────────────
    run_name = f"{config_dict['demo_mode']}_{timestamp}"
    local_root = args.output_dir or config_dict.get("output", {}).get("local_run_root", "runs")
    local_run_dir = Path(local_root) / run_name
    local_run_dir.mkdir(parents=True, exist_ok=True)

    for sub in (
        "metadata", "images", "images/before_after",
        "images/plate", "images/paper", "errors", "protocols", "logs",
    ):
        (local_run_dir / sub).mkdir(parents=True, exist_ok=True)

    shutil.copy2(
        run_script_path,
        local_run_dir / "protocols" / f"printing_demo_run_{timestamp}.py",
    )

    # ── 6. Build step log and expected images ─────────────────────────
    flat_step_log = build_flat_step_log(config_dict)
    camera_cfg = config_dict["camera"]
    expected_images: List[Dict[str, Any]] = []
    if camera_cfg["enabled"]:
        for step in flat_step_log:
            if step["image_expected"]:
                phase = step["phase"]
                fn = step["image_name"]
                if phase in ("before", "after"):
                    ep = f"images/before_after/{fn}"
                elif phase == "dilution":
                    ep = f"images/plate/{fn}"
                else:
                    ep = f"images/paper/{fn}"
                expected_images.append({
                    "phase": phase, "filename": fn,
                    "step_index": step["step_index"],
                    "source_well": step["source_well"],
                    "destination_well": step["destination_well"],
                    "print_label": step["print_label"],
                    "expected_path": ep,
                })

    image_manifest_data: List[Dict[str, Any]] = []

    # ── 7. Execute: mock or real robot ───────────────────────────────
    try:
        from src.core.config import Config as RobotConfig  # host-only project import
        robot_ip = args.robot_ip or config_dict.get("robot_ip") or RobotConfig.ROBOT_IP
    except ImportError:
        robot_ip = args.robot_ip or config_dict.get("robot_ip")

    is_mock = args.mock or (not robot_ip) or (robot_ip in ("127.0.0.1", "localhost"))
    run_ok = True
    start_time = datetime.utcnow().isoformat() + "Z"

    if is_mock:
        logger.info("Executing MOCK run pipeline.")
        create_mock_images(local_run_dir, expected_images)
        for step in flat_step_log:
            step["status"] = "ok"
            if step["image_expected"]:
                fn = step["image_name"]
                phase = step["phase"]
                if phase in ("before", "after"):
                    ep = f"images/before_after/{fn}"
                elif phase == "dilution":
                    ep = f"images/plate/{fn}"
                else:
                    ep = f"images/paper/{fn}"
                image_manifest_data.append({
                    "image_name": fn, "phase": phase,
                    "step_index": step["step_index"],
                    "source_well": step["source_well"],
                    "destination_well": step["destination_well"],
                    "print_label": step["print_label"],
                    "expected_path": ep,
                    "status": "valid", "validation_result": "passed",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
    else:
        # ── Real robot run ─────────────────────────────────────────
        logger.info(f"Connecting to physical OT-2 robot: {robot_ip}")

        try:
            from src.core.config import Config as RobotConfig
            ssh_key = args.ssh_key or os.environ.get("ROBOT_SSH_KEY_PATH") or RobotConfig.ROBOT_SSH_KEY_PATH
            ssh_user = RobotConfig.ROBOT_SSH_USER
        except ImportError:
            ssh_key = args.ssh_key or os.environ.get("ROBOT_SSH_KEY_PATH")
            ssh_user = "root"

        if not ssh_key:
            default_key = r"C:\Users\iyersn\.ssh\id_rsa_opentrons"
            if os.path.exists(default_key):
                ssh_key = default_key

        if not ssh_key:
            logger.error("ROBOT_SSH_KEY_PATH is missing. Check .env configuration or pass via --ssh-key.")
            sys.exit(1)

        ssh_opts = [
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            "-o", "StrictHostKeyChecking=no", "-i", ssh_key,
        ]

        remote_dest_dir = "/var/lib/opentrons/user_storage/ot2_runs"
        remote_script_path = f"{remote_dest_dir}/printing_demo_run.py"

        logger.info("Deploying generated robot protocol...")
        deploy_cmd = (
            ["scp", "-O"] + ssh_opts
            + [str(run_script_path), f"{ssh_user}@{robot_ip}:{remote_script_path}"]
        )
        dep_res = subprocess.run(deploy_cmd, capture_output=True, text=True, check=False)
        if dep_res.returncode != 0:
            logger.error(f"Protocol deployment FAILED: {dep_res.stderr}")
            sys.exit(1)
        logger.info("Protocol deployed successfully.")

        logger.info("Triggering protocol run on OT-2 robot...")
        exec_cmd = (
            ["ssh"] + ssh_opts
            + [f"{ssh_user}@{robot_ip}", f"opentrons_execute {remote_script_path}"]
        )
        run_res = subprocess.run(exec_cmd, capture_output=True, text=True, check=False)

        with open(local_run_dir / "logs" / "run.log", "w", encoding="utf-8") as lf:
            lf.write(f"=== REMOTE COMMAND ===\n{' '.join(exec_cmd)}\n\n")
            lf.write(f"=== RETURN CODE ===\n{run_res.returncode}\n\n")
            lf.write(f"=== STDOUT ===\n{run_res.stdout}\n\n")
            lf.write(f"=== STDERR ===\n{run_res.stderr}\n\n")

        if run_res.returncode != 0:
            logger.error("=" * 80)
            logger.error(" REMOTE PROTOCOL EXECUTION FAILED ")
            logger.error("=" * 80)
            logger.error(f"Exit Code  : {run_res.returncode}")
            logger.error(f"STDOUT:\n{run_res.stdout or '(none)'}")
            logger.error(f"STDERR:\n{run_res.stderr or '(none)'}")
            logger.error("=" * 80)
            run_ok = False
        else:
            logger.info("Remote execution completed successfully.")

        # ── SCP images back and reconcile ────────────────────────
        if camera_cfg["enabled"]:
            logger.info("Retrieving captured images from OT-2...")
            temp_transfer_dir = local_run_dir / "temp_raw"
            temp_transfer_dir.mkdir(exist_ok=True)

            remote_image_dir = camera_cfg.get("robot_image_dir", "/data/vision/printing_demo")
            scp_cmd = (
                ["scp", "-O"] + ssh_opts
                + ["-r", f"{ssh_user}@{robot_ip}:{remote_image_dir}/*", str(temp_transfer_dir)]
            )
            scp_res = subprocess.run(scp_cmd, capture_output=True, text=True, check=False)
            if scp_res.returncode != 0:
                logger.warning(f"Could not download remote images: {scp_res.stderr}")

            try:
                from PIL import Image as PILImage  # host-only
            except ImportError:
                PILImage = None  # type: ignore[assignment]

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
                elif temp_file.stat().st_size < 1000:
                    failure_reason = f"File too small ({temp_file.stat().st_size} bytes)"
                elif PILImage:
                    try:
                        with PILImage.open(temp_file) as img:
                            img.verify()
                        validation_passed = True
                    except Exception as e:
                        failure_reason = f"Corrupt image: {e}"
                else:
                    validation_passed = True  # fallback without PIL

                if validation_passed:
                    if phase in ("before", "after"):
                        dest_dir = local_run_dir / "images" / "before_after"
                    elif phase == "dilution":
                        dest_dir = local_run_dir / "images" / "plate"
                    else:
                        dest_dir = local_run_dir / "images" / "paper"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(temp_file), str(dest_dir / fn))

                    for s in flat_step_log:
                        if s["step_index"] == step_index and s["image_name"] == fn:
                            s["status"] = "ok"

                    image_manifest_data.append({
                        "image_name": fn, "phase": phase, "step_index": step_index,
                        "source_well": source_well, "destination_well": destination_well,
                        "print_label": print_label, "expected_path": expected_path,
                        "status": "valid", "validation_result": "passed",
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    })
                else:
                    logger.warning(f"Capture reconciliation failure for {fn}: {failure_reason}")
                    err_path = local_run_dir / "errors" / f"{Path(fn).stem}.json"
                    with open(err_path, "w", encoding="utf-8") as ef:
                        json.dump({
                            "step_index": step_index, "action": phase,
                            "source_well": source_well,
                            "destination_well": destination_well,
                            "attempted_image_name": fn,
                            "failure_reason": failure_reason,
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                        }, ef, indent=2)

                    if temp_file.exists():
                        shutil.move(
                            str(temp_file),
                            str(local_run_dir / "errors" / f"{Path(fn).stem}.partial.jpg"),
                        )

                    for s in flat_step_log:
                        if s["step_index"] == step_index and s["image_name"] == fn:
                            s["status"] = "failed"

                    image_manifest_data.append({
                        "image_name": fn, "phase": phase, "step_index": step_index,
                        "source_well": source_well, "destination_well": destination_well,
                        "print_label": print_label, "expected_path": expected_path,
                        "status": "failed", "validation_result": failure_reason,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    })

            logger.info("Cleaning up remote images on robot...")
            subprocess.run(
                ["ssh"] + ssh_opts + [f"{ssh_user}@{robot_ip}", f"rm -rf {remote_image_dir}/*"],
                check=False,
            )
            if temp_transfer_dir.exists():
                shutil.rmtree(temp_transfer_dir)

        for s in flat_step_log:
            if not s["image_expected"]:
                s["status"] = "ok" if run_ok else "failed"

    # ── 8. Write manifests and metadata ──────────────────────────────
    end_time = datetime.utcnow().isoformat() + "Z"
    plate_map_dict, ascii_map = generate_plate_map(config_dict)

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
        "overall_status": "SUCCESS" if run_ok else "FAILED",
    }
    metadata_path = local_run_dir / "metadata" / "run_metadata.json"
    with open(metadata_path, "w", encoding="utf-8") as mf:
        json.dump(run_metadata, mf, indent=2)
    logger.info(f"Saved run metadata to: {metadata_path}")

    printing_cfg = config_dict.get("printing", {})
    origin = printing_cfg.get("print_origin")
    spacing = printing_cfg.get("print_spacing")
    manifest_data = []
    for pos in config_dict["printing"]["print_positions"]:
        x_index = pos.get("x_index")
        y_index = pos.get("y_index")
        if x_index is not None and y_index is not None and origin and spacing:
            x_mm = float(origin.get("x_mm", 5.0)) + int(x_index) * float(spacing.get("x_mm", 20.0))
            y_mm = float(origin.get("y_mm", 5.0)) + int(y_index) * float(spacing.get("y_mm", 10.0))
        else:
            x_mm = float(pos.get("x_mm", 0.0))
            y_mm = float(pos.get("y_mm", 0.0))
        manifest_data.append({
            "source_well": pos["source_well"],
            "target_label": pos["label"],
            "x_mm": x_mm,
            "y_mm": y_mm,
            "volume_ul": config_dict["printing"]["droplet_volume_ul"],
            "concentration": (
                "Water" if config_dict["demo_mode"] == "water_print" else "Diluted"
            ),
        })

    pm_json = local_run_dir / "metadata" / "printer_manifest.json"
    with open(pm_json, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    pm_csv = local_run_dir / "metadata" / "printer_manifest.csv"
    with open(pm_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["source_well", "target_label", "x_mm", "y_mm", "volume_ul", "concentration"]
        )
        writer.writeheader()
        writer.writerows(manifest_data)

    image_manifest_csv = local_run_dir / "metadata" / "image_manifest.csv"
    with open(image_manifest_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "image_name", "phase", "step_index", "source_well", "destination_well",
                "print_label", "expected_path", "status", "validation_result", "timestamp",
            ],
        )
        writer.writeheader()
        writer.writerows(image_manifest_data)

    step_log_csv = local_run_dir / "metadata" / "step_log.csv"
    with open(step_log_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "step_index", "phase", "action", "source_well", "destination_well",
                "volume_ul", "print_label", "x_mm", "y_mm", "image_expected",
                "image_name", "status",
            ],
        )
        writer.writeheader()
        writer.writerows(flat_step_log)

    logger.info(f"Saved all manifests to: {local_run_dir / 'metadata'}")
    logger.info(f"Run complete. Overall status: {'SUCCESS' if run_ok else 'FAILED'}")


# ─── CLI Entry Point ──────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Build and orchestrate the OT-2 Printing Demo robot protocol."
    )
    parser.add_argument(
        "--config",
        type=str,
        default="configs/workflows/defaults/printing_demo.yaml",
        help="Path to YAML config file.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run local simulation + mock camera captures only (no robot connection).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Override output runs directory.",
    )
    parser.add_argument(
        "--robot-ip",
        type=str,
        default=None,
        help="IP address of the physical OT-2 robot.",
    )
    parser.add_argument(
        "--ssh-key",
        type=str,
        default=None,
        help="Path to the SSH key for connection.",
    )
    host_args = parser.parse_args()
    host_run(host_args)
