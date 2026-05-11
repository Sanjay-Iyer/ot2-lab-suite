#!/usr/bin/env python3
"""
Mock AI-Agent Printing Simulation
==================================

This script demonstrates the full agent workflow WITHOUT needing an OpenAI key
or a physical robot. It:

  1. Generates an `agent_protocol_config.json` (what the LangChain tool would produce)
  2. Runs `printing_protocol.py` through `opentrons.simulate`
  3. Prints the simulated runlog so you can see every pipetting step

Usage:
    python tools/test_printing_simulation.py
"""

import json
import pathlib
import sys

# ---------------------------------------------------------------------------
# Step 1: Generate a mock agent configuration
#   This is exactly what the `configure_printing_protocol` tool in tools.py
#   would produce when the AI agent calls it.
# ---------------------------------------------------------------------------

mock_config = {
    "total_mix_volume": 200,
    "jobs": [
        {
            "fraction": 1.00,
            "print_volume": 10,
            "mix_well": "A1",
            "print_well": "A1",
        },
        {
            "fraction": 0.75,
            "print_volume": 10,
            "mix_well": "A2",
            "print_well": "A2",
        },
        {
            "fraction": 0.50,
            "print_volume": 10,
            "mix_well": "A3",
            "print_well": "A3",
        },
        {
            "fraction": 0.25,
            "print_volume": 10,
            "mix_well": "A4",
            "print_well": "A4",
        },
        {
            "fraction": 0.10,
            "print_volume": 10,
            "mix_well": "A5",
            "print_well": "A5",
        },
    ],
}

# Write the config file that the protocol expects
config_path = pathlib.Path("agent_protocol_config.json")
with open(config_path, "w") as f:
    json.dump(mock_config, f, indent=4)

print("=" * 70)
print("  MOCK AI-AGENT PRINTING SIMULATION")
print("=" * 70)
print(f"\n✓ Generated config: {config_path.resolve()}")
print(f"  → {len(mock_config['jobs'])} print jobs")
print(f"  → Total mix volume: {mock_config['total_mix_volume']} µL")
concentrations = [f"{j['fraction']*100:.0f}%" for j in mock_config['jobs']]
print(f"  → Concentrations: {concentrations}")
print()

# ---------------------------------------------------------------------------
# Step 2: Run the Opentrons simulator
# ---------------------------------------------------------------------------

print("Running Opentrons simulator...")
print("-" * 70)

try:
    from opentrons.simulate import simulate, format_runlog

    protocol_path = pathlib.Path("protocols/printing_protocol.py")

    if not protocol_path.is_file():
        print(f"ERROR: Protocol not found at {protocol_path}")
        print(f"  Make sure you run this from the opentrons/ project root:")
        print(f"  cd /home/sanjay/opentrons_home/opentrons")
        print(f"  python tools/test_printing_simulation.py")
        sys.exit(1)

    with open(protocol_path, "r") as fh:
        runlog, _bundle = simulate(fh, str(protocol_path))

    transcript = format_runlog(runlog)

    print("\n✓ SIMULATION SUCCESSFUL")
    print("=" * 70)
    print("FULL TRANSCRIPT:")
    print("=" * 70)
    print(transcript)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    comments = [
        entry.get("payload", {}).get("text", "")
        for entry in runlog
        if entry.get("payload", {}).get("text", "").startswith("Agent Task")
    ]
    for c in comments:
        print(f"  • {c}")
    print(f"\n  Total runlog entries: {len(runlog)}")
    print(f"  Agent tasks executed: {len(comments)}")
    print("\n✓ No robot connection needed — this was a pure simulation.")

except ImportError:
    print("ERROR: The 'opentrons' package is not installed.")
    print("  Install it with: pip install opentrons")
    sys.exit(1)
except Exception as e:
    print(f"\n✗ SIMULATION FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(2)
finally:
    # Clean up the temp config
    # (Keep it around so you can inspect it)
    print(f"\n  Config file kept at: {config_path.resolve()}")
