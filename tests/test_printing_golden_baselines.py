"""Golden behavioral references for the established v9 and four-clover printers.

These tests intentionally describe the current implementations. They do not define a
new print-plan abstraction. Future refactors must reproduce these normalized scientific
and operational behaviors before replacing either reference protocol.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re

from src.printing.artifacts import prepare_printing_request, simulate_prepared_request
from src.printing.plans import resolve_print_plan


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "printing"
PAPER_JSON = REPO / "labware" / "paper_print_96_flat.json"
STANDARD_PROTOCOL = (
    REPO / "src" / "protocols" / "printing" / "09_plate_well_direct_paper_print_v9.py"
)
CLOVER_PROTOCOL = (
    REPO / "src" / "protocols" / "printing" / "12_four_clover_paper_print.py"
)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _paper_wells() -> dict:
    return json.loads(PAPER_JSON.read_text(encoding="utf-8"))["wells"]


def _paper_xy(name: str) -> tuple[float, float]:
    well = _paper_wells()[name.upper()]
    return float(well["x"]), float(well["y"])


def _config_sha256(config: dict) -> str:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _standard_behavior(config: dict) -> dict:
    module = _module(STANDARD_PROTOCOL, "golden_standard_v9")
    module.CONFIG = deepcopy(config)
    passes, layers = module._layer_plan()
    source = config["source"]
    printing = config["print"]
    columns = [int(column) for column in printing["replicate_columns"]]
    volume = float(printing["volume_ul"])
    air_gap = float(printing["air_gap_ul"])
    deposits = []
    for layer_pass in passes:
        for row in layer_pass["rows"]:
            for column in columns:
                destination = f"{row}{column}"
                deposits.append(
                    {
                        "sequence": len(deposits) + 1,
                        "layer": layer_pass["index"],
                        "source_well": source["well"].upper(),
                        "destination_well": destination,
                        "destination_xy_mm": list(_paper_xy(destination)),
                        "liquid_volume_ul": volume,
                    }
                )
    return {
        "source": {
            "labware_role": "plate",
            "slot": int(config["deck"]["plate"]["slot"]),
            "well": source["well"].upper(),
            "material": source["material"],
            "aspirate_height_mm": float(source["aspirate_height_mm"]),
        },
        "destination": {
            "labware": config["deck"]["paper"]["load_name"],
            "slot": int(config["deck"]["paper"]["slot"]),
            "dispense_height_mm": float(printing["z_mm"]),
        },
        "pipette": dict(config["pipette"]),
        "tip": {
            "well": config["tips"]["p20"]["print_tip"].upper(),
            "return_tip": bool(config["tips"]["return_tips"]),
            "held_for_complete_run": True,
        },
        "layers_by_row": layers,
        "replicate_columns": columns,
        "order_mode": "layer_then_row_then_column",
        "liquid_handling": {
            "droplet_volume_ul": volume,
            "trailing_air_gap_ul": air_gap,
            "air_gap_height_mm": float(printing["air_gap_height_mm"]),
            "piston_dispense_ul": volume + air_gap,
            "push_out_ul": float(printing["push_out_ul"]),
            "blow_out": bool(printing["blow_out"]),
            "aspirate_flow_ul_s": float(config["flow_rates"]["p20"]["aspirate"]),
            "dispense_flow_ul_s": float(config["flow_rates"]["p20"]["dispense"]),
        },
        "delays": {
            "post_dispense_s": float(printing["post_dispense_delay_s"]),
            "rest_minutes_between_nonfinal_layer_passes": float(printing["rest_minutes"]),
            "total_rest_minutes": sum(float(item["rest_minutes"]) for item in passes),
        },
        "total_deposits": len(deposits),
        "total_liquid_ul": len(deposits) * volume,
        "deposits": deposits,
    }


def _clover_behavior(config: dict) -> dict:
    module = _module(CLOVER_PROTOCOL, "golden_four_clover")
    module.CONFIG = deepcopy(config)
    clovers = module._resolve_clovers(_paper_xy)
    order_mode, plan = module._print_order(clovers)
    printing = config["printing"]
    volume = float(printing["droplet_volume_ul"])
    air_gap = float(printing["air_gap_ul"])
    clover_indexes = {clover["name"]: index for index, clover in enumerate(clovers, 1)}
    deck_offset = (132.5, 90.5)  # OT-2 slot 5 origin, pinned by the simulator trace.
    deposits = []
    for clover, layer, key in plan:
        paper_x, paper_y = clover["droplets"][key]["absolute"]
        deposits.append(
            {
                "sequence": len(deposits) + 1,
                "clover_index": clover_indexes[clover["name"]],
                "clover_name": clover["name"],
                "layer": layer,
                "droplet": key,
                "offset_xy_mm": list(clover["droplets"][key]["offset"]),
                "paper_xy_mm": [paper_x, paper_y],
                "deck_xy_mm": [paper_x + deck_offset[0], paper_y + deck_offset[1]],
                "liquid_volume_ul": volume,
            }
        )
    clover_records = []
    for index, clover in enumerate(clovers, 1):
        center_x, center_y = clover["center"]
        clover_records.append(
            {
                "clover_index": index,
                "name": clover["name"],
                "reference_well": clover["reference_well"],
                "center_offset_mm": list(clover["center_offset"]),
                "paper_center_xy_mm": [center_x, center_y],
                "deck_center_xy_mm": [
                    center_x + deck_offset[0],
                    center_y + deck_offset[1],
                ],
                "layers": clover["layers"],
                "pre_air_chase_ul": clover["pre_air_chase_ul"],
            }
        )
    first = clovers[0]
    return {
        "source": {
            "labware_role": "source",
            "slot": int(config["deck"]["source"]["slot"]),
            "well": config["source"]["well"].upper(),
            "material": config["source"]["material"],
            "aspirate_height_mm": float(config["source"]["aspirate_height_mm"]),
        },
        "destination": {
            "labware": config["deck"]["paper"]["load_name"],
            "slot": int(config["deck"]["paper"]["slot"]),
            "dispense_height_mm": float(printing["dispense_height_mm"]),
        },
        "pipette": dict(config["pipette"]),
        "tip": {
            "well": config["tips"]["p20"]["print_tip"].upper(),
            "return_tip": bool(config["tips"]["return_tips"]),
            "held_for_complete_run": True,
        },
        "clovers": clover_records,
        "droplet_offsets_mm": {
            key: list(first["droplets"][key]["offset"])
            for key in module.DROPLET_KEYS
        },
        "order_mode": order_mode,
        "liquid_handling": {
            "droplet_volume_ul": volume,
            "pre_air_chase_ul": first["pre_air_chase_ul"],
            "trailing_air_gap_ul": air_gap,
            "air_gap_height_mm": float(printing["air_gap_height_mm"]),
            "piston_dispense_ul": first["pre_air_chase_ul"] + volume + air_gap,
            "push_out_ul": float(printing["push_out_ul"]),
            "blow_out": bool(printing["blow_out"]),
            "aspirate_flow_ul_s": float(config["flow_rates"]["p20"]["aspirate"]),
            "dispense_flow_ul_s": float(config["flow_rates"]["p20"]["dispense"]),
        },
        "delays": {
            "inter_drop_s": float(printing["inter_drop_delay_s"]),
            "inter_layer_s": float(printing["inter_layer_delay_s"]),
            "inter_clover_s": float(printing["inter_clover_delay_s"]),
        },
        "total_clovers": len(clovers),
        "total_deposits": len(deposits),
        "total_liquid_ul": len(deposits) * volume,
        "deposits": deposits,
    }


def test_standard_v9_golden_behavior_is_unchanged():
    golden = _fixture("plate_well_direct_v9_golden.json")
    prepared = prepare_printing_request(golden["request"])

    assert prepared.validation.valid, prepared.validation.model_dump_json(indent=2)
    assert prepared.validation.calculated == {
        "deposit_count": 6,
        "liquid_required_ul": 30.0,
    }
    assert _config_sha256(prepared.config) == golden["resolved_config_sha256"]
    assert _standard_behavior(prepared.config) == golden["behavior"]


def test_four_clover_golden_behavior_is_unchanged():
    golden = _fixture("four_clover_air_chase_v12_golden.json")
    prepared = prepare_printing_request(golden["request"])

    assert prepared.validation.valid, prepared.validation.model_dump_json(indent=2)
    assert prepared.validation.calculated == {
        "clover_count": 1,
        "deposit_count": 4,
        "liquid_required_ul": 20.0,
        "coordinates": {
            "air_chase_5ul": {
                "d1": [61.88, 44.74],
                "d2": [65.88, 44.74],
                "d3": [61.88, 40.74],
                "d4": [65.88, 40.74],
            }
        },
    }
    assert _config_sha256(prepared.config) == golden["resolved_config_sha256"]
    assert _clover_behavior(prepared.config) == golden["behavior"]


def _standard_plan_behavior(plan) -> dict:
    source = plan.machine.sources[0]
    destination = plan.machine.destination_labware
    first = plan.deposits[0].deposition
    layers: dict[str, int] = {}
    columns: list[int] = []
    deposits = []
    for deposit in plan.deposits:
        provenance = deposit.provenance
        layers[provenance.row] = max(
            layers.get(provenance.row, 0), provenance.layer_index
        )
        if provenance.column not in columns:
            columns.append(provenance.column)
        deposits.append(
            {
                "sequence": deposit.sequence_index,
                "layer": provenance.layer_index,
                "source_well": source.well,
                "destination_well": deposit.destination.well,
                "destination_xy_mm": [
                    deposit.destination.paper_xy_mm.x_mm,
                    deposit.destination.paper_xy_mm.y_mm,
                ],
                "liquid_volume_ul": deposit.deposition.liquid_volume_ul,
            }
        )
    return {
        "source": {
            "labware_role": source.labware_role,
            "slot": source.deck_slot,
            "well": source.well,
            "material": source.material_id,
            "aspirate_height_mm": source.aspirate_height_mm,
        },
        "destination": {
            "labware": destination.labware_name,
            "slot": destination.deck_slot,
            "dispense_height_mm": destination.dispense_height_mm,
        },
        "pipette": {
            "name": plan.machine.pipette.name,
            "mount": plan.machine.pipette.mount,
        },
        "tip": {
            "well": plan.machine.tip_strategy.tip_well,
            "return_tip": plan.machine.tip_strategy.return_tip,
            "held_for_complete_run": plan.machine.tip_strategy.held_for_complete_run,
        },
        "layers_by_row": layers,
        "replicate_columns": columns,
        "order_mode": plan.order_mode,
        "liquid_handling": {
            "droplet_volume_ul": first.liquid_volume_ul,
            "trailing_air_gap_ul": first.trailing_air_gap_ul,
            "air_gap_height_mm": first.air_gap_height_mm,
            "piston_dispense_ul": first.piston_dispense_ul,
            "push_out_ul": first.push_out_ul,
            "blow_out": first.blow_out,
            "aspirate_flow_ul_s": plan.machine.flow_rates.aspirate_ul_s,
            "dispense_flow_ul_s": plan.machine.flow_rates.dispense_ul_s,
        },
        "delays": {
            "post_dispense_s": plan.timing.post_dispense_delay_s,
            "rest_minutes_between_nonfinal_layer_passes": (
                plan.timing.inter_pass_rest_s / 60.0
            ),
            "total_rest_minutes": plan.timing.total_rest_s / 60.0,
        },
        "total_deposits": plan.totals.deposit_count,
        "total_liquid_ul": plan.totals.total_liquid_ul,
        "deposits": deposits,
    }


def _clover_plan_behavior(plan) -> dict:
    source = plan.machine.sources[0]
    destination = plan.machine.destination_labware
    first = plan.deposits[0].deposition
    clover_records: dict[int, dict] = {}
    offsets: dict[str, list[float]] = {}
    deposits = []
    for deposit in plan.deposits:
        provenance = deposit.provenance
        target = deposit.destination
        clover_records.setdefault(
            provenance.clover_index,
            {
                "clover_index": provenance.clover_index,
                "name": provenance.clover_name,
                "reference_well": target.reference_well,
                "center_offset_mm": [
                    target.center_translation_mm.x_mm,
                    target.center_translation_mm.y_mm,
                ],
                "paper_center_xy_mm": [
                    target.paper_center_xy_mm.x_mm,
                    target.paper_center_xy_mm.y_mm,
                ],
                "deck_center_xy_mm": [
                    target.deck_center_xy_mm.x_mm,
                    target.deck_center_xy_mm.y_mm,
                ],
                "layers": max(
                    item.provenance.layer_index
                    for item in plan.deposits
                    if item.provenance.clover_index == provenance.clover_index
                ),
                "pre_air_chase_ul": deposit.deposition.pre_air_chase_ul,
            },
        )
        offsets.setdefault(
            provenance.design_point.lower(),
            [target.point_offset_mm.x_mm, target.point_offset_mm.y_mm],
        )
        deposits.append(
            {
                "sequence": deposit.sequence_index,
                "clover_index": provenance.clover_index,
                "clover_name": provenance.clover_name,
                "layer": provenance.layer_index,
                "droplet": provenance.design_point.lower(),
                "offset_xy_mm": [
                    target.point_offset_mm.x_mm,
                    target.point_offset_mm.y_mm,
                ],
                "paper_xy_mm": [
                    target.paper_xy_mm.x_mm,
                    target.paper_xy_mm.y_mm,
                ],
                "deck_xy_mm": [
                    target.deck_xy_mm.x_mm,
                    target.deck_xy_mm.y_mm,
                ],
                "liquid_volume_ul": deposit.deposition.liquid_volume_ul,
            }
        )
    return {
        "source": {
            "labware_role": source.labware_role,
            "slot": source.deck_slot,
            "well": source.well,
            "material": source.material_id,
            "aspirate_height_mm": source.aspirate_height_mm,
        },
        "destination": {
            "labware": destination.labware_name,
            "slot": destination.deck_slot,
            "dispense_height_mm": destination.dispense_height_mm,
        },
        "pipette": {
            "name": plan.machine.pipette.name,
            "mount": plan.machine.pipette.mount,
        },
        "tip": {
            "well": plan.machine.tip_strategy.tip_well,
            "return_tip": plan.machine.tip_strategy.return_tip,
            "held_for_complete_run": plan.machine.tip_strategy.held_for_complete_run,
        },
        "clovers": list(clover_records.values()),
        "droplet_offsets_mm": offsets,
        "order_mode": plan.order_mode,
        "liquid_handling": {
            "droplet_volume_ul": first.liquid_volume_ul,
            "pre_air_chase_ul": first.pre_air_chase_ul,
            "trailing_air_gap_ul": first.trailing_air_gap_ul,
            "air_gap_height_mm": first.air_gap_height_mm,
            "piston_dispense_ul": first.piston_dispense_ul,
            "push_out_ul": first.push_out_ul,
            "blow_out": first.blow_out,
            "aspirate_flow_ul_s": plan.machine.flow_rates.aspirate_ul_s,
            "dispense_flow_ul_s": plan.machine.flow_rates.dispense_ul_s,
        },
        "delays": {
            "inter_drop_s": plan.timing.inter_drop_delay_s,
            "inter_layer_s": plan.timing.inter_layer_delay_s,
            "inter_clover_s": plan.timing.inter_clover_delay_s,
        },
        "total_clovers": plan.totals.clover_count,
        "total_deposits": plan.totals.deposit_count,
        "total_liquid_ul": plan.totals.total_liquid_ul,
        "deposits": deposits,
    }


def test_standard_v9_resolved_plan_normalizes_to_stage_zero_fixture():
    golden = _fixture("plate_well_direct_v9_golden.json")
    plan = resolve_print_plan(golden["request"])
    assert _standard_plan_behavior(plan) == golden["behavior"]


def test_four_clover_resolved_plan_normalizes_to_stage_zero_fixture():
    golden = _fixture("four_clover_air_chase_v12_golden.json")
    plan = resolve_print_plan(golden["request"])
    assert _clover_plan_behavior(plan) == golden["behavior"]


def _simulate_golden(golden: dict, tmp_path: Path):
    prepared = prepare_printing_request(golden["request"])
    result = simulate_prepared_request(prepared, output_dir=tmp_path, record=False)
    assert result.status == "PASS", result.output_tail
    assert result.motion_path_exercised is True
    assert result.artifact.protocol_dry_run is False
    assert result.artifact.resolved_config_sha256 == golden["resolved_config_sha256"]
    protocol = Path(result.artifact.protocol_path)
    assert hashlib.sha256(protocol.read_bytes()).hexdigest() == result.artifact.sha256
    return result.output_tail


def test_standard_v9_golden_request_validates_builds_and_simulates(tmp_path):
    golden = _fixture("plate_well_direct_v9_golden.json")
    output = _simulate_golden(golden, tmp_path)
    deposits = golden["behavior"]["deposits"]

    dispenses = re.findall(
        r"Dispensing ([0-9.]+) uL into ([A-H][0-9]+) of Paper Print Surface 96",
        output,
    )
    assert dispenses == [
        (str(golden["behavior"]["liquid_handling"]["piston_dispense_ul"]), item["destination_well"])
        for item in deposits
    ]
    assert output.count("Aspirating 5.0 uL from A1 of Corning 96 Well Plate") == 6
    assert output.count("Air gap of 1.5 uL at height 5.0") == 6
    assert output.count("Blowing out into") == 6
    assert output.count("Delaying for 0 minutes and 2.0 seconds") == 6
    assert output.count("Delaying for 0 minutes and 15.0 seconds") == 1


def test_four_clover_golden_request_validates_builds_and_simulates(tmp_path):
    golden = _fixture("four_clover_air_chase_v12_golden.json")
    output = _simulate_golden(golden, tmp_path)
    deposits = golden["behavior"]["deposits"]

    placements = re.findall(
        r"air_chase_5ul layer ([0-9]+) (D[1-4]) placed at x ([0-9.]+) y ([0-9.]+)",
        output,
    )
    assert placements == [
        (
            str(item["layer"]),
            item["droplet"].upper(),
            str(item["deck_xy_mm"][0]),
            str(item["deck_xy_mm"][1]),
        )
        for item in deposits
    ]
    # Every drop aspirates 5 uL chase air and then 5 uL liquid from the same
    # current source location, so the simulator records two 5 uL aspirations.
    assert output.count("Aspirating 5.0 uL from A2 of TubeRack_3Dprint_20ml_8vials_v2") == 8
    assert output.count("Air gap of 1.5 uL at height 5.0") == 4
    assert output.count("Dispensing 11.5 uL into E6 of Paper Print Surface 96") == 4
    assert "Blowing out into" not in output
    assert output.count("Delaying for 0 minutes and 2.0 seconds") == 4
