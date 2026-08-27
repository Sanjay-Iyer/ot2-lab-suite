"""Conversation-level tests for the LangGraph SERS agent.

These use a scripted chat model so the assertions are about graph routing,
approval gating, and state transitions rather than about a model's wording.
"""

from __future__ import annotations

import json

import pytest

from src.sers_engine.agent.graph import SERSExperimentAgent
from src.sers_engine.state import REGISTRY, ExperimentStatus
from tests.fake_llm import ScriptedChatModel

DECK = [
    {"role": "working_plate", "kind": "plate", "slot": 1},
    {"role": "paper", "kind": "paper", "slot": 5},
    {"role": "vial_rack", "kind": "vial_rack", "slot": 7},
    {"role": "tips", "kind": "tiprack", "slot": 9},
]
LIQUIDS = [
    {"name": "nanoparticles", "labware": "vial_rack", "well": "A1",
     "loaded_volume_ul": 5000, "minimum_remaining_volume_ul": 2500},
    {"name": "water", "labware": "vial_rack", "well": "A2",
     "loaded_volume_ul": 15000, "minimum_remaining_volume_ul": 2500},
    {"name": "crystal_violet", "labware": "vial_rack", "well": "B1",
     "loaded_volume_ul": 5000, "minimum_remaining_volume_ul": 2500},
]
STEPS = [
    {"step_type": "dilution", "step_id": "np_a", "source": "nanoparticles",
     "diluent": "water", "destination": "working_plate:A1",
     "dilution_factor": 10, "final_volume_ul": 150},
    {"step_type": "dilution", "step_id": "np_b", "source": "nanoparticles",
     "diluent": "water", "destination": "working_plate:A2",
     "dilution_factor": 20, "final_volume_ul": 150},
    {"step_type": "print", "step_id": "print_a", "source": "np_a",
     "targets": ["A1:C1"], "drop_volume_ul": 5, "drops_per_target": 1},
    {"step_type": "print", "step_id": "print_b", "source": "np_b",
     "targets": ["A2:C2"], "drop_volume_ul": 5, "drops_per_target": 3},
    {"step_type": "wait", "step_id": "dry", "duration_s": 1800, "reason": "dry"},
    {"step_type": "print", "step_id": "print_cv", "source": "crystal_violet",
     "targets": ["A1:C1", "A2:C2"], "drop_volume_ul": 5, "drops_per_target": 1},
]

CREATE_CALL = {
    "name": "create_experiment",
    "args": {"experiment_name": "np_cv", "deck": DECK, "liquids": LIQUIDS, "steps": STEPS},
}


@pytest.fixture(autouse=True)
def _clean_registry():
    REGISTRY.clear()
    yield
    REGISTRY.clear()


def _tool_result(agent: SERSExperimentAgent, name: str) -> dict:
    for message in reversed(agent.tool_transcript()):
        if message["name"] == name:
            return json.loads(message["content"])
    raise AssertionError(f"no tool result for {name}")


def test_conversation_creates_then_revises_without_losing_configuration():
    agent = SERSExperimentAgent(
        ScriptedChatModel([
            [CREATE_CALL],
            "Here is the proposed workflow.",
            [{"name": "update_experiment", "args": {"update_steps": [
                {"step_id": "np_a", "dilution_factor": 30},
                {"step_id": "np_b", "dilution_factor": 50},
            ]}}],
            "Updated to 30x and 50x.",
        ]),
        thread_id="revise",
    )

    first = agent.send("two NP dilutions, print them, dry, then CV")
    assert first["state"]["status"] == ExperimentStatus.VALIDATED.value
    original_hash = first["state"]["config_hash"]

    second = agent.send("change them to 30x and 50x")
    update = _tool_result(agent, "update_experiment")
    assert update["ok"] is True
    assert update["changes"] == [
        "step np_a: dilution_factor=30",
        "step np_b: dilution_factor=50",
    ]

    session = REGISTRY.get()
    assert [step.step_id for step in session.experiment.steps] == [
        "np_a", "np_b", "print_a", "print_b", "dry", "print_cv"
    ]
    prints = [step for step in session.experiment.steps if step.step_type == "print"]
    assert [step.drops_per_target for step in prints] == [1, 3, 1]
    assert second["state"]["config_hash"] != original_hash
    assert second["state"]["revision"] == 1


