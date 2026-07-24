"""
Direct vial -> paper print, protocol v8 (OT-2 Python Protocol API 2.15).

NO DILUTION, NO MIXING PLATE. Every droplet is drawn straight from the 20 mL
stock vial in the slot-7 tube rack and laid on the slot-5 paper (96-flat) plate.

The unit of work is a PASS. A pass prints one droplet on every configured row of
every column it lists, then rests for `rest_minutes` so those droplets can dry
before the next layer lands on top of them. Layering therefore falls out of the
pass list rather than needing a per-column drop count:

    passes:
      - {columns: [6, 8, 9], rest_minutes: 5}   # layer 1 on 6, 8 and 9
      - {columns: [8, 9],    rest_minutes: 5}   # layer 2 on 8 and 9
      - {columns: [9],       rest_minutes: 5}   # layer 3 on 9
      - {columns: [9],       rest_minutes: 5}   # layer 4 on 9
      - {columns: [9],       rest_minutes: 0}   # layer 5 on 9, then done

  -> column 6 ends with 1 layer, column 8 with 2, column 9 with 5.

Anti-drip / full-delivery: each droplet is aspirated from the vial, an air gap is
pulled in below it for the trip across the deck, and the pair is dispensed
together above the paper. The dispense carries a `push_out` that drives the
plunger past its bottom stop, and a blow-out follows it, so the tip is cleared
twice over and nothing is left clinging.

One P20 tip serves the whole job (single source, single material) and is held
through the rests; between passes the tip parks over the open source vial so any
seepage lands back in the stock rather than on a drying spot.

The CONFIG block is replaced by scripts/build_vial_dilution_print.py. Edit the
workflow YAML (configs/printing/*_v8.yaml), not a generated protocol.
"""
from __future__ import annotations

from opentrons import protocol_api


