import subprocess
import os
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    print("ERROR: This script must be run as a module from the project root.")
    print("Command: python -m scripts.deploy")
    sys.exit(1)

from src.core.config import Config

def deploy():
    """Deploys staged protocols and data to the physical OT-2 robot."""
    robot_ip = Config.ROBOT_IP
    deploy_src = Config.DEPLOY_BASE_DIR
    key_path = Config.ROBOT_SSH_KEY_PATH
    ssh_user = Config.ROBOT_SSH_USER

    if not key_path:
        print("Error: ROBOT_SSH_KEY_PATH is missing from .env. A private key is required for OT-2 SSH access.")
        return

    # Remote path MUST use forward slashes (Linux)
    robot_dest = Config.REMOTE_USER_STORAGE
    
    print(f"--- Deploying to OT-2 ({robot_ip}) ---")
    print(f"Source: {deploy_src}")
    print(f"Destination: {robot_dest}")
    
    if not deploy_src.exists():
        print(f"Error: Source directory {deploy_src} does not exist.")
        return
    
    # Professional SCP: Deploy the entire directory content
    # On Windows, we use the local path as is (Pathlib handles it)
    # The remote side is a string with forward slashes
    ssh_opts = ["-i", key_path, "-o", "BatchMode=yes", "-o", "ConnectTimeout=30"]

    # Ensure the remote destination exists before copying into it
    mkdir_cmd = ["ssh"] + ssh_opts + [f"{ssh_user}@{robot_ip}", f"mkdir -p {robot_dest}"]
    try:
        subprocess.run(mkdir_cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: Could not reach robot / create remote dir (code {e.returncode}). Check SSH keys and connectivity.")
        return

    # We use a '.' to mean 'everything in this folder'.
    # -O forces the legacy SCP protocol (the OT-2's dropbear server lacks SFTP).
    cmd = ["scp", "-O", "-r"] + ssh_opts + [".", f"{ssh_user}@{robot_ip}:{robot_dest}"]

    print(f"Executing: {' '.join(cmd)}")
    try:
        # Run from the source directory to make the SCP command cleaner
        subprocess.run(cmd, check=True, cwd=str(deploy_src))
        print("Success: Deployment complete.")
    except subprocess.CalledProcessError as e:
        print(f"Error: Deployment failed with code {e.returncode}")

if __name__ == "__main__":
    deploy()
