"""11 - Standard Print (Version 11) - the generalized paper printing executor.

DETERMINISTIC. This file needs no agent, no LLM, no runtime skill and no
approval workflow. It executes exactly the CONFIG block below and nothing else.

    configs/templates/11_standard_print_template.yaml   <- copy and edit this
         |  src/printing/v11/standard_loader.py   (validate + flatten + resolve)
         v
    this file (CONFIG block substituted between the sentinels)
         |  src/printing/v11/standard_builder.py  (render + hash + simulate)
         v
    src/protocols/generated/11_standard_print_latest.py  <- upload this

PHYSICS IS INHERITED, NOT REINVENTED. The print cycle below is copied from the
physically validated Version 1 executor
(src/protocols/printing/01_print_from_vial.py, itself taken from
02_printing_four_clover.py): aspirate -> air gap -> move -> dispense with
push-out -> blow out. Version 11 only generalizes the parameters *around* that
cycle. With template defaults the emitted command sequence is identical to
Version 1's.

TWO DIFFERENT AIR VOLUMES -- not interchangeable:
  release.pre_air_chase_ul   aspirated BEFORE the liquid; chases the droplet off
                             the tip. Laboratory-owned (machine profile).
  pipetting.air_gap_ul       aspirated AFTER the liquid via air_gap(); anti-drip
                             in transit only. It cannot help droplet release.

KEY EXPERIMENTAL PARAMETER. paper.print_height_mm is the height above the paper
surface the droplet is released from. 0.5 mm is the physically confirmed release
height; larger values left drops hanging on the tip in earlier testing.

EXECUTION ORDER (printing.order):
  layer_major  (default, the proven Version 1 behaviour) - every target that
               still owes a droplet gets one in layer 1, then the whole pass
               rests for timing.inter_layer_delay_s so the deposited liquid can
               dry, then layer 2 runs across every target that owes a second
               droplet, and so on. A group asking for 1 droplet takes part in
               layer 1 only; a group asking for 3 takes part in layers 1-3.
  target_major - one target receives all of its droplets before the run moves
               on. inter_layer_delay_s still separates consecutive layers on the
               same paper spot, because that is the same physical drying pause.

RESOLVED BEFORE EXECUTION. target_selection, replicates and per-group droplet
counts are all resolved into explicit paper wells by the loader. This file never
invents a coordinate: every target below is a named well on the paper labware.
"""
from __future__ import annotations

import math

from opentrons import protocol_api


