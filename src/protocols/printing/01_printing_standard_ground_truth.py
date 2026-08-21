"""Frozen static ground truth for paper Experiment 01.

This file intentionally hardcodes the scientific experiment.  It is an
independently prepared reference, not the reusable configuration executor.
Future experiment configurations must match its canonical physical-action trace;
they must never import this module as their implementation.
"""

from __future__ import annotations

from fractions import Fraction
import json
import math
from pathlib import Path
from typing import Any

from opentrons import protocol_api


metadata = {
    "protocolName": "Experiment 01 - Standard SERS Printing Ground Truth",
    "author": "Laboratory automation ground truth",
    "description": "Static reference: NP/CV preparation and four-column paper print",
}
requirements = {"robotType": "OT-2", "apiLevel": "2.15"}


ROWS = tuple("ABCDEFGH")
FACTORS = tuple(2**index for index in range(8))
TARGET_USABLE_UL = Fraction(30, 1)
MAX_P20_TRANSFER_UL = Fraction(20, 1)
PRINT_VOLUME_UL = 5.0
DRYING_DELAY_S = 300.0
MIX_CYCLES = 3
MIX_VOLUME_UL = 3.0
PLATE_ASPIRATE_HEIGHT_MM = 0.2
PLATE_WELL_DIAMETER_MM = 6.86
VIAL_ASPIRATE_HEIGHT_MM = 4.0
VIAL_DIAMETER_MM = 28.0
VIAL_LOADED_VOLUME_UL = 5000.0
VIAL_MINIMUM_REMAINING_UL = 2600.0
PAPER_DISPENSE_HEIGHT_MM = 0.5
POST_DISPENSE_DELAY_S = 2.0
AIR_GAP_UL = 1.5
AIR_GAP_HEIGHT_MM = 5.0
PUSH_OUT_UL = 3.0

NP_PLATE_WELLS = tuple(f"{row}1" for row in ROWS)
CV_PLATE_WELLS = tuple(f"{row}2" for row in ROWS)


def _as_float(value: Fraction | float | int) -> float:
    return float(value)


def split_p20_volume(
    total_ul: Fraction | float | int,
    maximum_ul: Fraction | float | int = MAX_P20_TRANSFER_UL,
) -> tuple[float, ...]:
    """Split one physical transfer into exact positive P20-sized chunks."""
    remaining = Fraction(str(total_ul))
    maximum = Fraction(str(maximum_ul))
    if remaining <= 0 or maximum <= 0:
        raise ValueError("transfer volumes must be positive")
    chunks: list[Fraction] = []
    while remaining > 0:
        chunk = min(remaining, maximum)
        chunks.append(chunk)
        remaining -= chunk
    if sum(chunks, Fraction(0)) != Fraction(str(total_ul)):
        raise RuntimeError("P20 splitting changed the requested volume")
    return tuple(_as_float(chunk) for chunk in chunks)


def serial_cascade(
    *,
    target_usable_ul: Fraction = TARGET_USABLE_UL,
    count: int = 8,
    fold: int = 2,
) -> dict[str, Any]:
    """Resolve a serial dilution that retains exactly the target in every well.

    Working backwards avoids unexplained round numbers.  Each destination's
    pre-transfer volume is chosen so that, after its downstream aliquot leaves,
    exactly ``target_usable_ul`` remains.  For eight twofold conditions this
    requires 59.765625 uL of original stock, matching the approximately 60 uL
    scientific allocation without any sub-microlitre transfer.
    """
    if count < 1 or fold < 2 or target_usable_ul <= 0:
        raise ValueError("count, fold, and target usable volume are invalid")
    pre_transfer = [Fraction(0) for _ in range(count)]
    outgoing = [Fraction(0) for _ in range(count)]
    diluent = [Fraction(0) for _ in range(count)]
    pre_transfer[-1] = target_usable_ul
    for index in range(count - 2, -1, -1):
        outgoing[index] = pre_transfer[index + 1] / fold
        diluent[index + 1] = pre_transfer[index + 1] - outgoing[index]
        pre_transfer[index] = target_usable_ul + outgoing[index]

    retained = [
        pre_transfer[index] - outgoing[index] for index in range(count)
    ]
    if any(value != target_usable_ul for value in retained):
        raise RuntimeError("serial cascade did not retain the requested usable volume")
    return {
        "count": count,
        "fold": fold,
        "factors": [fold**index for index in range(count)],
        "stock_allocation_ul": _as_float(pre_transfer[0]),
        "pre_transfer_ul": [_as_float(value) for value in pre_transfer],
        "outgoing_ul": [_as_float(value) for value in outgoing],
        "diluent_ul": [_as_float(value) for value in diluent],
        "retained_usable_ul": [_as_float(value) for value in retained],
        "total_diluent_ul": _as_float(sum(diluent, Fraction(0))),
    }


