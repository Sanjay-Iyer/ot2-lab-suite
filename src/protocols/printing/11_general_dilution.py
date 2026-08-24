"""Version 11 general dilution - deterministic executor (P20, API 2.15).

Dilution ONLY. This never prints and never touches the paper: run a printing
workflow separately afterwards if you want to print what you just made.

    configs/templates/11_dilution_template.yaml   <- or any 11_ dilution config
         |  src/printing/v11/dilution_loader.py   (resolve + flatten)
         v
    this file (CONFIG block regenerated in place by the builder)
         |  src/printing/v11/dilution_builder.py
         v
    src/protocols/generated/11_general_dilution_latest.py   <- uploaded

Everything convenient lives in the loader. By the time a configuration reaches
this file it is fully explicit: no dilution factors, no start_well/steps
selectors, no inherited defaults. This executor only ever sees resolved volumes
and resolved well lists, and it re-validates all of them before it moves.

Two modes, both driven entirely by YAML:

  mode: single
      each destination well gets diluent_volume_ul + stock_volume_ul, then a mix.

  mode: series
      every destination well gets diluent_volume_ul; the first also gets
      stock_volume_ul; then transfer_volume_ul is carried from each well into
      the next, mixing at every step.

Every transfer is split into chunks that always leave room for the trailing air
gap, so no single command can exceed the pipette capacity. The air gap is taken
AFTER the liquid and leaves the tip first; it is anti-drip in transit only.

Tips: pipette_tip_reuse true keeps one tip per distinct source well; false takes
a fresh tip for every individual chunk transfer and every mix.
"""
from __future__ import annotations

import math

from opentrons import protocol_api


metadata = {
    "protocolName": "V11 General Dilution (P20, API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Version 11 config-driven single or serial dilution between any "
        "registered source and destination labware. No printing."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN = False


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'protocol_label': 'v11_dilution',
  'deck': { 'stock': { 'slot': 7,
                       'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                       'namespace': 'custom_beta',
                       'version': 1},
            'diluent': { 'slot': 7,
                         'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                         'namespace': 'custom_beta',
                         'version': 1},
            'destination': { 'slot': 1,
                             'load_name': 'brand_96_wellplate_350ul_flat_781662',
                             'namespace': 'custom_beta',
                             'version': 1},
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': { 'name': 'p20_single_gen2',
               'mount': 'left',
               'max_volume_ul': 20.0,
               'min_volume_ul': 1.0},
  'stock': { 'well': 'A1',
             'material': 'stock',
             'aspirate_height_mm': 4.0,
             'loaded_volume_ul': 5000.0,
             'minimum_remaining_ul': 100.0},
  'diluent': { 'well': 'A2',
               'material': 'water',
               'aspirate_height_mm': 4.0,
               'loaded_volume_ul': 5000.0,
               'minimum_remaining_ul': 100.0},
  'destination': { 'wells': ['B11'],
                   'aspirate_height_mm': 1.0,
                   'dispense_height_mm': 2.0,
                   'well_depth_mm': 10.65,
                   'well_max_volume_ul': 350.0},
  'dilution': { 'mode': 'single',
                'stock_volume_ul': 20.0,
                'diluent_volume_ul': 80.0,
                'transfer_volume_ul': 0.0,
                'final_volume_ul': 100.0,
                'dilution_factor': 5.0},
  'transfer': { 'max_chunk_ul': 18.0,
                'air_gap_ul': 1.5,
                'air_gap_height_mm': 5.0,
                'on_capacity_conflict': 'reduce_chunk'},
  'mix': { 'enabled': True,
           'cycles': 5,
           'volume_ul': 15.0,
           'aspirate_height_mm': 1.0,
           'dispense_height_mm': 2.0},
  'delays': { 'after_transfer_s': 0.0,
              'after_mix_s': 0.0,
              'post_aspirate_delay_s': 0.0,
              'post_dispense_delay_s': 0.0},
  'tips': {'start_tip': 'A1', 'return_tips': True, 'pipette_tip_reuse': True},
  'flow_rates': {'p20': {'aspirate': 3.0, 'dispense': 3.0}},
  'safety': {'p20_max_volume_ul': 20.0, 'p20_min_volume_ul': 1.0},
  'plan': { 'estimated_tips': 3,
            'volumes_resolved_from': 'explicit',
            'series_resolved_from': None,
            'stock_needed_ul': 20.0,
            'diluent_needed_ul': 80.0,
            'chunks': {'stock': 2, 'diluent': 5, 'series': 0}}}
