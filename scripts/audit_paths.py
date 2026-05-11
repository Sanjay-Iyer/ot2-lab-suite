import os
import re
import sys
import argparse
from pathlib import Path

# --- Configuration ---
# Discover root relative to this script
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Patterns to flag (Absolute and Suspicious Relative)
# We use patterns that require a slash NOT followed by n, t, r (escape sequences) or whitespace
SUSPICIOUS_PATTERNS = [
    r"[A-Z]:[\\/][^ntr\s]",   # Windows absolute paths (e.g. C:\c)
    r"^/home/[^ntr\s]",       # Linux absolute home
    r"^/Users/[^ntr\s]",      # Mac absolute home
    r"^/mnt/[^ntr\s]",        # Mount points
    r"^/tmp/[^ntr\s]",        # Temp dirs
    r"Desktop",               # User-specific desktop
    r"Downloads",             # User-specific downloads
    r"Documents",             # User-specific documents
    r"os\.getcwd\(\)",        # CWD dependency
    r"configs/user",          # Hardcoded relative config path
    r"robot_data/data",       # Hardcoded relative data path
    r"last_printing_run\.yaml" # Hardcoded specific file
]

# Files to ignore
IGNORE_DIRS = {".git", "__pycache__", ".venv", "env", "node_modules", "logs", "robot_data", "archive"}
IGNORE_EXTS = {".log", ".pyc", ".png", ".jpg", ".csv", ".json"}

def audit_files(strict=False):
    print(f"--- Hardcoded Path Audit (Root: {PROJECT_ROOT}, Strict: {strict}) ---")
    suspicious_count = 0
    
    # Compile regex
    regex = re.compile("|".join(SUSPICIOUS_PATTERNS), re.IGNORECASE)

    for root, dirs, files in os.walk(PROJECT_ROOT):
        # Filter directories
        if not strict and "tests" in dirs:
            dirs.remove("tests")
            
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        
        for file in files:
            path = Path(root) / file
            
            # Skip itself
            if path == Path(__file__).resolve():
                continue
            
            # Skip MD files if not strict
            if not strict and path.suffix == ".md":
                continue
                
            # Filter extensions
            if path.suffix in IGNORE_EXTS:
                continue
                
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                    for i, line in enumerate(lines):
                        # Skip comments (mostly)
                        if line.strip().startswith("#"):
                            continue
                            
                        match = regex.search(line)
                        if match:
                            # Skip paths that are relative to PROJECT_ROOT or defined in paths.py
                            if "src\\utils\\paths.py" in str(path) or "src/utils/paths.py" in str(path):
                                if "=" in line and "PROJECT_ROOT /" in line:
                                    continue
                            
                            # Skip if it's already using a path utility properly
                            if ("USER_CONFIG_DIR" in line or "AGENT_LOG_DIR" in line or "DEFAULT_CONFIG_DIR" in line) and "configs/user" not in line:
                                continue
                                
                            # Explicitly skip URLs
                            if "http://" in line.lower() or "https://" in line.lower():
                                continue

                            rel_path = path.relative_to(PROJECT_ROOT)
                            print(f"[FLAG] {rel_path}:{i+1} -> {line.strip()}")
                            suspicious_count += 1
            except Exception as e:
                print(f"[ERROR] Could not read {path}: {e}")

    print("-" * 50)
    if suspicious_count == 0:
        print("SUCCESS: No suspicious hardcoded paths found.")
    else:
        print(f"WARNING: Found {suspicious_count} suspicious lines. Please review.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit project for hardcoded paths")
    parser.add_argument("--strict", action="store_true", help="Include tests and documentation in the scan")
    args = parser.parse_args()
    
    audit_files(strict=args.strict)
