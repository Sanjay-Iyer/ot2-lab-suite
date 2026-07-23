import os
import sys
import socket
import subprocess
from urllib.parse import urlparse
import requests
from pathlib import Path

# Run this script using `python -m scripts.check_connectivity` from the project root.


from src.core.config import Config
from src.lab.robot_connection import connection_summary, resolve_host
from src.utils.ot2_ssh import OT2SSHSettings

def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "***"
    return secret[:4] + "***" + secret[-4:]

def main():
    print("=" * 60)
    print("OT-2 Lab Suite - Connectivity Diagnostics")
    print("=" * 60)

    # --- SECTION 1: Configuration Loaded ---
    print("\n--- 1. Configuration Loaded ---")
    
    api_key = os.getenv("GOOGLE_API_KEY", "")
    print(f"GOOGLE_API_KEY: {mask_secret(api_key)}")
    print(f"GEMINI_MODEL: {Config.GEMINI_MODEL}")
    print(f"GEMINI_BASE_URL: {Config.GEMINI_BASE_URL or '(default Google API)'}")
    print(f"HTTP_PROXY: {os.getenv('HTTP_PROXY', '(not set)')}")
    print(f"HTTPS_PROXY: {os.getenv('HTTPS_PROXY', '(not set)')}")
    
    no_proxy = os.getenv('NO_PROXY', '(not set)')
    print(f"NO_PROXY: {no_proxy}")
    
    try:
        robot_ip = resolve_host()
        print(connection_summary(robot_ip))
    except RuntimeError as exc:
        robot_ip = ""
        print(f"ROBOT HOST DISCOVERY FAILED: {exc}")
    if robot_ip and robot_ip not in no_proxy.split(','):
        print("WARNING: Robot host is not in NO_PROXY. Local robot traffic might be routed to a proxy and fail.")
    
    print(f"ROBOT_HOST: {robot_ip or '(unresolved)'}")
    print(f"ROBOT_SSH_USER: {Config.ROBOT_SSH_USER}")
    print(f"ROBOT_SSH_IDENTITIES_ONLY: {Config.ROBOT_SSH_IDENTITIES_ONLY}")
    print(f"ROBOT_SSH_LEGACY_RSA: {Config.ROBOT_SSH_LEGACY_RSA}")
    
    key_path_val = Config.ROBOT_SSH_KEY_PATH
    if key_path_val:
        key_exists = Path(key_path_val).exists()
        masked_path = mask_secret(key_path_val)
        print(f"ROBOT_SSH_KEY_PATH: {masked_path} (Exists: {key_exists})")
    else:
        print("ROBOT_SSH_KEY_PATH: (not set)")

    # --- SECTION 2: Gemini/API DNS Check ---
    print("\n--- 2. Gemini/API DNS Check ---")
    base_url = Config.GEMINI_BASE_URL or "https://generativelanguage.googleapis.com"
    parsed = urlparse(base_url)
    hostname = parsed.hostname
    if not hostname:
        print("FAIL: Could not parse hostname from base URL.")
    else:
        try:
            ip = socket.gethostbyname(hostname)
            print(f"PASS: DNS resolved {hostname} to {ip}")
        except socket.gaierror as e:
            print(f"FAIL: DNS resolution failed for {hostname}: {e}")
            print("\nTroubleshooting Stale DNS/VPN State on Windows:")
            print("If you recently switched networks or VPNs, try running this command to flush DNS:")
            print("  ipconfig /flushdns")
            print("Also consider clearing stale proxy environment variables in your terminal:")
            print("  Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue")
            print("  Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue")
            print("  Remove-Item Env:NO_PROXY -ErrorAction SilentlyContinue")
            
            import shutil
            if shutil.which("gcloud"):
                print("\nIf you are using Google Cloud CLI, verify its proxy configuration:")
                print("  gcloud config get-value proxy/type")
                print("  gcloud config get-value proxy/address")
                print("  gcloud config get-value proxy/port")

    # --- SECTION 3: Gemini/API Request Check ---
    print("\n--- 3. Gemini/API Request Check ---")
    if not api_key:
        print("SKIP: Missing GOOGLE_API_KEY, cannot perform request.")
    else:
        try:
            url = f"{base_url.rstrip('/')}/v1beta/models?key={api_key}"
            # Use short timeout to fail fast
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print("PASS: Successfully connected and authenticated to Gemini API.")
            else:
                print(f"FAIL: API request returned status code {response.status_code}")
                print(f"Response: {response.text[:200]}")
        except requests.exceptions.ProxyError as e:
            print(f"FAIL: Proxy error encountered: {e}")
            print("Troubleshooting: Verify HTTP_PROXY/HTTPS_PROXY settings and proxy server status.")
        except requests.exceptions.ConnectionError as e:
            print(f"FAIL: Connection error: {e}")
        except requests.exceptions.Timeout:
            print("FAIL: Connection timed out.")
            print("Troubleshooting: Check if a proxy is required but not configured, or if the firewall is blocking outbound traffic.")

    # --- SECTION 4: OT-2 IP/Socket Reachability ---
    print("\n--- 4. OT-2 IP/Socket Reachability ---")
    if robot_ip in ["127.0.0.1", "localhost", ""]:
        print("FAIL: Physical robot host could not be resolved.")
    else:
        try:
            # Try connecting to SSH port to see if host is reachable
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((robot_ip, 22))
            if result == 0:
                print(f"PASS: Reached OT-2 on port 22 at {robot_ip}")
            else:
                print(f"FAIL: Port 22 unreachable on {robot_ip} (code {result})")
            sock.close()
        except Exception as e:
            print(f"FAIL: Network error attempting to reach {robot_ip}: {e}")

    # --- SECTION 5: OT-2 SSH BatchMode Check ---
    print("\n--- 5. OT-2 SSH BatchMode Check ---")
    if not key_path_val:
        print("FAIL: ROBOT_SSH_KEY_PATH is missing. SSH cannot be tested.")
    elif not Path(key_path_val).exists():
        print(f"FAIL: Configured SSH key path does not exist.")
    else:
        settings = OT2SSHSettings.from_config(Config, robot_ip=robot_ip)
        cmd = settings.ssh_command(
            "echo 'SSH Connection Successful'",
            connect_timeout=30,
        )
        
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                print(f"PASS: Non-interactive SSH access works. ({res.stdout.strip()})")
            else:
                print("FAIL: SSH authentication rejected or timed out.")
                print(f"Error output: {res.stderr.strip()}")
                print("\nTroubleshooting Commands:")
                legacy = " --legacy-rsa" if settings.legacy_rsa else " --no-legacy-rsa"
                print(
                    f"  python scripts/check_ot2_ssh.py --robot-host {robot_ip} "
                    f"--identity-file \"{key_path_val}\"{legacy}"
                )
        except Exception as e:
            print(f"FAIL: Failed to run SSH command: {e}")

    # --- SECTION 6: OT-2 Calibration Paths Check ---
    if robot_ip and robot_ip not in ["127.0.0.1", "localhost", ""] and key_path_val and Path(key_path_val).exists():
        check_robot_calibration_paths(robot_ip, key_path_val)

    print("\n" + "=" * 60)

