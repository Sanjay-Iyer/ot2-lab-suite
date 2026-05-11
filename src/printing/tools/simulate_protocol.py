#!/usr/bin/env python3
"""
Monkey-patching Simulator Runner
================================

Fixes Pydantic v2 compatibility issues in the Opentrons library by
patching 'pydantic.BaseSettings' before importing opentrons.
"""

import sys
import os
import pathlib
from pathlib import Path

# 1. Monkey-patch Pydantic for OT-2 library compatibility
try:
    import pydantic
    if pydantic.__version__.startswith("2."):
        import pydantic_settings
        pydantic.BaseSettings = pydantic_settings.BaseSettings
        sys.modules["pydantic.env_settings"] = pydantic_settings
except ImportError:
    pass

# 2. Now import opentrons
from opentrons.simulate import simulate, format_runlog

def run_simulation(protocol_path_str: str):
    """Runs a high-fidelity Opentrons simulation with proper environment setup."""
    protocol_path = Path(protocol_path_str).resolve()
    
    if not protocol_path.exists():
        print(f"Error: Protocol file not found at {protocol_path}")
        return

    # We need to be in the printing suite root so protocols can find 'configs/'
    # The protocol is usually in src/printing/protocols/
    # So we move to src/printing/
    suite_dir = protocol_path.parent.parent
    os.chdir(str(suite_dir))
    
    print(f"--- Starting Simulation ---")
    print(f"Protocol: {protocol_path.name}")
    print(f"Environment: {suite_dir}")
    
    # Use context manager for professional file handling
    with protocol_path.open() as protocol_file:
        runlog, _bundle = simulate(protocol_file)
        
    print("Simulation PASSED.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python simulate_protocol.py <path_to_protocol>")
        sys.exit(1)
    run_simulation(sys.argv[1])
