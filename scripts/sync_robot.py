import subprocess
import os
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    print("ERROR: This script must be run as a module from the project root.")
    print("Command: python -m scripts.sync_robot")
    sys.exit(1)

from src.core.config import Config
from src.lab.robot_connection import (
    add_robot_host_arguments,
    connection_summary,
    resolve_host,
)
from src.utils.ot2_ssh import MissingIdentityFileError, OT2SSHSettings

def sync_from_robot(robot_host=None):
    robot_ip = resolve_host(robot_host)
    local_dest = Config.ROBOT_DATA
    robot_src = "/var/lib/opentrons" # Base dir for configs and logs
    
    print(connection_summary(robot_ip))
    print("--- Syncing FROM OT-2 ---")
    print(f"Destination: {local_dest}")
    
    # Run scp command to pull data
    # We pull config and logs
    targets = ["config.json", "labware", "pipettes", "logs"]
    settings = OT2SSHSettings.from_config(Config, robot_ip=robot_ip)
    
    for target in targets:
        print(f"Syncing {target}...")
        try:
            cmd = settings.scp_command(
                settings.remote_path(f"{robot_src}/{target}"),
                str(local_dest),
                recursive=True,
                legacy_protocol=True,
            )
            subprocess.run(cmd, check=True)
        except MissingIdentityFileError as e:
            print(f"Error: {e}")
            return
        except subprocess.CalledProcessError:
            print(f"Warning: Failed to sync {target}")

    print("Sync complete.")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sync config and logs from the OT-2.")
    add_robot_host_arguments(parser)
    args = parser.parse_args()
    sync_from_robot(args.robot_host)