def _location(
    labware: str,
    well: str,
    reference: str,
    z_mm: float,
) -> dict[str, Any]:
    return {
        "labware": labware,
        "well": well,
        "reference": reference,
        "z_mm": float(z_mm),
    }


def build_ground_truth_plan() -> dict[str, Any]:
    """Return Experiment 01 as a canonical ordered physical-action trace."""
    cascade = serial_cascade()
    actions: list[dict[str, Any]] = []

    def add(action_type: str, **fields: Any) -> None:
        actions.append(
            {
                "sequence_index": len(actions) + 1,
                "action": action_type,
                **fields,
            }
        )

    add(
        "LOAD_LABWARE",
        role="vial_rack",
        slot=7,
        load_name="tuberack_3dprint_20ml_8vials_v2",
        namespace="custom_beta",
        version=1,
    )
    add(
        "LOAD_LABWARE",
        role="plate",
        slot=4,
        load_name="corning_96_wellplate_360ul_custom",
        namespace="custom_beta",
        version=1,
    )
    add(
        "LOAD_LABWARE",
        role="paper",
        slot=5,
        load_name="paper_print_96_flat",
        namespace="custom_beta",
        version=1,
    )
    add(
        "LOAD_LABWARE",
        role="tiprack",
        slot=9,
        load_name="opentrons_96_tiprack_20ul",
        namespace="opentrons",
        version=1,
    )
    add(
        "LOAD_PIPETTE",
        pipette="p20_single_gen2",
        mount="left",
        tiprack_role="tiprack",
        minimum_volume_ul=1.0,
        maximum_volume_ul=20.0,
        flow_rates={"aspirate_ul_s": 3.0, "dispense_ul_s": 3.0},
    )

    initial_liquids = [
        {
            "liquid_id": "np_stock",
            "display_name": "Nanoparticle stock",
            "location": _location("vial_rack", "A1", "bottom", VIAL_ASPIRATE_HEIGHT_MM),
            "loaded_volume_ul": VIAL_LOADED_VOLUME_UL,
            "minimum_remaining_ul": VIAL_MINIMUM_REMAINING_UL,
            "scientific_allocation_ul": cascade["stock_allocation_ul"],
        },
        {
            "liquid_id": "np_diluent",
            "display_name": "Nanoparticle diluent",
            "location": _location("vial_rack", "A2", "bottom", VIAL_ASPIRATE_HEIGHT_MM),
            "loaded_volume_ul": VIAL_LOADED_VOLUME_UL,
            "minimum_remaining_ul": VIAL_MINIMUM_REMAINING_UL,
            "scientific_allocation_ul": cascade["total_diluent_ul"],
        },
        {
            "liquid_id": "cv_stock",
            "display_name": "Crystal violet stock",
            "location": _location("vial_rack", "A3", "bottom", VIAL_ASPIRATE_HEIGHT_MM),
            "loaded_volume_ul": VIAL_LOADED_VOLUME_UL,
            "minimum_remaining_ul": VIAL_MINIMUM_REMAINING_UL,
            "scientific_allocation_ul": cascade["stock_allocation_ul"] + 120.0,
        },
        {
            "liquid_id": "cv_diluent",
            "display_name": "Crystal violet diluent",
            "location": _location("vial_rack", "A4", "bottom", VIAL_ASPIRATE_HEIGHT_MM),
            "loaded_volume_ul": VIAL_LOADED_VOLUME_UL,
            "minimum_remaining_ul": VIAL_MINIMUM_REMAINING_UL,
            "scientific_allocation_ul": cascade["total_diluent_ul"],
        },
    ]

    def transfer_volume(
        *,
        operation_id: str,
        liquid_id: str,
        result_liquid_id: str,
        source: dict[str, Any],
        destination: dict[str, Any],
        total_ul: float,
        tip_group: str,
    ) -> None:
        chunks = split_p20_volume(total_ul)
        for chunk_index, chunk in enumerate(chunks, start=1):
            add(
                "TRANSFER",
                operation_id=operation_id,
                liquid_id=liquid_id,
                result_liquid_id=result_liquid_id,
                source=source,
                destination=destination,
                volume_ul=chunk,
                chunk_index=chunk_index,
                chunk_count=len(chunks),
                pipette="p20_single_gen2",
                tip_group=tip_group,
            )

    def mix(
        *,
        operation_id: str,
        liquid_id: str,
        well: str,
        tip_group: str,
    ) -> None:
        add(
            "MIX",
            operation_id=operation_id,
            liquid_id=liquid_id,
            location=_location(
                "plate", well, "bottom", PLATE_ASPIRATE_HEIGHT_MM
            ),
            cycles=MIX_CYCLES,
            volume_ul=MIX_VOLUME_UL,
            pipette="p20_single_gen2",
            tip_group=tip_group,
        )

    def prepare_series(
        *,
        prefix: str,
        stock_well: str,
        diluent_well: str,
        plate_wells: tuple[str, ...],
    ) -> None:
        series_ids = tuple(
            f"{prefix}_{'stock' if factor == 1 else f'1_{factor}x'}"
            for factor in FACTORS
        )
        stock_group = f"{prefix}_stock_setup"
        transfer_volume(
            operation_id=f"prepare_{prefix}_stock",
            liquid_id=f"{prefix}_stock",
            result_liquid_id=series_ids[0],
            source=_location("vial_rack", stock_well, "bottom", VIAL_ASPIRATE_HEIGHT_MM),
            destination=_location("plate", plate_wells[0], "top", -2.0),
            total_ul=cascade["stock_allocation_ul"],
            tip_group=stock_group,
        )
        mix(
            operation_id=f"mix_{prefix}_stock",
            liquid_id=series_ids[0],
            well=plate_wells[0],
            tip_group=stock_group,
        )

        diluent_group = f"{prefix}_diluent_setup"
        for index in range(1, len(plate_wells)):
            transfer_volume(
                operation_id=f"prepare_{prefix}_diluent_{index + 1}",
                liquid_id=f"{prefix}_diluent",
                result_liquid_id=f"{prefix}_diluent",
                source=_location("vial_rack", diluent_well, "bottom", VIAL_ASPIRATE_HEIGHT_MM),
                destination=_location("plate", plate_wells[index], "top", -2.0),
                total_ul=cascade["diluent_ul"][index],
                tip_group=diluent_group,
            )

        for index in range(len(plate_wells) - 1):
            serial_group = f"{prefix}_serial_{index + 1}_to_{index + 2}"
            transfer_volume(
                operation_id=f"serial_{prefix}_{index + 1}_to_{index + 2}",
                liquid_id=series_ids[index],
                result_liquid_id=series_ids[index + 1],
                source=_location(
                    "plate", plate_wells[index], "bottom", PLATE_ASPIRATE_HEIGHT_MM
                ),
                destination=_location("plate", plate_wells[index + 1], "top", -2.0),
                total_ul=cascade["outgoing_ul"][index],
                tip_group=serial_group,
            )
            mix(
                operation_id=f"mix_{prefix}_{index + 2}",
                liquid_id=series_ids[index + 1],
                well=plate_wells[index + 1],
                tip_group=serial_group,
            )

    prepare_series(prefix="np", stock_well="A1", diluent_well="A2", plate_wells=NP_PLATE_WELLS)
    prepare_series(prefix="cv", stock_well="A3", diluent_well="A4", plate_wells=CV_PLATE_WELLS)

    def print_drop(
        *,
        operation_id: str,
        condition_id: str,
        liquid_id: str,
        source: dict[str, Any],
        destination_well: str,
        tip_group: str,
        drop_index: int = 1,
        repeat_count: int = 1,
    ) -> None:
        add(
            "PRINT",
            operation_id=operation_id,
            condition_id=condition_id,
            liquid_id=liquid_id,
            source=source,
            destination=_location(
                "paper", destination_well, "bottom", PAPER_DISPENSE_HEIGHT_MM
            ),
            volume_ul=PRINT_VOLUME_UL,
            air_gap_ul=AIR_GAP_UL,
            air_gap_height_mm=AIR_GAP_HEIGHT_MM,
            piston_dispense_ul=PRINT_VOLUME_UL + AIR_GAP_UL,
            push_out_ul=PUSH_OUT_UL,
            blow_out=True,
            post_dispense_delay_s=POST_DISPENSE_DELAY_S,
            pipette="p20_single_gen2",
            drop_index=drop_index,
            repeat_count=repeat_count,
            tip_group=tip_group,
        )

    def mix_and_print_np(row_index: int, paper_column: int, phase: str, drop_index: int, repeat_count: int) -> None:
        row = ROWS[row_index]
        factor = FACTORS[row_index]
        liquid_id = f"np_{'stock' if factor == 1 else f'1_{factor}x'}"
        source_well = NP_PLATE_WELLS[row_index]
        group = f"{phase}_{row}"
        mix(
            operation_id=f"mix_before_{phase}_{row}",
            liquid_id=liquid_id,
            well=source_well,
            tip_group=group,
        )
        print_drop(
            operation_id=f"print_{phase}_{row}",
            condition_id=f"paper_{row}{paper_column}",
            liquid_id=liquid_id,
            source=_location(
                "plate", source_well, "bottom", PLATE_ASPIRATE_HEIGHT_MM
            ),
            destination_well=f"{row}{paper_column}",
            tip_group=group,
            drop_index=drop_index,
            repeat_count=repeat_count,
        )

    def print_stock_cv_column(column: int, phase: str) -> None:
        group = f"{phase}_stock_cv"
        for row in ROWS:
            print_drop(
                operation_id=f"print_{phase}_{row}",
                condition_id=f"paper_{row}{column}",
                liquid_id="cv_stock",
                source=_location(
                    "vial_rack", "A3", "bottom", VIAL_ASPIRATE_HEIGHT_MM
                ),
                destination_well=f"{row}{column}",
                tip_group=group,
            )

    # Column 1: one NP layer, standardized dry, then stock CV.
    for index in range(8):
        mix_and_print_np(index, 1, "column1_np", 1, 1)
    add(
        "DELAY",
        operation_id="column1_np_drying",
        duration_s=DRYING_DELAY_S,
        reason="dry all column-1 nanoparticle deposits before stock CV",
    )
    print_stock_cv_column(1, "column1_cv")

    # Column 2: three NP layer passes with an explicit five-minute dry after
    # every pass, including the third pass before stock CV.
    for drop_index in range(1, 4):
        phase = f"column2_np_drop_{drop_index}"
        for index in range(8):
            mix_and_print_np(index, 2, phase, drop_index, 3)
        add(
            "DELAY",
            operation_id=f"column2_np_drop_{drop_index}_drying",
            duration_s=DRYING_DELAY_S,
            reason=f"dry column-2 nanoparticle layer {drop_index} before the next layer",
        )
    print_stock_cv_column(2, "column2_cv")

    # Column 3: eight matched stock-CV-only controls.
    print_stock_cv_column(3, "column3_cv_control")

    # Column 4: one 5 uL print from every prepared relative CV concentration.
    for index, row in enumerate(ROWS):
        factor = FACTORS[index]
        liquid_id = f"cv_{'stock' if factor == 1 else f'1_{factor}x'}"
        source_well = CV_PLATE_WELLS[index]
        group = f"column4_cv_{row}"
        mix(
            operation_id=f"mix_before_column4_cv_{row}",
            liquid_id=liquid_id,
            well=source_well,
            tip_group=group,
        )
        print_drop(
            operation_id=f"print_column4_cv_{row}",
            condition_id=f"paper_{row}4",
            liquid_id=liquid_id,
            source=_location(
                "plate", source_well, "bottom", PLATE_ASPIRATE_HEIGHT_MM
            ),
            destination_well=f"{row}4",
            tip_group=group,
        )

    print_actions = [action for action in actions if action["action"] == "PRINT"]
    transfer_actions = [action for action in actions if action["action"] == "TRANSFER"]
    mix_actions = [action for action in actions if action["action"] == "MIX"]
    delay_actions = [action for action in actions if action["action"] == "DELAY"]
    if len(print_actions) != 64:
        raise RuntimeError("ground truth must resolve to exactly 64 printed droplets")
    if any(action["volume_ul"] > 20.0 for action in transfer_actions):
        raise RuntimeError("ground-truth transfer exceeds P20 capacity")
    if any(action["piston_dispense_ul"] > 20.0 for action in print_actions):
        raise RuntimeError("ground-truth print exceeds P20 piston capacity")

    plan = {
        "schema_version": "static-ground-truth-actions/v1",
        "experiment_id": "experiment_01_standard_sers_print",
        "architecture_role": "independent_static_reference",
        "initial_liquids": initial_liquids,
        "dilution_math": {
            "method": "back_calculated_serial_cascade",
            "target_usable_ul": _as_float(TARGET_USABLE_UL),
            "factors": list(FACTORS),
            "stock_allocation_ul_per_series": cascade["stock_allocation_ul"],
            "total_diluent_ul_per_series": cascade["total_diluent_ul"],
            "pre_transfer_ul": cascade["pre_transfer_ul"],
            "outgoing_ul": cascade["outgoing_ul"],
            "diluent_ul": cascade["diluent_ul"],
            "retained_usable_ul": cascade["retained_usable_ul"],
        },
        "actions": actions,
        "totals": {
            "action_count": len(actions),
            "transfer_count": len(transfer_actions),
            "mix_count": len(mix_actions),
            "print_count": len(print_actions),
            "delay_count": len(delay_actions),
            "configured_experimental_delay_s": sum(
                action["duration_s"] for action in delay_actions
            ),
            "printed_liquid_ul": sum(
                action["volume_ul"] for action in print_actions
            ),
            "tip_count": len(
                {
                    action["tip_group"]
                    for action in actions
                    if action["action"] in {"TRANSFER", "MIX", "PRINT"}
                }
            ),
        },
    }
    plan["source_accessibility"] = validate_source_accessibility(plan)
    return plan


