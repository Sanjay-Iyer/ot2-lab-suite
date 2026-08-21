"""Stage 1 canonical plan, adapter, artifact, and golden-equivalence tests."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.printing.artifacts import prepare_printing_request
from src.printing.plans import (
    resolve_print_plan,
    resolve_v12_clover_to_print_plan,
    resolve_v9_to_print_plan,
    resolved_plan_artifact_json,
)
from src.printing.schemas import ResolvedPrintPlanV1, parse_resolved_print_plan_json


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "printing"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def standard_case() -> tuple[dict, ResolvedPrintPlanV1]:
    golden = _fixture("plate_well_direct_v9_golden.json")
    return golden, resolve_print_plan(golden["request"])


@pytest.fixture(scope="module")
def clover_case() -> tuple[dict, ResolvedPrintPlanV1]:
    golden = _fixture("four_clover_air_chase_v12_golden.json")
    return golden, resolve_print_plan(golden["request"])


def _point(point) -> list[float]:
    return [point.x_mm, point.y_mm]


def test_standard_adapter_uses_prepared_v9_resolution_chain(standard_case):
    golden, expected = standard_case
    prepared = prepare_printing_request(golden["request"])
    actual = resolve_v9_to_print_plan(prepared)
    assert actual == expected
    assert actual.provenance.resolved_config_sha256 == golden["resolved_config_sha256"]
    assert actual.provenance.source_protocol_family == "plate_well_direct_v9"


def test_standard_plan_matches_every_stage_zero_behavior(standard_case):
    golden, plan = standard_case
    behavior = golden["behavior"]
    source = plan.machine.sources[0]

    assert (source.labware_role, source.deck_slot, source.well) == ("plate", 4, "A1")
    assert source.material_id == "sample"
    assert source.aspirate_height_mm == 1.0
    assert plan.machine.destination_labware.labware_name == behavior["destination"]["labware"]
    assert plan.machine.destination_labware.deck_slot == 5
    assert plan.machine.destination_labware.dispense_height_mm == 4.0
    assert plan.machine.pipette.name == "p20_single_gen2"
    assert plan.machine.pipette.mount == "left"
    assert plan.machine.pipette.max_volume_ul == 20.0
    assert plan.machine.tip_strategy.tip_well == "A1"
    assert plan.machine.tip_strategy.return_tip is True
    assert plan.machine.tip_strategy.held_for_complete_run is True
    assert plan.order_mode == behavior["order_mode"]
    assert plan.totals.deposit_count == 6
    assert plan.totals.total_liquid_ul == 30.0
    assert plan.totals.layer_count == 2
    assert plan.totals.replicate_count == 2
    assert plan.timing.inter_pass_rest_s == 15.0
    assert plan.timing.total_rest_s == 15.0

    assert [deposit.destination.well for deposit in plan.deposits] == [
        "A1", "A2", "B1", "B2", "B1", "B2"
    ]
    assert [deposit.provenance.layer_index for deposit in plan.deposits] == [1, 1, 1, 1, 2, 2]
    assert [deposit.provenance.replicate_index for deposit in plan.deposits] == [1, 2, 1, 2, 1, 2]
    assert [deposit.timing.rest_after_s for deposit in plan.deposits] == [0, 0, 0, 15, 0, 0]
    assert all(deposit.timing.post_dispense_delay_s == 2.0 for deposit in plan.deposits)
    assert [_point(deposit.destination.deck_xy_mm) for deposit in plan.deposits] == [
        [146.88, 164.74],
        [155.88, 164.74],
        [146.88, 155.74],
        [155.88, 155.74],
        [146.88, 155.74],
        [155.88, 155.74],
    ]

    for deposit, expected in zip(plan.deposits, behavior["deposits"]):
        assert deposit.sequence_index == expected["sequence"]
        assert source.well == expected["source_well"]
        assert deposit.provenance.layer_index == expected["layer"]
        assert deposit.destination.well == expected["destination_well"]
        assert _point(deposit.destination.paper_xy_mm) == expected["destination_xy_mm"]
        assert deposit.deposition.liquid_volume_ul == expected["liquid_volume_ul"]
        assert deposit.deposition.pre_air_chase_ul == 0.0
        assert deposit.deposition.trailing_air_gap_ul == 1.5
        assert deposit.deposition.piston_dispense_ul == 6.5
        assert deposit.deposition.push_out_ul == 3.0
        assert deposit.deposition.blow_out is True


def test_clover_adapter_uses_registered_v12_geometry_chain(clover_case):
    golden, expected = clover_case
    prepared = prepare_printing_request(golden["request"])
    actual = resolve_v12_clover_to_print_plan(prepared)
    assert actual == expected
    assert actual.provenance.resolved_config_sha256 == golden["resolved_config_sha256"]
    assert actual.provenance.source_protocol_family == "four_clover_v12"


def test_v12_adapter_supports_other_registered_current_clover_configs():
    plan = resolve_print_plan(
        {
            "family": "design",
            "workflow_name": "four_clover_spacing",
            "design_name": "four_clover",
            "parameters": {},
        }
    )
    assert plan.workflow_id == "four_clover_spacing"
    assert plan.totals.clover_count == 4
    assert plan.totals.deposit_count == 16
    assert plan.provenance.source_protocol_family == "four_clover_v12"


def test_clover_plan_matches_every_stage_zero_behavior(clover_case):
    golden, plan = clover_case
    behavior = golden["behavior"]
    source = plan.machine.sources[0]

    assert (source.labware_role, source.deck_slot, source.well) == ("source", 7, "A2")
    assert source.material_id == "BP"
    assert source.aspirate_height_mm == 4.0
    assert source.park_height_mm == 5.0
    assert plan.machine.destination_labware.deck_slot == 5
    assert plan.machine.destination_labware.dispense_height_mm == 4.0
    assert plan.machine.pipette.name == "p20_single_gen2"
    assert plan.machine.pipette.mount == "left"
    assert plan.machine.tip_strategy.tip_well == "A1"
    assert plan.machine.tip_strategy.return_tip is True
    assert plan.order_mode == "clover_by_clover"
    assert plan.totals.deposit_count == 4
    assert plan.totals.total_liquid_ul == 20.0
    assert plan.totals.clover_count == 1
    assert plan.totals.layer_count == 1
    assert plan.timing.inter_drop_delay_s == 2.0
    assert plan.timing.inter_layer_delay_s == 0.0
    assert plan.timing.inter_clover_delay_s == 0.0

    assert [deposit.provenance.design_point for deposit in plan.deposits] == [
        "D1", "D2", "D3", "D4"
    ]
    for deposit, expected in zip(plan.deposits, behavior["deposits"]):
        destination = deposit.destination
        assert deposit.sequence_index == expected["sequence"]
        assert deposit.provenance.clover_index == 1
        assert deposit.provenance.clover_name == "air_chase_5ul"
        assert deposit.provenance.layer_index == 1
        assert deposit.provenance.design_point.lower() == expected["droplet"]
        assert destination.reference_well == "E6"
        assert _point(destination.reference_well_paper_xy_mm) == [59.38, 38.24]
        assert _point(destination.center_translation_mm) == [4.5, 4.5]
        assert _point(destination.paper_center_xy_mm) == [63.88, 42.74]
        assert _point(destination.deck_center_xy_mm) == [196.38, 133.24]
        assert _point(destination.point_offset_mm) == expected["offset_xy_mm"]
        assert _point(destination.paper_xy_mm) == expected["paper_xy_mm"]
        assert _point(destination.deck_xy_mm) == expected["deck_xy_mm"]
        assert deposit.deposition.liquid_volume_ul == expected["liquid_volume_ul"]
        assert deposit.deposition.pre_air_chase_ul == 5.0
        assert deposit.deposition.trailing_air_gap_ul == 1.5
        assert deposit.deposition.piston_dispense_ul == 11.5
        assert deposit.deposition.push_out_ul == 3.0
        assert deposit.deposition.blow_out is False
        assert deposit.timing.post_dispense_delay_s == 2.0


@pytest.mark.parametrize("fixture_name", [
    "plate_well_direct_v9_golden.json",
    "four_clover_air_chase_v12_golden.json",
])
def test_plan_resolution_and_hash_are_repeatable(fixture_name):
    request = _fixture(fixture_name)["request"]
    first = resolve_print_plan(request)
    second = resolve_print_plan(deepcopy(request))
    assert first == second
    assert first.plan_id == second.plan_id == first.plan_sha256()
    assert first.canonical_bytes() == second.canonical_bytes()


@pytest.mark.parametrize("case_fixture,artifact_name", [
    ("plate_well_direct_v9_golden.json", "standard_resolved_plan.json"),
    ("four_clover_air_chase_v12_golden.json", "clover_resolved_plan.json"),
])
def test_stable_plan_artifacts_match_current_resolution(case_fixture, artifact_name):
    plan = resolve_print_plan(_fixture(case_fixture)["request"])
    artifact = (FIXTURES / artifact_name).read_text(encoding="utf-8")
    assert artifact == resolved_plan_artifact_json(plan)
    assert ResolvedPrintPlanV1.model_validate_json(artifact) == plan


@pytest.mark.parametrize("case_fixture", [
    "plate_well_direct_v9_golden.json",
    "four_clover_air_chase_v12_golden.json",
])
def test_json_round_trip_preserves_equivalence_and_hash(case_fixture):
    plan = resolve_print_plan(_fixture(case_fixture)["request"])
    formatted = resolved_plan_artifact_json(plan)
    round_tripped = parse_resolved_print_plan_json(formatted)
    assert round_tripped == plan
    assert round_tripped.plan_sha256() == plan.plan_sha256() == plan.plan_id


def _standard_payload() -> dict:
    plan = resolve_print_plan(_fixture("plate_well_direct_v9_golden.json")["request"])
    return plan.model_dump(mode="json")


@pytest.mark.parametrize(
    ("case", "mutation", "message"),
    [
        ("unknown field", lambda p: p.update(arbitrary="reasoning"), "extra_forbidden"),
        ("malformed deposit", lambda p: p["deposits"].__setitem__(0, "bad"), "model_type"),
        ("invalid enum", lambda p: p["deposits"][0]["provenance"].update(design_point="D5", kind="four_clover"), "literal_error"),
        ("inconsistent totals", lambda p: p["totals"].update(total_liquid_ul=31), "does not equal"),
        ("missing coordinate", lambda p: p["deposits"][0]["destination"].pop("deck_xy_mm"), "Field required"),
        ("invalid sequence", lambda p: p["deposits"][0].update(sequence_index=2), "continuous"),
        ("negative volume", lambda p: p["deposits"][0]["deposition"].update(liquid_volume_ul=-1), "greater than 0"),
        ("invalid destination well", lambda p: p["deposits"][0]["destination"].update(well="Z9"), "string_pattern_mismatch"),
        ("inconsistent piston", lambda p: p["deposits"][0]["deposition"].update(piston_dispense_ul=7), "piston_dispense_ul"),
        ("unknown source", lambda p: p["deposits"][0].update(source_id="other_source"), "unknown source_id"),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_invalid_plans_are_rejected(case, mutation, message):
    payload = _standard_payload()
    mutation(payload)
    with pytest.raises(ValidationError, match=message):
        ResolvedPrintPlanV1.model_validate(payload)


def test_plan_identity_tampering_is_rejected():
    payload = _standard_payload()
    payload["plan_id"] = "f" * 64
    with pytest.raises(ValidationError, match="plan_id does not match"):
        ResolvedPrintPlanV1.model_validate(payload)


def test_plan_models_are_immutable(standard_case):
    _, plan = standard_case
    with pytest.raises(ValidationError, match="frozen_instance"):
        plan.workflow_id = "changed"
