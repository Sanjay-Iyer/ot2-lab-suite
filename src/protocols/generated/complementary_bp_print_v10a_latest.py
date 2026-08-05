"""Complementary direct-source paper print, v10a/v10b (OT-2 API 2.15).

This standalone-compatible template powers two complementary generated scripts:

* v10a prints BP from one 20 mL vial. Columns 1/2/3 receive 1/3/10
  layers, with rows A/B/C acting as three identical replicates.
* v10b prints DMMP from one 96-well plate location (default A1) onto the same
  3 x 3 paper grid. Rows A/B/C receive 1/2/3 layers in every column.

There is no dilution. One P20 tip serves each entire protocol. Deposits are made
in layer passes so one configurable drying rest separates successive layers.
The shared destination grid comes from complementary_v10_locations.yaml during
the build; generated protocols contain the resolved grid and need no YAML file.
"""
from __future__ import annotations

import math

from opentrons import protocol_api


metadata = {
    "protocolName": "Complementary BP/DMMP Paper Print v10 (P20, API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Direct-source layered printing for the complementary v10a BP and v10b "
        "DMMP workflows. No dilution."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN     = True
DEFAULT_DO_DILUTION = False
DEFAULT_DO_PRINT    = True


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'protocol_label': 'v10a',
  'deck': { 'source': { 'slot': 7,
                        'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                        'namespace': 'custom_beta',
                        'version': 1},
            'paper': { 'slot': 5,
                       'load_name': 'paper_print_96_flat',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': {'name': 'p20_single_gen2', 'mount': 'left'},
  'source': { 'kind': '20 mL vial',
              'well': 'A2',
              'material': 'BP',
              'loaded_volume_ul': 5000.0,
              'minimum_remaining_ul': 100.0,
              'aspirate_height_mm': 4.0,
              'park_height_mm': 5.0},
  'print': { 'volume_ul': 5.0,
             'layer_mode': 'by_column',
             'layers': {1: 1, 2: 3, 3: 10},
             'rest_minutes': 5.0,
             'z_mm': 4.0,
             'air_gap_ul': 1.5,
             'air_gap_height_mm': 5.0,
             'push_out_ul': 3.0,
             'blow_out': True,
             'post_dispense_delay_s': 2.0},
  'tips': {'return_tips': True, 'p20': {'print_tip': 'A1'}},
  'flow_rates': {'p20': {'aspirate': 3.0, 'dispense': 3.0}},
  'safety': {'p20_max_volume_ul': 20.0, 'expected_source_slot': 7},
  'destination': {'rows': ['A', 'B', 'C'], 'columns': [1, 2, 3]},
  'protocol_version': 10}
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


def _spot_layers():
    """Resolve the editable row/column layer rule into paper-well layer counts."""
    destination = CONFIG["destination"]
    pr = CONFIG["print"]
    rows = [str(row).upper() for row in destination["rows"]]
    columns = [int(column) for column in destination["columns"]]
    mode = str(pr["layer_mode"]).lower()
    raw_layers = pr["layers"]
    spots = {}
    if mode == "by_column":
        layers = {int(key): int(value) for key, value in raw_layers.items()}
        for column in columns:
            for row in rows:
                if column in layers:
                    spots[f"{row}{column}"] = layers[column]
    elif mode == "by_row":
        layers = {str(key).upper(): int(value) for key, value in raw_layers.items()}
        for column in columns:
            for row in rows:
                if row in layers:
                    spots[f"{row}{column}"] = layers[row]
    else:
        raise ValueError(f"print.layer_mode must be by_column or by_row, got {mode!r}")
    return rows, columns, spots


def _layer_plan():
    """Return efficient layer passes, omitting spots once their target is reached."""
    rows, columns, spots = _spot_layers()
    maximum = max(spots.values(), default=0)
    rest_minutes = float(CONFIG["print"].get("rest_minutes", 0.0) or 0.0)
    passes = []
    for layer in range(1, maximum + 1):
        active = [name for name, count in spots.items() if count >= layer]
        passes.append(
            {
                "index": layer,
                "spots": active,
                "rest_minutes": rest_minutes if layer < maximum else 0.0,
            }
        )
    return rows, columns, spots, passes


def _preflight(protocol, labware, p20):
    errors = []
    deck = CONFIG["deck"]
    src = CONFIG["source"]
    pr = CONFIG["print"]
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

    volume = float(pr["volume_ul"])
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    if not (0 < volume <= p20_max):
        errors.append(f"print.volume_ul must be in (0, {p20_max:g}]")
    if air_gap < 0 or volume + max(air_gap, 0.0) > p20_max:
        errors.append("print volume + nonnegative air gap must fit in the P20")
    if not (0 <= push_out <= p20_max):
        errors.append(f"print.push_out_ul must be in [0, {p20_max:g}]")
    for key in ("air_gap_height_mm", "z_mm", "post_dispense_delay_s", "rest_minutes"):
        if float(pr.get(key, 0.0) or 0.0) < 0:
            errors.append(f"print.{key} must be >= 0")

    try:
        rows, columns, spots, passes = _layer_plan()
    except (TypeError, ValueError) as exc:
        rows, columns, spots, passes = [], [], {}, []
        errors.append(str(exc))
    if not rows or len(rows) != len(set(rows)):
        errors.append("destination.rows must be nonempty and unique")
    if not columns or len(columns) != len(set(columns)):
        errors.append("destination.columns must be nonempty and unique")
    if len(spots) != len(rows) * len(columns):
        errors.append("print.layers must define every shared destination row/column")
    for name, count in spots.items():
        if count < 1:
            errors.append(f"destination {name} layer count must be >= 1")

    source_name = str(src["well"]).upper()
    source_names = labware["source"].wells_by_name()
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
        required = sum(spots.values()) * volume
        if not (0 < loaded <= source_well.max_volume):
            errors.append(
                f"source.loaded_volume_ul must be in (0, {source_well.max_volume:g}]"
            )
        if reserve < 0 or loaded < required + max(reserve, 0.0):
            errors.append(
                f"source needs {required:g} uL print volume plus {reserve:g} uL reserve; "
                f"loaded_volume_ul is {loaded:g}"
            )
        diameter = getattr(source_well, "diameter", None)
        if diameter:
            # For circular flat-bottom wells, ensure the final liquid column still
            # reaches above the configured aspiration point. This catches a wide
            # 20 mL vial loaded with too little volume even when the raw uL budget
            # would otherwise pass.
            cover_volume = math.pi * (float(diameter) / 2.0) ** 2 * aspirate_height
            remaining = loaded - required
            if remaining <= cover_volume:
                errors.append(
                    f"source would retain {remaining:g} uL after printing, below the "
                    f"approximately {cover_volume:g} uL needed to cover the "
                    f"{aspirate_height:g} mm aspiration height"
                )

    paper_names = labware["paper"].wells_by_name()
    for name in spots:
        if name not in paper_names:
            errors.append(f"paper destination {name} does not exist")
    tip_name = str(CONFIG["tips"]["p20"]["print_tip"]).upper()
    if tip_name not in labware["tiprack_p20"].wells_by_name():
        errors.append(f"P20 tip {tip_name} does not exist")

    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    protocol.comment("Pre-flight validation passed: config + labware geometry OK.")
    return rows, columns, spots, passes, source_name


def _set_flow_rates(p20):
    rates = CONFIG.get("flow_rates", {}).get("p20", {})
    if rates.get("aspirate"):
        p20.flow_rate.aspirate = float(rates["aspirate"])
    if rates.get("dispense"):
        p20.flow_rate.dispense = float(rates["dispense"])


def _print_paper(protocol, labware, p20, spots, passes, source_name):
    src = CONFIG["source"]
    pr = CONFIG["print"]
    source_well = labware["source"][source_name]
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
    protocol.comment(f"P20 tip {tip_name} picked and held for the complete run.")
    for spec in passes:
        protocol.comment(
            f"--- Layer pass {spec['index']}/{len(passes)}: "
            f"{', '.join(spec['spots'])} ---"
        )
        for destination_name in spec["spots"]:
            destination = labware["paper"][destination_name]
            p20.aspirate(volume, source_well.bottom(aspirate_height))
            if air_gap > 0:
                p20.air_gap(air_gap, height=air_gap_height)
            if push_out > 0:
                p20.dispense(
                    volume + air_gap, destination.bottom(z), push_out=push_out
                )
            else:
                p20.dispense(volume + air_gap, destination.bottom(z))
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
    deposits = sum(spots.values())
    protocol.comment(
        f"Print complete: {len(spots)} locations, {deposits} deposits, "
        f"{deposits * volume:g} uL total."
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
    rows, columns, spots, passes, source_name = _preflight(protocol, labware, p20)
    deposits = sum(spots.values())
    rest_total = sum(spec["rest_minutes"] for spec in passes)

    label = CONFIG.get("protocol_label", "v10")
    protocol.comment(f"=== Complementary Paper Print {label} Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}, do_print={DEFAULT_DO_PRINT}")
    protocol.comment(
        f"Source: {CONFIG['source']['kind']} well {source_name}, "
        f"material {CONFIG['source']['material']}; NO DILUTION."
    )
    protocol.comment(
        f"Shared grid: rows {', '.join(rows)}, columns "
        f"{', '.join(str(column) for column in columns)}; {len(spots)} locations."
    )
    protocol.comment(
        f"Totals: {deposits} x {float(CONFIG['print']['volume_ul']):g} uL = "
        f"{deposits * float(CONFIG['print']['volume_ul']):g} uL; "
        f"{rest_total:g} min drying rests."
    )

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: pre-flight only; no robot motion or liquid handling.")
        protocol.comment(f"=== Complementary Paper Print {label} Completed (dry run) ===")
        return
    _set_flow_rates(p20)
    if DEFAULT_DO_PRINT:
        _print_paper(protocol, labware, p20, spots, passes, source_name)
    protocol.comment(f"=== Complementary Paper Print {label} Completed ===")