# <<< CONFIG END <<<


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #

def _chunk_volume(total, max_chunk, air_gap, capacity, on_conflict, label):
    """Split a volume into equal chunks that always fit liquid + air gap.

    Identical maths to src/printing/v11/labware.py::chunk_volume; inlined
    because a protocol uploaded to the robot cannot import the repository.
    Never returns a chunk where chunk + air_gap > capacity.
    """
    total = float(total)
    if total <= 0:
        return []
    if max_chunk <= 0:
        raise RuntimeError(f"{label}: transfer.max_chunk_ul must be > 0")
    if air_gap >= capacity:
        raise RuntimeError(
            f"{label}: air gap {air_gap:g} uL leaves no room in a {capacity:g} uL "
            "pipette"
        )
    usable = capacity - air_gap
    if max_chunk > usable + 1e-9:
        if str(on_conflict) == "fail":
            raise RuntimeError(
                f"{label}: transfer.max_chunk_ul {max_chunk:g} + air gap "
                f"{air_gap:g} exceeds the {capacity:g} uL pipette capacity"
            )
        max_chunk = usable
    if max_chunk <= 0:
        raise RuntimeError(f"{label}: no usable chunk size remains")
    count = max(1, int(math.ceil(total / max_chunk - 1e-9)))
    return [total / count] * count


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


def _load_deck(protocol):
    """Load each distinct deck slot once; roles may share one labware."""
    by_slot = {}
    labware = {}
    for role, spec in CONFIG["deck"].items():
        slot = str(spec["slot"])
        claimed = by_slot.get(slot)
        if claimed is not None:
            if claimed[0] != spec["load_name"]:
                raise RuntimeError(
                    f"deck slot {slot} is claimed by {claimed[0]} and by "
                    f"{spec['load_name']} ({role}); one slot holds one labware"
                )
            labware[role] = claimed[1]
            continue
        kwargs = {}
        if spec.get("namespace"):
            kwargs["namespace"] = spec["namespace"]
        if spec.get("version") is not None:
            kwargs["version"] = int(spec["version"])
        item = protocol.load_labware(spec["load_name"], slot, **kwargs)
        by_slot[slot] = (spec["load_name"], item)
        labware[role] = item
    return labware


