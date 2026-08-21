"""Stage 7 generalized Printing Agent boundary tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import Field

from src.agents.printing_agent import (
    STANDARD_EXPERIMENT_AGENT_TOOLS,
    create_standard_experiment_agent,
)
import src.agents.printing_tools as printing_tools


class CapturingModel(FakeMessagesListChatModel):
    seen_messages: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        self.seen_messages.append(messages)
        return super()._generate(messages, *args, **kwargs)


def test_generalized_agent_surface_has_no_legacy_or_motion_bypasses():
    names = {item.name for item in STANDARD_EXPERIMENT_AGENT_TOOLS}

    assert names == {
        "list_standard_printing_experiment_capabilities",
        "create_standard_printing_experiment_config",
        "validate_standard_printing_experiment",
        "resolve_standard_printing_experiment",
        "inspect_standard_printing_layout",
        "simulate_approved_standard_printing_experiment",
        "report_printing_request_issue",
    }
    assert not any(
        token in name
        for name in names
        for token in ("aspirate", "dispense", "move", "live", "execute")
    )


def test_agent_loads_only_generalized_skill_and_invokes_high_level_preview():
    repo = Path(__file__).resolve().parents[1]
    config = yaml.safe_load(
        (repo / "configs/templates/printing/01_printing_standard.template.yaml").read_text(
            encoding="utf-8"
        )
    )
    model = CapturingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "inspect_standard_printing_layout",
                        "args": {"experiment_config": config},
                        "id": "preview-1",
                    }
                ],
            ),
            AIMessage(content="Proposed experiment is ready for external review."),
        ]
    )
    agent = create_standard_experiment_agent(model=model)
    result = agent.invoke(
        {"messages": [("user", "Prepare a three-point dye ladder and controls.")]}
    )

    context = model.seen_messages[0][0].content
    assert "# Standard printing experiment" in context
    assert "standard-paper-printing" not in context
    assert "four-clover" not in context
    tool_message = next(
        message for message in result["messages"] if isinstance(message, ToolMessage)
    )
    payload = json.loads(tool_message.content)
    assert payload["status"] == "PASS"
    assert payload["totals"]["print_count"] == 6


@pytest.mark.parametrize(
    ("scientist_request", "procedure", "expected_prints"),
    (
        (
            "Print one dye droplet from the vial onto H12.",
            [
                {
                    "id": "single_spot",
                    "type": "print",
                    "source": {"kind": "liquid", "liquid_id": "solution_a"},
                    "substrate": "paper",
                    "targets": ["H12"],
                    "volume_ul": 3.0,
                }
            ],
            1,
        ),
        (
            "Make a working aliquot and print it at D1.",
            [
                {
                    "id": "make_aliquot",
                    "type": "transfer",
                    "liquid_id": "solution_a",
                    "destination": {"labware": "plate", "well": "D1"},
                    "volume_ul": 12.0,
                    "result_liquid_id": "working_solution",
                },
                {
                    "id": "print_aliquot",
                    "type": "print",
                    "source": {"kind": "liquid", "liquid_id": "working_solution"},
                    "substrate": "paper",
                    "targets": ["D1"],
                    "volume_ul": 3.0,
                },
            ],
            1,
        ),
        (
            "Mix the vial twice, then print two control replicates.",
            [
                {
                    "id": "mix_source",
                    "type": "mix",
                    "liquid_id": "solution_a",
                    "location": {"labware": "vial_rack", "well": "A1"},
                    "cycles": 2,
                    "volume_ul": 4.0,
                },
                {
                    "id": "control_replicates",
                    "type": "print",
                    "source": {"kind": "liquid", "liquid_id": "solution_a"},
                    "substrate": "paper",
                    "targets": ["E1", "F1"],
                    "volume_ul": 3.0,
                    "purpose": "control",
                },
            ],
            2,
        ),
    ),
)
def test_agent_handles_multiple_unrelated_requests_through_config_and_preview(
    scientist_request, procedure, expected_prints, tmp_path, monkeypatch
):
    repo = Path(__file__).resolve().parents[1]
    config = yaml.safe_load(
        (repo / "configs/templates/printing/01_printing_standard.template.yaml").read_text(
            encoding="utf-8"
        )
    )
    config = deepcopy(config)
    config["experiment"]["metadata"]["experiment_id"] = "agent_neutral_variant"
    config["experiment"]["procedure"] = procedure
    monkeypatch.setattr(
        printing_tools, "STANDARD_EXPERIMENT_PROPOSAL_DIR", tmp_path / "proposals"
    )
    model = CapturingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_standard_printing_experiment_config",
                        "args": {
                            "experiment_config": config,
                            "output_name": "agent_neutral_variant",
                        },
                        "id": "create-1",
                    },
                    {
                        "name": "inspect_standard_printing_layout",
                        "args": {"experiment_config": config},
                        "id": "preview-1",
                    },
                ],
            ),
            AIMessage(content="The validated proposal is ready for external review."),
        ]
    )

    result = create_standard_experiment_agent(model=model).invoke(
        {"messages": [("user", scientist_request)]}
    )
    payloads = [
        json.loads(message.content)
        for message in result["messages"]
        if isinstance(message, ToolMessage)
    ]

    assert any(payload.get("canonical_config_yaml") for payload in payloads)
    preview = next(payload for payload in payloads if "review" in payload)
    assert preview["totals"]["print_count"] == expected_prints