def validate_source_accessibility(plan: dict[str, Any]) -> dict[str, Any]:
    """Replay volumes and reject any nominally dry aspiration or mix action."""
    volumes: dict[tuple[str, str], float] = {}
    minimum_remaining: dict[tuple[str, str], float] = {}
    for source in plan["initial_liquids"]:
        location = source["location"]
        key = (location["labware"], location["well"])
        volumes[key] = float(source["loaded_volume_ul"])
        minimum_remaining[key] = float(source["minimum_remaining_ul"])

    def cross_section_area(labware: str) -> float:
        diameter = (
            VIAL_DIAMETER_MM if labware == "vial_rack" else PLATE_WELL_DIAMETER_MM
        )
        return math.pi * (diameter / 2.0) ** 2

    minimum_margin = float("inf")
    checked_actions = 0
    for action in plan["actions"]:
        action_type = action["action"]
        if action_type == "TRANSFER":
            source = action["source"]
            source_key = (source["labware"], source["well"])
            volume = float(action["volume_ul"])
            available = volumes.get(source_key, 0.0)
            after = available - volume
            required_cover = cross_section_area(source["labware"]) * float(source["z_mm"])
            margin = after - required_cover
            if margin < -1e-9:
                raise ValueError(
                    f"action {action['sequence_index']} aspirates from a nominally dry "
                    f"location {source_key}: {after:g} uL remains, {required_cover:g} uL "
                    "is required to cover the configured height"
                )
            volumes[source_key] = after
            destination = action["destination"]
            destination_key = (destination["labware"], destination["well"])
            volumes[destination_key] = volumes.get(destination_key, 0.0) + volume
            minimum_margin = min(minimum_margin, margin)
            checked_actions += 1
        elif action_type == "MIX":
            location = action["location"]
            key = (location["labware"], location["well"])
            available = volumes.get(key, 0.0)
            trough = available - float(action["volume_ul"])
            required_cover = cross_section_area(location["labware"]) * float(location["z_mm"])
            margin = trough - required_cover
            if margin < -1e-9:
                raise ValueError(
                    f"action {action['sequence_index']} mixes from a nominally dry "
                    f"location {key}: trough {trough:g} uL, cover {required_cover:g} uL"
                )
            minimum_margin = min(minimum_margin, margin)
            checked_actions += 1
        elif action_type == "PRINT":
            source = action["source"]
            key = (source["labware"], source["well"])
            available = volumes.get(key, 0.0)
            after = available - float(action["volume_ul"])
            required_cover = cross_section_area(source["labware"]) * float(source["z_mm"])
            margin = after - required_cover
            if margin < -1e-9:
                raise ValueError(
                    f"action {action['sequence_index']} prints from a nominally dry "
                    f"location {key}: {after:g} uL remains, cover {required_cover:g} uL"
                )
            volumes[key] = after
            minimum_margin = min(minimum_margin, margin)
            checked_actions += 1

    for key, required in minimum_remaining.items():
        if volumes[key] < required:
            raise ValueError(
                f"initial source {key} finishes at {volumes[key]:g} uL below its "
                f"minimum remaining volume {required:g} uL"
            )
    return {
        "status": "PASS",
        "checked_aspirate_or_mix_actions": checked_actions,
        "minimum_nominal_submerged_margin_ul": minimum_margin,
        "ending_volumes_ul": {
            f"{labware}:{well}": volume
            for (labware, well), volume in sorted(volumes.items())
        },
        "geometry": {
            "plate_well_diameter_mm": PLATE_WELL_DIAMETER_MM,
            "plate_aspirate_height_mm": PLATE_ASPIRATE_HEIGHT_MM,
            "vial_diameter_mm": VIAL_DIAMETER_MM,
            "vial_aspirate_height_mm": VIAL_ASPIRATE_HEIGHT_MM,
        },
    }


