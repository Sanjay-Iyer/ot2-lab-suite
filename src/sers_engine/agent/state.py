"""LangGraph state for one conversational experiment session."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """Conversation plus a mirror of the deterministic session state.

    The authoritative experiment lives in :data:`sers_engine.state.REGISTRY`;
    these fields are a read-only reflection so graph logic and the CLI can see
    where the workflow stands without reaching into the session object.
    """

    messages: Annotated[list, add_messages]

    experiment_id: str | None
    experiment_name: str | None
    status: str | None
    revision: int
    config_hash: str | None
    resolved_hash: str | None
    simulated_hash: str | None
    plan_approved: bool
    live_execution_approved: bool
    validation_status: str | None
    validation_errors: list[str]
    validation_warnings: list[str]
    simulation_status: str | None
    robot_run_id: str | None
    robot_run_status: str | None
    last_change: str | None
    snapshot_dir: str | None
    provenance_dir: str | None


def blank_state() -> dict[str, Any]:
    return {
        "experiment_id": None,
        "experiment_name": None,
        "status": None,
        "revision": 0,
        "config_hash": None,
        "resolved_hash": None,
        "simulated_hash": None,
        "plan_approved": False,
        "live_execution_approved": False,
        "validation_status": None,
        "validation_errors": [],
        "validation_warnings": [],
        "simulation_status": None,
        "robot_run_id": None,
        "robot_run_status": None,
        "last_change": None,
        "snapshot_dir": None,
        "provenance_dir": None,
    }
