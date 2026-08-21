from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.printing.job_compiler import compile_print_job, labware_definition_sha256
from src.printing.schemas import PrintJobV1, parse_print_job_json


FIXTURES = Path(__file__).parent / "fixtures" / "printing"
STANDARD_PLAN_HASH = "a36c314184c15eb94eb8a8cb2ccf7a492405f4cfe66d1a4a12bdb9cd64bbad0a"
CLOVER_PLAN_HASH = "664c92e97743239aadb677566c673a0e8d63fa3b8fb5e34a86f16da7c7695ab7"


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def standard_payload() -> dict:
    return _payload("standard_print_job_v1.json")


@pytest.fixture
def clover_payload() -> dict:
    return _payload("four_clover_print_job_v1.json")


@pytest.mark.parametrize(
    "fixture",
    ["standard_print_job_v1.json", "four_clover_print_job_v1.json"],
)
def test_job_round_trip_preserves_semantics_canonical_json_and_hash(fixture):
    job = parse_print_job_json((FIXTURES / fixture).read_bytes())
    round_tripped = PrintJobV1.model_validate_json(job.model_dump_json())
    assert round_tripped == job
    assert round_tripped.canonical_json() == job.canonical_json()
    assert round_tripped.job_sha256() == job.job_sha256() == job.job_id


def test_labels_and_inspection_metadata_are_not_part_of_scientific_identity(
    standard_payload,
):
    standard_payload.pop("job_id")
    first = PrintJobV1.from_content(**standard_payload)
    standard_payload["name"] = "A differently formatted label"
    standard_payload["description"] = "Conversation-specific prose"
    standard_payload["metadata"] = {"conversation": "not-identity"}
    second = PrintJobV1.from_content(**standard_payload)
    assert second.job_id == first.job_id


def test_job_identity_tampering_is_rejected(standard_payload):
    standard_payload["job_id"] = "f" * 64
    with pytest.raises(ValidationError, match="job_id does not match"):
        PrintJobV1.model_validate(standard_payload)


def test_job_contract_contains_no_machine_owned_implementation_fields():
    schema_text = json.dumps(PrintJobV1.model_json_schema())
    for forbidden in (
        "piston_dispense_ul",
        "push_out_ul",
        "api_level",
        "pipette",
        "mount",
        "deck_slot",
        "source_well",
        "aspirate_height_mm",
        "tiprack",
        "flow_rates",
    ):
        assert forbidden not in schema_text


def test_standard_job_compiles_to_unchanged_stage1_plan_hash(standard_payload):
    job = PrintJobV1.model_validate(standard_payload)
    plan = compile_print_job(job)
    assert plan.plan_id == plan.plan_sha256() == STANDARD_PLAN_HASH
    assert plan.provenance.source_job_sha256 == job.job_id
    assert plan.provenance.source_request_sha256 is not None
    assert [item.destination.well for item in plan.deposits] == [
        "A1",
        "A2",
        "B1",
        "B2",
        "B1",
        "B2",
    ]
    assert [item.provenance.layer_index for item in plan.deposits] == [1, 1, 1, 1, 2, 2]


def test_clover_job_compiles_to_unchanged_stage1_plan_hash(clover_payload):
    job = PrintJobV1.model_validate(clover_payload)
    plan = compile_print_job(job)
    assert plan.plan_id == plan.plan_sha256() == CLOVER_PLAN_HASH
    assert plan.provenance.source_job_sha256 == job.job_id
    assert [item.provenance.design_point for item in plan.deposits] == [
        "D1",
        "D2",
        "D3",
        "D4",
    ]
    assert plan.totals.clover_count == 1


