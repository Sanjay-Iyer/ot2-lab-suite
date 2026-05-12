import subprocess
import os
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    print("ERROR: This script must be run as a module from the project root.")
    print("Command: python -m scripts.sync_robot")
    sys.exit(1)

from src.core.config import Config

def sync_from_robot():
    robot_ip = Config.ROBOT_IP
    local_dest = Config.ROBOT_DATA
    robot_src = "/var/lib/opentrons" # Base dir for configs and logs
    
    print(f"--- Syncing FROM OT-2 ({robot_ip}) ---")
    print(f"Destination: {local_dest}")
    
    # Run scp command to pull data
    # We pull config and logs
    targets = ["config.json", "labware", "pipettes", "logs"]
    
    for target in targets:
        cmd = ["scp", "-r", f"root@{robot_ip}:{robot_src}/{target}", str(local_dest)]
        print(f"Syncing {target}...")
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            print(f"Warning: Failed to sync {target}")

    print("Sync complete.")

if __name__ == "__main__":
    sync_from_robot()
