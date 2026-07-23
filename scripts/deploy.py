import subprocess
import os
import sys
import json
from pathlib import Path

if __name__ == "__main__" and not __package__:
    print("ERROR: This script must be run as a module from the project root.")
    print("Command: python -m scripts.deploy")
    sys.exit(1)

from src.core.config import Config
from src.lab.robot_connection import (
    add_robot_host_arguments,
    connection_summary,
    resolve_host,
)
from src.utils.ot2_ssh import MissingIdentityFileError, OT2SSHSettings

# Robot-side store that opentrons_execute resolves custom labware from:
#   <this dir>/<namespace>/<loadName>/<version>.json
REMOTE_CUSTOM_LABWARE_DIR = "/data/labware/v2/custom_definitions"

def deploy(robot_host=None):
    """Deploys staged protocols and data to the physical OT-2 robot."""
    robot_ip = resolve_host(robot_host)
    deploy_src = Config.DEPLOY_BASE_DIR
    key_path = Config.ROBOT_SSH_KEY_PATH

    if not key_path:
        print("Error: ROBOT_SSH_KEY_PATH is missing from .env. A private key is required for OT-2 SSH access.")
        return

    # Remote path MUST use forward slashes (Linux)
    robot_dest = Config.REMOTE_USER_STORAGE
    
    print(connection_summary(robot_ip))
    print("--- Deploying to OT-2 ---")
    print(f"Source: {deploy_src}")
    print(f"Destination: {robot_dest}")
    
    if not deploy_src.exists():
        print(f"Error: Source directory {deploy_src} does not exist.")
        return
    
    # Professional SCP: Deploy the entire directory content
    # On Windows, we use the local path as is (Pathlib handles it)
    # The remote side is a string with forward slashes
    settings = OT2SSHSettings.from_config(Config, robot_ip=robot_ip)

    # Ensure the remote destination exists before copying into it
    try:
        mkdir_cmd = settings.ssh_command(f"mkdir -p {robot_dest}")
        subprocess.run(mkdir_cmd, check=True)
    except MissingIdentityFileError as e:
        print(f"Error: {e}")
        return
    except subprocess.CalledProcessError as e:
        print(f"Error: Could not reach robot / create remote dir (code {e.returncode}). Check SSH keys and connectivity.")
        return

    # We use a '.' to mean 'everything in this folder'.
    # -O forces the legacy SCP protocol (the OT-2's dropbear server lacks SFTP).
    cmd = settings.scp_command(
        ".",
        settings.remote_path(robot_dest),
        recursive=True,
        legacy_protocol=True,
    )

    print(f"Executing: {' '.join(cmd)}")
    try:
        # Run from the source directory to make the SCP command cleaner
        subprocess.run(cmd, check=True, cwd=str(deploy_src))
        print("Success: Deployment complete.")
    except subprocess.CalledProcessError as e:
        print(f"Error: Deployment failed with code {e.returncode}")

def deploy_labware(labware_path, dry_run=False, robot_host=None):
    """Deploy one custom labware JSON to the OT-2's custom_definitions store.

    opentrons_execute resolves load_labware(load_name, namespace=, version=) from
    <REMOTE_CUSTOM_LABWARE_DIR>/<namespace>/<loadName>/<version>.json. This reads
    those three fields from the definition and uploads the file there (renamed to
    <version>.json), creating the nested directory first. Returns True on success.
    """
    labware_path = Path(labware_path)
    if not labware_path.is_file():
        print(f"Error: labware file not found: {labware_path}")
        return False

    # Identity comes from the definition itself - never hardcode it.
    try:
        definition = json.loads(labware_path.read_text(encoding="utf-8"))
        namespace = definition["namespace"]
        load_name = definition["parameters"]["loadName"]
        version = definition["version"]
    except (json.JSONDecodeError, KeyError, OSError) as e:
        print(f"Error: could not read namespace/loadName/version from {labware_path.name}: {e}")
        return False

    # Remote paths MUST use forward slashes (Linux).
    remote_dir = f"{REMOTE_CUSTOM_LABWARE_DIR}/{namespace}/{load_name}"
    remote_dest = f"{remote_dir}/{version}.json"

    robot_ip = resolve_host(robot_host)
    print(connection_summary(robot_ip))
    print("--- Deploying labware to OT-2 ---")
    print(f"Source     : {labware_path}")
    print(f"Identity   : namespace={namespace}, loadName={load_name}, version={version}")
    print(f"Destination: {remote_dest}")

    if dry_run:
        print("[DRY RUN] No SSH/SCP executed.")
        print(f"[DRY RUN] Would run : mkdir -p {remote_dir}")
        print(f"[DRY RUN] Would copy: {labware_path.name} -> {remote_dest}")
        return True

    key_path = Config.ROBOT_SSH_KEY_PATH
    if not key_path:
        print("Error: ROBOT_SSH_KEY_PATH is missing from .env. A private key is required for OT-2 SSH access.")
        return False

    settings = OT2SSHSettings.from_config(Config, robot_ip=robot_ip)

    # Create the nested destination dir before copying into it.
    try:
        mkdir_cmd = settings.ssh_command(f"mkdir -p {remote_dir}")
        subprocess.run(mkdir_cmd, check=True)
    except MissingIdentityFileError as e:
        print(f"Error: {e}")
        return False
    except subprocess.CalledProcessError as e:
        print(f"Error: could not reach robot / create remote dir (code {e.returncode}). Check SSH keys and connectivity.")
        return False

    # -O forces the legacy SCP protocol (the OT-2's dropbear server lacks SFTP).
    cmd = settings.scp_command(
        str(labware_path),
        settings.remote_path(remote_dest),
        legacy_protocol=True,
    )
    print(f"Executing: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: labware deployment failed with code {e.returncode}")
        return False

    # Confirm the file actually landed (directly answers "does it exist on the robot").
    verify_cmd = settings.ssh_command(f"ls -l {remote_dest}")
    try:
        subprocess.run(verify_cmd, check=True)
    except subprocess.CalledProcessError:
        print("Warning: upload reported success but the remote file could not be verified.")
    print("Success: Labware deployed.")
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Deploy protocols (default) or a custom labware JSON to the OT-2."
    )
    add_robot_host_arguments(parser)
    parser.add_argument(
        "--labware",
        metavar="JSON",
        help="Path to a custom labware .json to deploy to the robot's custom_definitions store.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse the definition and print the destination without running SSH/SCP.",
    )
    args = parser.parse_args()

    if args.labware:
        sys.exit(
            0
            if deploy_labware(
                args.labware,
                dry_run=args.dry_run,
                robot_host=args.robot_host,
            )
            else 1
        )
    else:
        deploy(args.robot_host)