def test_job_references_validated_paper_definition_without_embedding_geometry(
    standard_payload,
):
    job = PrintJobV1.model_validate(standard_payload)
    definition = json.loads(
        Path("labware/paper_print_96_flat.json").read_text(encoding="utf-8")
    )
    assert job.substrate.definition_sha256 == labware_definition_sha256(definition)
    assert set(job.substrate.model_dump()) == {
        "load_name",
        "namespace",
        "version",
        "definition_sha256",
        "template_id",
    }
    serialized = job.model_dump(mode="json")
    assert "wells" not in serialized["substrate"]
    assert "x_offset" not in serialized["substrate"]
    plan = compile_print_job(job)
    assert plan.machine.destination_labware.labware_name == "paper_print_96_flat"
    assert (
        plan.deposits[0].destination.paper_xy_mm.x_mm,
        plan.deposits[0].destination.paper_xy_mm.y_mm,
    ) == (14.38, 74.24)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p["deposition"].update(volume_ul=0), "greater than 0"),
        (lambda p: p.pop("pattern"), "Field required"),
        (lambda p: p["pattern"].update(type="ring"), "union_tag_invalid"),
        (lambda p: p["pattern"].update(rows=[]), "too_short"),
        (lambda p: p["pattern"].update(columns=[13]), "integers from 1 through 12"),
        (lambda p: p["pattern"]["layers_by_row"].update(A=0), "integer >= 1"),
        (lambda p: p["replication"].update(replicates=0), "greater than or equal to 1"),
        (lambda p: p["ordering_intent"].update(mode="random"), "union_tag_invalid"),
        (
            lambda p: p.update(machine={"pipette": "p20"}),
            "Extra inputs are not permitted",
        ),
    ],
)
def test_invalid_standard_scientific_jobs_are_rejected(
    standard_payload, mutation, message
):
    payload = deepcopy(standard_payload)
    payload.pop("job_id")
    mutation(payload)
    with pytest.raises((ValidationError, ValueError), match=message):
        PrintJobV1.from_content(**payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda p: p["pattern"]["centers"][0].update(reference_well="Z9"),
            "string_pattern_mismatch",
        ),
        (
            lambda p: p["pattern"].update(geometry={"half_width_mm": 2.0}),
            "both half_width_mm",
        ),
        (lambda p: p["pattern"].update(centers=[]), "too_short"),
        (
            lambda p: p["replication"].update(replicates=2),
            "must equal the number of centers",
        ),
    ],
)
def test_invalid_clover_scientific_jobs_are_rejected(clover_payload, mutation, message):
    payload = deepcopy(clover_payload)
    payload.pop("job_id")
    mutation(payload)
    with pytest.raises((ValidationError, ValueError), match=message):
        PrintJobV1.from_content(**payload)


def test_unknown_material_and_labware_references_fail_deterministically(
    standard_payload,
):
    material_payload = deepcopy(standard_payload)
    material_payload.pop("job_id")
    material_payload["materials"][0]["material_id"] = "unknown"
    material_payload["deposition"]["material_id"] = "unknown"
    with pytest.raises(ValueError, match="unknown material reference"):
        compile_print_job(PrintJobV1.from_content(**material_payload))

    labware_payload = deepcopy(standard_payload)
    labware_payload.pop("job_id")
    labware_payload["substrate"]["load_name"] = "missing_paper_plate"
    with pytest.raises(ValueError, match="unknown labware reference"):
        compile_print_job(PrintJobV1.from_content(**labware_payload))


def test_impossible_clover_placement_reaches_existing_physical_validation(
    clover_payload,
):
    payload = deepcopy(clover_payload)
    payload.pop("job_id")
    payload["pattern"]["centers"][0].update(
        reference_well="A12",
        x_offset_mm=100.0,
        y_offset_mm=100.0,
    )
    with pytest.raises(ValueError, match="failed deterministic validation"):
        compile_print_job(PrintJobV1.from_content(**payload))


def test_job_summaries_are_deterministic_and_human_readable(
    standard_payload, clover_payload
):
    standard = PrintJobV1.model_validate(standard_payload).summary()
    clover = PrintJobV1.model_validate(clover_payload).summary()
    assert "Targets: A1, A2, B1, B2" in standard
    assert "Layers: A=1, B=2" in standard
    assert "Clovers: 1" in clover
    assert "Points per clover: 4" in clover
