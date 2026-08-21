"""V1 regular 96-well family: schema, math, tool, skill, and golden reference."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import Field, ValidationError

from src.agents.custom_labware_agent import (
    create_custom_labware_agent,
    labware_agent_prompt,
    load_labware_skill,
    plan_labware_intent,
)
from src.agents.labware_tools import generate_96_well_labware
from src.labware.builder import read_labware_json
from src.labware.families import get_family
from src.labware.pipeline import generate_labware
from src.labware.schemas import WellPlate96SpecV1
from src.labware.skills import discover_labware_skills, load_labware_skill_content
from src.labware.well_plate_96_templates import (
    derive_well_plate_96_template,
    list_well_plate_96_templates,
    load_well_plate_96_template,
)
from src.utils.paths import LABWARE_OUTPUT_DIR


def reference_spec() -> WellPlate96SpecV1:
    return load_well_plate_96_template("paper_print_96_flat_v1")


def payload() -> dict[str, Any]:
    return reference_spec().model_dump(mode="json")


def test_schema_is_versioned_strict_and_fixed_to_8_by_12():
    spec = reference_spec()
    assert spec.model_json_schema()["title"] == "96WellPlateSpecV1"
    assert spec.schema_version == 1
    assert (spec.rows, spec.cols, spec.position_count) == (8, 12, 96)
    assert spec.family == "well_plate_96"

    bad = payload()
    bad["grid"]["rows"] = 7
    with pytest.raises(ValidationError):
        WellPlate96SpecV1.model_validate(bad)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("footprint", "length_mm"), -1),
        (("footprint", "width_mm"), 0),
        (("footprint", "height_mm"), -1),
        (("spacing", "x_spacing_mm"), -1),
        (("spacing", "y_spacing_mm"), 0),
        (("well", "depth_mm"), -1),
        (("well", "diameter_mm"), 0),
        (("well", "diameter_mm"), -1),
        (("well", "volume_ul"), 0),
    ],
)
def test_nonpositive_physical_values_are_rejected(path, value):
    bad = payload()
    bad[path[0]][path[1]] = value
    with pytest.raises(ValidationError):
        WellPlate96SpecV1.model_validate(bad)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("well", "shape"), "hexagonal"),
        (("well", "bottom_shape"), "conical"),
    ],
)
def test_unsupported_enums_are_rejected(path, value):
    bad = payload()
    bad[path[0]][path[1]] = value
    with pytest.raises(ValidationError):
        WellPlate96SpecV1.model_validate(bad)


def test_missing_unknown_and_irrelevant_shape_fields_are_rejected():
    missing = payload()
    del missing["spacing"]["x_spacing_mm"]
    with pytest.raises(ValidationError):
        WellPlate96SpecV1.model_validate(missing)

    unknown = payload()
    unknown["droplet_volume_ul"] = 5
    with pytest.raises(ValidationError):
        WellPlate96SpecV1.model_validate(unknown)

    circular_with_rectangle = payload()
    circular_with_rectangle["well"]["x_size_mm"] = 4
    with pytest.raises(ValidationError):
        WellPlate96SpecV1.model_validate(circular_with_rectangle)

    rectangular_with_diameter = payload()
    rectangular_with_diameter["well"] = {
        "shape": "rectangular",
        "volume_ul": 360,
        "diameter_mm": 5,
        "x_size_mm": 5,
        "y_size_mm": 5,
        "bottom_shape": "flat",
        "depth_mm": 0.1,
        "well_bottom_z_mm": 6,
    }
    with pytest.raises(ValidationError):
        WellPlate96SpecV1.model_validate(rectangular_with_diameter)


def test_nonuniform_and_uneven_requests_are_structurally_rejected():
    bad = payload()
    bad["well_overrides"] = {"A1": {"diameter_mm": 4}}
    with pytest.raises(ValidationError):
        WellPlate96SpecV1.model_validate(bad)

    for key in ("same_well_shape_and_size", "evenly_spaced_rows", "evenly_spaced_columns"):
        bad = payload()
        bad["regularity"][key] = False
        with pytest.raises(ValidationError):
            WellPlate96SpecV1.model_validate(bad)


def test_out_of_footprint_overlap_and_incompatible_height_fail_before_generation():
    for patch in (
        {"footprint": {"length_mm": 110}},
        {"spacing": {"x_spacing_mm": 5}},
        {"well": {"well_bottom_z_mm": 13.95, "depth_mm": 0.1}},
    ):
        with pytest.raises(ValidationError):
            derive_well_plate_96_template("paper_print_96_flat_v1", patch)


def test_coordinate_math_and_all_96_positions():
    spec = reference_spec()
    positions = list(get_family("well_plate_96").positions(spec))
    by_name = {position.name: position for position in positions}
    assert len(positions) == len(by_name) == 96
    assert len({(position.x, position.y) for position in positions}) == 96
    expected = {
        "A1": (14.38, 74.24),
        "A2": (23.38, 74.24),
        "A12": (113.38, 74.24),
        "B1": (14.38, 65.24),
        "H1": (14.38, 11.24),
        "H12": (113.38, 11.24),
    }
    for name, coordinates in expected.items():
        assert (by_name[name].x, by_name[name].y) == coordinates
    assert by_name["A2"].x - by_name["A1"].x == pytest.approx(9.0)
    assert by_name["B1"].y - by_name["A1"].y == pytest.approx(-9.0)


def test_pipeline_generates_only_after_all_layers_pass(tmp_path):
    result = generate_labware(reference_spec(), output_dir=tmp_path)
    assert result.success
    assert result.position_count == 96
    assert result.validation.ok
    assert result.validation.layers == {
        "schema": "PASS",
        "geometry": "PASS",
        "json": "PASS",
        "opentrons": "PASS",
    }
    assert result.output_path and result.output_path.is_file()
    assert len(result.definition["wells"]) == 96
    assert [len(column) for column in result.definition["ordering"]] == [8] * 12


def test_bottom_position_check_and_stacking_mapping():
    spec = derive_well_plate_96_template(
        "paper_print_96_flat_v1",
        {
            "identity": {"load_name": "position_check_excluded"},
            "regularity": {"exclude_from_position_check": True},
            "well": {"bottom_shape": "round"},
        },
    )
    result = generate_labware(spec, write=False)
    assert result.success
    assert result.definition["parameters"]["quirks"] == ["excludeFromLabwarePositionCheck"]
    assert result.definition["groups"][0]["metadata"]["wellBottomShape"] == "u"
    assert "stackingOffsetWithLabware" not in result.definition
    assert "stackingOffsetWithModule" not in result.definition


def test_rectangular_wells_use_only_opentrons_xy_dimensions():
    rectangular = payload()
    rectangular["identity"]["load_name"] = "rectangular_96_probe"
    rectangular["well"] = {
        "shape": "rectangular",
        "volume_ul": 300,
        "x_size_mm": 5.0,
        "y_size_mm": 6.0,
        "bottom_shape": "v_bottom",
        "depth_mm": 1.0,
        "well_bottom_z_mm": 6.0,
    }
    result = generate_labware(WellPlate96SpecV1.model_validate(rectangular), write=False)
    assert result.success
    well = result.definition["wells"]["A1"]
    assert well["shape"] == "rectangular"
    assert well["xDimension"] == 5.0
    assert well["yDimension"] == 6.0
    assert "diameter" not in well
    assert result.definition["groups"][0]["metadata"]["wellBottomShape"] == "v"


def test_all_three_templates_use_one_registered_generator():
    assert set(list_well_plate_96_templates()) == {
        "paper_print_96_diameter_5_v1",
        "paper_print_96_flat_v1",
        "paper_print_96_spacing_8_v1",
    }
    for name in list_well_plate_96_templates():
        result = generate_labware(load_well_plate_96_template(name), write=False)
        assert result.success, result.summary()
        assert result.labware_family == "well_plate_96"
        assert result.position_count == 96


def test_golden_reference_is_geometrically_equivalent():
    generated = generate_labware(reference_spec(), write=False).definition
    reference = read_labware_json(LABWARE_OUTPUT_DIR / "paper_print_96_flat.json")
    assert len(generated["wells"]) == len(reference["wells"]) == 96
    assert generated["ordering"] == reference["ordering"]
    assert generated["dimensions"] == reference["dimensions"]
    assert generated["groups"] == reference["groups"]
    for name in reference["wells"]:
        for field in (
            "x", "y", "z", "shape", "diameter", "depth", "totalLiquidVolume"
        ):
            assert generated["wells"][name][field] == reference["wells"][name][field]


def test_bounded_tool_returns_structured_saved_result(monkeypatch, tmp_path):
    import src.agents.labware_tools as tools_module

    original = generate_labware
    monkeypatch.setattr(
        tools_module,
        "generate_labware",
        lambda spec, overwrite=False: original(spec, output_dir=tmp_path, overwrite=overwrite),
    )
    result = json.loads(
        generate_96_well_labware.invoke({"spec": payload(), "overwrite": False})
    )
    assert result["success"] is True
    assert result["family"] == "well_plate_96"
    assert result["position_count"] == 96
    assert result["validation"]["layers"]["opentrons"] == "PASS"
    assert len(result["definition_sha256"]) == 64
    assert Path(result["output_path"]).is_file()


def test_runtime_skill_is_discovered_loadable_and_injected():
    skills = {skill.name: skill for skill in discover_labware_skills()}
    assert "96-well-labware" in skills
    body = load_labware_skill.invoke({"skill_name": "96-well-labware"})
    assert "Never guess these values" in body
    assert body == load_labware_skill_content("96-well-labware")
    context = labware_agent_prompt(
        {"messages": [("user", "Make the same paper plate with 8 mm spacing.")]}
    )[0].content
    assert "Selected runtime skill content" in context
    assert "Never guess these values" in context


def test_agent_router_handles_template_new_and_nonuniform_requests():
    same = plan_labware_intent("Make the same 96-well paper plate with 8 mm spacing.")
    assert same.family == "well_plate_96"
    assert same.template_name == "paper_print_96_flat_v1"
    assert same.skill_names == ["96-well-labware"]

    new = plan_labware_intent("Make an 8 x 12 plate with a 120 x 80 mm footprint.")
    assert new.template_name is None
    assert any("requires all measured" in item for item in new.needs_clarification)

    invalid = plan_labware_intent("Make A1 4 mm but the rest of the wells 8 mm.")
    assert any("nonuniform" in item for item in invalid.needs_clarification)


class CapturingFakeToolModel(FakeMessagesListChatModel):
    seen_messages: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        self.seen_messages.append(messages)
        return super()._generate(messages, *args, **kwargs)


def test_production_react_path_has_skill_schema_and_bounded_tool(monkeypatch, tmp_path):
    import src.agents.labware_tools as tools_module

    spec_payload = payload()
    spec_payload["identity"]["load_name"] = "agent_spacing_8_probe"
    spec_payload["spacing"] = {"x_spacing_mm": 8.0, "y_spacing_mm": 8.0}
    original = generate_labware
    monkeypatch.setattr(
        tools_module,
        "generate_labware",
        lambda spec, overwrite=False: original(spec, output_dir=tmp_path, overwrite=overwrite),
    )
    model = CapturingFakeToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "generate_registered_labware",
                        "args": {"spec": spec_payload, "overwrite": False},
                        "id": "generate-1",
                    }
                ],
            ),
            AIMessage(content="Generated and validated the 96-well definition."),
        ]
    )
    agent = create_custom_labware_agent(model=model)
    result = agent.invoke(
        {"messages": [("user", "Make the same paper 96-well plate with 8 mm spacing.")]}
    )
    assert "Never guess these values" in model.seen_messages[0][0].content
    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert len(tool_messages) == 1
    tool_payload = json.loads(tool_messages[0].content)
    assert tool_messages[0].name == "generate_registered_labware"
    assert tool_payload["success"] is True
    assert tool_payload["position_count"] == 96
