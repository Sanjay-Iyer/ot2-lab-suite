import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from src.utils.paths import (
    LABWARE_CONSTRAINTS_PATH,
    PIPETTE_CONSTRAINTS_PATH,
    DECK_CONSTRAINTS_PATH,
    WORKFLOW_CONSTRAINTS_PATH,
    validate_path_exists
)

def load_constraints() -> Dict[str, Any]:
    """Loads all hardware and safety constraints from YAML."""
    constraints = {}
    
    paths = {
        "labware": LABWARE_CONSTRAINTS_PATH,
        "pipettes": PIPETTE_CONSTRAINTS_PATH,
        "deck": DECK_CONSTRAINTS_PATH,
        "workflows": WORKFLOW_CONSTRAINTS_PATH
    }
    
    for key, path in paths.items():
        validate_path_exists(path, f"{key.capitalize()} constraints")
        with open(path, "r") as f:
            data = yaml.safe_load(f)
            # Unwrap the root key if it matches the category
            constraints[key] = data.get(key, data)
            
    return constraints

# Singleton-style access for validators
_CONSTRAINTS = None

def get_constraints() -> Dict[str, Any]:
    global _CONSTRAINTS
    if _CONSTRAINTS is None:
        _CONSTRAINTS = load_constraints()
    return _CONSTRAINTS
