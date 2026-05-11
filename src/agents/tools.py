import subprocess
import os
import sys
import json
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from langchain.tools import tool
from src.utils.paths import USER_WORKFLOW_CONFIG_DIR, GENERATED_PROTOCOL_DIR, ensure_project_dirs
from src.core.workflows.registry import WORKFLOWS
from src.core.config_loader import (
    load_default_config, 
    merge_user_updates, 
    validate_workflow_config, 
    save_run_config,
    summarize_config
)
from src.core.validation.workflow_validator import validate_workflow_against_constraints

# --- Global Working State ---
_WORKING_CONFIG = None
_CURRENT_WORKFLOW = None

@tool
def list_available_workflows() -> str:
    """
    Returns a list of all registered lab workflows and their descriptions.
    Use this to discover what capabilities the system has.
    """
    lines = ["Available Workflows:"]
    for name, entry in WORKFLOWS.items():
        lines.append(f"- {name}: {entry.description}")
    return "\n".join(lines)

@tool
def load_workflow_defaults(workflow_type: str) -> str:
    """
    Loads the default configuration for a given workflow type (e.g., 'dilution', 'printing').
    Returns the default parameters and a human-readable summary.
    Always start here when a user asks for a workflow.
    """
    global _WORKING_CONFIG, _CURRENT_WORKFLOW
    try:
        config = load_default_config(workflow_type)
        _WORKING_CONFIG = config
        _CURRENT_WORKFLOW = workflow_type
        
        summary = summarize_config(config)
        return (
            f"Defaults for '{workflow_type}' loaded successfully.\n\n"
            f"{summary}\n"
            "Would you like to use these defaults, or should we update specific parameters?"
        )
    except Exception as e:
        return f"Error loading defaults for '{workflow_type}': {str(e)}"

@tool
def update_workflow_config(updates: dict) -> str:
    """
    Applies user-provided changes to the currently loaded workflow configuration.
    Input should be a dictionary of fields to update. Nested fields are supported.
    Example: {"dilution": {"final_volume_ul": 1000}, "labware": {"source": {"name": "corning_96_wellplate_360ul_flat"}}}
    This tool does NOT perform full constraint validation. Call validate_current_workflow() after updates.
    """
    global _WORKING_CONFIG
    if _WORKING_CONFIG is None:
        return "Error: No workflow loaded. Call load_workflow_defaults first."
    
    try:
        updated_config = merge_user_updates(_WORKING_CONFIG, updates)
        _WORKING_CONFIG = updated_config
        
        summary = summarize_config(_WORKING_CONFIG)
        return (
            f"Configuration updated locally.\n\n"
            f"{summary}\n"
            "Updates applied. Please run validate_current_workflow() to check for physical safety and constraints."
        )
    except Exception as e:
        return f"Update Error: {str(e)}"

@tool
def validate_current_workflow() -> str:
    """
    Runs Pydantic validation and full constraint validation (hardware limits, safety).
    Returns a detailed report of errors and warnings.
    Always call this before confirm_and_run_workflow().
    """
    global _WORKING_CONFIG
    if _WORKING_CONFIG is None:
        return "Error: No workflow loaded."
    
    try:
        result = validate_workflow_against_constraints(_WORKING_CONFIG)
        
        output = []
        if result.valid:
            output.append("VALIDATION PASSED: Workflow is physically safe according to current constraints.")
        else:
            output.append("VALIDATION FAILED: Physical constraints violated.")
            
        if result.errors:
            output.append("\nERRORS:")
            for e in result.errors:
                msg = f"- [{e.field}] {e.message}"
                if e.suggested_fix:
                    msg += f" (Suggested Fix: {e.suggested_fix})"
                output.append(msg)
                
        if result.warnings:
            output.append("\nWARNINGS:")
            for w in result.warnings:
                output.append(f"- [{w.field}] {w.message}")
                
        if result.valid:
            output.append("\nYou can now proceed to confirm_and_run_workflow().")
        else:
            output.append("\nPlease address the errors above before running.")
            
        return "\n".join(output)
    except Exception as e:
        return f"Internal Validation Error: {str(e)}"

@tool
def show_full_config() -> str:
    """Returns the full raw YAML representation of the current working configuration."""
    global _WORKING_CONFIG
    if _WORKING_CONFIG is None:
        return "Error: No workflow loaded."
    return yaml.dump(_WORKING_CONFIG, sort_keys=False)

@tool
def confirm_and_run_workflow() -> str:
    """
    Finalizes the workflow:
    1. Validates the final config (must have no errors).
    2. Saves the run config to configs/workflows/user/last_{workflow_type}_run.yaml.
    3. Generates the OT-2 protocol to src/protocols/generated/generated_{workflow_type}.py.
    4. Runs a local simulation.
    """
    global _WORKING_CONFIG, _CURRENT_WORKFLOW
    if _WORKING_CONFIG is None:
        return "Error: No workflow loaded."
    
    try:
        # 1. Final Validation Check
        validation_result = validate_workflow_against_constraints(_WORKING_CONFIG)
        if not validation_result.valid:
            return "Error: Cannot run workflow. Validation has errors. Call validate_current_workflow() to see details."
        
        # 2. Save Run Config
        run_id = f"last_{_CURRENT_WORKFLOW}_run"
        config_path = USER_WORKFLOW_CONFIG_DIR / f"{run_id}.yaml"
        USER_WORKFLOW_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            yaml.dump(_WORKING_CONFIG, f, sort_keys=False)
        
        # 3. Generate Protocol
        entry = WORKFLOWS[_CURRENT_WORKFLOW]
        config_obj = entry.schema(**_WORKING_CONFIG)
        protocol_content = entry.protocol_generator(config_obj)
        
        protocol_path = GENERATED_PROTOCOL_DIR / f"generated_{_CURRENT_WORKFLOW}.py"
        protocol_path.parent.mkdir(parents=True, exist_ok=True)
        with open(protocol_path, "w") as f:
            f.write(protocol_content)
            
        # 4. Run Simulation
        cmd = [sys.executable, "-m", "opentrons.simulate", str(protocol_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return f"SIMULATION FAILED:\n{result.stderr}"
        
        return (
            f"Local simulation passed. The protocol is generated, but physical execution still requires labware, volume, and robot setup verification.\n\n"
            f"- Config Saved: {config_path}\n"
            f"- Protocol Saved: {protocol_path}\n"
        )
    except Exception as e:
        return f"Error finalizing workflow: {str(e)}"