def _well_depth(well, fallback):
    depth = getattr(well, "depth", None)
    try:
        return float(depth)
    except (TypeError, ValueError):
        return float(fallback)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def _preflight(protocol, labware, p20):
    """Re-validate the resolved configuration against the real deck."""
    errors = []
    dil = CONFIG["dilution"]
    dest_cfg = CONFIG["destination"]
    mix_cfg = CONFIG.get("mix", {})
    xfer = CONFIG.get("transfer", {})
    p20_max = float(CONFIG["safety"]["p20_max_volume_ul"])
    p20_min = float(CONFIG["safety"].get("p20_min_volume_ul", 1.0))

    for role in ("stock", "diluent", "destination", "tiprack_p20"):
        slot = int(CONFIG["deck"][role]["slot"])
        if not 1 <= slot <= 11:
            errors.append(f"deck.{role} slot must be 1-11, got {slot}")
    tiprack_slot = int(CONFIG["deck"]["tiprack_p20"]["slot"])
    for role in ("stock", "diluent", "destination"):
        if int(CONFIG["deck"][role]["slot"]) == tiprack_slot:
            errors.append(f"deck.{role} may not share slot {tiprack_slot} with the tiprack")
    if p20.name != CONFIG["pipette"]["name"]:
        errors.append(f"pipette must be {CONFIG['pipette']['name']}, got {p20.name}")

    mode = str(dil.get("mode", "single")).lower()
    if mode not in ("single", "series"):
        errors.append(f"dilution.mode must be 'single' or 'series', got {mode!r}")

    stock_well = str(CONFIG["stock"]["well"]).upper()
    diluent_well = str(CONFIG["diluent"]["well"]).upper()
    if stock_well not in labware["stock"].wells_by_name():
        errors.append(f"stock.well {stock_well} does not exist")
    if diluent_well not in labware["diluent"].wells_by_name():
        errors.append(f"diluent.well {diluent_well} does not exist")
    if (int(CONFIG["deck"]["stock"]["slot"]) == int(CONFIG["deck"]["diluent"]["slot"])
            and stock_well == diluent_well):
        errors.append("stock and diluent cannot be the same well")

    dest_names = labware["destination"].wells_by_name()
    wells = [str(w).upper() for w in (dest_cfg.get("wells") or [])]
    if not wells:
        errors.append("destination.wells must list at least one well")
    missing = sorted({w for w in wells if w not in dest_names})
    if missing:
        errors.append(f"destination wells not on the labware: {', '.join(missing)}")
    if len(set(wells)) != len(wells):
        errors.append("destination wells must be unique")
    if mode == "series" and len(wells) < 2:
        errors.append("dilution.mode series needs at least two destination wells")
    dest_slot = int(CONFIG["deck"]["destination"]["slot"])
    for role, source_well in (("stock", stock_well), ("diluent", diluent_well)):
        if int(CONFIG["deck"][role]["slot"]) == dest_slot and source_well in wells:
            errors.append(
                f"{role} well {source_well} is also a destination well on slot {dest_slot}"
            )

    stock_volume = float(dil.get("stock_volume_ul", 0.0) or 0.0)
    diluent_volume = float(dil.get("diluent_volume_ul", 0.0) or 0.0)
    transfer_volume = float(dil.get("transfer_volume_ul", 0.0) or 0.0)
    if stock_volume < 0 or diluent_volume < 0 or transfer_volume < 0:
        errors.append("dilution volumes must be >= 0")
    if mode == "single" and stock_volume <= 0:
        errors.append("dilution.stock_volume_ul must be > 0 for mode single")
    if mode == "series" and transfer_volume <= 0:
        errors.append("dilution.transfer_volume_ul must be > 0 for mode series")
    for name, volume in (("stock_volume_ul", stock_volume),
                         ("diluent_volume_ul", diluent_volume),
                         ("transfer_volume_ul", transfer_volume)):
        if 0 < volume < p20_min:
            errors.append(
                f"dilution.{name} {volume:g} uL is below the {p20_min:g} uL pipette minimum"
            )

    # These fallbacks only apply to a hand-edited CONFIG that bypassed the
    # loader; they must match the loader/template defaults (18.0 / 1.5), not be
    # more permissive than them. p20_max here would leave no air-gap headroom.
    max_chunk = float(xfer.get("max_chunk_ul", 18.0))
    air_gap = float(xfer.get("air_gap_ul", 1.5) or 0.0)
    on_conflict = str(xfer.get("on_capacity_conflict", "reduce_chunk"))
    if not (0 < max_chunk <= p20_max + 1e-9):
        errors.append(f"transfer.max_chunk_ul must be in (0, {p20_max:g}]")
    if air_gap < 0:
        errors.append("transfer.air_gap_ul must be >= 0")
    if air_gap >= p20_max:
        errors.append(
            f"transfer.air_gap_ul {air_gap:g} leaves no room in a {p20_max:g} uL pipette"
        )
    if on_conflict not in ("reduce_chunk", "fail"):
        errors.append(
            "transfer.on_capacity_conflict must be 'reduce_chunk' or 'fail', "
            f"got {on_conflict!r}"
        )

    mix_enabled = bool(mix_cfg.get("enabled", True))
    mix_cycles = mix_cfg.get("cycles", 0)
    mix_volume = float(mix_cfg.get("volume_ul", 0.0) or 0.0)
    if isinstance(mix_cycles, bool) or not isinstance(mix_cycles, int) or mix_cycles < 0:
        errors.append(f"mix.cycles must be an integer >= 0, got {mix_cycles!r}")
    elif mix_enabled and mix_cycles < 1:
        errors.append("mix.cycles must be >= 1 while mix.enabled is true")
    if mix_enabled:
        if mix_volume <= 0:
            errors.append("mix.volume_ul must be > 0 while mix.enabled is true")
        elif mix_volume < p20_min:
            errors.append(
                f"mix.volume_ul {mix_volume:g} is below the {p20_min:g} uL pipette minimum"
            )
        if mix_volume > p20_max + 1e-9:
            errors.append(f"mix.volume_ul must be <= {p20_max:g}")

    # Heights against the real labware.
    dispense_height = float(dest_cfg.get("dispense_height_mm", 2.0))
    dest_aspirate_height = float(dest_cfg.get("aspirate_height_mm", 1.0))
    mix_aspirate_height = float(mix_cfg.get("aspirate_height_mm", dest_aspirate_height))
    mix_dispense_height = float(mix_cfg.get("dispense_height_mm", dispense_height))
    if wells and not missing:
        depth = _well_depth(dest_names[wells[0]], dest_cfg.get("well_depth_mm", 10.65))
        for name, height in (("destination.dispense_height_mm", dispense_height),
                             ("destination.aspirate_height_mm", dest_aspirate_height),
                             ("mix.aspirate_height_mm", mix_aspirate_height),
                             ("mix.dispense_height_mm", mix_dispense_height)):
            if not 0 < height < depth:
                errors.append(
                    f"{name} must be > 0 and < the {depth:g} mm well depth, got {height:g}"
                )
    stock_height = float(CONFIG["stock"].get("aspirate_height_mm", 4.0))
    diluent_height = float(CONFIG["diluent"].get("aspirate_height_mm", 4.0))
    for role, height in (("stock", stock_height), ("diluent", diluent_height)):
        well_name = stock_well if role == "stock" else diluent_well
        names = labware[role].wells_by_name()
        if well_name in names:
            depth = _well_depth(names[well_name], 55.0)
            if not 0 < height < depth:
                errors.append(
                    f"{role}.aspirate_height_mm must be > 0 and < the {depth:g} mm "
                    f"well depth, got {height:g}"
                )

    # Destination capacity.
    if wells and not missing:
        if mode == "single":
            per_well = stock_volume + diluent_volume
        else:
            per_well = diluent_volume + max(stock_volume, transfer_volume)
        capacity = dest_names[wells[0]].max_volume
        if per_well > capacity + 1e-9:
            errors.append(
                f"each destination well would hold {per_well:g} uL, over its "
                f"{capacity:g} uL capacity"
            )

    tip_names = list(labware["tiprack_p20"].wells_by_name())
    start_tip = str(CONFIG["tips"].get("start_tip", "A1")).upper()
    if start_tip not in tip_names:
        errors.append(f"tips.start_tip {start_tip} does not exist")

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    protocol.comment("Pre-flight validation passed.")
    return {
        "mode": mode,
        "stock_well": stock_well,
        "diluent_well": diluent_well,
        "stock_height": stock_height,
        "diluent_height": diluent_height,
        "wells": wells,
        "stock_volume": stock_volume,
        "diluent_volume": diluent_volume,
        "transfer_volume": transfer_volume,
        "max_chunk": max_chunk,
        "air_gap": air_gap,
        "air_gap_height": float(xfer.get("air_gap_height_mm", 5.0)),
        "on_conflict": on_conflict,
        "p20_max": p20_max,
        "p20_min": p20_min,
        "mix_enabled": mix_enabled and int(mix_cycles) > 0 and mix_volume > 0,
        "mix_cycles": int(mix_cycles),
        "mix_volume": mix_volume,
        "mix_aspirate_height": mix_aspirate_height,
        "mix_dispense_height": mix_dispense_height,
        "dispense_height": dispense_height,
        "dest_aspirate_height": dest_aspirate_height,
        "tip_reuse": bool(CONFIG["tips"].get("pipette_tip_reuse", True)),
        "return_tips": bool(CONFIG["tips"].get("return_tips", True)),
        "start_tip": start_tip,
        "after_transfer_s": float(CONFIG.get("delays", {}).get("after_transfer_s", 0.0) or 0.0),
        "after_mix_s": float(CONFIG.get("delays", {}).get("after_mix_s", 0.0) or 0.0),
        "post_aspirate_delay_s": float(
            CONFIG.get("delays", {}).get("post_aspirate_delay_s", 0.0) or 0.0
        ),
        "post_dispense_delay_s": float(
            CONFIG.get("delays", {}).get("post_dispense_delay_s", 0.0) or 0.0
        ),
    }