def canonical_ground_truth_bytes() -> bytes:
    """Serialize the static trace with repository canonical JSON semantics."""
    from src.printing.canonical import canonical_json_bytes

    return canonical_json_bytes(build_ground_truth_plan())


def ground_truth_sha256() -> str:
    from src.printing.canonical import canonical_sha256

    return canonical_sha256(build_ground_truth_plan())


def ground_truth_artifact_json() -> str:
    payload = build_ground_truth_plan()
    payload["canonical_sha256"] = ground_truth_sha256()
    return json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _load_labware(protocol: protocol_api.ProtocolContext, action: dict[str, Any]):
    load_name = action["load_name"]
    slot = str(action["slot"])
    if action["namespace"] == "opentrons":
        return protocol.load_labware(load_name, slot)
    try:
        return protocol.load_labware(
            load_name,
            slot,
            namespace=action["namespace"],
            version=action["version"],
        )
    except Exception:
        repository = Path(__file__).resolve().parents[3]
        definition = json.loads(
            (repository / "labware" / f"{load_name}.json").read_text(encoding="utf-8")
        )
        return protocol.load_labware_from_definition(definition, slot)


def _resolve_location(labware_by_role: dict[str, Any], spec: dict[str, Any]):
    well = labware_by_role[spec["labware"]][spec["well"]]
    if spec["reference"] == "bottom":
        return well.bottom(float(spec["z_mm"]))
    if spec["reference"] == "top":
        return well.top(float(spec["z_mm"]))
    raise ValueError(f"unknown location reference {spec['reference']!r}")


