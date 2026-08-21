"""Stage 3 production-agent path and PrintJobV1 boundary tests."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import Field

from src.agents.printing_agent import PRINTING_AGENT_TOOLS, create_printing_agent
from src.agents.printing_tools import (
    create_and_compile_print_job,
    list_printing_capabilities,
    list_registered_materials,
    list_registered_substrates,
    modify_and_compile_print_job,
)
from src.printing.agent_contract import (
    PrintingErrorStage,
    create_and_compile_draft,
)
from src.printing.job_compiler import PrintJobCompilationError


STANDARD_PLAN_HASH = "a36c314184c15eb94eb8a8cb2ccf7a492405f4cfe66d1a4a12bdb9cd64bbad0a"
CLOVER_PLAN_HASH = "664c92e97743239aadb677566c673a0e8d63fa3b8fb5e34a86f16da7c7695ab7"
STANDARD_JOB_HASH = "b7dd8754631fb2abf77af7cd559cec51322207024b37ba1349992c7adb759b2e"
CLOVER_JOB_HASH = "2984d6180d8832059043336f45223ffef7c0f5b9716beaaf05729e6a72ea5be6"


STANDARD_GOLDEN_ARGS = {
    "name": "Natural-language standard golden",
    "substrate_id": "our standard paper plate",
    "material_id": "sample",
    "pattern": {
        "type": "well_selection",
        "rows": ["A", "B"],
        "columns": [1, 2],
        "layers_by_row": {"A": 1, "B": 2},
    },
    "volume_ul": 5.0,
}

CLOVER_GOLDEN_ARGS = {
    "name": "Natural-language clover golden",
    "substrate_id": "our standard paper plate",
    "material_id": "BP",
    "pattern": {
        "type": "four_clover",
        "geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
        "centers": [
            {
                "name": "air_chase_5ul",
                "reference_well": "E6",
                "x_offset_mm": 4.5,
                "y_offset_mm": 4.5,
            }
        ],
        "replicates": 1,
        "layers": 1,
    },
    "volume_ul": 5.0,
}


class CapturingFakeToolModel(FakeMessagesListChatModel):
    seen_messages: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        self.seen_messages.append(messages)
        return super()._generate(messages, *args, **kwargs)


def _production_tool_result(
    intent: str,
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    model = CapturingFakeToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": tool_name, "args": arguments, "id": "stage3-1"}],
            ),
            AIMessage(content="Structured printing result prepared."),
        ]
    )
    agent = create_printing_agent(model=model, include_legacy_compatibility=True)
    result = agent.invoke({"messages": [("user", intent)]})
    messages = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert len(messages) == 1
    return json.loads(messages[0].content), model.seen_messages[0][0].content


def test_agent_surface_is_yaml_workflow_first_and_legacy_execution_tools_are_not_exposed():
    names = {item.name for item in PRINTING_AGENT_TOOLS}
    assert names == {
        "list_printing_capabilities",
        "list_registered_substrates",
        "list_registered_materials",
        "draft_printing_experiment",
        "describe_printing_experiment",
        "revise_printing_experiment",
        "approve_printing_experiment",
        "reject_printing_experiment",
        "prepare_approved_printing_experiment",
        "report_printing_request_issue",
        "load_printing_skill",
    }
    assert not names & {
        "validate_printing_request",
        "preview_design_coordinates",
        "build_printing_protocol",
        "simulate_printing_protocol",
    }
    schema = create_and_compile_print_job.args_schema.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "workflow_name" not in schema["properties"]
    assert "parameters" not in schema["properties"]
    assert "job_id" not in schema["properties"]


def test_capability_and_reference_tools_hide_derived_hashes_and_machine_locations():
    capabilities = json.loads(list_printing_capabilities.invoke({}))
    substrates = json.loads(list_registered_substrates.invoke({}))
    materials = json.loads(list_registered_materials.invoke({}))
    assert set(capabilities["patterns"]) == {"well_selection", "four_clover"}
    assert substrates[0]["substrate_id"] == "paper_print_96_flat"
    assert "definition_sha256" not in substrates[0]
    assert {item["material_id"] for item in materials} == {"sample", "BP"}
    assert not any("slot" in json.dumps(item).lower() for item in materials)


def test_standard_natural_language_golden_reaches_actual_skill_and_plan_hash():
    prompt = (
        "Print 5 uL into rows A and B and columns 1 and 2. Print one layer in "
        "row A and two layers in row B using our standard paper plate."
    )
    result, context = _production_tool_result(
        prompt,
        tool_name="create_and_compile_print_job",
        arguments=STANDARD_GOLDEN_ARGS,
    )
    assert "The selected wells are the Cartesian product" in context
    assert "Populate the Stage 2 four-clover semantics" not in context
    assert result["status"] == "success"
    assert result["job_id"] == STANDARD_JOB_HASH
    assert result["plan_id"] == STANDARD_PLAN_HASH
    assert result["plan"]["provenance"]["source_job_sha256"] == result["job_id"]
    assert result["preview"]["target_wells"] == ["A1", "A2", "B1", "B2"]
    assert result["preview"]["layers_by_row"] == {"A": 1, "B": 2}


def test_clover_natural_language_golden_reaches_both_skills_and_plan_hash():
    prompt = (
        "Print one four-point clover using 5 uL BP droplets on our standard paper "
        "plate. Use a 2 mm horizontal and vertical half-spacing and place the center "
        "relative to E6 with a 4.5 mm X and Y offset."
    )
    result, context = _production_tool_result(
        prompt,
        tool_name="create_and_compile_print_job",
        arguments=CLOVER_GOLDEN_ARGS,
    )
    assert "registered geometric design" in context
    assert "Populate the Stage 2 four-clover semantics" in context
    assert result["status"] == "success"
    assert result["job_id"] == CLOVER_JOB_HASH
    assert result["plan_id"] == CLOVER_PLAN_HASH
    assert result["preview"]["deposit_count"] == 4
    assert set(result["preview"]["clovers"][0]["points_mm"]) == {"D1", "D2", "D3", "D4"}


@pytest.mark.parametrize(
    ("prompt", "arguments", "expected_pattern", "expected_deposits"),
    [
        (
            "pritn 5 ul in a1 and a2",
            {
                "name": "Normalized misspelled wells",
                "pattern": {
                    "type": "well_selection",
                    "rows": ["A"],
                    "columns": [1, 2],
                    "layers_by_row": {"A": 1},
                },
                "volume_ul": 5.0,
            },
            "well_selection",
            2,
        ),
        (
            "5 ul rows a b cols 1 2",
            {
                "name": "Normalized abbreviated grid",
                "pattern": {
                    "type": "well_selection",
                    "rows": ["A", "B"],
                    "columns": [1, 2],
                    "layers_by_row": {"A": 1, "B": 1},
                },
                "volume_ul": 5.0,
            },
            "well_selection",
            4,
        ),
        (
            "print 3 clovers 5ul each standard paper",
            {
                "name": "Normalized three clovers",
                "pattern": {"type": "four_clover", "replicates": 3},
                "volume_ul": 5.0,
            },
            "four_clover",
            12,
        ),
    ],
)
def test_imperfect_language_normalizes_to_strict_jobs(
    prompt, arguments, expected_pattern, expected_deposits
):
    result, _ = _production_tool_result(
        prompt,
        tool_name="create_and_compile_print_job",
        arguments=arguments,
    )
    assert result["status"] == "success"
    assert result["job"]["pattern"]["type"] == expected_pattern
    assert result["preview"]["deposit_count"] == expected_deposits


def test_follow_up_modification_creates_new_job_and_preserves_old_job():
    original = json.loads(create_and_compile_print_job.invoke(CLOVER_GOLDEN_ARGS))
    old_job = original["job"]
    result, context = _production_tool_result(
        "use teh same clover but make 3 replicates",
        tool_name="modify_and_compile_print_job",
        arguments={
            "existing_job": old_job,
            "changes": {"clover_replicates": 3},
        },
    )
    assert "The old YAML remains unchanged" in context
    assert result["status"] == "success"
    assert len(old_job["pattern"]["centers"]) == 1
    assert len(result["job"]["pattern"]["centers"]) == 3
    assert result["job_id"] != original["job_id"]
    assert result["preview"]["deposit_count"] == 12
    assert result["preview"]["total_liquid_ul"] == 60.0
    assert result["plan"]["provenance"]["source_job_sha256"] == result["job_id"]


def test_standard_follow_up_changes_only_requested_row_layer():
    original = json.loads(create_and_compile_print_job.invoke(STANDARD_GOLDEN_ARGS))
    modified = json.loads(
        modify_and_compile_print_job.invoke(
            {
                "existing_job": original["job"],
                "changes": {"layers_by_row": {"B": 3}},
            }
        )
    )
    assert modified["status"] == "success"
    assert modified["job"]["pattern"]["layers_by_row"] == {"A": 1, "B": 3}
    assert modified["job"]["deposition"] == original["job"]["deposition"]
    assert modified["job_id"] != original["job_id"]


def test_follow_ups_can_change_volume_or_explicit_clover_center():
    standard = json.loads(create_and_compile_print_job.invoke(STANDARD_GOLDEN_ARGS))
    volume_change = json.loads(
        modify_and_compile_print_job.invoke(
            {
                "existing_job": standard["job"],
                "changes": {"volume_ul": 3.0},
            }
        )
    )
    assert volume_change["status"] == "success"
    assert volume_change["job"]["deposition"]["volume_ul"] == 3.0
    assert volume_change["job"]["pattern"] == standard["job"]["pattern"]

    clover = json.loads(create_and_compile_print_job.invoke(CLOVER_GOLDEN_ARGS))
    center_change = json.loads(
        modify_and_compile_print_job.invoke(
            {
                "existing_job": clover["job"],
                "changes": {
                    "clover_centers": [
                        {
                            "name": "moved_clover",
                            "reference_well": "E5",
                            "x_offset_mm": 4.5,
                            "y_offset_mm": 4.5,
                        }
                    ]
                },
            }
        )
    )
    assert center_change["status"] == "success"
    assert center_change["job"]["pattern"]["centers"][0]["reference_well"] == "E5"
    assert (
        center_change["job"]["pattern"]["geometry"]
        == clover["job"]["pattern"]["geometry"]
    )


@pytest.mark.parametrize(
    ("arguments", "stage"),
    [
        ({**STANDARD_GOLDEN_ARGS, "volume_ul": -5.0}, "schema_validation"),
        ({**STANDARD_GOLDEN_ARGS, "material_id": "invented"}, "reference_resolution"),
        ({**STANDARD_GOLDEN_ARGS, "volume_ul": 5000.0}, "physical_plan_validation"),
        (
            {
                **CLOVER_GOLDEN_ARGS,
                "pattern": {
                    **CLOVER_GOLDEN_ARGS["pattern"],
                    "centers": [
                        {
                            "name": "outside",
                            "reference_well": "A12",
                            "x_offset_mm": 100.0,
                            "y_offset_mm": 100.0,
                        }
                    ],
                },
            },
            "physical_plan_validation",
        ),
    ],
)
def test_schema_reference_and_physical_errors_remain_distinguishable(arguments, stage):
    result = json.loads(create_and_compile_print_job.invoke(arguments))
    assert result["status"] == "error"
    assert result["error"]["stage"] == stage
    assert result["validation"] == "FAIL"


def test_ambiguous_and_unsupported_requests_return_structured_interpretation_results():
    ambiguous, _ = _production_tool_result(
        "Print some clovers.",
        tool_name="report_printing_request_issue",
        arguments={
            "status": "needs_clarification",
            "code": "missing_scientific_information",
            "message": "Volume and replicate placement are required.",
            "details": ["droplet volume", "replicate count or centers"],
        },
    )
    unsupported, _ = _production_tool_result(
        "Print a ring.",
        tool_name="report_printing_request_issue",
        arguments={
            "status": "unsupported",
            "code": "unsupported_pattern",
            "message": "Ring printing is not registered in V1.",
        },
    )
    assert ambiguous["error"]["stage"] == "interpretation"
    assert ambiguous["status"] == "needs_clarification"
    assert unsupported["error"]["stage"] == "interpretation"
    assert unsupported["status"] == "unsupported"


def test_compiler_and_simulation_error_categories_are_distinct(monkeypatch):
    import src.printing.agent_contract as contract

    monkeypatch.setattr(
        contract,
        "compile_print_job",
        lambda job: (_ for _ in ()).throw(PrintJobCompilationError("adapter failed")),
    )
    result = create_and_compile_draft(STANDARD_GOLDEN_ARGS)
    assert result.error.stage == PrintingErrorStage.COMPILER
    assert PrintingErrorStage.SIMULATION.value == "simulation"