def _chunks_for(volume, resolved, label):
    return _chunk_volume(
        volume, resolved["max_chunk"], resolved["air_gap"], resolved["p20_max"],
        resolved["on_conflict"], label,
    )


def _plan_operations(resolved):
    """The ordered physical plan. Used for counting tips and for execution."""
    ops = []
    wells = resolved["wells"]
    stock_key = f"stock:{resolved['stock_well']}"
    diluent_key = f"diluent:{resolved['diluent_well']}"

    def transfer_op(role, source, height, dest, volume, key, label):
        return {"kind": "transfer", "role": role, "source": source, "height": height,
                "dest": dest, "volume": volume, "key": key, "label": label}

    def mix_op(name):
        return {"kind": "mix", "well": name, "key": f"mix:{name}", "label": f"mix {name}"}

    if resolved["mode"] == "single":
        for name in wells:
            if resolved["diluent_volume"] > 0:
                ops.append(transfer_op(
                    "diluent", resolved["diluent_well"], resolved["diluent_height"],
                    name, resolved["diluent_volume"], diluent_key, f"diluent -> {name}"))
            if resolved["stock_volume"] > 0:
                ops.append(transfer_op(
                    "stock", resolved["stock_well"], resolved["stock_height"],
                    name, resolved["stock_volume"], stock_key, f"stock -> {name}"))
            if resolved["mix_enabled"]:
                ops.append(mix_op(name))
    else:
        for name in wells:
            if resolved["diluent_volume"] > 0:
                ops.append(transfer_op(
                    "diluent", resolved["diluent_well"], resolved["diluent_height"],
                    name, resolved["diluent_volume"], diluent_key, f"diluent -> {name}"))
        first = wells[0]
        if resolved["stock_volume"] > 0:
            ops.append(transfer_op(
                "stock", resolved["stock_well"], resolved["stock_height"],
                first, resolved["stock_volume"], stock_key, f"stock -> {first}"))
        if resolved["mix_enabled"]:
            ops.append(mix_op(first))
        for previous, current in zip(wells, wells[1:]):
            ops.append(transfer_op(
                "destination", previous, resolved["dest_aspirate_height"],
                current, resolved["transfer_volume"], f"series:{previous}",
                f"{previous} -> {current}"))
            if resolved["mix_enabled"]:
                ops.append(mix_op(current))
    return ops