def test_agent_cannot_simulate_before_the_user_approves_the_plan():
    agent = SERSExperimentAgent(
        ScriptedChatModel([
            [CREATE_CALL],
            [{"name": "simulate_experiment", "args": {}}],
            "I need your approval of the plan first.",
        ]),
        thread_id="gate1",
    )
    agent.send("build it and simulate it")
    result = _tool_result(agent, "simulate_experiment")
    assert result["ok"] is False
    assert "plan approval" in result["error"]
    assert REGISTRY.get().simulation is None


def test_approved_plan_simulates_and_binds_the_hash():
    agent = SERSExperimentAgent(
        ScriptedChatModel([
            [CREATE_CALL],
            [{"name": "approve_plan", "args": {}}],
            [{"name": "simulate_experiment", "args": {}}],
            "Simulation passed.",
        ]),
        thread_id="gate1ok",
    )
    result = agent.send("build it; yes, approved; simulate")
    simulation = _tool_result(agent, "simulate_experiment")
    assert simulation["ok"] is True
    assert simulation["simulation"]["status"] == "passed"
    session = REGISTRY.get()
    assert session.status is ExperimentStatus.SIMULATED
    assert session.hash_is_current()
    assert result["state"]["simulated_hash"] == session.resolved.resolved_hash


def test_graph_interrupts_before_any_robot_tool_runs():
    agent = SERSExperimentAgent(
        ScriptedChatModel([
            [CREATE_CALL],
            [{"name": "approve_plan", "args": {}}],
            [{"name": "simulate_experiment", "args": {}}],
            [{"name": "execute_experiment", "args": {}}],
            "Started.",
        ]),
        thread_id="gate2",
    )
    result = agent.send("build, approve, simulate, then run it")
    assert result["interrupted"] is True
    assert [call["name"] for call in result["pending_tools"]] == ["execute_experiment"]
    # Nothing was executed, and no run id exists.
    assert REGISTRY.get().robot_run_id is None


def test_declining_the_interrupt_leaves_the_robot_untouched():
    agent = SERSExperimentAgent(
        ScriptedChatModel([
            [CREATE_CALL],
            [{"name": "approve_plan", "args": {}}],
            [{"name": "simulate_experiment", "args": {}}],
            [{"name": "execute_experiment", "args": {}}],
            "Understood, I did not run it.",
        ]),
        thread_id="declined",
    )
    result = agent.send("build, approve, simulate, then run it")
    assert result["interrupted"]
    final = agent.refuse_pending_tool("operator declined")
    assert final["interrupted"] is False
    assert "REFUSED" in agent.tool_transcript()[-1]["content"]
    session = REGISTRY.get()
    assert session.robot_run_id is None
    assert session.status is ExperimentStatus.SIMULATED


def test_editing_after_simulation_is_reported_as_invalidated():
    agent = SERSExperimentAgent(
        ScriptedChatModel([
            [CREATE_CALL],
            [{"name": "approve_plan", "args": {}}],
            [{"name": "simulate_experiment", "args": {}}],
            "Simulation passed.",
            [{"name": "update_experiment", "args": {"update_steps": [
                {"step_id": "print_b", "drops_per_target": 5}
            ]}}],
            "That change invalidated the simulation.",
        ]),
        thread_id="invalidate",
    )
    agent.send("build, approve, simulate")
    simulated_hash = REGISTRY.get().simulated_hash
    assert simulated_hash

    after = agent.send("actually make the second condition five drops")
    update = _tool_result(agent, "update_experiment")
    assert "simulation" in update["invalidated"]
    session = REGISTRY.get()
    assert session.simulated_hash is None
    assert session.plan_approved is False
    assert after["state"]["resolved_hash"] != simulated_hash


def test_simulation_only_agent_has_no_robot_tools_at_all():
    agent = SERSExperimentAgent(
        ScriptedChatModel(["nothing to do"]), thread_id="sim-only", allow_robot_tools=False
    )
    bound = agent.graph.get_graph()
    assert "robot_tools" in bound.nodes  # the node exists but holds no tools
    agent.send("hello")
    # A simulation-only session can still not reach execution.
    assert REGISTRY.list_ids() == []