metadata = {
    "protocolName": "11 - Standard Print (P20, API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Version 11 generalized paper printing: any registered source labware, "
        "any paper slot, configurable release height, droplet volume, groups, "
        "replicates, ordering and timing. Printing only - no dilution."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN = False
DEFAULT_DO_PRINT = True


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'protocol_label': 'standard_print',
  'workflow': '11_standard_print',
  'machine_profile': 'configs/machines/ot2_standard_printing_p20_v1.yaml',
  'deck': { 'source': { 'slot': 7,
                        'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                        'namespace': 'custom_beta',
                        'version': 1},
            'paper': { 'slot': 11,
                       'load_name': 'paper_print_96_flat',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': {'name': 'p20_single_gen2', 'mount': 'left', 'max_volume_ul': 20.0, 'min_volume_ul': 1.0},
  'source': { 'type': 'vial_rack',
              'wells': ['A1'],
              'material': 'print liquid',
              'loaded_volume_ul': 5000.0,
              'minimum_remaining_ul': 100.0,
              'aspirate_height_mm': 4.0},
  'paper': {'print_height_mm': 0.5},
  'printing': {'droplet_volume_ul': 5.0, 'order': 'layer_major'},
  'pipetting': { 'aspirate_flow_rate_ul_s': 3.0,
                 'dispense_flow_rate_ul_s': 3.0,
                 'air_gap_ul': 1.5,
                 'air_gap_height_mm': 5.0,
                 'post_aspirate_delay_s': 0.0,
                 'post_dispense_delay_s': 0.0},
  'release': {'pre_air_chase_ul': 0.0, 'push_out_ul': 3.0, 'blow_out': True},
  'timing': {'inter_drop_delay_s': 0.0, 'inter_layer_delay_s': 5.0, 'inter_target_delay_s': 0.0},
  'print_groups': [ { 'source_well': 'A1',
                      'targets': ['A1', 'B1', 'C1', 'D1', 'E1', 'F1', 'G1', 'H1'],
                      'droplets': 1,
                      'replicates': 1}],
  'tips': {'pipette_tip_reuse': True, 'return_tips': True, 'start_tip': 'A1'},
  'safety': {'p20_max_volume_ul': 20.0},
  'plan': { 'total_deposits': 8,
            'total_volume_ul': 40.0,
            'max_layers': 1,
            'tips_required': 1,
            'source_totals': {'A1': 40.0}}}
# <<< CONFIG END <<<


ORDERS = ("layer_major", "target_major")


# ── Small shared helpers (same contract as the Version 1 printing protocols) ────

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
    rates = CONFIG.get("pipetting", {})
    if rates.get("aspirate_flow_rate_ul_s"):
        p20.flow_rate.aspirate = float(rates["aspirate_flow_rate_ul_s"])
    if rates.get("dispense_flow_rate_ul_s"):
        p20.flow_rate.dispense = float(rates["dispense_flow_rate_ul_s"])


# ── Pre-flight ──────────────────────────────────────────────────────────────────

def _preflight(protocol, labware, p20):
    """Validate everything that can fail BEFORE any motion command is issued."""
    errors = []
    deck = CONFIG["deck"]
    src = CONFIG["source"]
    paper_cfg = CONFIG["paper"]
    pr = CONFIG["printing"]
    pip = CONFIG["pipetting"]
    rel = CONFIG["release"]
    timing = CONFIG["timing"]
    tips_cfg = CONFIG["tips"]
    p20_max = float(CONFIG["safety"]["p20_max_volume_ul"])
    p20_min = float(CONFIG["pipette"].get("min_volume_ul", 0.0) or 0.0)

    # -- deck ------------------------------------------------------------------
    # Slot 5 and slot 11 both hold a paper substrate today, so only legality is
    # checked here; slot uniqueness is enforced just below.
    paper_slot = int(deck["paper"]["slot"])
    if not (1 <= paper_slot <= 11):
        errors.append(f"deck.paper slot must be 1-11, got {paper_slot}")
    source_slot = int(deck["source"]["slot"])
    if not (1 <= source_slot <= 11):
        errors.append(f"deck.source slot must be 1-11, got {source_slot}")
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

    # -- release / volumes -----------------------------------------------------
    volume = float(pr["droplet_volume_ul"])
    air_gap = float(pip.get("air_gap_ul", 0.0) or 0.0)
    chase = float(rel.get("pre_air_chase_ul", 0.0) or 0.0)
    push_out = float(rel.get("push_out_ul", 0.0) or 0.0)
    if not (0 < volume <= p20_max):
        errors.append(f"printing.droplet_volume_ul must be in (0, {p20_max:g}]")
    elif p20_min and volume < p20_min:
        errors.append(
            f"printing.droplet_volume_ul {volume:g} is below the pipette minimum "
            f"{p20_min:g} uL"
        )
    if air_gap < 0:
        errors.append("pipetting.air_gap_ul must be >= 0")
    if chase < 0:
        errors.append("release.pre_air_chase_ul must be >= 0")
    if not (0 <= push_out <= p20_max):
        errors.append(f"release.push_out_ul must be in [0, {p20_max:g}]")
    piston = chase + volume + air_gap
    if piston > p20_max + 1e-9:
        errors.append(
            f"printing needs {piston:g} uL (pre-air chase + droplet + air gap), "
            f"exceeding the {CONFIG['pipette']['name']} maximum {p20_max:g} uL"
        )

    print_height = float(paper_cfg["print_height_mm"])
    if not (0 <= print_height < 10):
        errors.append(
            f"paper.print_height_mm must be >= 0 and < 10, got {print_height:g}"
        )
    if float(pip.get("air_gap_height_mm", 0.0) or 0.0) < 0:
        errors.append("pipetting.air_gap_height_mm must be >= 0")
    for key in ("post_aspirate_delay_s", "post_dispense_delay_s"):
        if float(pip.get(key, 0.0) or 0.0) < 0:
            errors.append(f"pipetting.{key} must be >= 0")
    for key in ("inter_drop_delay_s", "inter_layer_delay_s", "inter_target_delay_s"):
        if float(timing.get(key, 0.0) or 0.0) < 0:
            errors.append(f"timing.{key} must be >= 0")
    for key in ("aspirate_flow_rate_ul_s", "dispense_flow_rate_ul_s"):
        if float(pip.get(key, 0.0) or 0.0) <= 0:
            errors.append(f"pipetting.{key} must be > 0")

    order = str(pr.get("order", "layer_major"))
    if order not in ORDERS:
        errors.append(f"printing.order must be one of {ORDERS}, got {order!r}")

    # -- wells -----------------------------------------------------------------
    paper_names = labware["paper"].wells_by_name()
    source_names = labware["source"].wells_by_name()
    declared_wells = [str(w).upper() for w in (src.get("wells") or [])]
    if not declared_wells:
        errors.append("source.wells must list at least one well")
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
            {
                "targets": targets,
                "droplets": int(droplets),
                "source_well": source_well,
                "replicates": int(group.get("replicates", 1) or 1),
            }
        )

    # -- tips ------------------------------------------------------------------
    tip_name = str(tips_cfg.get("start_tip", "A1")).upper()
    tiprack_names = list(labware["tiprack_p20"].wells_by_name())
    if tip_name not in tiprack_names:
        errors.append(f"P20 start tip {tip_name} does not exist")

    # One tip per distinct source well: the tip may never move between liquids.
    used_sources = []
    for group in resolved_groups:
        if group["source_well"] and group["source_well"] not in used_sources:
            used_sources.append(group["source_well"])
    deposits = sum(len(g["targets"]) * g["droplets"] for g in resolved_groups)
    tip_reuse = bool(tips_cfg.get("pipette_tip_reuse", True))
    tips_needed = len(used_sources) if tip_reuse else deposits
    if tip_name in tiprack_names:
        start = tiprack_names.index(tip_name)
        if start + tips_needed > len(tiprack_names):
            errors.append(
                f"need {tips_needed} tip(s) starting at {tip_name} "
                f"(pipette_tip_reuse={tip_reuse}), but the tiprack only has "
                f"{len(tiprack_names) - start} left from there"
            )

    # -- source volume budget --------------------------------------------------
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
        "order": order,
        "tips_needed": tips_needed,
    }


