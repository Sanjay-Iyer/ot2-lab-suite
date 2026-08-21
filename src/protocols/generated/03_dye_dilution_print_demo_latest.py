"""Experiment 03 - dye dilution + standard print + one clover (deterministic executor).

MANUAL FALLBACK. No agent, no LLM, no runtime skill. It executes exactly the
CONFIG block below and nothing else.

    configs/experiments/03_dye_dilution_print_demo.yaml   <- edit this
         |  build (validate + flatten with the referenced machine profile)
         v
    this file (CONFIG block regenerated in place)
         |
         v
    src/protocols/generated/03_dye_dilution_print_demo_latest.py  <- uploaded

    python scripts/run_printing_experiment_robot.py dye-demo

Physical sequence:
    1. dye  A11 (BRAND plate, slot 1) -> 20 uL -> B11        fresh tip
    2. water A1 (vial rack, slot 7)   -> 20 uL -> B11  x4    fresh tip each
    3. mix B11                                                fresh tip
    4. print 8 droplets from B11 down paper column 1          fresh tip each
    5. print one four-droplet clover from B11 near B3         fresh tip each

The print cycle (aspirate -> air gap -> move -> dispense -> blow out) and the
clover d1..d4 geometry are copied from the physically validated
01_print_from_vial.py / 02_printing_four_clover.py. Nothing about the liquid
handling is new here.

AIR GAP ON TRANSFERS. The P20 holds 20 uL total, so a 20 uL transfer leaves no
room for a trailing air gap. The transfer step therefore takes the largest air
gap that still fits (which is 0 uL for a full 20 uL transfer) rather than
overfilling the pipette. Printing at 5 uL always keeps its full 1.5 uL gap.
"""
from __future__ import annotations

import math

from opentrons import protocol_api
from opentrons.types import Point


