#!/usr/bin/env python3
"""
OT-2 Protocol: Tube Rack Centering and Dimension Verification
==============================================================
Verifies the alignment, nozzle centering, and Z depth of the custom
3D-printed 20 mL, 8-vial tube rack, using a single active nozzle (SINGLE mode)
of an 8-channel pipette to check all 8 wells sequentially.

RUN-MODE FLAGS (set in the Opentrons App via Runtime Parameters, or override the
DEFAULT_* constants below for simulation)
------------------------------------------------------------------------------
  Run | dry_run_top_only | vials_present | wall_touch_check | Purpose
  ----+------------------+---------------+------------------+-----------------------
   1  |   True           |   True        |   False          | Tops only, vials loaded:
      |                  |               |                  | idle-nozzle clearance check
   2  |   False          |   False       |   True (opt.)    | Descend into EMPTY holes;
      |                  |               |                  | touch_tip bore probe
   3  |   False          |   True        |   False          | Descend + observable mix
      |                  |               |                  | in water-filled vials

SAFETY
------
- Pre-flight validation (_preflight) runs BEFORE any motion. It refuses to move if
  the config is unsafe OR the loaded labware does not match the expected geometry
  (catches the wrong/edited definition being imported into the App).
- Interlock: wall_touch_check requires vials_present = False. touch_tip drives the
  tip to the well wall and must NEVER run against glass vials.
- minimum_z_height forces a travel-arc clearance over the vial rims.
- The descent into each well uses an explicit slow speed= so it can be e-stopped.
  (ProtocolContext.max_speeds was removed after API 2.14; not available at 2.20.)
"""

from opentrons import protocol_api
from opentrons.protocol_api import SINGLE

metadata = {
    "protocolName": "OT-2 Custom Tube Rack Centering & Dimension Verification",
    "author": "Antigravity AI Agent",
    "description": (
        "Verifies physical dimensions, nozzle centering, and Z-depth clearances "
        "of a custom 3D-printed 8-vial tuberack using single-nozzle mode."
    ),
    "apiLevel": "2.20",  # Requires 2.20+ for SINGLE layout; 2.18+ for Runtime Parameters
}

# --- Runtime-parameter DEFAULTS (operator overrides these in the App per run) ---
DEFAULT_DRY_RUN_TOP_ONLY = True    # tops only: no descent, mix, or wall-touch
DEFAULT_VIALS_PRESENT    = True    # glass vials physically in the rack (SAFE DEFAULT)
DEFAULT_WALL_TOUCH_CHECK = False   # touch_tip bore probe (REQUIRES vials_present = False)
DEFAULT_WELLS_TO_TEST    = "all"   # "all" | "a1" | "corners" — subset for a cautious first run
DEFAULT_BOTTOM_CLEARANCE_MM = 15.0 # height above well bottom for descent/mix (keep below liquid)
DEFAULT_MIX_REPS = 3
DEFAULT_MIX_VOLUME = 150.0         # uL (<= pipette max of 300)

# --- Engineering constants (not operator-facing) ---
MIX_RATE = 0.3               # flow-rate multiplier for the mix (slow = observable)
ASPIRATE_FLOW_RATE = 50.0    # uL/s during the water run (default ~150)
DISPENSE_FLOW_RATE = 50.0
MOVEMENT_SPEED   = 50.0      # default gantry travel speed (mm/s)
TRAVEL_Z_MIN_MM  = 75.0      # minimum arc height between wells (clears the 60 mm rims)
DESCENT_SPEED    = 10.0      # per-move speed (mm/s) for the slow descent into each well
PAUSE_DELAY_SECONDS = 2.0
TOUCH_RADIUS   = 0.8         # fraction of well radius for touch_tip (<1 stays inside the bore)
TOUCH_V_OFFSET = -5.0        # mm below the well top when touching
TOUCH_SPEED    = 20.0        # mm/s lateral speed during touch_tip

