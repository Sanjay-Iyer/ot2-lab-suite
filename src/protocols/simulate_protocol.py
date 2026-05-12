import sys
import argparse
import subprocess
from pathlib import Path

def simulate(protocol_path: str):
    """
    Simulates an Opentrons protocol safely across platforms.
    Resolves the local path, checks existence, sets cwd to parent directory,
    and passes only the filename to avoid Windows absolute path injection errors.
    """
    p_path = Path(protocol_path).resolve()
    
    if not p_path.exists():
        print(f"ERROR: Protocol file not found: {p_path}")
        sys.exit(1)
        
    if not p_path.is_file():
        print(f"ERROR: Protocol path is not a file: {p_path}")
        sys.exit(1)

    cmd = [sys.executable, "-m", "opentrons.simulate", p_path.name]
    
    print(f"--- Simulating Protocol ---")
    print(f"File: {p_path.name}")
    print(f"Dir : {p_path.parent}")
    print(f"Cmd : {' '.join(cmd)}")
    print("-" * 27)
    
    try:
        # Run subprocess with cwd set to the parent directory
        result = subprocess.run(cmd, cwd=str(p_path.parent), capture_output=True, text=True)
        
        if result.returncode == 0:
            print("SIMULATION SUCCESS\n")
            print(result.stdout)
            sys.exit(0)
        else:
            print("SIMULATION FAILED\n")
            print(result.stderr)
            sys.exit(result.returncode)
            
    except FileNotFoundError:
        print("ERROR: Could not find 'conda' or simulator executable.")
        print("Ensure you are running this from an environment where conda is available.")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Unexpected simulation error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate OT-2 Protocol Locally")
    parser.add_argument("protocol", help="Path to the protocol python file")
    args = parser.parse_args()
    
    simulate(args.protocol)