def check_robot_calibration_paths(robot_ip: str, ssh_key: str) -> None:
    """SSH into the robot and report details for legacy and current calibration paths."""
    print("\n--- 6. OT-2 Calibration Paths Check ---")
    settings = OT2SSHSettings.from_config(
        Config,
        robot_ip=robot_ip,
        identity_file=ssh_key,
    )
    
    paths_to_check = [
        "/data/deck_calibration.json",
        "/data/robot/deck_calibration.json",
        "/data/robot/pipettes",
        "/data/tip_lengths",
        "/data/pipettes"
    ]
    
    check_script = (
        "for path in " + " ".join(f"'{p}'" for p in paths_to_check) + "; do "
        "  echo \"Path: $path\"; "
        "  if [ -e \"$path\" ]; then "
        "    if [ -d \"$path\" ]; then "
        "      echo \"  Status: Exists (Directory)\"; "
        "      ls -lah \"$path\" | head -n 10 | sed 's/^/  /'; "
        "    else "
        "      mod_time=$(stat -c '%y' \"$path\" 2>/dev/null || stat -f '%Sm' \"$path\" 2>/dev/null || echo 'unknown'); "
        "      echo \"  Status: Exists (File), Modified: $mod_time\"; "
        "      ls -lah \"$path\" | sed 's/^/  /'; "
        "    fi; "
        "  else "
        "    echo \"  Status: Missing\"; "
        "  fi; "
        "  echo \"---\"; "
        "done"
    )
    
    cmd = settings.ssh_command(check_script, connect_timeout=15)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode == 0:
            print("Remote path check completed successfully:\n")
            print(res.stdout)
            
            # Warn interpretation
            has_legacy = "/data/deck_calibration.json\n  Status: Exists" in res.stdout
            has_current = "/data/robot/deck_calibration.json\n  Status: Exists" in res.stdout
            
            if not has_legacy and has_current:
                print("NOTE: opentrons_execute reported that the legacy /data/deck_calibration.json file was not found,")
                print("but this robot stores calibration data under the current path: /data/robot/deck_calibration.json.")
                print("Since the current calibration file exists, the robot has calibration data.")
            elif not has_legacy and not has_current:
                print("WARNING: Deck calibration files were not found at either legacy (/data/deck_calibration.json)")
                print("or current (/data/robot/deck_calibration.json) paths. Calibration may be missing.")
        else:
            print(f"FAIL: SSH command to check paths failed with code {res.returncode}")
            print(f"Error output: {res.stderr.strip()}")
    except Exception as e:
        print(f"FAIL: Failed to run calibration path checks: {e}")

if __name__ == "__main__":
    main()