def _tip_requests(ops, resolved):
    """One entry per pick-up decision, in execution order."""
    requests = []
    for op in ops:
        if op["kind"] == "transfer":
            requests.extend([op["key"]] * len(_chunks_for(op["volume"], resolved, op["label"])))
        else:
            requests.append(op["key"])
    return requests


def _count_tips(requests, tip_reuse):
    if not tip_reuse:
        return len(requests)
    count = 0
    active = None
    for key in requests:
        if key != active:
            count += 1
            active = key
    return count


def _validate_plan(protocol, labware, resolved, ops):
    """Capacity, sufficiency and tip checks on the resolved plan. No motion."""
    errors = []
    p20_max = resolved["p20_max"]
    air_gap = resolved["air_gap"]

    used = {"stock": 0.0, "diluent": 0.0}
    for op in ops:
        if op["kind"] != "transfer":
            continue
        chunks = _chunks_for(op["volume"], resolved, op["label"])
        if abs(sum(chunks) - op["volume"]) > 1e-6:
            errors.append(f"{op['label']}: chunk plan does not add up to {op['volume']:g} uL")
        for chunk in chunks:
            gap = min(air_gap, max(0.0, p20_max - chunk))
            if chunk + gap > p20_max + 1e-9:
                errors.append(
                    f"{op['label']}: a {chunk:g} uL chunk plus a {gap:g} uL air gap "
                    f"exceeds the {p20_max:g} uL pipette"
                )
            if chunk < resolved["p20_min"] - 1e-9:
                errors.append(
                    f"{op['label']}: a {chunk:g} uL chunk is below the "
                    f"{resolved['p20_min']:g} uL pipette minimum"
                )
        if op["role"] in used:
            used[op["role"]] += op["volume"]

    for role in ("stock", "diluent"):
        loaded = float(CONFIG[role].get("loaded_volume_ul", 0.0) or 0.0)
        reserve = float(CONFIG[role].get("minimum_remaining_ul", 0.0) or 0.0)
        if loaded <= 0:
            continue
        if loaded - used[role] < reserve - 1e-9:
            errors.append(
                f"{role} well {CONFIG[role]['well']} holds {loaded:g} uL, this run needs "
                f"{used[role]:g} uL and must leave {reserve:g} uL"
            )

    tip_names = list(labware["tiprack_p20"].wells_by_name())
    start_index = tip_names.index(resolved["start_tip"])
    needed = _count_tips(_tip_requests(ops, resolved), resolved["tip_reuse"])
    available = len(tip_names) - start_index
    if needed > available:
        errors.append(
            f"this run needs {needed} tip(s) but only {available} remain from "
            f"{resolved['start_tip']}"
        )

    if errors:
        protocol.comment("PLAN VALIDATION FAILED")
        raise RuntimeError("PLAN VALIDATION FAILED:\n- " + "\n- ".join(errors))
    resolved["tips_needed"] = needed
    resolved["stock_used_ul"] = used["stock"]
    resolved["diluent_used_ul"] = used["diluent"]
    return resolved


