import streamlit as st
import subprocess
import os
import pathlib
import yaml
import sys
import time

st.set_page_config(page_title="Opentrons Deployment Dashboard", page_icon="🤖", layout="wide")

st.title("🤖 Opentrons OT-2 Deployment Dashboard")
st.markdown("---")

# 1. Setup Paths
ROOT_DIR = pathlib.Path(__file__).parent.parent
CONFIG_DIR = ROOT_DIR / "configs"
PROTOCOL_DIR = ROOT_DIR / "protocols"
HISTORY_DIR = ROOT_DIR / "history"
DEPLOY_SCRIPT = ROOT_DIR / "tools" / "deploy_to_robot.py"

# Ensure history exists
HISTORY_DIR.mkdir(exist_ok=True)

# 2. Sidebar - Configuration
st.sidebar.header("🔌 Robot Connection")

# Try to get default IP from a config file if available
default_ip = "169.254.46.57"
robot_ip = st.sidebar.text_input("Robot IP Address", value=default_ip)

# 2. File Selection & Summaries
FILE_SUMMARIES = {
    # Configs
    "pipette_test.yaml": "Configuration for the nanoparticle dilution series. It defines stock concentrations, final volumes, and a range of dilution factors from 2x to 50x.",
    "example_experiment.yaml": "A template configuration for general nanoparticle experiments. It includes robot IP settings and placeholder values for source and destination labware.",
    "smoketest_quick.yaml": "A minimal configuration designed for rapid hardware validation. It runs a single-well transfer to ensure the robot's motors and pipettes are responsive.",
    "SCHEMA.md": "Documentation for the YAML configuration schema used in this project.",
    # Protocols
    "pipette_test.py": "Parallel dilution logic for nanoparticle (AuNS) stock and diluent. It calculates volumes based on dilution factors and performs automated mixing in each target well.",
    "dilution_protocol.py": "Relative concentration control using dilution factors (DF). It is optimized for scientific accuracy using stock suspensions and external JSON volume tables.",
    "smoketest_protocol.py": "Comprehensive hardware verification using exactly one tip. It exercises gantry motion, pump accuracy, and flow rates without consuming liquid.",
    "config.json": "Generated runtime configuration file used by the Opentrons API during protocol execution."
}

st.sidebar.header("📂 File Selection")

# Get list of configs and protocols
configs = [f.name for f in CONFIG_DIR.glob("*.yaml")]
protocols = [f.name for f in PROTOCOL_DIR.glob("*.py")]

selected_config = st.sidebar.selectbox("Select Experiment Config", configs)
selected_protocol = st.sidebar.selectbox("Select Protocol Script", protocols)

st.sidebar.markdown("---")
st.sidebar.header("⚙️ Execution Options")
execute_on_robot = st.sidebar.checkbox("Deploy & Execute on Hardware", value=False)
skip_sim = st.sidebar.checkbox("Skip Local Simulation", value=False)

# 3. Parameter Overrides
st.header("🔧 Parameter Overrides")
overrides = {}
if selected_config:
    with open(CONFIG_DIR / selected_config, "r") as f:
        cfg_data = yaml.safe_load(f)
    
    # Identify which section to override
    param_section = None
    if "test_parameters" in cfg_data:
        param_section = "test_parameters"
    elif "smoketest" in cfg_data:
        param_section = "smoketest"
    
    if param_section:
        params = cfg_data[param_section]
        # Display in a grid
        cols = st.columns(3)
        for i, (key, value) in enumerate(params.items()):
            with cols[i % 3]:
                if isinstance(value, int):
                    new_val = st.number_input(f"{key}", value=int(value), step=1)
                elif isinstance(value, float):
                    new_val = st.number_input(f"{key}", value=float(value))
                elif isinstance(value, list):
                    new_val = st.text_input(f"{key} (comma-separated)", value=", ".join(map(str, value)))
                    # Convert back to list of appropriate type
                    try:
                        new_val = [type(value[0])(x.strip()) for x in new_val.split(",")] if value else []
                    except:
                        pass 
                else:
                    new_val = st.text_input(f"{key}", value=str(value))
                overrides[key] = new_val
    else:
        st.info("No overrideable parameters found in this config.")

st.markdown("---")

# 4. Main Area - File Preview
col1, col2 = st.columns(2)

with col1:
    st.subheader("📝 Config Preview")
    if selected_config:
        st.info(FILE_SUMMARIES.get(selected_config, "No description available for this configuration."))
        st.write(cfg_data)

with col2:
    st.subheader("🐍 Protocol Preview")
    if selected_protocol:
        st.info(FILE_SUMMARIES.get(selected_protocol, "No description available for this protocol."))
        with open(PROTOCOL_DIR / selected_protocol, "r") as f:
            st.code(f.read(), language="python")

# 4. Action Buttons
st.markdown("---")
col_btn1, col_btn2, _ = st.columns([1, 1, 2])

# Handle process state in session_state
if "process" not in st.session_state:
    st.session_state.process = None

if col_btn1.button("🚀 Start Workflow", use_container_width=True):
    # Update cfg_data with overrides and robot settings
    if param_section:
        cfg_data[param_section].update(overrides)
    
    if "robot" not in cfg_data:
        cfg_data["robot"] = {}
    cfg_data["robot"]["ip_address"] = robot_ip
    
    # Save to a temporary file for the tools to read
    temp_config_path = CONFIG_DIR / ".gui_active_config.yaml"
    with open(temp_config_path, "w") as f:
        yaml.dump(cfg_data, f)
    
    # Save a permanent record in history
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    history_path = HISTORY_DIR / f"run_{timestamp}_{selected_config}"
    with open(history_path, "w") as f:
        yaml.dump(cfg_data, f)
    st.toast(f"Experiment logged to history/{history_path.name}")

    # Construct the command
    cmd = [
        sys.executable, str(DEPLOY_SCRIPT),
        str(temp_config_path),
        "--protocol", str(PROTOCOL_DIR / selected_protocol),
        "--robot-ip", robot_ip
    ]
    
    if execute_on_robot:
        cmd.append("--execute")
    if skip_sim:
        cmd.append("--skip-sim")
    
    st.subheader("🖥️ Execution Output")
    output_area = st.empty()
    full_output = ""
    
    # Run the command and stream output
    with st.spinner("Executing workflow..."):
        st.session_state.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            cwd=str(ROOT_DIR)
        )
        
        # Stream output
        while st.session_state.process.poll() is None:
            line = st.session_state.process.stdout.readline()
            if line:
                full_output += line
                output_area.code(full_output)
        
        # Capture any remaining output
        remaining = st.session_state.process.stdout.read()
        if remaining:
            full_output += remaining
            output_area.code(full_output)
            
        return_code = st.session_state.process.wait()
        st.session_state.process = None # Clear state
        
        if return_code == 0:
            st.success("Workflow completed successfully!")
        elif return_code == -15 or return_code == -9:
            st.warning("Workflow was stopped by user.")
        else:
            st.error(f"Workflow failed with return code {return_code}")

if col_btn2.button("🛑 Stop Workflow", use_container_width=True):
    if st.session_state.process:
        st.session_state.process.terminate()
        st.session_state.process = None
        st.warning("Stopping workflow... please wait.")
        time.sleep(1)
        st.rerun()
    else:
        st.info("No workflow is currently running.")

st.markdown("---")
st.caption("Opentrons Offline Deployment GUI v1.0")
