#!/usr/bin/env python3
import argparse
import json
import os
import pathlib
import subprocess
import sys
import yaml

def run_simulation(protocol_path, config_path, local_data_path=None):
    """
    Runs a local simulation using opentrons_simulate.
    If local_data_path is provided, it uses that for robot calibration data.
    """
    print(f"\n[SIMULATE] Starting pre-deployment simulation...")
    
    env = os.environ.copy()
    # Ensure the protocol can find the generated config.json
    env["OT_CONFIG_PATH"] = str(config_path.absolute())
    
    if local_data_path:
        data_path = pathlib.Path(local_data_path)
        if data_path.exists():
            print(f"[SIMULATE] Using calibration data from: {data_path}")
            env["OT_API_CONFIG_DIR"] = str(data_path.absolute())
        else:
            print(f"[WARNING] Calibration path {data_path} not found. Using defaults.")

    sim_cmd = [sys.executable, "-m", "opentrons.simulate", str(protocol_path)]
    
    try:
        result = subprocess.run(sim_cmd, env=env, capture_output=True, text=True)
        if result.returncode == 0:
            print("[SUCCESS] Simulation passed.")
            return True
        else:
            print("\n" + "!" * 60)
            print("SIMULATION FAILED! Fix protocol/config before deploying.")
            print(result.stdout)
            print(result.stderr)
            print("!" * 60 + "\n")
            return False
    except Exception as e:
        print(f"[ERROR] Could not run simulation: {e}")
        return False

def main():
    """
    Orchestrates the deployment of the protocol to the physical Opentrons robot.
    
    Workflow:
    1. Reads the YAML config (laptop side).
    2. Serializes it to JSON (robot side compatibility).
    3. If --execute is passed:
       a. Transfers files via SCP over Ethernet.
       b. Remotely triggers 'opentrons_execute' via SSH.
    """
    parser = argparse.ArgumentParser(description="Prepare protocol for offline robot deployment.")
    parser.add_argument("config", help="Path to YAML config file")
    parser.add_argument("--protocol", help="Path to the protocol Python file (defaults to protocols/dilution_protocol.py)")
    parser.add_argument("--robot-ip", help="Override IP address of the robot")
    parser.add_argument("--execute", action="store_true", help="Automatically SCP files and trigger the run on the robot.")
    parser.add_argument("--skip-sim", action="store_true", help="Skip the pre-deployment simulation.")
    args = parser.parse_args()

    config_path = pathlib.Path(args.config)
    if not config_path.exists():
        print(f"Error: Config {config_path} not found.")
        sys.exit(1)

    # 1. Load YAML (AI Tool Note: Reads high-level experiment design)
    try:
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
    except Exception as e:
        print(f"Error parsing YAML: {e}")
        sys.exit(1)

    # 2. Save as JSON (AI Tool Note: Prepares data for offline robot)
    protocol_dir = pathlib.Path("protocols")
    output_path = protocol_dir / "config.json"
    
    # Resolve protocol file: CLI Arg > Config File > Default
    protocol_path_str = args.protocol or cfg.get("experiment", {}).get("protocol_file") or "protocols/dilution_protocol.py"
    protocol_file = pathlib.Path(protocol_path_str)

    if not protocol_file.exists():
        print(f"[ERROR] Protocol file {protocol_file} not found.")
        sys.exit(1)

    with open(output_path, 'w') as f:
        json.dump(cfg, f, indent=2)

    print(f"\n[SUCCESS] Config converted and saved to {output_path}")

    # 3. Validation Simulation
    robot_cfg = cfg.get("robot", {})
    local_data = robot_cfg.get("local_data_path")
    
    if not args.skip_sim:
        if not run_simulation(protocol_file, output_path, local_data):
            if args.execute:
                print("[ABORT] Deployment cancelled due to simulation failure.")
                sys.exit(1)
            else:
                print("[WARNING] Simulation failed. Review output before manual deployment.")

    # 4. Execution logic (AI Tool Note: Physical robot interaction)
    remote_path = "/var/lib/jupyter/notebooks/"
    
    # Resolve IP: CLI Arg > Config File > Default
    robot_ip = args.robot_ip or robot_cfg.get("ip_address") or "169.254.46.57"
    
    if args.execute:
        print(f"--- Automated Execution Mode ---")
        
        # Step A: SCP (Transferring the bundle)
        print(f"Transferring files to {robot_ip}...")
        scp_cmd = [
            "scp", 
            str(protocol_file), 
            str(output_path), 
            f"root@{robot_ip}:{remote_path}"
        ]
        subprocess.run(scp_cmd, check=True)
        
        # Step B: Remote SSH Execution (Starting the physical run)
        print(f"Triggering protocol run on robot...")
        ssh_cmd = [
            "ssh", 
            f"root@{robot_ip}", 
            f"cd {remote_path} && opentrons_execute {protocol_file.name}"
        ]
        # This will stream the robot's output back to the laptop terminal
        subprocess.run(ssh_cmd)
        
    else:
        print("-" * 50)
        print("MANUAL DEPLOYMENT STEPS:")
        print(f"1. scp {protocol_file} {output_path} root@{robot_ip}:{remote_path}")
        print(f"2. ssh root@{robot_ip} 'opentrons_execute {remote_path}{protocol_file.name}'")
        print("-" * 50)
        print("TIP: Use --execute to run these automatically!")

if __name__ == "__main__":
    main()
