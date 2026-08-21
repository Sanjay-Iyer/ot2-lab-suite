"""Runtime skill discovery and Printing Agent routing tests."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import Field

from src.agents.printing_agent import (
    PRINTING_AGENT_PROMPT,
    PRINTING_AGENT_TOOLS,
    create_printing_agent,
    load_printing_skill,
    plan_printing_intent,
)
from src.printing.skills import (
    discover_printing_skills,
    load_printing_skill_content,
    printing_skill_index,
    select_printing_skills,
)


def test_printing_skills_are_discovered_from_real_skill_files_and_bodies_load():
    skills = {spec.name: spec for spec in discover_printing_skills()}
    assert set(skills) == {
        "standard-paper-printing",
        "design-paper-printing",
        "four-clover-printing",
        # The two generalized experiment skills, one per workflow family.
        "standard-printing-experiment",
        "four-clover-experiment",
    }
    standard_path = skills["standard-paper-printing"].directory / "SKILL.md"
    assert standard_path.is_file()
    loaded = load_printing_skill.invoke({"skill_name": "standard-paper-printing"})
    assert "The selected wells are the Cartesian product" in loaded
    assert loaded == load_printing_skill_content("standard-paper-printing")


def test_prompt_contains_compact_index_but_not_skill_bodies_or_robot_control():
    assert printing_skill_index() in PRINTING_AGENT_PROMPT
    assert "standard-paper-printing" in PRINTING_AGENT_PROMPT
    assert "The selected wells are the Cartesian product" not in PRINTING_AGENT_PROMPT
    assert "ot2-robot-control" not in PRINTING_AGENT_PROMPT
    names = {item.name for item in PRINTING_AGENT_TOOLS}
    assert "load_printing_skill" in names
    assert not any(
        token in name
        for name in names
        for token in ("live", "deploy", "execute", "aspirate", "dispense")
    )


def test_skill_selection_loads_family_then_registered_design_specialization():
    assert select_printing_skills("standard") == ("standard-paper-printing",)
    assert select_printing_skills("design") == ("design-paper-printing",)
    assert select_printing_skills("design", design_name="four_clover") == (
        "design-paper-printing",
        "four-clover-printing",
    )


def test_reference_loading_is_allowlisted_and_cannot_escape_skill_directory():
    with pytest.raises(ValueError, match="not declared"):
        load_printing_skill_content(
            "standard-paper-printing", "../ot2-robot-control/SKILL.md"
        )


@pytest.mark.parametrize(
    ("intent", "family", "design", "tool", "mode"),
    [
        (
            "Print four replicates using the standard paper layout.",
            "standard",
            None,
            "draft_printing_experiment",
            "construct",
        ),
        (
            "Print a four-clover pattern centered at E6.",
            "design",
            "four_clover",
            "draft_printing_experiment",
            "construct",
        ),
        (
            "What printing capabilities are currently available?",
            None,
            None,
            "list_printing_capabilities",
            "inspect",
        ),
        (
            "Use the same clover but make three replicates.",
            "design",
            "four_clover",
            "revise_printing_experiment",
            "modify",
        ),
        ("Print a ring.", None, None, "report_printing_request_issue", "report"),
    ],
)
def test_representative_intents_expose_internal_capability_selection(
    intent, family, design, tool, mode
):
    plan = plan_printing_intent(intent)
    assert (plan.family.value if plan.family else None) == family
    assert plan.design_name == design
    assert plan.tool_name == tool
    assert plan.execution_mode.value == mode
    assert plan.validation_outcome == "not_run"
    if family:
        assert plan.skill_names
    assert plan.parameters == {}


def test_standard_replicate_request_routes_to_bounded_yaml_layout_planning():
    plan = plan_printing_intent(
        "Print four replicates using the standard paper layout."
    )
    assert plan.parameters == {}
    assert plan.needs_clarification == []
    assert plan.tool_name == "draft_printing_experiment"


def test_agent_prompt_has_no_workflow_patch_or_build_simulation_procedure():
    assert "workflow_name" not in PRINTING_AGENT_PROMPT
    assert "parameters" not in PRINTING_AGENT_PROMPT
    assert "build before simulation" not in PRINTING_AGENT_PROMPT


class CapturingFakeToolModel(FakeMessagesListChatModel):
    seen_messages: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        self.seen_messages.append(messages)
        return super()._generate(messages, *args, **kwargs)


@pytest.mark.parametrize(
    ("intent", "payload", "present", "absent"),
    [
        (
            "Print 5 uL in A1 and A2.",
            {
                "name": "Two standard wells",
                "pattern": {
                    "type": "well_selection",
                    "rows": ["A"],
                    "columns": [1, 2],
                    "layers_by_row": {"A": 1},
                },
                "volume_ul": 5.0,
            },
            "The selected wells are the Cartesian product",
            "Populate the Stage 2 four-clover semantics",
        ),
        (
            "Print one 5 uL four-clover using the standard geometry.",
            {
                "name": "One standard clover",
                "pattern": {"type": "four_clover", "replicates": 1},
                "volume_ul": 5.0,
            },
            "Populate the Stage 2 four-clover semantics",
            "The selected wells are the Cartesian product",
        ),
    ],
)
def test_production_react_path_loads_selected_skill_before_validated_tool(
    intent, payload, present, absent
):
    model = CapturingFakeToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_and_compile_print_job",
                        "args": payload,
                        "id": "create-1",
                    }
                ],
            ),
            AIMessage(content="Validation complete."),
        ]
    )
    agent = create_printing_agent(model=model, include_legacy_compatibility=True)
    result = agent.invoke({"messages": [("user", intent)]})

    system_context = model.seen_messages[0][0].content
    assert present in system_context
    assert absent not in system_context
    tool_messages = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0].name == "create_and_compile_print_job"
    assert '"status": "success"' in tool_messages[0].content
