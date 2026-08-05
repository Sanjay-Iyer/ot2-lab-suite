"""Combined BP + DMMP paper print v11 (OT-2 Python Protocol API 2.15).

This protocol executes both complementary printing stages in one run:

1. Part A prints BP directly from one 20 mL vial. On the shared A-C x 1-3
   paper grid, columns 1/2/3 receive 1/3/10 layers respectively.
2. The dedicated BP tip is released and the protocol waits 20 minutes.
3. Part B picks a separate tip and prints DMMP directly from one selectable
   96-well plate location (default A1). Rows A/B/C receive 1/2/3 layers in
   every shared column.

There is no dilution. Separate tips prevent BP/DMMP source cross-contamination.
Each part uses efficient layer passes and configurable five-minute drying rests.
The shared paper grid is resolved from complementary_v10_locations.yaml during
the build, then embedded into the standalone generated robot protocol.
"""
from __future__ import annotations

import math

from opentrons import protocol_api


metadata = {
    "protocolName": "Combined BP + DMMP Paper Print v11 (P20, API 2.15)",
    "author": "OT-2 Lab Suite",
    "description": (
        "Print BP from a 20 mL vial, wait 20 minutes, then overlay DMMP from "
        "a selectable 96-well plate source on the same 3 x 3 paper grid."
    ),
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


DEFAULT_DRY_RUN     = True
DEFAULT_DO_DILUTION = False
DEFAULT_DO_PRINT    = True


# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'protocol_label': 'v11',
  'deck': { 'bp_source': { 'slot': 7,
                           'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                           'namespace': 'custom_beta',
                           'version': 1},
            'dmmp_source': { 'slot': 4,
                             'load_name': 'corning_96_wellplate_360ul_custom',
                             'namespace': 'custom_beta',
                             'version': 1},
            'paper': { 'slot': 5,
                       'load_name': 'paper_print_96_flat',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack_p20': {'slot': 9, 'load_name': 'opentrons_96_tiprack_20ul'}},
  'pipette': {'name': 'p20_single_gen2', 'mount': 'left'},
  'between_parts_delay_minutes': 20.0,
  'parts': [ { 'label': 'Part A',
               'material': 'BP',
               'source_kind': '20 mL vial',
               'source_role': 'bp_source',
               'source_well': 'A2',
               'loaded_volume_ul': 5000.0,
               'minimum_remaining_ul': 100.0,
               'aspirate_height_mm': 4.0,
               'park_height_mm': 5.0,
               'print_tip': 'A1',
               'layer_mode': 'by_column',
               'layers': {1: 1, 2: 3, 3: 10},
               'rest_minutes': 5.0},
             { 'label': 'Part B',
               'material': 'DMMP',
               'source_kind': '96-well plate',
               'source_role': 'dmmp_source',
               'source_well': 'A1',
               'loaded_volume_ul': 150.0,
               'minimum_remaining_ul': 20.0,
               'aspirate_height_mm': 1.0,
               'park_height_mm': 5.0,
               'print_tip': 'A2',
               'layer_mode': 'by_row',
               'layers': {'A': 1, 'B': 2, 'C': 3},
               'rest_minutes': 5.0}],
  'print': { 'volume_ul': 5.0,
             'z_mm': 4.0,
             'air_gap_ul': 1.5,
             'air_gap_height_mm': 5.0,
             'push_out_ul': 3.0,
             'blow_out': True,
             'post_dispense_delay_s': 2.0},
  'tips': {'return_tips': True},
  'flow_rates': {'p20': {'aspirate': 3.0, 'dispense': 3.0}},
  'safety': {'p20_max_volume_ul': 20.0},
  'destination': {'rows': ['A', 'B', 'C'], 'columns': [1, 2, 3]},
  'protocol_version': 12}
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


def _spot_layers(part):
    destination = CONFIG["destination"]
    rows = [str(row).upper() for row in destination["rows"]]
    columns = [int(column) for column in destination["columns"]]
    mode = str(part["layer_mode"]).lower()
    raw_layers = part["layers"]
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
        raise ValueError(
            f"{part.get('label', 'part')}.layer_mode must be by_column or by_row"
        )
    return rows, columns, spots


def _resolve_plans():
    """Resolve both configurable parts into minimal ordered layer passes."""
    plans = []
    shared_rows = None
    shared_columns = None
    for part in CONFIG["parts"]:
        rows, columns, spots = _spot_layers(part)
        if shared_rows is None:
            shared_rows, shared_columns = rows, columns
        maximum = max(spots.values(), default=0)
        rest_minutes = float(part.get("rest_minutes", 0.0) or 0.0)
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
        plans.append({"part": part, "spots": spots, "passes": passes})
    return shared_rows or [], shared_columns or [], plans


def _preflight(protocol, labware, p20):
    errors = []
    deck = CONFIG["deck"]
    pr = CONFIG["print"]
    p20_max = float(CONFIG["safety"]["p20_max_volume_ul"])

    expected_slots = {
        "bp_source": 7,
        "dmmp_source": 4,
        "paper": 5,
        "tiprack_p20": 9,
    }
    for role, expected in expected_slots.items():
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
    for key in ("air_gap_height_mm", "z_mm", "post_dispense_delay_s"):
        if float(pr.get(key, 0.0) or 0.0) < 0:
            errors.append(f"print.{key} must be >= 0")
    between_delay = float(CONFIG.get("between_parts_delay_minutes", 0.0) or 0.0)
    if between_delay < 0:
        errors.append("between_parts_delay_minutes must be >= 0")

    try:
        rows, columns, plans = _resolve_plans()
    except (TypeError, ValueError) as exc:
        rows, columns, plans = [], [], []
        errors.append(str(exc))
    if len(CONFIG.get("parts", [])) != 2:
        errors.append("parts must contain exactly Part A and Part B")
    if not rows or len(rows) != len(set(rows)):
        errors.append("destination.rows must be nonempty and unique")
    if not columns or len(columns) != len(set(columns)):
        errors.append("destination.columns must be nonempty and unique")

    paper_names = labware["paper"].wells_by_name()
    tiprack_names = labware["tiprack_p20"].wells_by_name()
    assigned_tips = []
    for plan in plans:
        part = plan["part"]
        label = str(part.get("label", "part"))
        spots = plan["spots"]
        if len(spots) != len(rows) * len(columns):
            errors.append(f"{label}.layers must define every destination")
        for name, count in spots.items():
            if count < 1:
                errors.append(f"{label} destination {name} layers must be >= 1")
            if name not in paper_names:
                errors.append(f"paper destination {name} does not exist")
        if float(part.get("rest_minutes", 0.0) or 0.0) < 0:
            errors.append(f"{label}.rest_minutes must be >= 0")

        source_role = str(part["source_role"])
        if source_role not in ("bp_source", "dmmp_source"):
            errors.append(f"{label}.source_role is invalid: {source_role}")
            continue
        source_name = str(part["source_well"]).upper()
        source_names = labware[source_role].wells_by_name()
        if source_name not in source_names:
            errors.append(f"{label} source well {source_name} does not exist")
        else:
            source_well = source_names[source_name]
            aspirate_height = float(part["aspirate_height_mm"])
            if not (0 < aspirate_height < source_well.depth):
                errors.append(
                    f"{label}.aspirate_height_mm must be > 0 and "
                    f"< {source_well.depth:g} mm"
                )
            loaded = float(part["loaded_volume_ul"])
            reserve = float(part.get("minimum_remaining_ul", 0.0) or 0.0)
            required = sum(spots.values()) * volume
            if not (0 < loaded <= source_well.max_volume):
                errors.append(
                    f"{label}.loaded_volume_ul must be in "
                    f"(0, {source_well.max_volume:g}]"
                )
            if reserve < 0 or loaded < required + max(reserve, 0.0):
                errors.append(
                    f"{label} needs {required:g} uL plus {reserve:g} uL reserve; "
                    f"loaded_volume_ul is {loaded:g}"
                )
            diameter = getattr(source_well, "diameter", None)
            if diameter:
                cover_volume = (
                    math.pi * (float(diameter) / 2.0) ** 2 * aspirate_height
                )
                remaining = loaded - required
                if remaining <= cover_volume:
                    errors.append(
                        f"{label} would retain {remaining:g} uL, below the "
                        f"approximately {cover_volume:g} uL needed to cover the "
                        f"aspiration height"
                    )
        plan["source_name"] = source_name

        tip_name = str(part["print_tip"]).upper()
        assigned_tips.append(tip_name)
        if tip_name not in tiprack_names:
            errors.append(f"{label} P20 tip {tip_name} does not exist")
        plan["tip_name"] = tip_name

    if len(assigned_tips) != len(set(assigned_tips)):
        errors.append(f"Part A and Part B must use separate tips, got {assigned_tips}")
    if errors:
        protocol.comment("PRE-FLIGHT VALIDATION FAILED")
        raise RuntimeError("PRE-FLIGHT VALIDATION FAILED:\n- " + "\n- ".join(errors))
    protocol.comment("Pre-flight validation passed: both parts + labware geometry OK.")
    return rows, columns, plans


def _set_flow_rates(p20):
    rates = CONFIG.get("flow_rates", {}).get("p20", {})
    if rates.get("aspirate"):
        p20.flow_rate.aspirate = float(rates["aspirate"])
    if rates.get("dispense"):
        p20.flow_rate.dispense = float(rates["dispense"])


def _print_part(protocol, labware, p20, plan):
    part = plan["part"]
    pr = CONFIG["print"]
    source = labware[part["source_role"]][plan["source_name"]]
    volume = float(pr["volume_ul"])
    aspirate_height = float(part["aspirate_height_mm"])
    park_height = float(part.get("park_height_mm", 5.0))
    z = float(pr["z_mm"])
    air_gap = float(pr.get("air_gap_ul", 0.0) or 0.0)
    air_gap_height = float(pr.get("air_gap_height_mm", 5.0))
    push_out = float(pr.get("push_out_ul", 0.0) or 0.0)
    blow_out = bool(pr.get("blow_out", True))
    dwell = float(pr.get("post_dispense_delay_s", 0.0) or 0.0)

    p20.pick_up_tip(labware["tiprack_p20"][plan["tip_name"]])
    protocol.comment(
        f"{part['label']}: dedicated tip {plan['tip_name']} picked for "
        f"{part['material']}."
    )
    for spec in plan["passes"]:
        protocol.comment(
            f"--- {part['label']} layer {spec['index']}/{len(plan['passes'])}: "
            f"{', '.join(spec['spots'])} ---"
        )
        for destination_name in spec["spots"]:
            destination = labware["paper"][destination_name]
            p20.aspirate(volume, source.bottom(aspirate_height))
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
            p20.move_to(source.top(park_height))
            protocol.comment(
                f"{part['label']}: resting {rest:g} min after layer "
                f"{spec['index']}; tip parked over source."
            )
            protocol.delay(minutes=rest)
    _release_tip(p20, bool(CONFIG["tips"].get("return_tips", True)))
    deposits = sum(plan["spots"].values())
    protocol.comment(
        f"{part['label']} complete: {len(plan['spots'])} locations, "
        f"{deposits} deposits, {deposits * volume:g} uL {part['material']}."
    )


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    labware = {
        role: _load_labware(protocol, deck[role])
        for role in ("bp_source", "dmmp_source", "paper", "tiprack_p20")
    }
    pip_cfg = CONFIG["pipette"]
    p20 = protocol.load_instrument(
        pip_cfg["name"], pip_cfg["mount"], tip_racks=[labware["tiprack_p20"]]
    )
    rows, columns, plans = _preflight(protocol, labware, p20)
    between_delay = float(CONFIG["between_parts_delay_minutes"])

    protocol.comment("=== Combined BP + DMMP Paper Print v11 Started ===")
    protocol.comment(f"Flags: dry_run={DEFAULT_DRY_RUN}, do_print={DEFAULT_DO_PRINT}")
    protocol.comment(
        f"Shared grid: rows {', '.join(rows)}, columns "
        f"{', '.join(str(column) for column in columns)}."
    )
    for plan in plans:
        deposits = sum(plan["spots"].values())
        protocol.comment(
            f"{plan['part']['label']}: {plan['part']['material']} from "
            f"{plan['part']['source_kind']} well {plan['source_name']}; "
            f"{deposits} x {float(CONFIG['print']['volume_ul']):g} uL."
        )
    protocol.comment(
        f"Inter-part delay: {between_delay:g} min after Part A tip release."
    )

    if DEFAULT_DRY_RUN:
        protocol.comment("DRY RUN: pre-flight only; no robot motion or liquid handling.")
        protocol.comment("=== Combined BP + DMMP Paper Print v11 Completed (dry run) ===")
        return

    _set_flow_rates(p20)
    if DEFAULT_DO_PRINT:
        _print_part(protocol, labware, p20, plans[0])
        protocol.comment(
            f"Part A tip released. Waiting {between_delay:g} min before Part B."
        )
        if between_delay > 0:
            protocol.delay(minutes=between_delay)
        _print_part(protocol, labware, p20, plans[1])
    protocol.comment("=== Combined BP + DMMP Paper Print v11 Completed ===")