# --- Expected geometry of the custom rack (the loaded definition is checked vs these) ---
EXPECTED_LOAD_NAME      = "tuberack_3dprint_20ml_8vials_v1"
EXPECTED_WELL_COUNT     = 8
EXPECTED_DIAMETER_MM    = 28.0
EXPECTED_DEPTH_MM       = 55.0
EXPECTED_ROW_SPACING_MM = 34.0
EXPECTED_COL_SPACING_MM = 31.0
EXPECTED_LABWARE_TOP_MM = 60.0
GEOMETRY_TOLERANCE_MM   = 0.5

# Full column-major visiting order: A1, B1, A2, B2, A3, B3, A4, B4
_FULL_WELL_ORDER = ["A1", "B1", "A2", "B2", "A3", "B3", "A4", "B4"]


def add_parameters(parameters: protocol_api.ParameterContext):
    """Expose the run-mode flags + key tunables as App-selectable Runtime Parameters."""
    parameters.add_bool(
        variable_name="dry_run_top_only",
        display_name="Dry run (tops only)",
        description="Visit well tops only - no descent, mix, or wall-touch.",
        default=DEFAULT_DRY_RUN_TOP_ONLY,
    )
    parameters.add_bool(
        variable_name="vials_present",
        display_name="Glass vials loaded",
        description="Whether glass vials are physically in the rack. Keep on unless the rack is empty.",
        default=DEFAULT_VIALS_PRESENT,
    )
    parameters.add_bool(
        variable_name="wall_touch_check",
        display_name="Bore probe (EMPTY rack only)",
        description="touch_tip the well walls to check the printed bore. Requires vials NOT loaded.",
        default=DEFAULT_WALL_TOUCH_CHECK,
    )
    parameters.add_str(
        variable_name="wells_to_test",
        display_name="Wells to test",
        description="Limit the run to a subset for a cautious first physical run.",
        choices=[
            {"display_name": "All 8 wells", "value": "all"},
            {"display_name": "A1 only", "value": "a1"},
            {"display_name": "Corners (A1, A4, B1, B4)", "value": "corners"},
        ],
        default=DEFAULT_WELLS_TO_TEST,
    )
    parameters.add_float(
        variable_name="bottom_clearance_mm",
        display_name="Bottom clearance",
        description="Height above the well bottom for descent/mix. Keep below the liquid line.",
        default=DEFAULT_BOTTOM_CLEARANCE_MM,
        minimum=0.0,
        maximum=50.0,
        unit="mm",
    )
    parameters.add_int(
        variable_name="mix_reps",
        display_name="Mix repetitions",
        description="Number of mix cycles in the water run.",
        default=DEFAULT_MIX_REPS,
        minimum=1,
        maximum=10,
    )
    parameters.add_float(
        variable_name="mix_volume_ul",
        display_name="Mix volume",
        description="Volume per mix cycle (must be <= pipette max of 300).",
        default=DEFAULT_MIX_VOLUME,
        minimum=10.0,
        maximum=300.0,
        unit="uL",
    )


def _resolve_wells(selection: str) -> list:
    """Map the wells_to_test selection to an ordered list of well names."""
    if selection == "a1":
        return ["A1"]
    if selection == "corners":
        return ["A1", "B1", "A4", "B4"]
    return list(_FULL_WELL_ORDER)


