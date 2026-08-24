"""Print-from-vial - the minimal foundational printing primitive.

MANUAL FALLBACK. This file needs no agent, no LLM, no runtime skill, no
natural-language request and no approval workflow. It executes exactly the
CONFIG block below and nothing else.

    configs/experiments/01_print_from_vial.yaml   <- edit this
         |  build (validate + flatten with the referenced machine profile)
         v
    this file (CONFIG block regenerated in place)
         |
         v
    src/protocols/generated/01_print_from_vial_latest.py  <- upload this

    python scripts/build_print_from_vial.py

The physical loop below (one tip, aspirate / air-gap / dispense / blow-out /
delay) is copied from the physically validated
src/protocols/printing/02_printing_four_clover.py -- same P20, same 20 mL vial
rack, same paper labware, same print-release air handling. The only real change
is that a target is a flat paper well rather than a four-droplet clover cluster:
there is no clover geometry, no dilution, no intermediate plate, and no
per-target tip change.

VALIDATED PAPER GEOMETRY. dispense_height_mm and the print-release air handling
(pre_air_chase_ul, air_gap_ul, air_gap_height_mm, push_out_ul, blow_out) come
from the same laboratory-owned machine profile the standard workflow uses
(configs/machines/ot2_standard_printing_p20_v1.yaml) -- never hand-edited here.

Coordinate model: one droplet is dispensed at the well's own bottom (offset by
printing.dispense_height_mm), no sub-well offset.

LAYERED EXECUTION. Each print group declares how many droplets its targets get.
The executor runs LAYER BY LAYER across all groups, not target by target: every
target that still owes a droplet gets one in layer 1, then the whole pass rests
for `inter_layer_delay_s` so the deposited liquid can dry, then layer 2 runs
across every target that owes a second droplet, and so on. A group asking for 1
droplet participates only in layer 1; a group asking for 3 participates in layers
1, 2 and 3. Neither the layer count nor the delay is hard-coded here -- both come
from the configuration.

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
    "protocolName": "01 - Print From Vial (P20, API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Minimal vial-to-paper printing primitive: one 20 mL source vial, one "
        "tip for the whole run, an arbitrary flat list of paper targets. No "
        "dilution, no intermediate plate, no agent."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN = False
DEFAULT_DO_PRINT = True


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'protocol_label': 'quick_print_brand_to_paper11',
  'deck': { 'source': { 'slot': 1,
                        'load_name': 'brand_96_wellplate_350ul_flat_781662',
                        'namespace': 'custom_beta',
                        'version': 1},
            'paper': { 'slot': 11,
                       'load_name': 'paper_print_96_flat',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': {'name': 'p20_single_gen2', 'mount': 'left'},
  'source': { 'type': 'well_plate',
              'wells': ['B11'],
              'material': 'dye',
              'loaded_volume_ul': 300.0,
              'minimum_remaining_ul': 20.0,
              'aspirate_height_mm': 0.5},
  'printing': { 'droplet_volume_ul': 5.0,
                'dispense_height_mm': 0.5,
                'pre_air_chase_ul': 0.0,
                'air_gap_ul': 1.5,
                'air_gap_height_mm': 5.0,
                'push_out_ul': 3.0,
                'blow_out': True,
                'inter_drop_delay_s': 0.0,
                'inter_layer_delay_s': 0.0},
  'print_groups': [{'source_well': 'B11', 'targets': ['A5', 'B5', 'C5', 'D5'], 'droplets': 1}],
  'tips': {'print_tip': 'A1', 'return_tips': True, 'pipette_tip_reuse': True},
  'flow_rates': {'p20': {'aspirate_ul_s': 3.0, 'dispense_ul_s': 3.0}},
  'safety': {'p20_max_volume_ul': 20.0}}
# <<< CONFIG END <<<


# ── Small shared helpers (same contract as the clover/v10/v11 printing protocols) ──

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

    # The paper slot is chosen by configuration (slot 5 and slot 11 both hold a
    # paper substrate today), so only its legality is checked here. Slot
    # uniqueness is enforced just below.
    paper_slot = int(deck["paper"]["slot"])
    if not (1 <= paper_slot <= 11):
        errors.append(f"deck.paper slot must be 1-11, got {paper_slot}")
    for role, expected in (("tiprack_p20", 9),):
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
    if not (0 < volume <= p20_max):
        errors.append(f"printing.droplet_volume_ul must be in (0, {p20_max:g}]")
    if air_gap < 0:
        errors.append("printing.air_gap_ul must be >= 0")
    if chase < 0:
        errors.append("printing.pre_air_chase_ul must be >= 0")
    if not (0 <= push_out <= p20_max):
        errors.append(f"printing.push_out_ul must be in [0, {p20_max:g}]")
    for key in (
        "dispense_height_mm", "air_gap_height_mm", "inter_drop_delay_s",
        "inter_layer_delay_s",
    ):
        if float(pr.get(key, 0.0) or 0.0) < 0:
            errors.append(f"printing.{key} must be >= 0")
    piston = chase + volume + air_gap
    if piston > p20_max + 1e-9:
        errors.append(
            f"printing needs {piston:g} uL (pre-air chase + droplet + air gap), "
            f"exceeding the {CONFIG['pipette']['name']} maximum {p20_max:g} uL"
        )

    paper_names = labware["paper"].wells_by_name()
    source_names = labware["source"].wells_by_name()
    declared_wells = [str(w).upper() for w in (src.get("wells") or [])]
    for well in declared_wells:
        if well not in source_names:
            errors.append(f"source well {well} does not exist on the source labware")

    groups = CONFIG.get("print_groups") or []
    if not groups:
        errors.append("print_groups must contain at least one group")

    resolved_groups = []
    for index, group in enumerate(groups, start=1):
        label = f"print_groups[{index}]"
        targets = [str(t).upper() for t in (group.get("targets") or [])]
        if not targets:
            errors.append(f"{label}: targets must list at least one paper well")
        missing = sorted({t for t in targets if t not in paper_names})
        if missing:
            errors.append(f"{label}: targets not on paper labware: {', '.join(missing)}")
        droplets = group.get("droplets", 1)
        if isinstance(droplets, bool) or not isinstance(droplets, int) or droplets < 1:
            errors.append(f"{label}: droplets must be an integer >= 1, got {droplets!r}")
            droplets = 1
        source_well = str(
            group.get("source_well") or (declared_wells[0] if declared_wells else "")
        ).upper()
        if not source_well:
            errors.append(f"{label}: no source_well and no source.wells to fall back on")
        elif source_well not in source_names:
            errors.append(f"{label}: source well {source_well} does not exist")
        elif declared_wells and source_well not in declared_wells:
            errors.append(
                f"{label}: source_well {source_well} is not listed in source.wells "
                f"({', '.join(declared_wells)})"
            )
        resolved_groups.append(
            {"targets": targets, "droplets": int(droplets), "source_well": source_well}
        )

    tip_name = str(CONFIG["tips"]["print_tip"]).upper()
    tiprack_names = list(labware["tiprack_p20"].wells_by_name())
    if tip_name not in tiprack_names:
        errors.append(f"P20 tip {tip_name} does not exist")

    # One tip per distinct source well: the tip may never move between liquids.
    used_sources = []
    for group in resolved_groups:
        if group["source_well"] and group["source_well"] not in used_sources:
            used_sources.append(group["source_well"])
    deposits = sum(len(g["targets"]) * g["droplets"] for g in resolved_groups)
    tip_reuse = bool(CONFIG["tips"].get("pipette_tip_reuse", True))
    tips_needed = len(used_sources) if tip_reuse else deposits
    if tip_name in tiprack_names:
        start = tiprack_names.index(tip_name)
        if start + tips_needed > len(tiprack_names):
            errors.append(
                f"need {tips_needed} tip(s) starting at {tip_name} "
                f"(pipette_tip_reuse={tip_reuse}), but the tiprack only has "
                f"{len(tiprack_names) - start} left from there"
            )

    # Volume budget per source well.
    loaded = float(src.get("loaded_volume_ul", 0.0) or 0.0)
    reserve = float(src.get("minimum_remaining_ul", 0.0) or 0.0)
    aspirate_height = float(src["aspirate_height_mm"])
    for source_well_name in used_sources:
        if source_well_name not in source_names:
            continue
        source_well = source_names[source_well_name]
        if not (0 < aspirate_height < source_well.depth):
            errors.append(
                f"source.aspirate_height_mm must be > 0 and < {source_well.depth:g} mm"
            )
        required = sum(
            len(g["targets"]) * g["droplets"] * volume
            for g in resolved_groups
            if g["source_well"] == source_well_name
        )
        if not (0 < loaded <= source_well.max_volume):
            errors.append(
                f"source.loaded_volume_ul must be in (0, {source_well.max_volume:g}] "
                f"for well {source_well_name}"
            )
        if reserve < 0 or loaded < required + max(reserve, 0.0):
            errors.append(
                f"source {source_well_name} needs {required:g} uL plus {reserve:g} uL "
                f"reserve; loaded_volume_ul is {loaded:g}"
            )
        diameter = getattr(source_well, "diameter", None)
        if diameter:
            cover_volume = math.pi * (float(diameter) / 2.0) ** 2 * aspirate_height
            remaining = loaded - required
            if remaining <= cover_volume:
                errors.append(
                    f"source {source_well_name} would retain {remaining:g} uL, below "
                    f"the approximately {cover_volume:g} uL needed to cover the "
                    f"{aspirate_height:g} mm aspiration height"
                )

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    protocol.comment("Pre-flight validation passed.")
    return {
        "groups": resolved_groups,
        "used_sources": used_sources,
        "max_layers": max(g["droplets"] for g in resolved_groups),
        "deposits": deposits,
    }


def _report_plan(protocol, resolved):
    pr = CONFIG["printing"]
    src = CONFIG["source"]
    volume = float(pr["droplet_volume_ul"])
    layer_delay = float(pr.get("inter_layer_delay_s", 0.0) or 0.0)
    protocol.comment("=== STANDARD PRINT PLAN ===")
    protocol.comment(
        f"Source: {src.get('type', 'vial_rack')} in slot "
        f"{CONFIG['deck']['source']['slot']} ({src['material']!r}); "
        f"wells used: {', '.join(resolved['used_sources'])}"
    )
    for index, group in enumerate(resolved["groups"], start=1):
        protocol.comment(
            f"  group {index}: source {group['source_well']} -> "
            f"{', '.join(group['targets'])} x {group['droplets']} droplet(s)"
        )
    protocol.comment(
        f"Layers: {resolved['max_layers']} (delay between layers: {layer_delay:g} s)"
    )
    protocol.comment(
        f"Total: {resolved['deposits']} deposits x {volume:g} uL = "
        f"{resolved['deposits'] * volume:g} uL"
    )
    if bool(CONFIG["tips"].get("pipette_tip_reuse", True)):
        protocol.comment(
            f"Tips: {len(resolved['used_sources'])} (pipette_tip_reuse=true, "
            "one per source well)"
        )
    else:
        protocol.comment(
            f"Tips: {resolved['deposits']} (pipette_tip_reuse=false, fresh tip "
            "per deposit)"
        )
    protocol.comment("=== END PLAN ===")


# ── Motion ──────────────────────────────────────────────────────────────────────

def _print_from_vial(protocol, labware, p20, resolved):
    src = CONFIG["source"]
    pr = CONFIG["printing"]
    paper = labware["paper"]
    source_labware = labware["source"]

    volume = float(pr["droplet_volume_ul"])
    aspirate_height = float(src["aspirate_height_mm"])
    z = float(pr["dispense_height_mm"])
    chase = float(pr.get("pre_air_chase_ul", 0.0) or 0.0)
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    air_gap_height = float(pr.get("air_gap_height_mm", 5.0))
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    blow_out = bool(pr.get("blow_out", True))
    drop_delay = float(pr.get("inter_drop_delay_s", 0.0) or 0.0)
    layer_delay = float(pr.get("inter_layer_delay_s", 0.0) or 0.0)
    return_tips = bool(CONFIG["tips"].get("return_tips", True))

    tiprack = labware["tiprack_p20"]
    tip_names = list(tiprack.wells_by_name())
    start_index = tip_names.index(str(CONFIG["tips"]["print_tip"]).upper())
    # tips.pipette_tip_reuse (default true): keep one tip per source well for the
    # whole run. False: a fresh tip for every single source -> paper deposit.
    tip_reuse = bool(CONFIG["tips"].get("pipette_tip_reuse", True))
    # One tip per distinct source well, assigned up front: a tip that has been in
    # one liquid is never put into another.
    tip_for_source = {
        source_well: tip_names[start_index + offset]
        for offset, source_well in enumerate(resolved["used_sources"])
    }
    next_tip = [start_index + len(resolved["used_sources"])]
    active_source = None

    def use_source(source_well):
        """Ensure the tip on the pipette is the one dedicated to this source."""
        nonlocal active_source
        if active_source == source_well:
            return
        _release_tip(p20, return_tips)
        tip_name = tip_for_source[source_well]
        p20.pick_up_tip(tiprack[tip_name])
        protocol.comment(f"P20 tip {tip_name} picked for source well {source_well}.")
        active_source = source_well

    def fresh_tip(source_well):
        """Discard whatever is on the pipette and take the next unused tip."""
        nonlocal active_source
        _release_tip(p20, return_tips)
        if next_tip[0] >= len(tip_names):
            raise RuntimeError(
                "ran out of P20 tips: pipette_tip_reuse is false, which needs one "
                "tip per deposit"
            )
        tip_name = tip_names[next_tip[0]]
        next_tip[0] += 1
        p20.pick_up_tip(tiprack[tip_name])
        protocol.comment(f"P20 fresh tip {tip_name} for source well {source_well}.")
        active_source = None

    # NOTE on blow_out in a loop: see 02_printing_four_clover.py for the full
    # rationale (blow_out leaves the plunger unprepared; API 2.15 has no
    # prepare_to_aspirate(), so this is deliberately identical to already-
    # physically-used behaviour).
    printed = 0
    for layer in range(1, resolved["max_layers"] + 1):
        active_groups = [g for g in resolved["groups"] if g["droplets"] >= layer]
        if not active_groups:
            continue
        protocol.comment(f"--- LAYER {layer} of {resolved['max_layers']} ---")
        for group in active_groups:
            source_well = source_labware[group["source_well"]]
            if tip_reuse:
                use_source(group["source_well"])
            for target in group["targets"]:
                if not tip_reuse:
                    fresh_tip(group["source_well"])
                destination = paper[target].bottom(z)
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
                printed += 1
                protocol.comment(
                    f"layer {layer}: {target} <- {group['source_well']} "
                    f"(drop {layer}/{group['droplets']})"
                )
                if drop_delay > 0:
                    protocol.delay(seconds=drop_delay)

        remaining = [g for g in resolved["groups"] if g["droplets"] >= layer + 1]
        if remaining and layer_delay > 0:
            protocol.comment(
                f"Drying {layer_delay:g} s before layer {layer + 1}."
            )
            protocol.delay(seconds=layer_delay)

    _release_tip(p20, return_tips)
    protocol.comment(
        f"Print complete: {printed} deposits over {resolved['max_layers']} layer(s), "
        f"{printed * volume:g} uL total."
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

    label = CONFIG.get("protocol_label", "standard_print")
    protocol.comment(f"=== Standard Print {label} Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}, do_print={DEFAULT_DO_PRINT}")

    resolved = _preflight(protocol, labware, p20)
    _report_plan(protocol, resolved)

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: plan only; no robot motion or liquid handling.")
        protocol.comment(f"=== Standard Print {label} Completed (dry run) ===")
        return
    _set_flow_rates(p20)
    if DEFAULT_DO_PRINT:
        _print_from_vial(protocol, labware, p20, resolved)
    protocol.comment(f"=== Standard Print {label} Completed ===")