def run(protocol: protocol_api.ProtocolContext) -> None:
    """Execute only the frozen static trace above."""
    plan = build_ground_truth_plan()
    labware_by_role: dict[str, Any] = {}
    for action in plan["actions"]:
        if action["action"] == "LOAD_LABWARE":
            labware_by_role[action["role"]] = _load_labware(protocol, action)

    pipette_action = next(
        action for action in plan["actions"] if action["action"] == "LOAD_PIPETTE"
    )
    for source in plan["initial_liquids"]:
        liquid = protocol.define_liquid(
            name=source["liquid_id"],
            description=source["display_name"],
            display_color="#7f8c8d",
        )
        location = source["location"]
        labware_by_role[location["labware"]][location["well"]].load_liquid(
            liquid=liquid,
            volume=float(source["loaded_volume_ul"]),
        )
    p20 = protocol.load_instrument(
        pipette_action["pipette"],
        pipette_action["mount"],
        tip_racks=[labware_by_role[pipette_action["tiprack_role"]]],
    )
    p20.flow_rate.aspirate = float(
        pipette_action["flow_rates"]["aspirate_ul_s"]
    )
    p20.flow_rate.dispense = float(
        pipette_action["flow_rates"]["dispense_ul_s"]
    )
    available_tips = iter(labware_by_role["tiprack"].wells())
    active_tip_group: str | None = None

    def release_tip() -> None:
        nonlocal active_tip_group
        if p20.has_tip:
            p20.drop_tip()
        active_tip_group = None

    def ensure_tip(group: str) -> None:
        nonlocal active_tip_group
        if active_tip_group == group and p20.has_tip:
            return
        release_tip()
        try:
            tip = next(available_tips)
        except StopIteration as exc:
            raise RuntimeError("ground truth exhausted the slot-9 P20 tip rack") from exc
        p20.pick_up_tip(tip)
        active_tip_group = group

    protocol.comment("=== Experiment 01 static ground truth started ===")
    protocol.comment(
        f"Canonical static trace SHA-256: {ground_truth_sha256()}"
    )
    for action in plan["actions"]:
        action_type = action["action"]
        if action_type in {"LOAD_LABWARE", "LOAD_PIPETTE"}:
            continue
        if action_type == "DELAY":
            release_tip()
            protocol.comment(action["reason"])
            protocol.delay(seconds=float(action["duration_s"]))
            continue

        ensure_tip(action["tip_group"])
        if action_type == "TRANSFER":
            p20.aspirate(
                float(action["volume_ul"]),
                _resolve_location(labware_by_role, action["source"]),
            )
            p20.dispense(
                float(action["volume_ul"]),
                _resolve_location(labware_by_role, action["destination"]),
            )
        elif action_type == "MIX":
            p20.mix(
                int(action["cycles"]),
                float(action["volume_ul"]),
                _resolve_location(labware_by_role, action["location"]),
            )
        elif action_type == "PRINT":
            destination = _resolve_location(labware_by_role, action["destination"])
            p20.aspirate(
                float(action["volume_ul"]),
                _resolve_location(labware_by_role, action["source"]),
            )
            if float(action["air_gap_ul"]) > 0:
                p20.air_gap(
                    float(action["air_gap_ul"]),
                    height=float(action["air_gap_height_mm"]),
                )
            try:
                p20.dispense(
                    float(action["piston_dispense_ul"]),
                    destination,
                    push_out=float(action["push_out_ul"]),
                )
            except TypeError:
                p20.dispense(float(action["piston_dispense_ul"]), destination)
            if action["blow_out"]:
                p20.blow_out(destination)
            if float(action["post_dispense_delay_s"]) > 0:
                protocol.delay(seconds=float(action["post_dispense_delay_s"]))
        else:
            raise ValueError(f"unsupported static action {action_type!r}")

    release_tip()
    protocol.comment(
        "Experiment 01 static ground truth complete: "
        f"{plan['totals']['print_count']} droplets, "
        f"{plan['totals']['printed_liquid_ul']:g} uL printed."
    )