# --------------------------------------------------------------------------- #
# Reporting and execution
# --------------------------------------------------------------------------- #

def _report_plan(protocol, resolved, ops):
    protocol.comment("=== V11 GENERAL DILUTION PLAN ===")
    protocol.comment(f"Mode: {resolved['mode']}")
    protocol.comment(
        f"Stock:   {CONFIG['deck']['stock']['load_name']} slot "
        f"{CONFIG['deck']['stock']['slot']} well {resolved['stock_well']} "
        f"({CONFIG['stock'].get('material', 'stock')}) at "
        f"{resolved['stock_height']:g} mm"
    )
    protocol.comment(
        f"Diluent: {CONFIG['deck']['diluent']['load_name']} slot "
        f"{CONFIG['deck']['diluent']['slot']} well {resolved['diluent_well']} "
        f"({CONFIG['diluent'].get('material', 'diluent')}) at "
        f"{resolved['diluent_height']:g} mm"
    )
    protocol.comment(
        f"Destination: {CONFIG['deck']['destination']['load_name']} slot "
        f"{CONFIG['deck']['destination']['slot']} wells "
        f"{', '.join(resolved['wells'])}"
    )
    if resolved["mode"] == "single":
        total = resolved["stock_volume"] + resolved["diluent_volume"]
        factor = CONFIG["dilution"].get("dilution_factor")
        protocol.comment(
            f"Each well: {resolved['diluent_volume']:g} uL diluent + "
            f"{resolved['stock_volume']:g} uL stock = {total:g} uL"
            + (f" ({float(factor):g}x)" if factor else "")
        )
    else:
        protocol.comment(
            f"Series: {resolved['diluent_volume']:g} uL diluent in every well, "
            f"{resolved['stock_volume']:g} uL stock into {resolved['wells'][0]}, "
            f"then {resolved['transfer_volume']:g} uL carried down the series"
        )
    protocol.comment(
        f"Transfer: chunks of at most {resolved['max_chunk']:g} uL with a "
        f"{resolved['air_gap']:g} uL trailing air gap "
        f"(on_capacity_conflict={resolved['on_conflict']})"
    )
    if resolved["mix_enabled"]:
        protocol.comment(
            f"Mix: {resolved['mix_cycles']} cycles x {resolved['mix_volume']:g} uL, "
            f"aspirate {resolved['mix_aspirate_height']:g} mm / dispense "
            f"{resolved['mix_dispense_height']:g} mm"
        )
    else:
        protocol.comment("Mix: disabled")
    protocol.comment(
        f"Delays: {resolved['after_transfer_s']:g} s after each transfer, "
        f"{resolved['after_mix_s']:g} s after each mix"
    )
    protocol.comment(
        f"Tips: reuse={resolved['tip_reuse']} return={resolved['return_tips']} "
        f"start={resolved['start_tip']} needed={resolved.get('tips_needed', '?')}"
    )
    protocol.comment(
        f"Liquid: {resolved.get('stock_used_ul', 0.0):g} uL stock and "
        f"{resolved.get('diluent_used_ul', 0.0):g} uL diluent consumed over "
        f"{len(ops)} operation(s)"
    )
    protocol.comment("=== END PLAN ===")


