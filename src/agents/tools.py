import subprocess
import os
import sys
import json
import yaml
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from langchain.tools import tool

from src.utils.paths import (
    USER_WORKFLOW_CONFIG_DIR, 
    GENERATED_PROTOCOL_DIR, 
    SIMULATION_RECORDS_PATH,
    DEPLOY_BASE_DIR,
    ensure_project_dirs
)
from src.core.config import Config
from src.core.workflows.registry import WORKFLOWS
from src.core.config_loader import (
    load_default_config, 
    merge_user_updates, 
    validate_workflow_config, 
    save_run_config,
    summarize_config
)
from src.core.validation.workflow_validator import validate_workflow_against_constraints
from src.utils.hashing import hash_file

# --- Global Working State ---
_WORKING_CONFIG = None
_CURRENT_WORKFLOW = None

# --- Helper Functions ---
def _load_simulation_records() -> dict:
    if not SIMULATION_RECORDS_PATH.exists():
        return {}
    try:
        with open(SIMULATION_RECORDS_PATH, "r") as f:
            return json.load(f)
    except:
        return {}

def _save_simulation_record(protocol_path: Path, sha256: str, status: str, result: str):
    records = _load_simulation_records()
    records[sha256] = {
        "path": str(protocol_path),
        "timestamp": datetime.now().isoformat(),
        "status": status,
        "result": result
    }
    SIMULATION_RECORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SIMULATION_RECORDS_PATH, "w") as f:
        json.dump(records, f, indent=2)

# --- Tools ---

@tool
def list_available_workflows() -> str:
    """
    Returns a list of all registered lab workflows and their descriptions.
    """
    lines = ["Available Workflows:"]
    for name, entry in WORKFLOWS.items():
        lines.append(f"- {name}: {entry.description}")
    return "\n".join(lines)

