#!/usr/bin/env python3
"""
scripts/test_ot2_camera_capture.py
==================================
Diagnostic script to test the camera capture pipeline:
SSH into robot -> curl camera endpoint -> save image -> SCP image back -> validate with Pillow

Usage::

    python scripts/test_ot2_camera_capture.py
    python scripts/test_ot2_camera_capture.py --mock
"""

import sys
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vision.config import load_vision_config
from PIL import Image

def run_diagnostics(mock: bool = False, robot_ip: str = None) -> int:
    print("=" * 60)
    print("  OT-2 Camera Capture Diagnostic Tool")
    print("=" * 60)

    # 1. Load vision configuration
    try:
        cfg = load_vision_config()
        print("[OK] Vision configuration loaded successfully.")
    except Exception as e:
        if mock:
            print(f"[WARN] Failed to load vision configuration: {e}. Using mock config values.")
            cfg = {
                "robot": {
                    "host": robot_ip or "127.0.0.1",
                    "username": "root",
                    "ssh_key_path": "mock_key_path"
                },
                "remote": {
                    "vision_dir": "/data/vision/printing_demo",
                    "camera_endpoint": "http://localhost:31950/camera/picture",
                    "opentrons_version_header": "*"
                }
            }
        else:
            print(f"[FAIL] Failed to load vision configuration: {e}", file=sys.stderr)
            return 1

    ip = robot_ip if robot_ip else cfg["robot"]["host"]
    user = cfg["robot"]["username"]
    key = Path(cfg["robot"]["ssh_key_path"]) if not mock else Path(cfg["robot"]["ssh_key_path"])
    remote_dir = cfg["remote"]["vision_dir"]
    camera_endpoint = cfg["remote"]["camera_endpoint"]
    header_val = cfg["remote"]["opentrons_version_header"]

    print(f"  Robot IP       : {ip}")
    print(f"  SSH User       : {user}")
    print(f"  SSH Key Path   : {key}")
    print(f"  Remote Dir     : {remote_dir}")
    print(f"  Camera Endpoint: {camera_endpoint}")
    print("-" * 60)

    # 2. Check local directories
    local_temp = PROJECT_ROOT / "runs" / "diagnostics"
    local_temp.mkdir(parents=True, exist_ok=True)
    local_filepath = local_temp / "test_diag_capture.jpg"
    if local_filepath.exists():
        local_filepath.unlink()

    if mock:
        print("[MOCK] Simulating diagnostic capture...")
        img = Image.new("RGB", (640, 480), color=(100, 150, 100))
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        draw.text((20, 20), f"Mock Diagnostic Capture\nTimestamp: {datetime.now()}", fill=(255, 255, 255))
        img.save(local_filepath)
        print(f"[MOCK] Saved mock image to: {local_filepath}")
    else:
        # Physical run checks
        if not key.exists():
            print(f"[FAIL] Private SSH key not found at: {key}", file=sys.stderr)
            return 1

        ssh_opts = [
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            "-i", str(key)
        ]

        # Step 1: Capture image on the robot
        remote_filepath = f"{remote_dir}/test_diag_capture.jpg"
        print(f"Executing remote capture command on robot ({ip})...")
        remote_cmd = (
            f"mkdir -p {remote_dir} && "
            f"curl -s -X POST -H 'opentrons-version: {header_val}' "
            f"{camera_endpoint} --output {remote_filepath}"
        )
        ssh_cmd = ["ssh"] + ssh_opts + [f"{user}@{ip}", remote_cmd]
        
        print(f"  Running: {' '.join(ssh_cmd)}")
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, check=False)
        
        if result.returncode != 0:
            print(f"[FAIL] SSH capture command failed (code {result.returncode}):", file=sys.stderr)
            print(f"  stderr: {result.stderr}", file=sys.stderr)
            return 1
        print("[OK] Remote capture command completed on the robot.")

        # Step 2: Transfer the image back
        print("Transferring image via SCP...")
        scp_cmd = ["scp", "-O"] + ssh_opts + [f"{user}@{ip}:{remote_filepath}", str(local_filepath)]
        print(f"  Running: {' '.join(scp_cmd)}")
        result = subprocess.run(scp_cmd, capture_output=True, text=True, check=False)
        
        if result.returncode != 0:
            print(f"[FAIL] SCP transfer failed (code {result.returncode}):", file=sys.stderr)
            print(f"  stderr: {result.stderr}", file=sys.stderr)
            return 1
        print(f"[OK] SCP transfer complete. Transferred to: {local_filepath}")

        # Step 3: Clean up remote file
        print("Cleaning up remote file on the robot...")
        cleanup_cmd = ["ssh"] + ssh_opts + [f"{user}@{ip}", f"rm -f {remote_filepath}"]
        subprocess.run(cleanup_cmd, check=False)

    # 3. Validate image file integrity using Pillow
    print("Validating image file...")
    if not local_filepath.exists():
        print(f"[FAIL] Local destination file not found: {local_filepath}", file=sys.stderr)
        return 1

    file_size = local_filepath.stat().st_size
    print(f"  File size: {file_size} bytes")
    if file_size < 1000:
        print(f"[FAIL] Transferred file is too small ({file_size} B). Likely empty or corrupt.", file=sys.stderr)
        return 1

    try:
        with Image.open(local_filepath) as img:
            img.verify()
        print("[OK] Image verified successfully by Pillow.")
    except Exception as e:
        print(f"[FAIL] Image verification failed: {e}", file=sys.stderr)
        return 1

    print("=" * 60)
    print("  Diagnostic Status: SUCCESS")
    print("=" * 60)
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test OT-2 camera capture and transfer diagnostics.")
    parser.add_argument("--mock", action="store_true", help="Run a mock capture test locally.")
    parser.add_argument("--robot-ip", type=str, default=None, help="IP of physical robot to override config.")
    args = parser.parse_args()

    sys.exit(run_diagnostics(mock=args.mock, robot_ip=args.robot_ip))
