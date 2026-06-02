#!/usr/bin/env python3
"""
OT-2 Protocol: Tube Rack Centering and Dimension Verification
==============================================================
Verifies the alignment, nozzle centering, and Z depth of the custom
3D-printed 20 mL, 8-vial tube rack.

This script uses a single active nozzle of an 8-channel pipette (SINGLE mode)
to check alignment on all 8 wells sequentially.

Safety Features:
- DRY_RUN_TOP_ONLY (default: True): Visits well tops only. Useful for first-pass
  nozzle centering and checking for collisions of idle nozzles.
- BOTTOM_CLEARANCE_MM: Tunable offset above well bottom. Initially set high
  (e.g., 15.0 mm) to prevent crashing on deep descent, then lowered to 0.0.
  Ensure this clearance is below the filled liquid line to observe aspiration.
- MOVEMENT_SPEED: Controls the travel speed of the pipette gantry.
"""

from opentrons import protocol_api
from opentrons.protocol_api import SINGLE

# Protocol Metadata (Required)
metadata = {
    "protocolName": "OT-2 Custom Tube Rack Centering & Dimension Verification",
    "author": "Antigravity AI Agent",
    "description": (
        "Verifies physical dimensions, nozzle centering, and Z-depth clearances "
        "of a custom 3D-printed 8-vial tuberack using single-nozzle mode."
    ),
    "apiLevel": "2.20",  # Requires 2.20+ for SINGLE layout support on OT-2
}

# --- Tunable Constants (Edit these to adjust safety and behavior) ---
DRY_RUN_TOP_ONLY = True       # Set to False to perform bottom descent and mix tests
BOTTOM_CLEARANCE_MM = 15.0   # Offset in mm above well bottom. MUST be below liquid level for mix test.
MIX_REPS = 3                 # Number of mix reps to confirm liquid interaction
MIX_VOLUME = 150.0           # Mix volume in uL (pipette capacity is 300 uL)
MOVEMENT_SPEED = 50.0        # Slow gantry travel speed (mm/s) for safety
PAUSE_DELAY_SECONDS = 2.0    # Pause delay in simulation (real run pauses for user confirmation)

def run(protocol: protocol_api.ProtocolContext):
    # 1. Load Tiprack and Pipette
    # Place tiprack in Slot 1
    tiprack = protocol.load_labware("opentrons_96_tiprack_300ul", 1)
    
    # Place custom tuberack in Slot 2
    # Loads from: labware/tuberack_3dprint_20ml_8vials_v1.json
    tuberack = protocol.load_labware(
        "tuberack_3dprint_20ml_8vials_v1",
        2,
        namespace="custom_beta",
        version=1
    )
    
    # Load 8-channel pipette (P300 Gen2) on the right mount (standard repo mount)
    pipette = protocol.load_instrument("p300_multi_gen2", "right", tip_racks=[tiprack])
    
    # 2. Configure SINGLE nozzle layout (Partial Tip Pickup)
    # Active nozzle: H1 (the front-most nozzle of the 8-channel head)
    # This limits execution to ONE channel while the other 7 idle nozzles remain high.
    # Note: Idle nozzles are spaced at 9 mm pitch and may extend over adjacent rows/walls.
    pipette.configure_nozzle_layout(style=SINGLE, start="H1", tip_racks=[tiprack])
    
    # Configure speed and safety settings
    pipette.default_speed = MOVEMENT_SPEED
    
    protocol.comment("=== Tube Rack Verification Protocol Started ===")
    if DRY_RUN_TOP_ONLY:
        protocol.comment("SAFETY MODE: DRY_RUN_TOP_ONLY = True. No descent or mixing will occur.")
    else:
        protocol.comment(f"DESCENT MODE: Will descend to {BOTTOM_CLEARANCE_MM} mm above bottom and mix.")
        protocol.comment("Ensure BOTTOM_CLEARANCE_MM is below the vial liquid level to confirm aspiration.")
    
    # 3. Pick up exactly ONE tip (nozzle H1)
    pipette.pick_up_tip()
    
    # Column-major well visiting order: A1, B1, A2, B2, A3, B3, A4, B4
    well_order = ["A1", "B1", "A2", "B2", "A3", "B3", "A4", "B4"]
    
    # 4. Visit each well sequentially
    for well_name in well_order:
        well = tuberack[well_name]
        
        protocol.comment(f"--- Testing Well: {well_name} ---")
        
        # Step A: Move to the well TOP center
        # This keeps the tip dry above the vial so the user can verify X/Y nozzle centering
        protocol.comment(f"Moving to top of well {well_name}...")
        pipette.move_to(well.top())
        
        # Pause for visual inspection of centering and idle nozzles clearance
        if protocol.is_simulating():
            protocol.delay(seconds=PAUSE_DELAY_SECONDS)
        else:
            protocol.pause(
                f"Nozzle centered over {well_name} (TOP). "
                "Confirm active nozzle X/Y alignment and that idle nozzles do not collide. "
                "Resume to continue."
            )
            
        # Step B: If descent is enabled, descend and mix
        if not DRY_RUN_TOP_ONLY:
            # Descend slowly to the bottom clearance position
            # Make sure this height is submerged under the filled water line!
            protocol.comment(f"Descending to bottom + {BOTTOM_CLEARANCE_MM} mm clearance in well {well_name}...")
            pipette.move_to(well.bottom(z=BOTTOM_CLEARANCE_MM))
            
            # Pause for depth inspection
            if protocol.is_simulating():
                protocol.delay(seconds=PAUSE_DELAY_SECONDS)
            else:
                protocol.pause(
                    f"Nozzle at bottom + {BOTTOM_CLEARANCE_MM} mm in {well_name}. "
                    "Confirm active nozzle depth/Z-clearance. "
                    "Resume to test mix (aspiration)."
                )
            
            # Perform verification mix at depth
            protocol.comment(f"Performing test mix: {MIX_REPS} reps x {MIX_VOLUME} uL...")
            pipette.mix(MIX_REPS, MIX_VOLUME, well.bottom(z=BOTTOM_CLEARANCE_MM))
            
            # Brief pause after mixing
            if protocol.is_simulating():
                protocol.delay(seconds=PAUSE_DELAY_SECONDS)
            else:
                protocol.pause(f"Finished mix test in {well_name}. Resume to move to next well.")
                
    # 5. Drop the single tip at the end of the run
    protocol.comment("Dropping the single verification tip into trash...")
    pipette.drop_tip()
    protocol.comment("=== Tube Rack Verification Protocol Completed ===")