metadata = {
    "protocolName": "Experiment 03 - Dye Dilution + Print Demo (P20, API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "5x dye dilution in a BRAND plate, then standard paper printing and one "
        "four-droplet clover from the diluted well. No serial dilution, no agent."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN = False
DEFAULT_DO_PRINT = True

DROPLET_KEYS = ("d1", "d2", "d3", "d4")


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'protocol_label': 'dye_dilution_print_demo',
  'deck': { 'plate': { 'slot': 1,
                       'load_name': 'brand_96_wellplate_350ul_flat_781662',
                       'namespace': 'custom_beta',
                       'version': 1},
            'vial_rack': { 'slot': 7,
                           'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                           'namespace': 'custom_beta',
                           'version': 1},
            'paper': { 'slot': 11,
                       'load_name': 'paper_print_96_flat',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': {'name': 'p20_single_gen2', 'mount': 'left'},
  'dye_source': {'well': 'A11', 'loaded_volume_ul': 300.0, 'aspirate_height_mm': 1.0},
  'water_source': {'well': 'A1', 'material': 'water', 'aspirate_height_mm': 4.0},
  'dilution': { 'enabled': False,
                'destination_well': 'B11',
                'dye_volume_ul': 60.0,
                'water_volume_ul': 240.0,
                'total_volume_ul': 300.0,
                'transfer_chunk_ul': 20.0,
                'mix_cycles': 5,
                'mix_volume_ul': 15.0,
                'dispense_height_mm': 2.0},
  'printing': { 'droplet_volume_ul': 5.0,
                'source_aspirate_height_mm': 0.5,
                'dispense_height_mm': 0.5,
                'pre_air_chase_ul': 0.0,
                'air_gap_ul': 1.5,
                'air_gap_height_mm': 5.0,
                'push_out_ul': 3.0,
                'blow_out': True,
                'inter_drop_delay_s': 0.0},
  'standard_print': { 'source_well': 'B11',
                      'targets': ['A1', 'B1', 'C1', 'D1', 'E1', 'F1', 'G1', 'H1'],
                      'droplets_per_target': 1},
  'clover_print': { 'source_well': 'B11',
                    'reference': 'B3',
                    'clovers': 1,
                    'half_width_mm': 2.0,
                    'half_height_mm': 2.0,
                    'x_offset_mm': 0.0,
                    'y_offset_mm': 0.0},
  'tips': {'start_tip': 'A1', 'return_tips': False, 'pipette_tip_reuse': False},
  'flow_rates': {'p20': {'aspirate_ul_s': 3.0, 'dispense_ul_s': 3.0}},
  'safety': {'p20_max_volume_ul': 20.0, 'source_minimum_remaining_ul': 10.0}}
# <<< CONFIG END <<<


# ── Shared helpers (same contract as 01_print_from_vial / 02_printing_four_clover) ──

def _load_labware(protocol, spec):
    kwargs = {}
    if spec.get("namespace"):
        kwargs["namespace"] = spec["namespace"]
    if spec.get("version") is not None:
        kwargs["version"] = int(spec["version"])
    return protocol.load_labware(spec["load_name"], str(spec["slot"]), **kwargs)


def _release_tip(pipette, return_tips):
    if not pipette.has_tip:
        return
    if return_tips:
        pipette.return_tip()
    else:
        pipette.drop_tip()


def _set_flow_rates(p20):
    rates = CONFIG.get("flow_rates", {}).get("p20", {})
    if rates.get("aspirate"):
        p20.flow_rate.aspirate = float(rates["aspirate"])
    if rates.get("dispense"):
        p20.flow_rate.dispense = float(rates["dispense"])


def _clover_offsets():
    """d1..d4 corner offsets, identical to 02_printing_four_clover.py.

        d1 = (-half_width, +half_height)   d2 = (+half_width, +half_height)
        d3 = (-half_width, -half_height)   d4 = (+half_width, -half_height)
    """
    clover = CONFIG["clover_print"]
    half_w = float(clover["half_width_mm"])
    half_h = float(clover["half_height_mm"])
    return {
        "d1": (-half_w, half_h),
        "d2": (half_w, half_h),
        "d3": (-half_w, -half_h),
        "d4": (half_w, -half_h),
    }


# ── Pre-flight ──────────────────────────────────────────────────────────────────

def _preflight(protocol, labware, p20):
    errors = []
    deck = CONFIG["deck"]
    dil = CONFIG["dilution"]
    pr = CONFIG["printing"]
    std = CONFIG["standard_print"]
    clover = CONFIG["clover_print"]
    p20_max = float(CONFIG["safety"]["p20_max_volume_ul"])

    for role, expected in (("tiprack_p20", 9),):
        if int(deck[role]["slot"]) != expected:
            errors.append(f"deck.{role} must be slot {expected}")
    for role in ("plate", "vial_rack", "paper"):
        slot = int(deck[role]["slot"])
        if not (1 <= slot <= 11):
            errors.append(f"deck.{role} slot must be 1-11, got {slot}")
    if len({int(spec["slot"]) for spec in deck.values()}) != len(deck):
        errors.append("deck slots must be unique")
    if p20.name != CONFIG["pipette"]["name"]:
        errors.append(f"pipette must be {CONFIG['pipette']['name']}, got {p20.name}")

    plate_names = labware["plate"].wells_by_name()
    vial_names = labware["vial_rack"].wells_by_name()
    paper_names = labware["paper"].wells_by_name()

    dye_well = str(CONFIG["dye_source"]["well"]).upper()
    water_well = str(CONFIG["water_source"]["well"]).upper()
    dest_well = str(dil["destination_well"]).upper()
    if dye_well not in plate_names:
        errors.append(f"dye_source.well {dye_well} is not on the plate")
    if dest_well not in plate_names:
        errors.append(f"dilution.destination_well {dest_well} is not on the plate")
    if dye_well == dest_well:
        errors.append("dilution.destination_well must differ from dye_source.well")
    if water_well not in vial_names:
        errors.append(f"water_source.well {water_well} is not on the vial rack")

    # dilution.enabled false: the diluted well is prepared BY HAND before the run,
    # so the robot only prints from it and no transfer/mix step is emitted.
    dilution_enabled = bool(dil.get("enabled", True))
    dye_volume = float(dil["dye_volume_ul"])
    water_volume = float(dil["water_volume_ul"])
    chunk = float(dil.get("transfer_chunk_ul", p20_max))
    total = float(dil.get("total_volume_ul", dye_volume + water_volume))
    if dilution_enabled:
        if not (0 < dye_volume <= p20_max):
            errors.append(f"dilution.dye_volume_ul must be in (0, {p20_max:g}]")
        if water_volume < 0:
            errors.append("dilution.water_volume_ul must be >= 0")
        if not (0 < chunk <= p20_max):
            errors.append(f"dilution.transfer_chunk_ul must be in (0, {p20_max:g}]")
        if abs((dye_volume + water_volume) - total) > 1e-6:
            errors.append(
                f"dilution: dye {dye_volume:g} + water {water_volume:g} does not equal "
                f"total_volume_ul {total:g}"
            )
    elif total <= 0:
        errors.append(
            "dilution.enabled is false, so dilution.total_volume_ul must state how "
            "much liquid was prepared by hand"
        )
    dest_max = plate_names[dest_well].max_volume if dest_well in plate_names else None
    if dest_max is not None and total > dest_max + 1e-9:
        errors.append(
            f"dilution total {total:g} uL exceeds well {dest_well} capacity {dest_max:g} uL"
        )
    mix_volume = float(dil.get("mix_volume_ul", 0.0) or 0.0)
    mix_cycles = dil.get("mix_cycles", 0)
    if mix_volume > p20_max + 1e-9:
        errors.append(f"dilution.mix_volume_ul must be <= {p20_max:g}")
    if isinstance(mix_cycles, bool) or not isinstance(mix_cycles, int) or mix_cycles < 0:
        errors.append(f"dilution.mix_cycles must be an integer >= 0, got {mix_cycles!r}")

    volume = float(pr["droplet_volume_ul"])
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    chase = float(pr.get("pre_air_chase_ul", 0.0) or 0.0)
    if not (0 < volume <= p20_max):
        errors.append(f"printing.droplet_volume_ul must be in (0, {p20_max:g}]")
    if chase + volume + air_gap > p20_max + 1e-9:
        errors.append(
            f"printing needs {chase + volume + air_gap:g} uL, over the {p20_max:g} uL P20"
        )

    std_source = str(std["source_well"]).upper()
    clover_source = str(clover["source_well"]).upper()
    for label, well in (("standard_print", std_source), ("clover_print", clover_source)):
        if well not in plate_names:
            errors.append(f"{label}.source_well {well} is not on the plate")

    print_source_height = float(pr.get("source_aspirate_height_mm", 0.5))
    if std_source in plate_names:
        depth = plate_names[std_source].depth
        if not (0 < print_source_height < depth):
            errors.append(
                f"printing.source_aspirate_height_mm must be > 0 and < {depth:g} mm, "
                f"got {print_source_height:g}"
            )

    targets = [str(t).upper() for t in (std.get("targets") or [])]
    missing = sorted({t for t in targets if t not in paper_names})
    if missing:
        errors.append(f"standard_print targets not on paper: {', '.join(missing)}")
    reference = str(clover["reference"]).upper()
    if reference not in paper_names:
        errors.append(f"clover_print.reference {reference} is not on the paper")

    droplets_per_target = int(std.get("droplets_per_target", 1))
    clover_count = int(clover.get("clovers", 1))
    if clover_count != 1:
        errors.append("clover_print.clovers must be 1 for this demo")
    standard_deposits = len(targets) * droplets_per_target
    clover_deposits = clover_count * len(DROPLET_KEYS)
    printed_ul = (standard_deposits + clover_deposits) * volume

    reserve = float(CONFIG["safety"].get("source_minimum_remaining_ul", 0.0) or 0.0)
    if printed_ul + reserve > total + 1e-9:
        errors.append(
            f"printing needs {printed_ul:g} uL plus {reserve:g} uL reserve, but the "
            f"dilution only makes {total:g} uL"
        )

    dye_loaded = float(CONFIG["dye_source"].get("loaded_volume_ul", 0.0) or 0.0)
    if dilution_enabled and dye_loaded and dye_volume > dye_loaded:
        errors.append(
            f"dye_source holds {dye_loaded:g} uL but the dilution needs {dye_volume:g} uL"
        )

    # Tip budget.
    if dilution_enabled:
        water_transfers = (
            int(math.ceil(water_volume / chunk)) if water_volume > 0 else 0
        )
        dilution_tips = 1 + water_transfers + (1 if mix_cycles else 0)
    else:
        water_transfers = 0
        dilution_tips = 0
    tip_reuse = bool(CONFIG["tips"].get("pipette_tip_reuse", False))
    if tip_reuse:
        tips_needed = 3  # dye, water, mix+print share where possible
    else:
        tips_needed = dilution_tips + standard_deposits + clover_deposits
    tip_names = list(labware["tiprack_p20"].wells_by_name())
    start_tip = str(CONFIG["tips"].get("start_tip", "A1")).upper()
    if start_tip not in tip_names:
        errors.append(f"tips.start_tip {start_tip} does not exist")
    else:
        available = len(tip_names) - tip_names.index(start_tip)
        if tips_needed > available:
            errors.append(
                f"need {tips_needed} tips from {start_tip} but only {available} remain"
            )

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))

    protocol.comment("Pre-flight validation passed.")
    return {
        "dilution_enabled": dilution_enabled,
        "dye_well": dye_well,
        "water_well": water_well,
        "dest_well": dest_well,
        "water_transfers": water_transfers,
        "chunk": chunk,
        "targets": targets,
        "droplets_per_target": droplets_per_target,
        "reference": reference,
        "standard_deposits": standard_deposits,
        "clover_deposits": clover_deposits,
        "printed_ul": printed_ul,
        "tips_needed": tips_needed,
        "total": total,
    }


