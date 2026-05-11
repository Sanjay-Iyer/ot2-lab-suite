import yaml
import copy
from pathlib import Path
from typing import Dict, Any, Optional
from src.utils.paths import (
    DEFAULT_WORKFLOW_CONFIG_DIR, 
    USER_WORKFLOW_CONFIG_DIR, 
    validate_path_exists
)
from src.core.validation.workflow_validator import validate_workflow_against_constraints

# --- Workflow Config Management ---

def load_default_config(workflow_type: str) -> dict:
    """Loads the default YAML config for a given workflow."""
    path = DEFAULT_WORKFLOW_CONFIG_DIR / f"{workflow_type}.yaml"
    validate_path_exists(path, f"Default config for {workflow_type}")
    
    with open(path, "r") as f:
        return yaml.safe_load(f)

def merge_user_updates(default_config: dict, user_updates: dict) -> dict:
    """Recursively merges user updates into the default config."""
    def deep_merge(target, source):
        for k, v in source.items():
            if k in target and isinstance(target[k], dict) and isinstance(v, dict):
                deep_merge(target[k], v)
            else:
                target[k] = v
        return target
    
    merged = copy.deepcopy(default_config)
    return deep_merge(merged, user_updates)

def validate_workflow_config(config: dict) -> None:
    """Validates the config against constraints using the validation service."""
    result = validate_workflow_against_constraints(config)
    
    if not result.valid:
        error_msgs = [f"[{e.field}] {e.message} (Fix: {e.suggested_fix or 'N/A'})" for e in result.errors]
        raise ValueError("VALIDATION ERROR:\n" + "\n".join(error_msgs))
    
    # Optional: Log warnings
    if result.warnings:
        print("\n".join([f"WARNING: {w.message}" for w in result.warnings]))

def save_run_config(config: dict, workflow_type: str, run_id: Optional[str] = None) -> Path:
    """Saves the final run config to configs/workflows/user/."""
    import uuid
    from datetime import datetime
    
    if not run_id:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"{workflow_type}_{timestamp}_{uuid.uuid4().hex[:4]}"
    
    USER_WORKFLOW_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = USER_WORKFLOW_CONFIG_DIR / f"{run_id}.yaml"
    
    with open(path, "w") as f:
        yaml.dump(config, f, sort_keys=False)
    
    return path

def summarize_config(config: dict) -> str:
    """Provides a concise human-readable summary of the config."""
    workflow = config.get("workflow_type") or config.get("workflow")
    
    if workflow == "dilution":
        d = config.get("dilution", {})
        l = config.get("labware", {})
        p = config.get("pipette", {})
        
        summary = (
            f"--- Dilution Workflow Summary ---\n"
            f"- Steps: {d.get('steps')} stages ({d.get('factors')})\n"
            f"- Final Volume: {d.get('final_volume_ul')} uL\n"
            f"- Labware: Source={l.get('source', {}).get('name')}, Dilution={l.get('dilution', {}).get('name')}, Destination={l.get('destination', {}).get('name')}\n"
            f"- Pipette: {p.get('name')} on {p.get('mount')}\n"
        )
        return summary
    
    elif workflow == "printing":
        summary = (
            f"--- Printing Workflow Summary ---\n"
            f"- Target Positions: {config.get('total_print_positions')}\n"
            f"- Reagent: {config.get('reagent_source')}\n"
            f"- Target Volume: {config.get('target_final_volume_ul')} uL\n"
        )
        return summary
    
    return f"Summary for {workflow} not implemented."