@tool
def load_workflow_defaults(workflow_type: str) -> str:
    """
    Loads the default configuration for a given workflow type (e.g., 'dilution', 'printing').
    """
    global _WORKING_CONFIG, _CURRENT_WORKFLOW
    try:
        config = load_default_config(workflow_type)
        _WORKING_CONFIG = config
        _CURRENT_WORKFLOW = workflow_type
        
        summary = summarize_config(config)
        # Terminal-level debug for the user
        print(f"\n[DEBUG] Loaded YAML defaults for {workflow_type}:")
        print(f"        Pipette: {config.get('pipette', {}).get('name')} on {config.get('pipette', {}).get('mount')}")
        
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
    """
    global _WORKING_CONFIG
    if _WORKING_CONFIG is None:
        return "Error: No workflow loaded. Call load_workflow_defaults first."
    
    try:
        updated_config = merge_user_updates(_WORKING_CONFIG, updates)
        _WORKING_CONFIG = updated_config
        summary = summarize_config(_WORKING_CONFIG)
        return f"Configuration updated locally.\n\n{summary}"
    except Exception as e:
        return f"Update Error: {str(e)}"

@tool
def validate_current_workflow() -> str:
    """
    Runs Pydantic validation and full constraint validation (hardware limits, safety).
    Returns a detailed report of errors and warnings.
    """
    global _WORKING_CONFIG
    if _WORKING_CONFIG is None:
        return "Error: No workflow loaded. Call load_workflow_defaults first."
    
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
                output.append(f"- [{e.field}] {e.message}")
        return "\n".join(output)
    except Exception as e:
        return f"Internal Validation Error: {str(e)}"

@tool
def validate_config(config_path: str) -> str:
    """
    Validates a YAML workflow configuration file against schemas and hardware constraints.
    Returns a success message or a detailed list of errors.
    """
    try:
        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)
        
        result = validate_workflow_against_constraints(config_data)
        
        output = []
        if result.valid:
            output.append(f"VALIDATION PASSED for {config_path}")
        else:
            output.append(f"VALIDATION FAILED for {config_path}")
            
        if result.errors:
            for e in result.errors:
                output.append(f"- [{e.field}] {e.message}")
        return "\n".join(output)
    except Exception as e:
        return f"Validation error: {str(e)}"

@tool
def generate_protocol() -> str:
    """
    Generates the OT-2 protocol based on current working configuration.
    Returns the absolute path to the generated protocol file.
    """
    global _WORKING_CONFIG, _CURRENT_WORKFLOW
    if _WORKING_CONFIG is None:
        return "Error: No workflow loaded."
    
    try:
        # Safety: Re-read pipette config from YAML defaults to prevent LLM hallucination
        fresh_defaults = load_default_config(_CURRENT_WORKFLOW)
        if "pipette" in fresh_defaults and "pipette" in _WORKING_CONFIG:
            _WORKING_CONFIG["pipette"] = fresh_defaults["pipette"]
        
        entry = WORKFLOWS[_CURRENT_WORKFLOW]
        config_obj = entry.schema(**_WORKING_CONFIG)
        protocol_content = entry.protocol_generator(config_obj)
        
        # Log actual values used
        base = config_obj.to_base_config()
        pipette_info = base.get("deck", {}).get("pipettes", [{}])[0]
        print(f"[GENERATE] Pipette: {pipette_info.get('type')} on {pipette_info.get('mount')}")
        print(f"[GENERATE] API Level: {base.get('apiLevel')}")
        
        protocol_path = GENERATED_PROTOCOL_DIR / f"generated_{_CURRENT_WORKFLOW}.py"
        protocol_path.parent.mkdir(parents=True, exist_ok=True)
        with open(protocol_path, "w") as f:
            f.write(protocol_content)
        return str(protocol_path)
    except Exception as e:
        return f"Error generating protocol: {str(e)}"

@tool
def simulate_protocol(protocol_path: str) -> str:
    """
    Runs a local Opentrons simulation using 'conda run -n ai'.
    Saves a SHA256 record of the result for traceability.
    """
    p_path = Path(protocol_path).resolve()
    if not p_path.exists():
        return f"Error: Protocol file not found at {protocol_path}"
    
    try:
        sha256 = hash_file(p_path)
        # Use sys.executable to run inside the current environment reliably
        # Pass only the filename and set cwd to the parent directory to avoid [WinError 2]
        cmd = [sys.executable, "-m", "opentrons.simulate", p_path.name]
        result = subprocess.run(cmd, cwd=str(p_path.parent), capture_output=True, text=True)
        
        status = "PASS" if result.returncode == 0 else "FAIL"
        output = result.stdout if result.returncode == 0 else result.stderr
        
        _save_simulation_record(p_path, sha256, status, output)
        
        if result.returncode == 0:
            return (
                f"SIMULATION PASSED for hash {sha256[:8]}.\n"
                f"Record saved to simulations.json.\n\n"
                f"Output summary:\n{result.stdout[:500]}..."
            )
        else:
            return f"SIMULATION FAILED for hash {sha256[:8]}.\n\nError:\n{result.stderr}"
    except Exception as e:
        return f"Simulation error: {str(e)}"

@tool
def get_robot_hardware_status() -> str:
    """
    Queries the physical OT-2 robot via SSH to find exactly which pipettes are attached to which mounts.
    Use this to verify the hardware matches your configuration before generating a protocol.
    """
    robot_ip = Config.ROBOT_IP
    key_path = Config.ROBOT_SSH_KEY_PATH
    ssh_user = Config.ROBOT_SSH_USER
    
    if not key_path:
        return "Error: ROBOT_SSH_KEY_PATH is missing. Cannot check hardware."

    ssh_opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-i", key_path]
    
    try:
        # OT-2 stores attached instruments in this JSON file
        cmd = ["ssh"] + ssh_opts + [f"{ssh_user}@{robot_ip}", "cat /var/lib/opentrons/attached_instruments.json"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return f"Hardware check FAILED: {result.stderr}"
            
        data = json.loads(result.stdout)
        summary = ["Physical Robot Hardware:"]
        for mount in ["left", "right"]:
            info = data.get(mount, {})
            model = info.get("model", "None")
            summary.append(f"- {mount.upper()} Mount: {model}")
            
        return "\n".join(summary)
    except Exception as e:
        return f"Hardware check error: {str(e)}"

@tool
def check_robot_connection() -> str:
    """
    Verifies ROBOT_IP connectivity and SSH access (non-interactive BatchMode).
    Checks for 'opentrons_execute' availability on the instrument.
    """
    robot_ip = Config.ROBOT_IP
    if robot_ip in ["127.0.0.1", "localhost"]:
        return f"Error: ROBOT_IP is set to {robot_ip}. Physical robot IP required in .env."

    key_path = Config.ROBOT_SSH_KEY_PATH
    if not key_path:
        return "Error: ROBOT_SSH_KEY_PATH is missing from config/.env. A private key is required for OT-2 SSH access."

    ssh_user = Config.ROBOT_SSH_USER
    ssh_opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "-i", key_path]
    
    try:
        # 1. Test SSH + path existence
        cmd_ls = ["ssh"] + ssh_opts + [f"{ssh_user}@{robot_ip}", f"mkdir -p {Config.REMOTE_USER_STORAGE}/ot2_runs && ls -d {Config.REMOTE_USER_STORAGE}"]
        result_ls = subprocess.run(cmd_ls, capture_output=True, text=True)
        
        if result_ls.returncode != 0:
            return f"Connectivity FAILED: SSH unreachable at {robot_ip} (check keys/BatchMode). Error: {result_ls.stderr}"

        # 2. Check for opentrons_execute
        cmd_exe = ["ssh"] + ssh_opts + [f"{ssh_user}@{robot_ip}", "which opentrons_execute"]
        result_exe = subprocess.run(cmd_exe, capture_output=True, text=True)
        
        if result_exe.returncode != 0:
            return f"Connectivity WARNING: SSH connected, but 'opentrons_execute' missing on robot."

        return f"Connectivity PASSED: Instrument {robot_ip} is READY (SSH/BatchMode)."
    except Exception as e:
        return f"Connection check internal error: {str(e)}"

@tool
def deploy_protocol_to_robot(protocol_path: str, config_paths: Optional[List[str]] = None) -> str:
    """
    Stages and SCPs the protocol and configs to a unique run folder on the robot.
    Returns the remote run directory path.
    """
    local_protocol_path = Path(protocol_path)
    if not local_protocol_path.exists():
        return f"Error: Protocol not found: {protocol_path}"
    
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%HH%MM%SS')}"
    local_staging_dir = DEPLOY_BASE_DIR / run_id
    local_staging_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # 1. Stage files
        shutil.copy2(local_protocol_path, local_staging_dir)
        if config_paths:
            for cp in config_paths:
                shutil.copy2(Path(cp), local_staging_dir)
        
        # 2. Create manifest
        sha256 = hash_file(local_protocol_path)
        manifest = {
            "run_id": run_id,
            "protocol": local_protocol_path.name,
            "protocol_hash": sha256,
            "timestamp": datetime.now().isoformat(),
            "robot_ip": Config.ROBOT_IP
        }
        local_manifest_path = local_staging_dir / "manifest.json"
        with open(local_manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
            
        # 3. SCP to robot
        from pathlib import PurePosixPath
        robot_ip = Config.ROBOT_IP
        key_path = Config.ROBOT_SSH_KEY_PATH
        if not key_path:
            return "Error: ROBOT_SSH_KEY_PATH is missing from config/.env. A private key is required for OT-2 SSH access."

        ssh_user = Config.ROBOT_SSH_USER
        
        # Use PurePosixPath for all remote robot paths
        remote_base_dir = PurePosixPath(Config.REMOTE_USER_STORAGE) / "ot2_runs"
        remote_run_dir = remote_base_dir / run_id
        
        # Ensure remote base exists
        ssh_opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "-i", key_path]
        subprocess.run(["ssh"] + ssh_opts + [f"{ssh_user}@{robot_ip}", f"mkdir -p {remote_base_dir}"], check=True)
        
        # SCP staging dir
        # local_staging_dir is cast to string. Remote path uses posix string format.
        subprocess.run(["scp", "-O"] + ssh_opts + ["-r", str(local_staging_dir), f"{ssh_user}@{robot_ip}:{remote_base_dir}/"], check=True)
        
        return f"Deployment SUCCESS. Staged locally at {local_staging_dir}. Remote path: {remote_run_dir}"
    except Exception as e:
        return f"Deployment FAILED: {str(e)}"

@tool
def execute_protocol_on_robot(remote_protocol_path: str, protocol_hash: str) -> str:
    """
    Triggers physical execution of a protocol on the robot via SSH.
    VERIFIES that the protocol_hash matches a recent passing simulation.
    """
    # 1. Hash verification
    records = _load_simulation_records()
    if protocol_hash not in records:
        return f"Error: No simulation record found for hash {protocol_hash}. You MUST simulate the exact file first."
    
    if records[protocol_hash]["status"] != "PASS":
        return f"Error: Last simulation for hash {protocol_hash} FAILED. Refusing to run on physical hardware."
    
    # 2. SSH Execution
    from pathlib import PurePosixPath
    robot_ip = Config.ROBOT_IP
    key_path = Config.ROBOT_SSH_KEY_PATH
    if not key_path:
        return "Error: ROBOT_SSH_KEY_PATH is missing from config/.env. A private key is required for OT-2 SSH access."

    ssh_user = Config.ROBOT_SSH_USER
    ssh_opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "-i", key_path]
    
    # Ensure the remote_protocol_path is treated as a POSIX path
    remote_path_obj = PurePosixPath(remote_protocol_path)
    cmd = ["ssh"] + ssh_opts + [f"{ssh_user}@{robot_ip}", f"opentrons_execute {remote_path_obj}"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return f"Execution COMPLETE.\n\nOutput:\n{result.stdout}"
        else:
            return f"Execution FAILED (Return Code {result.returncode}).\n\nError:\n{result.stderr}"
    except Exception as e:
        return f"Remote execution error: {str(e)}"

@tool
def deploy_and_run_on_robot(protocol_path: str) -> str:
    """
    High-level wrapper: Check Connection -> Deploy -> Execute.
    Requires manual user confirmation 'RUN ROBOT' before calling.
    """
    # This tool is intended for the agent to orchestrate the sub-tools.
    return (
        "Deployment wrapper ready. Please call the following tools in order:\n"
        "1. check_robot_connection()\n"
        "2. deploy_protocol_to_robot(protocol_path)\n"
        "3. execute_protocol_on_robot(remote_path, hash)"
    )

@tool
def show_full_config() -> str:
    """Returns the full raw YAML representation of the current working configuration."""
    global _WORKING_CONFIG
    if _WORKING_CONFIG is None:
        return "Error: No workflow loaded."
    return yaml.dump(_WORKING_CONFIG, sort_keys=False)