def _preflight(protocol, tuberack, pipette, params):
    """Validate config + loaded-labware geometry BEFORE any motion. Raise to abort."""
    errors = []

    # Safety interlock: never touch_tip against glass.
    if params.wall_touch_check and params.vials_present:
        errors.append(
            "Bore probe (wall_touch_check) requires vials_present = False; "
            "touch_tip must never run against glass vials."
        )
    # Bore probe only happens at depth.
    if params.wall_touch_check and params.dry_run_top_only:
        errors.append("Bore probe requires dry_run_top_only = False (it runs at depth).")

    # Labware identity + geometry cross-check (catches a wrong/edited App import).
    if tuberack.load_name != EXPECTED_LOAD_NAME:
        errors.append(f"Loaded labware '{tuberack.load_name}' != expected '{EXPECTED_LOAD_NAME}'.")
    n_wells = len(tuberack.wells())
    if n_wells != EXPECTED_WELL_COUNT:
        errors.append(f"Labware has {n_wells} wells, expected {EXPECTED_WELL_COUNT}.")
    a1 = tuberack["A1"]
    if a1.diameter is None or abs(a1.diameter - EXPECTED_DIAMETER_MM) > GEOMETRY_TOLERANCE_MM:
        errors.append(f"A1 diameter {a1.diameter} != {EXPECTED_DIAMETER_MM} +/- {GEOMETRY_TOLERANCE_MM} mm.")
    if abs(a1.depth - EXPECTED_DEPTH_MM) > GEOMETRY_TOLERANCE_MM:
        errors.append(f"A1 depth {a1.depth} != {EXPECTED_DEPTH_MM} +/- {GEOMETRY_TOLERANCE_MM} mm.")
    row_spacing = round(tuberack["A1"].center().point.y - tuberack["B1"].center().point.y, 2)
    col_spacing = round(tuberack["A2"].center().point.x - tuberack["A1"].center().point.x, 2)
    if abs(row_spacing - EXPECTED_ROW_SPACING_MM) > GEOMETRY_TOLERANCE_MM:
        errors.append(f"Row spacing {row_spacing} != {EXPECTED_ROW_SPACING_MM} mm.")
    if abs(col_spacing - EXPECTED_COL_SPACING_MM) > GEOMETRY_TOLERANCE_MM:
        errors.append(f"Col spacing {col_spacing} != {EXPECTED_COL_SPACING_MM} mm.")

    # Numeric range / safety checks.
    if not (0.0 <= params.bottom_clearance_mm < EXPECTED_DEPTH_MM):
        errors.append(
            f"bottom_clearance_mm {params.bottom_clearance_mm} must be in [0, {EXPECTED_DEPTH_MM})."
        )
    if TRAVEL_Z_MIN_MM < EXPECTED_LABWARE_TOP_MM:
        errors.append(f"TRAVEL_Z_MIN_MM {TRAVEL_Z_MIN_MM} < labware top {EXPECTED_LABWARE_TOP_MM} mm.")
    if params.mix_volume_ul > pipette.max_volume:
        errors.append(f"mix_volume_ul {params.mix_volume_ul} > pipette max {pipette.max_volume} uL.")
    if not (0.0 < TOUCH_RADIUS < 1.0):
        errors.append(f"TOUCH_RADIUS {TOUCH_RADIUS} must be in (0, 1).")

    if errors:
        raise RuntimeError(
            "PRE-FLIGHT VALIDATION FAILED - no motion performed:\n  - " + "\n  - ".join(errors)
        )
    protocol.comment("Pre-flight validation passed: config + labware geometry OK.")


