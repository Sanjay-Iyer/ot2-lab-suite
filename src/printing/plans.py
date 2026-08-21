"""Deterministic adapters from trusted v9/v12 behavior to resolved print plans."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from typing import Any

from opentrons_shared_data.deck import load as load_deck_definition

from .artifacts import PreparedPrintingRequest, prepare_printing_request
from .canonical import canonical_sha256
from .config import REPO_ROOT
from .designs import get_design
from .schemas import PrintingFamily, ResolvedPrintPlanV1


_SOURCE_ID = "primary_source"
_DESTINATION_ID = "paper"


def _repo_reference(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def _fresh_protocol_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load printing protocol: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _labware_wells(load_name: str) -> dict[str, dict[str, Any]]:
    path = REPO_ROOT / "labware" / f"{load_name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"custom labware definition not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))["wells"]


def _slot_origin_xy(slot: int) -> tuple[float, float]:
    """Resolve the OT-2 slot origin from the pinned deck definition data."""
    deck = load_deck_definition("ot2_standard", 3)
    for entry in deck["locations"]["orderedSlots"]:
        if int(entry["id"]) == slot:
            x, y, _ = entry["position"]
            return float(x), float(y)
    raise ValueError(f"OT-2 deck definition has no slot {slot}")


def _xy(x: float, y: float) -> dict[str, float]:
    return {"x_mm": float(x), "y_mm": float(y)}


def _machine_configuration(
    config: dict[str, Any],
    *,
    source_role: str,
    print_section: str,
    dispense_height_field: str,
) -> dict[str, Any]:
    source_deck = config["deck"][source_role]
    paper_deck = config["deck"]["paper"]
    tiprack = config["deck"]["tiprack_p20"]
    source = config["source"]
    printing = config[print_section]
    rates = config["flow_rates"]["p20"]
    tips = config["tips"]
    return {
        "robot_type": "OT-2",
        "api_level": "2.15",
        "pipette": {
            "name": config["pipette"]["name"],
            "mount": config["pipette"]["mount"],
            "max_volume_ul": float(config["safety"]["p20_max_volume_ul"]),
        },
        "tip_strategy": {
            "rack_labware_name": tiprack["load_name"],
            "rack_deck_slot": int(tiprack["slot"]),
            "tip_well": str(tips["p20"]["print_tip"]).upper(),
            "return_tip": bool(tips["return_tips"]),
            "held_for_complete_run": True,
        },
        "flow_rates": {
            "aspirate_ul_s": float(rates["aspirate"]),
            "dispense_ul_s": float(rates["dispense"]),
        },
        "sources": [
            {
                "source_id": _SOURCE_ID,
                "material_id": str(source["material"]),
                "labware_role": source_role,
                "labware_name": source_deck["load_name"],
                "deck_slot": int(source_deck["slot"]),
                "well": str(source["well"]).upper(),
                "aspirate_height_mm": float(source["aspirate_height_mm"]),
                "park_height_mm": float(source.get("park_height_mm", 0.0) or 0.0),
            }
        ],
        "destination_labware": {
            "destination_labware_id": _DESTINATION_ID,
            "labware_role": "paper",
            "labware_name": paper_deck["load_name"],
            "deck_slot": int(paper_deck["slot"]),
            "dispense_height_mm": float(printing[dispense_height_field]),
        },
    }


def _provenance(
    prepared: PreparedPrintingRequest,
    *,
    source_protocol_family: str,
    adapter_id: str,
) -> dict[str, Any]:
    resolved = prepared.resolved
    workflow = resolved.workflow
    assert workflow.default_config is not None
    return {
        "resolved_config_sha256": canonical_sha256(prepared.config),
        "source_request_sha256": canonical_sha256(
            resolved.request.model_dump(mode="json")
        ),
        "source_config_reference": _repo_reference(workflow.default_config),
        "source_protocol": _repo_reference(workflow.base_protocol),
        "source_protocol_family": source_protocol_family,
        "source_builder_version": workflow.builder_version,
        "adapter_id": adapter_id,
    }


def _totals(
    deposits: list[dict[str, Any]],
    *,
    replicate_count: int | None,
    clover_count: int | None,
) -> dict[str, Any]:
    return {
        "deposit_count": len(deposits),
        "total_liquid_ul": sum(
            deposit["deposition"]["liquid_volume_ul"] for deposit in deposits
        ),
        "total_air_ul": sum(
            deposit["deposition"]["pre_air_chase_ul"]
            + deposit["deposition"]["trailing_air_gap_ul"]
            for deposit in deposits
        ),
        "total_piston_dispense_ul": sum(
            deposit["deposition"]["piston_dispense_ul"] for deposit in deposits
        ),
        "total_delay_s": sum(
            deposit["timing"]["delay_before_s"]
            + deposit["timing"]["post_dispense_delay_s"]
            + deposit["timing"]["rest_after_s"]
            for deposit in deposits
        ),
        "source_count": 1,
        "layer_count": max(
            deposit["provenance"]["layer_index"] for deposit in deposits
        ),
        "replicate_count": replicate_count,
        "clover_count": clover_count,
    }


def _require_prepared(
    prepared: PreparedPrintingRequest,
    *,
    family: PrintingFamily,
    workflow_name: str,
) -> None:
    if not prepared.validation.valid:
        raise ValueError("printing request failed deterministic validation")
    resolved = prepared.resolved
    if resolved.workflow.family != family or resolved.workflow.name != workflow_name:
        raise ValueError(
            f"adapter requires {workflow_name!r}, got {resolved.workflow.name!r}"
        )


def resolve_v9_to_print_plan(
    prepared: PreparedPrintingRequest,
) -> ResolvedPrintPlanV1:
    """Adapt the existing v9 layer resolver/order into a canonical plan."""
    _require_prepared(
        prepared,
        family=PrintingFamily.STANDARD,
        workflow_name="plate_well_direct_v9",
    )
    config = prepared.config
    workflow = prepared.resolved.workflow
    module = _fresh_protocol_module(workflow.base_protocol, "resolved_plan_v9")
    module.CONFIG = deepcopy(config)
    passes, _ = module._layer_plan()

    printing = config["print"]
    paper = config["deck"]["paper"]
    wells = _labware_wells(paper["load_name"])
    deck_x, deck_y = _slot_origin_xy(int(paper["slot"]))
    columns = [int(column) for column in printing["replicate_columns"]]
    volume = float(printing["volume_ul"])
    trailing_air = float(printing.get("air_gap_ul", 0.0) or 0.0)
    deposition = {
        "liquid_volume_ul": volume,
        "pre_air_chase_ul": 0.0,
        "trailing_air_gap_ul": trailing_air,
        "air_gap_height_mm": float(printing.get("air_gap_height_mm", 0.0) or 0.0),
        "piston_dispense_ul": volume + trailing_air,
        "push_out_ul": float(printing.get("push_out_ul", 0.0) or 0.0),
        "blow_out": bool(printing.get("blow_out", True)),
    }
    post_dispense = float(printing.get("post_dispense_delay_s", 0.0) or 0.0)
    deposits: list[dict[str, Any]] = []
    for layer_pass in passes:
        rows = list(layer_pass["rows"])
        for row_index, row in enumerate(rows):
            for replicate_index, column in enumerate(columns, 1):
                well_name = f"{row}{column}"
                well = wells[well_name]
                paper_x, paper_y = float(well["x"]), float(well["y"])
                is_last_in_pass = (
                    row_index == len(rows) - 1
                    and replicate_index == len(columns)
                )
                deposits.append(
                    {
                        "sequence_index": len(deposits) + 1,
                        "source_id": _SOURCE_ID,
                        "destination_labware_id": _DESTINATION_ID,
                        "destination": {
                            "kind": "well",
                            "well": well_name,
                            "row": row,
                            "column": column,
                            "paper_xy_mm": _xy(paper_x, paper_y),
                            "deck_xy_mm": _xy(paper_x + deck_x, paper_y + deck_y),
                        },
                        "deposition": deposition,
                        "provenance": {
                            "kind": "well_grid",
                            "layer_index": int(layer_pass["index"]),
                            "row": row,
                            "column": column,
                            "replicate_index": replicate_index,
                        },
                        "timing": {
                            "delay_before_s": 0.0,
                            "post_dispense_delay_s": post_dispense,
                            "rest_after_s": (
                                float(layer_pass["rest_minutes"]) * 60.0
                                if is_last_in_pass
                                else 0.0
                            ),
                        },
                    }
                )

    return ResolvedPrintPlanV1.from_content(
        workflow_id=workflow.name,
        provenance=_provenance(
            prepared,
            source_protocol_family="plate_well_direct_v9",
            adapter_id="plate-well-direct-v9-to-plan/v1",
        ),
        machine=_machine_configuration(
            config,
            source_role="plate",
            print_section="print",
            dispense_height_field="z_mm",
        ),
        order_mode="layer_then_row_then_column",
        timing={
            "kind": "standard_layer_passes",
            "post_dispense_delay_s": post_dispense,
            "inter_pass_rest_s": float(printing.get("rest_minutes", 0.0) or 0.0)
            * 60.0,
            "total_rest_s": sum(
                float(layer_pass["rest_minutes"]) * 60.0
                for layer_pass in passes
            ),
        },
        deposits=deposits,
        totals=_totals(
            deposits,
            replicate_count=len(columns),
            clover_count=None,
        ),
    )


def resolve_v12_clover_to_print_plan(
    prepared: PreparedPrintingRequest,
) -> ResolvedPrintPlanV1:
    """Adapt the existing registered v12 geometry/order into a canonical plan."""
    if not prepared.validation.valid:
        raise ValueError("printing request failed deterministic validation")
    resolved = prepared.resolved
    if (
        resolved.workflow.family != PrintingFamily.DESIGN
        or resolved.workflow.design_name != "four_clover"
        or resolved.workflow.base_protocol.name != "12_four_clover_paper_print.py"
    ):
        raise ValueError("adapter requires a registered v12 four_clover workflow")
    config = prepared.config
    workflow = prepared.resolved.workflow
    resolved_geometry = get_design("four_clover").generate(config)
    clovers = resolved_geometry["clovers"]
    by_name = {clover["name"]: clover for clover in clovers}
    indexes = {clover["name"]: index for index, clover in enumerate(clovers, 1)}

    printing = config["printing"]
    paper = config["deck"]["paper"]
    deck_x, deck_y = _slot_origin_xy(int(paper["slot"]))
    volume = float(printing["droplet_volume_ul"])
    trailing_air = float(printing.get("air_gap_ul", 0.0) or 0.0)
    drop_delay = float(printing.get("inter_drop_delay_s", 0.0) or 0.0)
    layer_delay = float(printing.get("inter_layer_delay_s", 0.0) or 0.0)
    clover_delay = float(printing.get("inter_clover_delay_s", 0.0) or 0.0)
    deposits: list[dict[str, Any]] = []
    previous: tuple[str, int] | None = None
    for item in resolved_geometry["plan"]:
        clover = by_name[item["clover"]]
        layer = int(item["layer"])
        key = str(item["droplet"]).lower()
        delay_before = 0.0
        if previous is not None:
            previous_name, previous_layer = previous
            if clover["name"] != previous_name:
                delay_before = clover_delay
            elif layer != previous_layer:
                delay_before = layer_delay

        center_x, center_y = clover["center"]
        reference_x = center_x - float(clover["center_offset"][0])
        reference_y = center_y - float(clover["center_offset"][1])
        offset_x, offset_y = clover["droplets"][key]["offset"]
        paper_x, paper_y = clover["droplets"][key]["absolute"]
        chase = float(clover["pre_air_chase_ul"])
        deposits.append(
            {
                "sequence_index": len(deposits) + 1,
                "source_id": _SOURCE_ID,
                "destination_labware_id": _DESTINATION_ID,
                "destination": {
                    "kind": "coordinate",
                    "reference_well": clover["reference_well"],
                    "reference_well_paper_xy_mm": _xy(reference_x, reference_y),
                    "reference_well_deck_xy_mm": _xy(
                        reference_x + deck_x, reference_y + deck_y
                    ),
                    "center_translation_mm": _xy(*clover["center_offset"]),
                    "paper_center_xy_mm": _xy(center_x, center_y),
                    "deck_center_xy_mm": _xy(center_x + deck_x, center_y + deck_y),
                    "point_offset_mm": _xy(offset_x, offset_y),
                    "paper_xy_mm": _xy(paper_x, paper_y),
                    "deck_xy_mm": _xy(paper_x + deck_x, paper_y + deck_y),
                },
                "deposition": {
                    "liquid_volume_ul": volume,
                    "pre_air_chase_ul": chase,
                    "trailing_air_gap_ul": trailing_air,
                    "air_gap_height_mm": float(
                        printing.get("air_gap_height_mm", 0.0) or 0.0
                    ),
                    "piston_dispense_ul": chase + volume + trailing_air,
                    "push_out_ul": float(printing.get("push_out_ul", 0.0) or 0.0),
                    "blow_out": bool(printing.get("blow_out", True)),
                },
                "provenance": {
                    "kind": "four_clover",
                    "layer_index": layer,
                    "clover_index": indexes[clover["name"]],
                    "clover_name": clover["name"],
                    "design_point": key.upper(),
                },
                "timing": {
                    "delay_before_s": delay_before,
                    "post_dispense_delay_s": drop_delay,
                    "rest_after_s": 0.0,
                },
            }
        )
        previous = (clover["name"], layer)

    return ResolvedPrintPlanV1.from_content(
        workflow_id=workflow.name,
        provenance=_provenance(
            prepared,
            source_protocol_family="four_clover_v12",
            adapter_id="four-clover-v12-to-plan/v1",
        ),
        machine=_machine_configuration(
            config,
            source_role="source",
            print_section="printing",
            dispense_height_field="dispense_height_mm",
        ),
        order_mode=resolved_geometry["order_mode"],
        timing={
            "kind": "four_clover",
            "inter_drop_delay_s": drop_delay,
            "inter_layer_delay_s": layer_delay,
            "inter_clover_delay_s": clover_delay,
        },
        deposits=deposits,
        totals=_totals(
            deposits,
            replicate_count=None,
            clover_count=len(clovers),
        ),
    )


def resolve_print_plan(payload: dict[str, Any]) -> ResolvedPrintPlanV1:
    """Resolve a supported Stage 1 request into its canonical execution plan."""
    prepared = prepare_printing_request(payload)
    workflow = prepared.resolved.workflow.name
    if workflow == "plate_well_direct_v9":
        return resolve_v9_to_print_plan(prepared)
    if (
        prepared.resolved.workflow.family == PrintingFamily.DESIGN
        and prepared.resolved.workflow.design_name == "four_clover"
        and prepared.resolved.workflow.base_protocol.name
        == "12_four_clover_paper_print.py"
    ):
        return resolve_v12_clover_to_print_plan(prepared)
    raise ValueError(
        "ResolvedPrintPlanV1 Stage 1 supports only plate_well_direct_v9 and "
        "registered v12 four_clover workflows"
    )


def resolved_plan_artifact_json(plan: ResolvedPrintPlanV1) -> str:
    """Return stable, human-inspectable JSON including the verified plan identity."""
    payload = plan.model_dump(mode="json")
    if payload["provenance"].get("source_job_sha256") is None:
        payload["provenance"].pop("source_job_sha256")
    if payload["provenance"].get("source_experiment_config_sha256") is None:
        payload["provenance"].pop("source_experiment_config_sha256")
    if payload["provenance"].get("source_experiment_config_reference") is None:
        payload["provenance"].pop("source_experiment_config_reference")
    return json.dumps(
        payload,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
