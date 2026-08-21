"""Print-from-plate - minimal foundational printing primitive (plate source).

MANUAL FALLBACK. This file needs no agent, no LLM, no runtime skill, no
natural-language request and no approval workflow. It executes exactly the
CONFIG block below and nothing else.

    configs/experiments/01b_print_from_plate.yaml   <- edit this
         |  build (validate + flatten with the referenced machine profile)
         v
    this file (CONFIG block regenerated in place)
         |
         v
    src/protocols/generated/01b_print_from_plate_latest.py  <- upload this

    python scripts/build_print_from_plate.py

The physical loop below (one tip, aspirate / air-gap / dispense / blow-out /
delay) is copied unchanged from the physically validated
src/protocols/printing/01_print_from_vial.py -- same P20, same paper labware,
same print-release air handling. The only real change is that the source
labware is a BRAND 781662 96-well plate (one selected well) instead of a 20 mL
vial rack: there is no dilution, no intermediate plate, no clover geometry, and
no per-target tip change.

VALIDATED PAPER GEOMETRY. dispense_height_mm and the print-release air handling
(pre_air_chase_ul, air_gap_ul, air_gap_height_mm, push_out_ul, blow_out) come
from the same laboratory-owned machine profile family the standard workflow
uses (configs/machines/ot2_print_from_plate_p20_v1.yaml) -- never hand-edited
here.

Coordinate model: one droplet is dispensed at the well's own bottom (offset by
printing.dispense_height_mm), no sub-well offset. `droplets_per_target` repeats
the same aspirate/air-gap/dispense/blow-out cycle at the SAME location, e.g. to
stack layers with a drying delay in between.

TWO DIFFERENT AIR VOLUMES -- not interchangeable (see 02_printing_four_clover.py
for the full physical rationale):
  pre_air_chase_ul  aspirated BEFORE the liquid; chases the droplet off the tip.
  air_gap_ul        aspirated AFTER the liquid via air_gap(); anti-drip only,
                    added to EVERY aspirate/dispense cycle in this workflow.
"""
from __future__ import annotations

import math

from opentrons import protocol_api


