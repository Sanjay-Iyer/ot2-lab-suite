"""Regression for the explicitly deprecated generic-agent mock path."""
from __future__ import annotations

from src.agents.main import create_opentrons_agent


def test_generic_mock_path_is_defined_and_fails_closed():
    agent = create_opentrons_agent(use_mock=True)
    result = agent.invoke({"messages": [("user", "Run a protocol on the robot.")]})
    text = result["messages"][-1].content
    assert "--mock agent is deprecated" in text
    assert "No tools were called" in text
    assert not any(getattr(message, "tool_calls", []) for message in result["messages"])