metadata = {
    "protocolName": "Direct Vial Paper Print v8 (P20, OT-2 API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Print droplets straight from a 20 mL stock vial onto paper with the P20, "
        "in layered passes separated by drying rests. No dilution, no plate."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN     = True
DEFAULT_DO_DILUTION = False
DEFAULT_DO_PRINT    = True


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'deck': { 'paper': { 'slot': 5,
                       'load_name': 'paper_print_96_flat',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tuberack': { 'slot': 7,
                          'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                          'namespace': 'custom_beta',
                          'version': 1},
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': {'name': 'p20_single_gen2', 'mount': 'left'},
  'source': { 'vial': 'A2',
              'material': 'nanoparticle',
              'aspirate_height_mm': 4.0,
              'park_height_mm': 5.0,
              'mix_before_pass': {'reps': 0, 'volume_ul': 15.0, 'height_mm': 4.0}},
  'print': { 'volume_ul': 5.0,
             'rows': ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'],
             'z_mm': 4.4,
             'air_gap_ul': 1.5,
             'air_gap_height_mm': 5.0,
             'push_out_ul': 3.0,
             'blow_out': True,
             'post_dispense_delay_s': 2.0,
             'passes': [ {'columns': [7, 8, 9], 'rest_minutes': 5},
                         {'columns': [8, 9], 'rest_minutes': 5},
                         {'columns': [9], 'rest_minutes': 5},
                         {'columns': [9], 'rest_minutes': 5},
                         {'columns': [9], 'rest_minutes': 0}]},
  'tips': {'return_tips': True, 'p20': {'print_tip': 'A1'}},
  'flow_rates': {'p20': {'aspirate': 3.0, 'dispense': 3.0}},
  'safety': { 'p20_max_volume_ul': 20.0,
              'expected_tuberack_load_name': 'tuberack_3dprint_20ml_8vials_v2'},
  'protocol_version': 8}
# <<< CONFIG END <<<


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


def _layer_plan():
    """Expand `print.passes` into per-pass work plus a running layer count per column.

    Returns (passes, layers) where passes is a list of
    {index, columns, rest_minutes, layer_of} — layer_of maps a column to which
    layer this pass lays down — and layers maps column -> total layers.
    """
    seen: dict = {}
    passes = []
    for index, spec in enumerate(CONFIG["print"]["passes"], start=1):
        columns = [int(c) for c in spec.get("columns", [])]
        layer_of = {}
        for column in columns:
            seen[column] = seen.get(column, 0) + 1
            layer_of[column] = seen[column]
        passes.append({
            "index": index,
            "columns": columns,
            "rest_minutes": float(spec.get("rest_minutes", 0) or 0),
            "layer_of": layer_of,
        })
    return passes, dict(seen)


def _preflight(protocol, labware, p20):
    errors = []
    deck = CONFIG["deck"]
    src = CONFIG["source"]
    pr = CONFIG["print"]
    safety = CONFIG["safety"]
    rows = [str(r) for r in pr["rows"]]

    for role, expected in (("tuberack", 7), ("paper", 5), ("tiprack_p20", 9)):
        actual = int(deck[role]["slot"])
        if actual != expected:
            errors.append(f"deck.{role} must be slot {expected}, got {actual}")
    if requirements != {"robotType": "OT-2", "apiLevel": "2.15"}:
        errors.append("protocol requirements must be OT-2 / API 2.15")
    if p20.name != CONFIG["pipette"]["name"]:
        errors.append(f"pipette must be {CONFIG['pipette']['name']}, got {p20.name}")

    p20_max = float(safety["p20_max_volume_ul"])
    volume = float(pr["volume_ul"])
    air_gap = float(pr.get("air_gap_ul", 0.0))
    if not (0 < volume <= p20_max):
        errors.append(f"print.volume_ul must be in (0, {p20_max:g}], got {volume:g}")
    if air_gap < 0:
        errors.append(f"print.air_gap_ul must be >= 0, got {air_gap:g}")
    if float(pr.get("air_gap_height_mm", 0.0)) < 0:
        errors.append("print.air_gap_height_mm must be >= 0")
    # The tip carries droplet + air gap at the same time, so the pair must fit.
    if volume + max(air_gap, 0.0) > p20_max:
        errors.append(
            f"print: volume {volume:g} + air gap {air_gap:g} = "
            f"{volume + max(air_gap, 0.0):g} uL exceeds the P20's {p20_max:g} uL"
        )
    if float(pr.get("z_mm", 0.0)) < 0:
        errors.append("print.z_mm must be >= 0")
    if float(pr.get("post_dispense_delay_s", 0.0) or 0.0) < 0:
        errors.append("print.post_dispense_delay_s must be >= 0")
    # push_out drives the plunger past its bottom stop on the dispense itself. It is
    # only legal on a dispense that empties the tip (ours always is), and the hardware
    # caps how far past bottom the plunger can go — the robot rejects an overlarge
    # value at run time, so keep it modest.
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    if push_out < 0:
        errors.append(f"print.push_out_ul must be >= 0, got {push_out:g}")
    if push_out > p20_max:
        errors.append(
            f"print.push_out_ul {push_out:g} exceeds the P20's {p20_max:g} uL"
        )

    mix = src.get("mix_before_pass") or {}
    mix_reps = int(mix.get("reps", 0) or 0)
    if mix_reps < 0:
        errors.append("source.mix_before_pass.reps must be >= 0")
    if mix_reps and not (0 < float(mix.get("volume_ul", 0.0)) <= p20_max):
        errors.append(f"source.mix_before_pass.volume_ul must be in (0, {p20_max:g}]")

    if not rows:
        errors.append("print.rows must list at least one paper row")
    passes, layers = _layer_plan()
    if not passes:
        errors.append("print.passes must list at least one pass")
    for spec in passes:
        if not spec["columns"]:
            errors.append(f"print pass {spec['index']} lists no columns")
        if spec["rest_minutes"] < 0:
            errors.append(f"print pass {spec['index']} rest_minutes must be >= 0")
        if len(spec["columns"]) != len(set(spec["columns"])):
            errors.append(
                f"print pass {spec['index']} repeats a column: {spec['columns']}"
            )

    # Geometry: every well this run touches must actually exist.
    rack_names = labware["tuberack"].wells_by_name()
    vial = str(src["vial"])
    if vial not in rack_names:
        errors.append(f"source vial {vial} is not in the slot-7 tube rack")
    else:
        depth = labware["tuberack"][vial].depth
        height = float(src["aspirate_height_mm"])
        if not (0 < height < depth):
            errors.append(
                f"source.aspirate_height_mm {height:g} must be > 0 and < the "
                f"{depth:g} mm vial depth"
            )
    paper_names = labware["paper"].wells_by_name()
    paper_columns = len(labware["paper"].columns())
    for column in sorted(layers):
        if not (1 <= column <= paper_columns):
            errors.append(
                f"paper column {column} is outside the {paper_columns}-column paper"
            )
            continue
        for row in rows:
            if f"{row}{column}" not in paper_names:
                errors.append(f"paper well {row}{column} does not exist")

    tip_name = str(CONFIG["tips"]["p20"]["print_tip"])
    if tip_name not in labware["tiprack_p20"].wells_by_name():
        errors.append(f"P20 tip {tip_name} is outside the slot-9 tip rack")

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    protocol.comment("Pre-flight validation passed: config + labware geometry OK.")
    return rows, passes, layers


def _set_flow_rates(p20):
    rates = CONFIG.get("flow_rates", {}).get("p20", {})
    if rates.get("aspirate"):
        p20.flow_rate.aspirate = float(rates["aspirate"])
    if rates.get("dispense"):
        p20.flow_rate.dispense = float(rates["dispense"])


def _print_paper(protocol, labware, p20, rows, passes, layers):
    src = CONFIG["source"]
    pr = CONFIG["print"]
    return_tips = bool(CONFIG["tips"].get("return_tips", True))
    tip_name = str(CONFIG["tips"]["p20"]["print_tip"])
    vial = labware["tuberack"][str(src["vial"])]
    asp_h = float(src["aspirate_height_mm"])
    park_h = float(src.get("park_height_mm", 5.0))
    volume = float(pr["volume_ul"])
    z = float(pr["z_mm"])
    air_gap = float(pr.get("air_gap_ul", 0.0))
    air_gap_h = float(pr.get("air_gap_height_mm", 5.0))
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    blow_out = bool(pr.get("blow_out", True))
    dwell = float(pr.get("post_dispense_delay_s", 0.0) or 0.0)
    mix = src.get("mix_before_pass") or {}
    mix_reps = int(mix.get("reps", 0) or 0)

    # One tip for the entire job: a single vial, a single material, and the tip must
    # survive the drying rests between passes.
    p20.pick_up_tip(labware["tiprack_p20"][tip_name])
    protocol.comment(f"P20 print tip {tip_name} picked (held for the whole run).")

    for spec in passes:
        protocol.comment(
            f"--- Pass {spec['index']}/{len(passes)}: column(s) "
            + ", ".join(
                f"{c} (layer {spec['layer_of'][c]}/{layers[c]})"
                for c in spec["columns"]
            )
            + f", {len(rows)} row(s) each ---"
        )
        if mix_reps:
            p20.mix(mix_reps, float(mix["volume_ul"]),
                    vial.bottom(float(mix.get("height_mm", asp_h))))
            protocol.comment(f"Stock re-suspended: {mix_reps} mix(es) in vial {src['vial']}.")
        for column in spec["columns"]:
            for row in rows:
                paper_well = labware["paper"][f"{row}{column}"]
                p20.aspirate(volume, vial.bottom(asp_h))
                # Anti-drip: pull an air gap in below the droplet for the trip across
                # the deck. air_gap() lifts to the vial top first, so the gap is taken
                # out of the liquid.
                if air_gap > 0:
                    p20.air_gap(air_gap, height=air_gap_h)
                # Push the air gap and the droplet back out together. push_out drives
                # the plunger past its bottom stop on the same dispense, which shifts
                # the residual film off the orifice; blow_out then clears the tip
                # again. Belt and braces — blow-out alone was leaving liquid behind.
                if push_out > 0:
                    p20.dispense(volume + air_gap, paper_well.bottom(z),
                                 push_out=push_out)
                else:
                    p20.dispense(volume + air_gap, paper_well.bottom(z))
                if blow_out:
                    p20.blow_out(paper_well.bottom(z))
                if dwell > 0:
                    protocol.delay(seconds=dwell)
            protocol.comment(
                f"Column {column} layer {spec['layer_of'][column]}/{layers[column]} "
                f"done ({len(rows)} x {volume:g} uL)."
            )
        rest = spec["rest_minutes"]
        if rest > 0:
            # Park over the open source vial: any seepage during the rest falls back
            # into the stock instead of onto a drying spot.
            p20.move_to(vial.top(park_h))
            protocol.comment(
                f"Resting {rest:g} min for pass {spec['index']} to dry "
                f"(tip parked over vial {src['vial']})."
            )
            protocol.delay(minutes=rest)

    _release_tip(p20, return_tips)

    drops = sum(len(s["columns"]) for s in passes) * len(rows)
    protocol.comment(
        f"Paper print complete: {drops} droplet(s), {drops * volume:g} uL total. "
        + ", ".join(f"column {c} = {n} layer(s)" for c, n in sorted(layers.items()))
    )


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    labware = {
        role: _load_labware(protocol, deck[role])
        for role in ("tuberack", "paper", "tiprack_p20")
    }
    # Optional, unused: declared only so its height is known for travel clearance.
    if deck.get("plate"):
        labware["plate"] = _load_labware(protocol, deck["plate"])

    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )

    rows, passes, layers = _preflight(protocol, labware, p20)
    src = CONFIG["source"]
    pr = CONFIG["print"]

    protocol.comment("=== Direct Vial -> Paper Print V8 Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}, do_print={DEFAULT_DO_PRINT}")
    protocol.comment(
        f"Source: vial {src['vial']} ({src['material']}) in the slot-7 rack, "
        f"aspirating {src['aspirate_height_mm']} mm above the vial bottom. "
        "NOTHING IS DILUTED — this prints neat stock."
    )
    protocol.comment(
        f"Droplet: {pr['volume_ul']:g} uL + {pr.get('air_gap_ul', 0.0):g} uL air gap, "
        f"dispensed {pr['z_mm']:g} mm above the paper; "
        f"push_out {float(pr.get('push_out_ul', 0.0) or 0.0):g} uL"
        + (" then blow-out." if pr.get("blow_out", True) else ", blow-out OFF.")
    )
    protocol.comment(
        "Layer plan: "
        + ", ".join(f"column {c} x{n}" for c, n in sorted(layers.items()))
        + f" over {len(passes)} pass(es), rows {', '.join(rows)}."
    )
    drops = sum(len(s["columns"]) for s in passes) * len(rows)
    rest_total = sum(s["rest_minutes"] for s in passes)
    protocol.comment(
        f"Totals: {drops} droplet(s) = {drops * float(pr['volume_ul']):g} uL drawn, "
        f"{rest_total:g} min of drying rests."
    )

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: pre-flight only; no robot motion or liquid handling.")
        protocol.comment("=== Direct Vial -> Paper Print V8 Completed (dry run) ===")
        return

    _set_flow_rates(p20)
    if DEFAULT_DO_PRINT:
        _print_paper(protocol, labware, p20, rows, passes, layers)
    protocol.comment("=== Direct Vial -> Paper Print V8 Completed ===")