def run(protocol: protocol_api.ProtocolContext):
    params = protocol.params

    # 1. Load labware + pipette
    tiprack = protocol.load_labware("opentrons_96_tiprack_300ul", 1)
    # On the robot this resolves to the labware imported via the Opentrons App
    # (namespace custom_beta, loadName below) - NOT a local file path.
    tuberack = protocol.load_labware(
        "tuberack_3dprint_20ml_8vials_v1", 2, namespace="custom_beta", version=1
    )
    pipette = protocol.load_instrument("p300_multi_gen2", "right", tip_racks=[tiprack])
    pipette.configure_nozzle_layout(style=SINGLE, start="H1", tip_racks=[tiprack])

    # 2. PRE-FLIGHT VALIDATION (refuses to move on bad config / wrong labware)
    _preflight(protocol, tuberack, pipette, params)

    # 3. Motion / liquid-handling configuration
    pipette.default_speed = MOVEMENT_SPEED
    if params.vials_present and not params.dry_run_top_only:
        pipette.flow_rate.aspirate = ASPIRATE_FLOW_RATE
        pipette.flow_rate.dispense = DISPENSE_FLOW_RATE

    protocol.comment("=== Tube Rack Verification Protocol Started ===")
    protocol.comment(
        f"Flags: dry_run_top_only={params.dry_run_top_only}, "
        f"vials_present={params.vials_present}, wall_touch_check={params.wall_touch_check}"
    )
    if params.dry_run_top_only:
        protocol.comment("SAFETY MODE: tops only. No descent, mix, or wall-touch will occur.")
    else:
        protocol.comment(f"DESCENT MODE: descend to {params.bottom_clearance_mm} mm above bottom.")
        if params.wall_touch_check:
            protocol.comment("EMPTY-RACK MODE: touch_tip bore/centering probe enabled.")
        if params.vials_present:
            protocol.comment("WATER MODE: observable mix enabled. Keep clearance below liquid line.")

    well_order = _resolve_wells(params.wells_to_test)
    protocol.comment(f"Wells to test ({params.wells_to_test}): {well_order}")

    # Gate: hold before ANY motion until the operator confirms the deck.
    if protocol.is_simulating():
        protocol.delay(seconds=PAUSE_DELAY_SECONDS)
    else:
        protocol.pause("Deck loaded and e-stop in hand? Resume to begin motion.")

    # 4. Pick up exactly ONE tip (nozzle H1), reused for the whole run
    pipette.pick_up_tip()

    # 5. Visit each selected well sequentially
    for well_name in well_order:
        well = tuberack[well_name]
        protocol.comment(f"--- Testing Well: {well_name} ---")

        # Step A: Travel to the well TOP, forcing a high arc that clears the vial rims.
        protocol.comment(f"Moving to top of well {well_name} (arc >= {TRAVEL_Z_MIN_MM} mm)...")
        pipette.move_to(well.top(), minimum_z_height=TRAVEL_Z_MIN_MM)

        if protocol.is_simulating():
            protocol.delay(seconds=PAUSE_DELAY_SECONDS)
        else:
            protocol.pause(
                f"Nozzle centered over {well_name} (TOP). Confirm active-nozzle X/Y "
                "alignment and that idle nozzles do not collide. Resume to continue."
            )

        # Step B: Descent-mode actions (gated behind dry_run_top_only = False)
        if not params.dry_run_top_only:
            protocol.comment(
                f"Descending to bottom + {params.bottom_clearance_mm} mm in {well_name} "
                f"at {DESCENT_SPEED} mm/s..."
            )
            pipette.move_to(well.bottom(z=params.bottom_clearance_mm), speed=DESCENT_SPEED)

            if protocol.is_simulating():
                protocol.delay(seconds=PAUSE_DELAY_SECONDS)
            else:
                protocol.pause(
                    f"Nozzle at bottom + {params.bottom_clearance_mm} mm in {well_name}. "
                    "Confirm active-nozzle depth/Z-clearance. Resume to continue."
                )

            # Step B1: EMPTY-rack bore/centering probe (never against glass).
            if params.wall_touch_check:
                protocol.comment(
                    f"Bore probe in {well_name}: touch_tip radius={TOUCH_RADIUS}, "
                    f"v_offset={TOUCH_V_OFFSET} mm..."
                )
                pipette.touch_tip(well, radius=TOUCH_RADIUS, v_offset=TOUCH_V_OFFSET, speed=TOUCH_SPEED)
                if protocol.is_simulating():
                    protocol.delay(seconds=PAUSE_DELAY_SECONDS)
                else:
                    protocol.pause(
                        f"Bore probe done in {well_name}. Note whether the tip contacted the "
                        "wall early/late vs the 28 mm definition. Resume to continue."
                    )

            # Step B2: Observable mix in water (vials present).
            if params.vials_present:
                protocol.comment(
                    f"Test mix in {well_name}: {params.mix_reps} x {params.mix_volume_ul} uL "
                    f"at rate {MIX_RATE}..."
                )
                pipette.mix(
                    params.mix_reps,
                    params.mix_volume_ul,
                    well.bottom(z=params.bottom_clearance_mm),
                    rate=MIX_RATE,
                )
                if protocol.is_simulating():
                    protocol.delay(seconds=PAUSE_DELAY_SECONDS)
                else:
                    protocol.pause(f"Finished mix test in {well_name}. Resume to move to next well.")

    # 6. Drop the single tip at the end of the run
    protocol.comment("Dropping the single verification tip into trash...")
    pipette.drop_tip()
    protocol.comment("=== Tube Rack Verification Protocol Completed ===")
