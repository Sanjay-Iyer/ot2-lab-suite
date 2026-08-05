"""
Direct 96-well plate -> paper print, protocol v9 (OT-2 API 2.15).

NO DILUTION. Every 5 uL deposit is drawn directly from one selectable well in
the slot-4 96-well source plate. The default source is A1; set `source.well` in
the v9 YAML to choose another well. Lowercase names such as `a1` are accepted.

The default paper pattern is:

    rows A/B/C = 1/3/10 layers
    columns 7/8/9 = three identical replicates

A layer pass puts one 5 uL deposit on every spot that still needs a layer, then
rests for five minutes before the next pass. There is no unnecessary rest after
the final pass. The default plan therefore makes 42 deposits (210 uL total) and
contains nine five-minute rests.

The liquid-delivery mechanics intentionally match the direct-vial v8 protocol:
one P20 tip, a small anti-drip air gap, push-out, blow-out, and a short dwell over
the paper. During drying rests, the empty tip parks over the source well.

The CONFIG block is replaced by scripts/build_vial_dilution_print.py. Edit
configs/printing/plate_well_direct_print_v9.yaml, not a generated protocol.
"""
from __future__ import annotations

from opentrons import protocol_api


metadata = {
    "protocolName": "Direct Plate-Well Paper Print v9 (P20, OT-2 API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Print neat stock from one selectable 96-well plate location onto three "
        "triplicate paper columns using 1/3/10 layered 5 uL deposits."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN     = True
DEFAULT_DO_DILUTION = False
DEFAULT_DO_PRINT    = True


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'deck': { 'plate': { 'slot': 4,
                       'load_name': 'corning_96_wellplate_360ul_custom',
                       'namespace': 'custom_beta',
                       'version': 1},
            'paper': { 'slot': 5,
                       'load_name': 'paper_print_96_flat',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': {'name': 'p20_single_gen2', 'mount': 'left'},
  'source': { 'well': 'A1',
              'material': 'sample',
              'loaded_volume_ul': 300.0,
              'minimum_remaining_ul': 20.0,
              'aspirate_height_mm': 1.0,
              'park_height_mm': 5.0},
  'print': { 'volume_ul': 5.0,
             'replicate_columns': [7, 8, 9],
             'layers_by_row': {'A': 1, 'B': 3, 'C': 10},
             'rest_minutes': 5.0,
             'z_mm': 4.0,
             'air_gap_ul': 1.5,
             'air_gap_height_mm': 5.0,
             'push_out_ul': 3.0,
             'blow_out': True,
             'post_dispense_delay_s': 2.0},
  'tips': {'return_tips': True, 'p20': {'print_tip': 'A1'}},
  'flow_rates': {'p20': {'aspirate': 3.0, 'dispense': 3.0}},
  'safety': {'p20_max_volume_ul': 20.0},
  'protocol_version': 9}
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
    """Return ordered layer passes and final layer counts by paper row."""
    raw = CONFIG["print"]["layers_by_row"]
    layers = {str(row).upper(): int(count) for row, count in raw.items()}
    maximum = max(layers.values(), default=0)
    rest_minutes = float(CONFIG["print"].get("rest_minutes", 0.0) or 0.0)
    passes = []
    for layer in range(1, maximum + 1):
        active_rows = [row for row, count in layers.items() if count >= layer]
        passes.append(
            {
                "index": layer,
                "rows": active_rows,
                "rest_minutes": rest_minutes if layer < maximum else 0.0,
            }
        )
    return passes, layers


def _preflight(protocol, labware, p20):
    errors = []
    deck = CONFIG["deck"]
    src = CONFIG["source"]
    pr = CONFIG["print"]
    p20_max = float(CONFIG["safety"]["p20_max_volume_ul"])

    for role, expected in (("plate", 4), ("paper", 5), ("tiprack_p20", 9)):
        actual = int(deck[role]["slot"])
        if actual != expected:
            errors.append(f"deck.{role} must be slot {expected}, got {actual}")
    if requirements != {"robotType": "OT-2", "apiLevel": "2.15"}:
        errors.append("protocol requirements must be OT-2 / API 2.15")
    if p20.name != CONFIG["pipette"]["name"]:
        errors.append(f"pipette must be {CONFIG['pipette']['name']}, got {p20.name}")

    volume = float(pr["volume_ul"])
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    if not (0 < volume <= p20_max):
        errors.append(f"print.volume_ul must be in (0, {p20_max:g}], got {volume:g}")
    if air_gap < 0:
        errors.append("print.air_gap_ul must be >= 0")
    if volume + max(air_gap, 0.0) > p20_max:
        errors.append(
            f"print volume {volume:g} + air gap {air_gap:g} exceeds "
            f"the P20 capacity of {p20_max:g} uL"
        )
    if push_out < 0 or push_out > p20_max:
        errors.append(f"print.push_out_ul must be in [0, {p20_max:g}]")
    for key in ("air_gap_height_mm", "z_mm", "post_dispense_delay_s", "rest_minutes"):
        if float(pr.get(key, 0.0) or 0.0) < 0:
            errors.append(f"print.{key} must be >= 0")

    passes, layers = _layer_plan()
    if not layers:
        errors.append("print.layers_by_row must list at least one paper row")
    for row, count in layers.items():
        if count < 1:
            errors.append(f"print.layers_by_row.{row} must be >= 1, got {count}")
    columns = [int(column) for column in pr.get("replicate_columns", [])]
    if not columns:
        errors.append("print.replicate_columns must list at least one paper column")
    if len(columns) != len(set(columns)):
        errors.append(f"print.replicate_columns contains duplicates: {columns}")

    source_name = str(src["well"]).upper()
    plate_names = labware["plate"].wells_by_name()
    if source_name not in plate_names:
        errors.append(f"source well {source_name} does not exist in the slot-4 plate")
    else:
        source_well = plate_names[source_name]
        aspirate_height = float(src["aspirate_height_mm"])
        if not (0 < aspirate_height < source_well.depth):
            errors.append(
                f"source.aspirate_height_mm {aspirate_height:g} must be > 0 and "
                f"< source-well depth {source_well.depth:g} mm"
            )
        loaded = float(src["loaded_volume_ul"])
        minimum_remaining = float(src.get("minimum_remaining_ul", 0.0) or 0.0)
        required = sum(layers.values()) * len(columns) * volume
        if not (0 < loaded <= source_well.max_volume):
            errors.append(
                f"source.loaded_volume_ul must be in (0, {source_well.max_volume:g}], "
                f"got {loaded:g}"
            )
        if minimum_remaining < 0:
            errors.append("source.minimum_remaining_ul must be >= 0")
        if loaded < required + max(minimum_remaining, 0.0):
            errors.append(
                f"source well needs at least {required + max(minimum_remaining, 0.0):g} "
                f"uL ({required:g} uL print + {minimum_remaining:g} uL reserve), "
                f"but loaded_volume_ul is {loaded:g}"
            )

    paper_names = labware["paper"].wells_by_name()
    paper_column_count = len(labware["paper"].columns())
    for column in columns:
        if not (1 <= column <= paper_column_count):
            errors.append(
                f"paper column {column} is outside the {paper_column_count}-column paper"
            )
            continue
        for row in layers:
            if f"{row}{column}" not in paper_names:
                errors.append(f"paper well {row}{column} does not exist")

    tip_name = str(CONFIG["tips"]["p20"]["print_tip"]).upper()
    if tip_name not in labware["tiprack_p20"].wells_by_name():
        errors.append(f"P20 tip {tip_name} is outside the slot-9 tip rack")

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    protocol.comment("Pre-flight validation passed: config + labware geometry OK.")
    return passes, layers, columns, source_name


def _set_flow_rates(p20):
    rates = CONFIG.get("flow_rates", {}).get("p20", {})
    if rates.get("aspirate"):
        p20.flow_rate.aspirate = float(rates["aspirate"])
    if rates.get("dispense"):
        p20.flow_rate.dispense = float(rates["dispense"])


def _print_paper(protocol, labware, p20, passes, layers, columns, source_name):
    src = CONFIG["source"]
    pr = CONFIG["print"]
    source_well = labware["plate"][source_name]
    volume = float(pr["volume_ul"])
    aspirate_height = float(src["aspirate_height_mm"])
    park_height = float(src.get("park_height_mm", 5.0))
    z = float(pr["z_mm"])
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    air_gap_height = float(pr.get("air_gap_height_mm", 5.0))
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    blow_out = bool(pr.get("blow_out", True))
    dwell = float(pr.get("post_dispense_delay_s", 0.0) or 0.0)
    tip_name = str(CONFIG["tips"]["p20"]["print_tip"]).upper()

    p20.pick_up_tip(labware["tiprack_p20"][tip_name])
    protocol.comment(f"P20 print tip {tip_name} picked and held for the whole run.")

    for spec in passes:
        protocol.comment(
            f"--- Layer pass {spec['index']}/{len(passes)}: paper rows "
            f"{', '.join(spec['rows'])}, columns "
            f"{', '.join(str(column) for column in columns)} ---"
        )
        for row in spec["rows"]:
            for column in columns:
                destination = labware["paper"][f"{row}{column}"]
                p20.aspirate(volume, source_well.bottom(aspirate_height))
                if air_gap > 0:
                    p20.air_gap(air_gap, height=air_gap_height)
                dispense_volume = volume + air_gap
                if push_out > 0:
                    p20.dispense(
                        dispense_volume, destination.bottom(z), push_out=push_out
                    )
                else:
                    p20.dispense(dispense_volume, destination.bottom(z))
                if blow_out:
                    p20.blow_out(destination.bottom(z))
                if dwell > 0:
                    protocol.delay(seconds=dwell)
        rest = spec["rest_minutes"]
        if rest > 0:
            p20.move_to(source_well.top(park_height))
            protocol.comment(
                f"Resting {rest:g} min after layer {spec['index']}; "
                f"tip parked over source {source_name}."
            )
            protocol.delay(minutes=rest)

    _release_tip(p20, bool(CONFIG["tips"].get("return_tips", True)))
    drops = sum(layers.values()) * len(columns)
    protocol.comment(
        f"Paper print complete: {drops} deposits, {drops * volume:g} uL total; "
        + ", ".join(f"row {row} x{count}" for row, count in layers.items())
        + f" in each of columns {', '.join(str(column) for column in columns)}."
    )


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    labware = {
        role: _load_labware(protocol, deck[role])
        for role in ("plate", "paper", "tiprack_p20")
    }
    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )

    passes, layers, columns, source_name = _preflight(protocol, labware, p20)
    pr = CONFIG["print"]
    drops = sum(layers.values()) * len(columns)
    total_rest = sum(spec["rest_minutes"] for spec in passes)

    protocol.comment("=== Direct Plate-Well -> Paper Print V9 Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}, do_print={DEFAULT_DO_PRINT}")
    protocol.comment(
        f"Source: slot-4 plate well {source_name} ({CONFIG['source']['material']}); "
        "NO DILUTION."
    )
    protocol.comment(
        "Pattern: "
        + ", ".join(f"row {row} x{count}" for row, count in layers.items())
        + f"; triplicate columns {', '.join(str(column) for column in columns)}."
    )
    protocol.comment(
        f"Totals: {drops} x {float(pr['volume_ul']):g} uL = "
        f"{drops * float(pr['volume_ul']):g} uL drawn; "
        f"{total_rest:g} min of drying rests."
    )

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: pre-flight only; no robot motion or liquid handling.")
        protocol.comment("=== Direct Plate-Well -> Paper Print V9 Completed (dry run) ===")
        return

    _set_flow_rates(p20)
    if DEFAULT_DO_PRINT:
        _print_paper(protocol, labware, p20, passes, layers, columns, source_name)
    protocol.comment("=== Direct Plate-Well -> Paper Print V9 Completed ===")