def _report_plan(protocol, resolved):
    dil = CONFIG["dilution"]
    volume = float(CONFIG["printing"]["droplet_volume_ul"])
    protocol.comment("=== DYE DILUTION + PRINT DEMO ===")
    if resolved["dilution_enabled"]:
        protocol.comment(
            f"Dilution: {dil['dye_volume_ul']:g} uL dye ({resolved['dye_well']}) + "
            f"{dil['water_volume_ul']:g} uL water ({resolved['water_well']}) in "
            f"{resolved['water_transfers']} x {resolved['chunk']:g} uL "
            f"-> {resolved['dest_well']} ({resolved['total']:g} uL total)"
        )
        protocol.comment(
            f"Mix: {dil.get('mix_cycles', 0)} cycles x {dil.get('mix_volume_ul', 0):g} uL"
        )
    else:
        protocol.comment(
            f"Dilution: PREPARED BY HAND. {resolved['dest_well']} must already hold "
            f"{resolved['total']:g} uL of diluted dye. The robot performs no "
            "transfer and no mix."
        )
    protocol.comment(
        f"Standard print: {', '.join(resolved['targets'])} x "
        f"{resolved['droplets_per_target']} droplet(s) = "
        f"{resolved['standard_deposits']} deposits"
    )
    protocol.comment(
        f"Clover: 1 at {resolved['reference']} = {resolved['clover_deposits']} droplets"
    )
    protocol.comment(
        f"Printed liquid: {resolved['printed_ul']:g} uL of {resolved['total']:g} uL; "
        f"nominal remaining {resolved['total'] - resolved['printed_ul']:g} uL"
    )
    protocol.comment(
        f"Tips: {resolved['tips_needed']} "
        f"(pipette_tip_reuse={CONFIG['tips'].get('pipette_tip_reuse', False)})"
    )
    protocol.comment("=== END PLAN ===")


