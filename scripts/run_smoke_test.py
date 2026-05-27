#!/usr/bin/env python3
import sys
from pathlib import Path
import argparse
import subprocess

def main():
    parser = argparse.ArgumentParser(description="Laptop-side runner for OT-2 Smoke Test.")
    parser.add_argument(
        "--robot-ip",
        type=str,
        default="169.254.46.57",
        help="IP address of the physical OT-2 robot."
    )
    parser.add_argument(
        "--ssh-key",
        type=str,
        default=r"C:\Users\iyersn\.ssh\id_rsa_opentrons",
        help="Path to the SSH key."
    )
    parser.add_argument(
        "--local-protocol",
        type=str,
        default=str(Path("src/protocols/generated/smoke_test.py")),
        help="Path to the local smoke test protocol."
    )
    parser.add_argument(
        "--remote-protocol",
        type=str,
        default="/var/lib/opentrons/user_storage/ot2_runs/smoke_test.py",
        help="Target path for the protocol on the robot."
    )
    args = parser.parse_args()

    robot_ip = args.robot_ip
    ssh_key = args.ssh_key
    local_proto = Path(args.local_protocol).resolve()
    remote_proto = args.remote_protocol
    remote_dir = "/var/lib/opentrons/user_storage/ot2_runs"

    ssh_opts = [
        "-i", ssh_key,
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=no"
    ]

    print("=" * 60)
    print(" OT-2 SMOKE TEST RUNNER ")
    print("=" * 60)

    # [1/5] Checking local files
    print("\n[1/5] Checking local files")
    if not local_proto.exists():
        print(f"ERROR: Local protocol file not found at: {local_proto}")
        sys.exit(1)
    print(f"  ✓ Local protocol file found: {local_proto}")

    if not Path(ssh_key).exists():
        print(f"ERROR: SSH key file not found at: {ssh_key}")
        sys.exit(1)
    print(f"  ✓ SSH key found: {ssh_key}")

    # [2/5] Testing SSH
    print("\n[2/5] Testing SSH")
    ssh_cmd = ["ssh"] + ssh_opts + [f"root@{robot_ip}", "echo SSH_OK"]
    try:
        print(f"Running command: {' '.join(ssh_cmd)}")
        res = subprocess.run(ssh_cmd, capture_output=True, text=True, check=True)
        if "SSH_OK" in res.stdout:
            print("  ✓ SSH Connection successful: SSH_OK")
        else:
            print(f"ERROR: SSH command ran but output did not contain SSH_OK. stdout: {res.stdout.strip()}")
            sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: SSH connection test failed.\nstdout: {e.stdout}\nstderr: {e.stderr}")
        sys.exit(1)

    # [3/5] Uploading smoke_test.py
    print("\n[3/5] Uploading smoke_test.py")
    mkdir_cmd = ["ssh"] + ssh_opts + [f"root@{robot_ip}", f"mkdir -p {remote_dir}"]
    try:
        print(f"Creating remote folder: {' '.join(mkdir_cmd)}")
        subprocess.run(mkdir_cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to create remote folder on the robot.")
        sys.exit(1)

    scp_cmd = ["scp", "-O", "-i", ssh_key, "-o", "StrictHostKeyChecking=no", str(local_proto), f"root@{robot_ip}:{remote_proto}"]
    try:
        print(f"Copying file via SCP: {' '.join(scp_cmd)}")
        subprocess.run(scp_cmd, check=True)
        print("  ✓ smoke_test.py copied successfully")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: SCP upload failed.")
        sys.exit(1)

    # [4/5] Verifying remote file
    print("\n[4/5] Verifying remote file")
    verify_cmd = ["ssh"] + ssh_opts + [f"root@{robot_ip}", f"ls -lah {remote_proto}"]
    try:
        print(f"Verifying: {' '.join(verify_cmd)}")
        res = subprocess.run(verify_cmd, capture_output=True, text=True, check=True)
        print(res.stdout.strip())
        print("  ✓ Remote file verified")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Verification of remote file failed. Is it missing?")
        sys.exit(1)

    # [5/5] Running opentrons_execute
    print("\n[5/5] Running opentrons_execute")
    exec_cmd = ["ssh"] + ssh_opts + [f"root@{robot_ip}", f"opentrons_execute {remote_proto}"]
    print(f"Executing: {' '.join(exec_cmd)}")
    try:
        process = subprocess.Popen(
            exec_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                print(line.strip())
        rc = process.poll()
        if rc != 0:
            print(f"\nERROR: Robot execution failed with exit code {rc}")
            sys.exit(rc)
        else:
            print("\n" + "=" * 60)
            print(" SMOKE TEST SUCCESSFUL ")
            print("=" * 60)
    except Exception as e:
        print(f"ERROR: Failed to execute command. {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