def _report_plan(protocol, resolved):
    pr = CONFIG["printing"]
    src = CONFIG["source"]
    timing = CONFIG["timing"]
    volume = float(pr["droplet_volume_ul"])
    layer_delay = float(timing.get("inter_layer_delay_s", 0.0) or 0.0)
    protocol.comment("=== VERSION 11 STANDARD PRINT PLAN ===")
    protocol.comment(
        f"Source: {src.get('type', 'vial_rack')} in slot "
        f"{CONFIG['deck']['source']['slot']} ({src.get('material', 'liquid')!r}); "
        f"wells used: {', '.join(resolved['used_sources'])}"
    )
    protocol.comment(
        f"Paper: slot {CONFIG['deck']['paper']['slot']}, release height "
        f"{float(CONFIG['paper']['print_height_mm']):g} mm above the surface"
    )
    for index, group in enumerate(resolved["groups"], start=1):
        replicates = int(group.get("replicates", 1) or 1)
        suffix = f" (replicates: {replicates})" if replicates > 1 else ""
        protocol.comment(
            f"  group {index}: source {group['source_well']} -> "
            f"{', '.join(group['targets'])} x {group['droplets']} droplet(s){suffix}"
        )
    protocol.comment(
        f"Order: {resolved['order']}; layers: {resolved['max_layers']} "
        f"(delay between layers: {layer_delay:g} s)"
    )
    protocol.comment(
        f"Delays: inter-drop {float(timing.get('inter_drop_delay_s', 0.0) or 0.0):g} s, "
        f"inter-target {float(timing.get('inter_target_delay_s', 0.0) or 0.0):g} s"
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

def _standard_print(protocol, labware, p20, resolved):
    src = CONFIG["source"]
    pr = CONFIG["printing"]
    pip = CONFIG["pipetting"]
    rel = CONFIG["release"]
    timing = CONFIG["timing"]
    paper = labware["paper"]
    source_labware = labware["source"]

    volume = float(pr["droplet_volume_ul"])
    aspirate_height = float(src["aspirate_height_mm"])
    z = float(CONFIG["paper"]["print_height_mm"])
    chase = float(rel.get("pre_air_chase_ul", 0.0) or 0.0)
    air_gap = float(pip.get("air_gap_ul", 0.0) or 0.0)
    air_gap_height = float(pip.get("air_gap_height_mm", 5.0))
    push_out = float(rel.get("push_out_ul", 0.0) or 0.0)
    blow_out = bool(rel.get("blow_out", True))
    post_aspirate_delay = float(pip.get("post_aspirate_delay_s", 0.0) or 0.0)
    post_dispense_delay = float(pip.get("post_dispense_delay_s", 0.0) or 0.0)
    drop_delay = float(timing.get("inter_drop_delay_s", 0.0) or 0.0)
    layer_delay = float(timing.get("inter_layer_delay_s", 0.0) or 0.0)
    target_delay = float(timing.get("inter_target_delay_s", 0.0) or 0.0)
    return_tips = bool(CONFIG["tips"].get("return_tips", True))

    tiprack = labware["tiprack_p20"]
    tip_names = list(tiprack.wells_by_name())
    start_index = tip_names.index(str(CONFIG["tips"].get("start_tip", "A1")).upper())
    # tips.pipette_tip_reuse (default true): keep one tip per source well for the
    # whole run. False: a fresh tip for every single source -> paper deposit.
    tip_reuse = bool(CONFIG["tips"].get("pipette_tip_reuse", True))
    # One tip per distinct source well, assigned up front: a tip that has been in
    # one liquid is never put into another.
    tip_for_source = {
        source_well: tip_names[start_index + offset]
        for offset, source_well in enumerate(resolved["used_sources"])
    }
    # With reuse the first len(used_sources) tips are reserved above; without it
    # every tip from start_tip onwards is consumed one deposit at a time, which is
    # exactly the count the pre-flight tip check budgeted for.
    next_tip = [start_index + (len(resolved["used_sources"]) if tip_reuse else 0)]
    active_source = None
    last_target = [None]

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

    counter = [0]

    # NOTE on blow_out in a loop: see 02_printing_four_clover.py for the full
    # rationale (blow_out leaves the plunger unprepared; API 2.15 has no
    # prepare_to_aspirate(), so this is deliberately identical to already-
    # physically-used behaviour).
    def deposit(group, target, layer):
        """One droplet: the physically validated Version 1 print cycle, verbatim."""
        source_well = source_labware[group["source_well"]]
        if tip_reuse:
            use_source(group["source_well"])
        else:
            fresh_tip(group["source_well"])
        if target_delay > 0 and last_target[0] != target:
            protocol.delay(seconds=target_delay)
        last_target[0] = target

        destination = paper[target].bottom(z)
        if chase > 0:
            p20.aspirate(chase, source_well.bottom(aspirate_height))
        p20.aspirate(volume, source_well.bottom(aspirate_height))
        if post_aspirate_delay > 0:
            protocol.delay(seconds=post_aspirate_delay)
        if air_gap > 0:
            p20.air_gap(air_gap, height=air_gap_height)

        piston = chase + volume + air_gap
        if push_out > 0:
            p20.dispense(piston, destination, push_out=push_out)
        else:
            p20.dispense(piston, destination)
        if blow_out:
            p20.blow_out(destination)
        counter[0] += 1
        protocol.comment(
            f"layer {layer}: {target} <- {group['source_well']} "
            f"(drop {layer}/{group['droplets']})"
        )
        if post_dispense_delay > 0:
            protocol.delay(seconds=post_dispense_delay)
        if drop_delay > 0:
            protocol.delay(seconds=drop_delay)

    if resolved["order"] == "target_major":
        # One target receives all of its droplets before the run moves on.
        for group in resolved["groups"]:
            for target in group["targets"]:
                for layer in range(1, group["droplets"] + 1):
                    deposit(group, target, layer)
                    if layer < group["droplets"] and layer_delay > 0:
                        protocol.comment(
                            f"Drying {layer_delay:g} s before layer {layer + 1} on "
                            f"{target}."
                        )
                        protocol.delay(seconds=layer_delay)
    else:
        # layer_major (default): the proven Version 1 pass structure.
        for layer in range(1, resolved["max_layers"] + 1):
            active_groups = [g for g in resolved["groups"] if g["droplets"] >= layer]
            if not active_groups:
                continue
            protocol.comment(f"--- LAYER {layer} of {resolved['max_layers']} ---")
            for group in active_groups:
                for target in group["targets"]:
                    deposit(group, target, layer)

            remaining = [g for g in resolved["groups"] if g["droplets"] >= layer + 1]
            if remaining and layer_delay > 0:
                protocol.comment(f"Drying {layer_delay:g} s before layer {layer + 1}.")
                protocol.delay(seconds=layer_delay)

    _release_tip(p20, return_tips)
    protocol.comment(
        f"Print complete: {counter[0]} deposits over {resolved['max_layers']} layer(s), "
        f"{counter[0] * volume:g} uL total."
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
    protocol.comment(f"=== Version 11 Standard Print {label} Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}, do_print={DEFAULT_DO_PRINT}")

    resolved = _preflight(protocol, labware, p20)
    _report_plan(protocol, resolved)

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: plan only; no robot motion or liquid handling.")
        protocol.comment(f"=== Version 11 Standard Print {label} Completed (dry run) ===")
        return
    _set_flow_rates(p20)
    if DEFAULT_DO_PRINT:
        _standard_print(protocol, labware, p20, resolved)
    protocol.comment(f"=== Version 11 Standard Print {label} Completed ===")
