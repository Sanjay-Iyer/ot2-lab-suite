import subprocess
import os
import sys
from pathlib import Path

# Add src to path so we can use Config
sys.path.append(str(Path(__file__).resolve().parent.parent))
from src.core.config import Config

def deploy():
    """Deploys staged protocols and data to the physical OT-2 robot."""
    robot_ip = Config.ROBOT_IP
    deploy_src = Config.DEPLOY_DIR
    
    # Remote path MUST use forward slashes (Linux)
    robot_dest = "/var/lib/opentrons/user_storage"
    
    print(f"--- Deploying to OT-2 ({robot_ip}) ---")
    print(f"Source: {deploy_src}")
    print(f"Destination: {robot_dest}")
    
    if not deploy_src.exists():
        print(f"Error: Source directory {deploy_src} does not exist.")
        return
    
    # Professional SCP: Deploy the entire directory content
    # On Windows, we use the local path as is (Pathlib handles it)
    # The remote side is a string with forward slashes
    
    # We use a '.' to mean 'everything in this folder'
    cmd = ["scp", "-r", ".", f"root@{robot_ip}:{robot_dest}"]
    
    print(f"Executing: {' '.join(cmd)}")
    try:
        # Run from the source directory to make the SCP command cleaner
        subprocess.run(cmd, check=True, cwd=str(deploy_src))
        print("Success: Deployment complete.")
    except subprocess.CalledProcessError as e:
        print(f"Error: Deployment failed with code {e.returncode}")

if __name__ == "__main__":
    deploy()