# ── Motion ──────────────────────────────────────────────────────────────────────

def _run_demo(protocol, labware, p20, resolved):
    deck_plate = labware["plate"]
    vial_rack = labware["vial_rack"]
    paper = labware["paper"]
    tiprack = labware["tiprack_p20"]

    dil = CONFIG["dilution"]
    pr = CONFIG["printing"]
    std = CONFIG["standard_print"]
    clover_cfg = CONFIG["clover_print"]
    p20_max = float(CONFIG["safety"]["p20_max_volume_ul"])
    return_tips = bool(CONFIG["tips"].get("return_tips", False))
    tip_reuse = bool(CONFIG["tips"].get("pipette_tip_reuse", False))

    tip_names = list(tiprack.wells_by_name())
    next_tip = [tip_names.index(str(CONFIG["tips"].get("start_tip", "A1")).upper())]

    def fresh_tip(reason):
        _release_tip(p20, return_tips)
        if next_tip[0] >= len(tip_names):
            raise RuntimeError("ran out of P20 tips")
        name = tip_names[next_tip[0]]
        next_tip[0] += 1
        p20.pick_up_tip(tiprack[name])
        protocol.comment(f"tip {name}: {reason}")

    def ensure_tip(reason):
        """Fresh tip per operation, or one held tip when reuse is enabled."""
        if tip_reuse and p20.has_tip:
            return
        fresh_tip(reason)

    dye_well = deck_plate[resolved["dye_well"]]
    water_well = vial_rack[resolved["water_well"]]
    dest_well = deck_plate[resolved["dest_well"]]

    dye_height = float(CONFIG["dye_source"].get("aspirate_height_mm", 1.0))
    water_height = float(CONFIG["water_source"].get("aspirate_height_mm", 4.0))
    dispense_height = float(dil.get("dispense_height_mm", 2.0))
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    air_gap_height = float(pr.get("air_gap_height_mm", 5.0))

    def transfer(source, source_height, volume, label):
        """One aspirate/dispense pair into the dilution well.

        The air gap is trimmed to whatever still fits the P20 alongside the
        liquid, so a full 20 uL transfer simply carries no gap.
        """
        ensure_tip(label)
        gap = min(air_gap, max(0.0, p20_max - volume))
        p20.aspirate(volume, source.bottom(source_height))
        if gap > 0:
            p20.air_gap(gap, height=air_gap_height)
        p20.dispense(volume + gap, dest_well.bottom(dispense_height))
        p20.blow_out(dest_well.bottom(dispense_height))
        protocol.comment(f"{label}: {volume:g} uL -> {resolved['dest_well']}")

    if resolved["dilution_enabled"]:
        # 1. dye
        protocol.comment("--- STEP 1: dye transfer ---")
        transfer(dye_well, dye_height, float(dil["dye_volume_ul"]),
                 f"dye {resolved['dye_well']}")

        # 2. water, in P20-sized chunks
        protocol.comment("--- STEP 2: water transfers ---")
        remaining = float(dil["water_volume_ul"])
        chunk = resolved["chunk"]
        index = 0
        while remaining > 1e-9:
            index += 1
            this = min(chunk, remaining)
            transfer(
                water_well, water_height, this,
                f"water {resolved['water_well']} ({index}/{resolved['water_transfers']})",
            )
            remaining -= this

        # 3. mix
        mix_cycles = int(dil.get("mix_cycles", 0) or 0)
        mix_volume = float(dil.get("mix_volume_ul", 0.0) or 0.0)
        if mix_cycles and mix_volume > 0:
            protocol.comment("--- STEP 3: mix ---")
            ensure_tip(f"mix {resolved['dest_well']}")
            p20.mix(mix_cycles, mix_volume, dest_well.bottom(dispense_height))
            protocol.comment(
                f"mixed {resolved['dest_well']}: {mix_cycles} x {mix_volume:g} uL"
            )
    else:
        protocol.comment(
            f"--- STEPS 1-3 SKIPPED: {resolved['dest_well']} was diluted by hand ---"
        )

    if not DEFAULT_DO_PRINT:
        _release_tip(p20, return_tips)
        return

    # 4 + 5. printing, reusing the validated print cycle
    volume = float(pr["droplet_volume_ul"])
    z = float(pr["dispense_height_mm"])
    chase = float(pr.get("pre_air_chase_ul", 0.0) or 0.0)
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    blow_out = bool(pr.get("blow_out", True))
    drop_delay = float(pr.get("inter_drop_delay_s", 0.0) or 0.0)

    def deposit(source_well, source_height, destination, label):
        ensure_tip(label)
        if chase > 0:
            p20.aspirate(chase, source_well.bottom(source_height))
        p20.aspirate(volume, source_well.bottom(source_height))
        if air_gap > 0:
            p20.air_gap(air_gap, height=air_gap_height)
        piston = chase + volume + air_gap
        if push_out > 0:
            p20.dispense(piston, destination, push_out=push_out)
        else:
            p20.dispense(piston, destination)
        if blow_out:
            p20.blow_out(destination)
        protocol.comment(label)
        if drop_delay > 0:
            protocol.delay(seconds=drop_delay)

    # Aspiration height inside the diluted well. It starts at 100 uL and finishes
    # near 40 uL (~1.06 mm of liquid in a 6.94 mm well), so this stays low enough
    # to keep the tip submerged for the last deposits.
    print_source_height = float(pr.get("source_aspirate_height_mm", 0.5))

    std_source = deck_plate[str(std["source_well"]).upper()]
    protocol.comment("--- STEP 4: standard print ---")
    protocol.comment(
        f"aspirating from {std['source_well']} at {print_source_height:g} mm"
    )
    for layer in range(1, resolved["droplets_per_target"] + 1):
        for target in resolved["targets"]:
            deposit(std_source, print_source_height, paper[target].bottom(z),
                    f"standard print {target} (drop {layer})")

    protocol.comment("--- STEP 5: clover print ---")
    clover_source = deck_plate[str(clover_cfg["source_well"]).upper()]
    reference = paper[resolved["reference"]]
    center_x = float(clover_cfg.get("x_offset_mm", 0.0) or 0.0)
    center_y = float(clover_cfg.get("y_offset_mm", 0.0) or 0.0)
    offsets = _clover_offsets()
    for key in DROPLET_KEYS:
        dx, dy = offsets[key]
        destination = reference.bottom(z).move(
            Point(x=center_x + dx, y=center_y + dy, z=0)
        )
        deposit(clover_source, print_source_height, destination,
                f"clover {resolved['reference']} {key.upper()}")

    _release_tip(p20, return_tips)
    protocol.comment(
        f"Demo complete: {resolved['standard_deposits']} standard + "
        f"{resolved['clover_deposits']} clover deposits, "
        f"{resolved['printed_ul']:g} uL printed, {next_tip[0]} tip(s) used."
    )


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    labware = {
        role: _load_labware(protocol, deck[role])
        for role in ("plate", "vial_rack", "paper", "tiprack_p20")
    }
    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )

    label = CONFIG.get("protocol_label", "dye_demo")
    protocol.comment(f"=== Dye Dilution Print Demo {label} Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}, do_print={DEFAULT_DO_PRINT}")

    resolved = _preflight(protocol, labware, p20)
    _report_plan(protocol, resolved)

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: plan only; no robot motion or liquid handling.")
        protocol.comment(f"=== Dye Dilution Print Demo {label} Completed (dry run) ===")
        return
    _set_flow_rates(p20)
    _run_demo(protocol, labware, p20, resolved)
    protocol.comment(f"=== Dye Dilution Print Demo {label} Completed ===")