def _run_dilution(protocol, labware, p20, resolved, ops):
    tiprack = labware["tiprack_p20"]
    tip_names = list(tiprack.wells_by_name())
    next_tip = [tip_names.index(resolved["start_tip"])]
    active_source = [None]
    return_tips = resolved["return_tips"]
    tip_reuse = resolved["tip_reuse"]

    def use_tip(source_key, reason):
        """One tip per distinct source when reusing; otherwise always fresh."""
        if tip_reuse and p20.has_tip and active_source[0] == source_key:
            return
        _release_tip(p20, return_tips)
        if next_tip[0] >= len(tip_names):
            raise RuntimeError("ran out of P20 tips")
        name = tip_names[next_tip[0]]
        next_tip[0] += 1
        p20.pick_up_tip(tiprack[name])
        active_source[0] = source_key
        protocol.comment(f"tip {name}: {reason}")

    def do_transfer(op):
        source_well = labware[op["role"]][op["source"]]
        destination_well = labware["destination"][op["dest"]]
        chunks = _chunks_for(op["volume"], resolved, op["label"])
        for index, this in enumerate(chunks, start=1):
            use_tip(op["key"], f"{op['label']} ({index}/{len(chunks)})")
            gap = min(resolved["air_gap"], max(0.0, resolved["p20_max"] - this))
            p20.aspirate(this, source_well.bottom(op["height"]))
            if resolved["post_aspirate_delay_s"] > 0:
                protocol.delay(seconds=resolved["post_aspirate_delay_s"])
            if gap > 0:
                p20.air_gap(gap, height=resolved["air_gap_height"])
            p20.dispense(this + gap, destination_well.bottom(resolved["dispense_height"]))
            p20.blow_out(destination_well.bottom(resolved["dispense_height"]))
            if resolved["post_dispense_delay_s"] > 0:
                protocol.delay(seconds=resolved["post_dispense_delay_s"])
        protocol.comment(f"{op['label']}: {op['volume']:g} uL in {len(chunks)} chunk(s)")
        if resolved["after_transfer_s"] > 0:
            protocol.delay(seconds=resolved["after_transfer_s"])

    def do_mix(op):
        well = labware["destination"][op["well"]]
        use_tip(op["key"], op["label"])
        for _ in range(resolved["mix_cycles"]):
            p20.aspirate(resolved["mix_volume"], well.bottom(resolved["mix_aspirate_height"]))
            p20.dispense(resolved["mix_volume"], well.bottom(resolved["mix_dispense_height"]))
        protocol.comment(
            f"mixed {op['well']}: {resolved['mix_cycles']} x "
            f"{resolved['mix_volume']:g} uL"
        )
        if resolved["after_mix_s"] > 0:
            protocol.delay(seconds=resolved["after_mix_s"])

    current = [None]
    for op in ops:
        marker = op["dest"] if op["kind"] == "transfer" else op["well"]
        if marker != current[0]:
            current[0] = marker
            protocol.comment(f"--- {marker} ---")
        if op["kind"] == "transfer":
            do_transfer(op)
        else:
            do_mix(op)

    _release_tip(p20, return_tips)
    protocol.comment(
        f"Dilution complete: {len(resolved['wells'])} well(s), "
        f"{next_tip[0] - tip_names.index(resolved['start_tip'])} tip(s) used."
    )


def run(protocol: protocol_api.ProtocolContext):
    labware = _load_deck(protocol)
    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )

    label = CONFIG.get("protocol_label", "v11_dilution")
    protocol.comment(f"=== V11 General Dilution {label} Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}")

    resolved = _preflight(protocol, labware, p20)
    ops = _plan_operations(resolved)
    _validate_plan(protocol, labware, resolved, ops)
    _report_plan(protocol, resolved, ops)

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: plan only; no robot motion or liquid handling.")
        protocol.comment(f"=== V11 General Dilution {label} Completed (dry run) ===")
        return
    _set_flow_rates(p20)
    _run_dilution(protocol, labware, p20, resolved, ops)
    protocol.comment(f"=== V11 General Dilution {label} Completed ===")