metadata = {
    "protocolName": "01b - Print From Plate (P20, API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Minimal plate-to-paper printing primitive: one selected well of a "
        "BRAND 781662 96-well plate, one tip for the whole run, an arbitrary "
        "flat list of paper targets. No dilution, no intermediate plate, no agent."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN = False
DEFAULT_DO_PRINT = True


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'protocol_label': 'print_from_plate_test',
  'deck': { 'source': { 'slot': 4,
                        'load_name': 'brand_96_wellplate_350ul_flat_781662',
                        'namespace': 'custom_beta',
                        'version': 1},
            'paper': { 'slot': 5,
                       'load_name': 'paper_print_96_flat',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': {'name': 'p20_single_gen2', 'mount': 'left'},
  'source': { 'well': 'A1',
              'material': 'print test liquid',
              'loaded_volume_ul': 300.0,
              'minimum_remaining_ul': 20.0,
              'aspirate_height_mm': 0.2},
  'printing': { 'droplet_volume_ul': 5.0,
                'droplets_per_target': 1,
                'dispense_height_mm': 0.5,
                'pre_air_chase_ul': 0.0,
                'air_gap_ul': 1.5,
                'air_gap_height_mm': 5.0,
                'push_out_ul': 3.0,
                'blow_out': True,
                'inter_drop_delay_s': 0.0},
  'targets': ['A1', 'A2', 'A3'],
  'tips': {'print_tip': 'A1', 'return_tips': True},
  'flow_rates': {'p20': {'aspirate': 3.0, 'dispense': 3.0}},
  'safety': {'p20_max_volume_ul': 20.0, 'expected_source_slot': 4}}
# <<< CONFIG END <<<


# ── Small shared helpers (same contract as the print-from-vial / clover printing protocols) ──

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


# ── Pre-flight ──────────────────────────────────────────────────────────────────

def _preflight(protocol, labware, p20):
    errors = []
    deck = CONFIG["deck"]
    src = CONFIG["source"]
    pr = CONFIG["printing"]
    safety = CONFIG["safety"]
    p20_max = float(safety["p20_max_volume_ul"])

    expected_source_slot = int(safety["expected_source_slot"])
    for role, expected in (
        ("source", expected_source_slot), ("paper", 5), ("tiprack_p20", 9)
    ):
        actual = int(deck[role]["slot"])
        if actual != expected:
            errors.append(f"deck.{role} must be slot {expected}, got {actual}")
    if len({int(spec["slot"]) for spec in deck.values()}) != len(deck):
        errors.append("deck slots must be unique")
    if requirements != {"robotType": "OT-2", "apiLevel": "2.15"}:
        errors.append("protocol requirements must be OT-2 / API 2.15")
    if p20.name != CONFIG["pipette"]["name"]:
        errors.append(f"pipette must be {CONFIG['pipette']['name']}, got {p20.name}")

    volume = float(pr["droplet_volume_ul"])
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    chase = float(pr.get("pre_air_chase_ul", 0.0) or 0.0)
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    droplets_per_target = pr.get("droplets_per_target", 1)
    if not (0 < volume <= p20_max):
        errors.append(f"printing.droplet_volume_ul must be in (0, {p20_max:g}]")
    if air_gap < 0:
        errors.append("printing.air_gap_ul must be >= 0")
    if chase < 0:
        errors.append("printing.pre_air_chase_ul must be >= 0")
    if not (0 <= push_out <= p20_max):
        errors.append(f"printing.push_out_ul must be in [0, {p20_max:g}]")
    for key in ("dispense_height_mm", "air_gap_height_mm", "inter_drop_delay_s"):
        if float(pr.get(key, 0.0) or 0.0) < 0:
            errors.append(f"printing.{key} must be >= 0")
    if (isinstance(droplets_per_target, bool) or not isinstance(droplets_per_target, int)
            or droplets_per_target < 1):
        errors.append(
            f"printing.droplets_per_target must be an integer >= 1, got "
            f"{droplets_per_target!r}"
        )
    piston = chase + volume + air_gap
    if piston > p20_max + 1e-9:
        errors.append(
            f"printing needs {piston:g} uL (pre-air chase + droplet + air gap), "
            f"exceeding the {CONFIG['pipette']['name']} maximum {p20_max:g} uL"
        )

    targets = CONFIG.get("targets") or []
    if not targets:
        errors.append("targets must list at least one paper well")
    paper_names = labware["paper"].wells_by_name()
    missing = sorted({t for t in targets if str(t).upper() not in paper_names})
    if missing:
        errors.append(f"targets not found on paper labware: {', '.join(missing)}")

    tip_name = str(CONFIG["tips"]["print_tip"]).upper()
    if tip_name not in labware["tiprack_p20"].wells_by_name():
        errors.append(f"P20 tip {tip_name} does not exist")

    source_name = str(src["well"]).upper()
    source_names = labware["source"].wells_by_name()
    deposits = len(targets) * int(droplets_per_target) if not errors else 0
    if source_name not in source_names:
        errors.append(f"source well {source_name} does not exist")
    else:
        source_well = source_names[source_name]
        aspirate_height = float(src["aspirate_height_mm"])
        if not (0 < aspirate_height < source_well.depth):
            errors.append(
                f"source.aspirate_height_mm must be > 0 and < {source_well.depth:g} mm"
            )
        loaded = float(src["loaded_volume_ul"])
        reserve = float(src.get("minimum_remaining_ul", 0.0) or 0.0)
        required = deposits * volume
        if not (0 < loaded <= source_well.max_volume):
            errors.append(
                f"source.loaded_volume_ul must be in (0, {source_well.max_volume:g}]"
            )
        if reserve < 0 or loaded < required + max(reserve, 0.0):
            errors.append(
                f"source needs {required:g} uL print volume plus {reserve:g} uL "
                f"reserve; loaded_volume_ul is {loaded:g}"
            )
        diameter = getattr(source_well, "diameter", None)
        if diameter:
            cover_volume = math.pi * (float(diameter) / 2.0) ** 2 * aspirate_height
            remaining = loaded - required
            if remaining <= cover_volume:
                errors.append(
                    f"source would retain {remaining:g} uL after printing, below the "
                    f"approximately {cover_volume:g} uL needed to cover the "
                    f"{aspirate_height:g} mm aspiration height"
                )

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    protocol.comment("Pre-flight validation passed.")
    return {"targets": [str(t).upper() for t in targets], "source_name": source_name,
            "deposits": deposits}


def _report_plan(protocol, resolved):
    pr = CONFIG["printing"]
    src = CONFIG["source"]
    volume = float(pr["droplet_volume_ul"])
    droplets_per_target = int(pr.get("droplets_per_target", 1))
    protocol.comment("=== PRINT FROM PLATE PLAN ===")
    protocol.comment(
        f"Source {src['material']!r} well {resolved['source_name']}: "
        f"{len(resolved['targets'])} target(s) x {droplets_per_target} droplet(s) "
        f"= {resolved['deposits']} deposits x {volume:g} uL = "
        f"{resolved['deposits'] * volume:g} uL total"
    )
    protocol.comment(f"Targets: {', '.join(resolved['targets'])}")
    protocol.comment("=== END PLAN ===")


# ── Motion ──────────────────────────────────────────────────────────────────────

def _print_from_plate(protocol, labware, p20, resolved):
    src = CONFIG["source"]
    pr = CONFIG["printing"]
    paper = labware["paper"]
    source_well = labware["source"][resolved["source_name"]]

    volume = float(pr["droplet_volume_ul"])
    aspirate_height = float(src["aspirate_height_mm"])
    z = float(pr["dispense_height_mm"])
    chase = float(pr.get("pre_air_chase_ul", 0.0) or 0.0)
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    air_gap_height = float(pr.get("air_gap_height_mm", 5.0))
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    blow_out = bool(pr.get("blow_out", True))
    drop_delay = float(pr.get("inter_drop_delay_s", 0.0) or 0.0)
    droplets_per_target = int(pr.get("droplets_per_target", 1))
    tip_name = str(CONFIG["tips"]["print_tip"]).upper()

    # NOTE on blow_out in a loop: see 02_printing_four_clover.py for the full
    # rationale (blow_out leaves the plunger unprepared; API 2.15 has no
    # prepare_to_aspirate(), so this is deliberately identical to already-
    # physically-used behaviour).
    p20.pick_up_tip(labware["tiprack_p20"][tip_name])
    protocol.comment(f"P20 tip {tip_name} picked and held for the complete run.")

    for target in resolved["targets"]:
        destination = paper[target].bottom(z)
        for repeat in range(1, droplets_per_target + 1):
            if chase > 0:
                p20.aspirate(chase, source_well.bottom(aspirate_height))
            p20.aspirate(volume, source_well.bottom(aspirate_height))
            if air_gap > 0:
                p20.air_gap(air_gap, height=air_gap_height)

            piston = chase + volume + air_gap
            if push_out > 0:
                p20.dispense(piston, destination, push_out=push_out)
            else:
                p20.dispense(piston, destination)
            if blow_out:
                p20.blow_out(destination)
            protocol.comment(
                f"{target} drop {repeat}/{droplets_per_target} printed."
            )
            if drop_delay > 0:
                protocol.delay(seconds=drop_delay)

    _release_tip(p20, bool(CONFIG["tips"].get("return_tips", True)))
    protocol.comment(
        f"Print complete: {len(resolved['targets'])} target(s), "
        f"{resolved['deposits']} deposits, {resolved['deposits'] * volume:g} uL total."
    )


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    labware = {
        role: _load_labware(protocol, deck[role])
        for role in ("source", "paper", "tiprack_p20")
    }
    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )

    label = CONFIG.get("protocol_label", "print_from_plate")
    protocol.comment(f"=== Print From Plate {label} Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}, do_print={DEFAULT_DO_PRINT}")

    resolved = _preflight(protocol, labware, p20)
    _report_plan(protocol, resolved)

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: plan only; no robot motion or liquid handling.")
        protocol.comment(f"=== Print From Plate {label} Completed (dry run) ===")
        return
    _set_flow_rates(p20)
    if DEFAULT_DO_PRINT:
        _print_from_plate(protocol, labware, p20, resolved)
    protocol.comment(f"=== Print From Plate {label} Completed ===")
