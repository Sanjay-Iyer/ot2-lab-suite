"""
Paper print QUICK TEST, protocol v4 (OT-2 Python Protocol API 2.15).

The smallest real-hardware check of the printing rig. It does NOT dilute and does
NOT touch the plate or the P300. One P20 tip picks up, aspirates a few microliters
straight from ONE vial, and prints a handful of spots onto the paper. That exercises
everything a full run depends on — connection + upload at API 2.15, custom labware
loading, P20 tip pickup/return, the reach down into the 55 mm vial, dispense at the
configured paper height — in well under a minute of motion.

Use this first, before the full v3 run, to confirm the bench is wired correctly.
Default source is WATER so a failed first attempt wastes no nanoparticle stock.

The CONFIG block is replaced by scripts/build_vial_dilution_print.py. Edit the
workflow YAML (configs/printing/*_v4.yaml), not a generated protocol.
"""
from __future__ import annotations

from opentrons import protocol_api


metadata = {
    "protocolName": "Paper Print Quick Test v4 (OT-2 API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Minimal P20-only print: aspirate from one vial and dab a few spots onto "
        "paper. No dilution, no plate, no P300. A fast bring-up check."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


# Run modes (the build script rewrites these from the YAML's run_modes / CLI flags).
# API 2.15 has no runtime parameters, so the modes are baked into the file.
DEFAULT_DRY_RUN = True      # pre-flight only, no motion
DEFAULT_DO_DILUTION = False # v4 never dilutes; kept so the build flag substitution is uniform
DEFAULT_DO_PRINT = True     # do the print


# >>> CONFIG START >>>
CONFIG = {
    "protocol_version": 4,
    "deck": {
        "tuberack": {
            "slot": 7,
            "load_name": "tuberack_3dprint_20ml_8vials_v2",
            "namespace": "custom_beta",
            "version": 1,
        },
        "paper": {
            "slot": 5,
            "load_name": "corning_96_wellplate_360ul_custom",
            "namespace": "custom_beta",
            "version": 1,
        },
        "tiprack_p20": {"slot": 9, "load_name": "opentrons_96_tiprack_20ul"},
    },
    "pipette": {"name": "p20_single_gen2", "mount": "left"},
    "source": {"vial": "A1", "material": "water", "aspirate_height_mm": 4.0},
    "print": {
        "volume_ul": 5.0,
        "paper_column": 1,
        "rows": ["A", "B", "C", "D"],
        "z_mm": 1.0,
        "blow_out": True,
        "post_dispense_delay_s": 0.3,
        "tip": "A1",
    },
    "flow_rates": {"aspirate": 3.0, "dispense": 3.0},
    "tips": {"return_tips": False},
    "safety": {
        "expected_tuberack_load_name": "tuberack_3dprint_20ml_8vials_v2",
        "expected_well_count": 8,
        "p20_max_volume_ul": 20.0,
    },
}
# <<< CONFIG END <<<


def _load_labware(protocol, spec):
    kwargs = {}
    if spec.get("namespace"):
        kwargs["namespace"] = spec["namespace"]
    if spec.get("version") is not None:
        kwargs["version"] = int(spec["version"])
    return protocol.load_labware(spec["load_name"], str(spec["slot"]), **kwargs)


def _preflight(protocol, labware, p20):
    errors = []
    deck = CONFIG["deck"]
    src = CONFIG["source"]
    pr = CONFIG["print"]
    safety = CONFIG["safety"]

    # Deck slots must match the physical bench (tuberack 7, paper 5, P20 tips 9).
    for role, expected in (("tuberack", 7), ("paper", 5), ("tiprack_p20", 9)):
        actual = int(deck[role]["slot"])
        if actual != expected:
            errors.append(f"deck.{role} must be slot {expected}, got {actual}")

    if requirements != {"robotType": "OT-2", "apiLevel": "2.15"}:
        errors.append("protocol requirements must be OT-2 / API 2.15")

    if p20.name != CONFIG["pipette"]["name"]:
        errors.append(f"left pipette must be {CONFIG['pipette']['name']}, got {p20.name}")

    tuberack = labware["tuberack"]
    if tuberack.load_name != safety["expected_tuberack_load_name"]:
        errors.append(
            f"tuberack is {tuberack.load_name!r}; expected "
            f"{safety['expected_tuberack_load_name']!r}"
        )
    if len(tuberack.wells()) != int(safety["expected_well_count"]):
        errors.append(
            f"tuberack has {len(tuberack.wells())} wells; expected "
            f"{safety['expected_well_count']}"
        )
    height = float(src["aspirate_height_mm"])
    if src["vial"] not in tuberack.wells_by_name():
        errors.append(f"source vial {src['vial']} is absent from the rack")
    else:
        # Only reachable when the vial exists; guards against a KeyError above.
        depth = float(tuberack[src["vial"]].depth)
        if not (0 < height < depth):
            errors.append(
                f"source.aspirate_height_mm {height} must be > 0 and < vial depth {depth}"
            )

    volume = float(pr["volume_ul"])
    if not (0 < volume <= float(safety["p20_max_volume_ul"])):
        errors.append(
            f"print.volume_ul {volume} must be in (0, {safety['p20_max_volume_ul']}]"
        )

    tip = pr["tip"]
    if tip not in labware["tiprack_p20"].wells_by_name():
        errors.append(f"print.tip {tip} is outside the slot-9 tip rack")

    col = int(pr["paper_column"])
    paper_names = labware["paper"].wells_by_name()
    for row in pr["rows"]:
        well = f"{row}{col}"
        if well not in paper_names:
            errors.append(f"paper well {well} does not exist")

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    protocol.comment("Pre-flight validation passed: config + labware geometry OK.")


def _set_flow_rates(p20):
    rates = CONFIG.get("flow_rates", {})
    if rates.get("aspirate"):
        p20.flow_rate.aspirate = float(rates["aspirate"])
    if rates.get("dispense"):
        p20.flow_rate.dispense = float(rates["dispense"])


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    labware = {
        role: _load_labware(protocol, deck[role])
        for role in ("tuberack", "paper", "tiprack_p20")
    }
    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )

    _preflight(protocol, labware, p20)

    src = CONFIG["source"]
    pr = CONFIG["print"]
    col = int(pr["paper_column"])
    rows = list(pr["rows"])
    volume = float(pr["volume_ul"])
    return_tips = bool(CONFIG["tips"].get("return_tips", False))

    protocol.comment("=== Paper Print Quick Test V4 Started ===")
    protocol.comment(
        f"Flags: dry_run={DEFAULT_DRY_RUN}, do_print={DEFAULT_DO_PRINT}"
    )
    protocol.comment(
        f"Plan: P20 tip {pr['tip']} -> {volume:g} uL {src['material']} from vial "
        f"{src['vial']} -> {len(rows)} paper spots "
        f"{rows[0]}{col}..{rows[-1]}{col}."
    )

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: pre-flight only; no robot motion or liquid handling.")
        protocol.comment("=== Paper Print Quick Test V4 Completed (dry run) ===")
        return

    if not DEFAULT_DO_PRINT:
        protocol.comment("do_print is False; nothing to do.")
        protocol.comment("=== Paper Print Quick Test V4 Completed ===")
        return

    _set_flow_rates(p20)

    vial = labware["tuberack"][src["vial"]]
    height = float(src["aspirate_height_mm"])
    z = float(pr["z_mm"])
    blow_out = bool(pr.get("blow_out", True))
    dwell = float(pr.get("post_dispense_delay_s", 0.0) or 0.0)

    # One tip for the whole test — the source is a single material, so there is no
    # carryover to worry about, and reusing the tip keeps the check fast.
    p20.pick_up_tip(labware["tiprack_p20"][pr["tip"]])
    protocol.comment(f"P20 picked tip {pr['tip']}.")
    for row in rows:
        target = labware["paper"][f"{row}{col}"]
        protocol.comment(
            f"P20: {volume:g} uL {src['material']} -> paper {row}{col} at z={z} mm."
        )
        p20.aspirate(volume, vial.bottom(height))
        p20.dispense(volume, target.bottom(z))
        if blow_out:
            p20.blow_out(target.bottom(z))
        if dwell > 0:
            protocol.delay(seconds=dwell)
    if return_tips:
        p20.return_tip()
    else:
        p20.drop_tip()
    protocol.comment(
        f"Quick test complete: {len(rows)} spot(s) printed on paper column {col}."
    )
    protocol.comment("=== Paper Print Quick Test V4 Completed ===")
