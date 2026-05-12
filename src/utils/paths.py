from pathlib import Path
import os

# --- Dynamic Root Discovery ---
# This file is located at src/utils/paths.py. 
# PROJECT_ROOT is 2 levels up from this file.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# --- Directory Registry ---
CONFIG_DIR = PROJECT_ROOT / "configs"

# Layer 1: Workflow Configuration (Experiment Parameters)
WORKFLOW_CONFIG_DIR = CONFIG_DIR / "workflows"
DEFAULT_WORKFLOW_CONFIG_DIR = WORKFLOW_CONFIG_DIR / "defaults"
USER_WORKFLOW_CONFIG_DIR = WORKFLOW_CONFIG_DIR / "user"

# Layer 2: Hardware/Labware Constraints (Safety & Reality)
CONSTRAINT_CONFIG_DIR = CONFIG_DIR / "constraints"

LABWARE_CONSTRAINTS_PATH = CONSTRAINT_CONFIG_DIR / "labware_constraints.yaml"
PIPETTE_CONSTRAINTS_PATH = CONSTRAINT_CONFIG_DIR / "pipette_constraints.yaml"
DECK_CONSTRAINTS_PATH = CONSTRAINT_CONFIG_DIR / "deck_constraints.yaml"
WORKFLOW_CONSTRAINTS_PATH = CONSTRAINT_CONFIG_DIR / "workflow_constraints.yaml"

# Data & Execution
ROBOT_DATA_DIR = PROJECT_ROOT / "robot_data"
ROBOT_DATA_MIRROR = ROBOT_DATA_DIR / "data"
LOG_DIR = ROBOT_DATA_MIRROR / "logs"
AGENT_LOG_DIR = LOG_DIR / "agents"
OUTPUT_DIR = ROBOT_DATA_MIRROR / "outputs"

PROTOCOL_DIR = PROJECT_ROOT / "src" / "protocols"
GENERATED_PROTOCOL_DIR = PROTOCOL_DIR / "generated"

# Staging & Tracking
DEPLOY_BASE_DIR = ROBOT_DATA_DIR / "deploy"
SIMULATION_RECORDS_PATH = ROBOT_DATA_MIRROR / "simulations.json"

def ensure_project_dirs():
    """Ensures all critical project directories exist."""
    dirs = [
        CONFIG_DIR,
        WORKFLOW_CONFIG_DIR,
        DEFAULT_WORKFLOW_CONFIG_DIR,
        USER_WORKFLOW_CONFIG_DIR,
        CONSTRAINT_CONFIG_DIR,
        ROBOT_DATA_DIR,
        ROBOT_DATA_MIRROR,
        LOG_DIR,
        AGENT_LOG_DIR,
        OUTPUT_DIR,
        PROTOCOL_DIR,
        GENERATED_PROTOCOL_DIR,
        DEPLOY_BASE_DIR
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

def validate_path_exists(path: Path, description: str = "File"):
    """Validates a path exists, raising a user-friendly error if not."""
    if not path.exists():
        msg = (
            f"\n[ERROR] {description} was not found at:\n"
            f"  {path}\n\n"
            f"Please ensure the file exists or check your PROJECT_ROOT configuration.\n"
            f"Current PROJECT_ROOT: {PROJECT_ROOT}\n"
        )
        raise FileNotFoundError(msg)

# Initialize on import
ensure_project_dirs()
