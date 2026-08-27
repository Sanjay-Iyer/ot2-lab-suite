"""Narrow laboratory capabilities exposed to the conversational agent.

Every tool is deterministic and JSON-shaped.  The agent can describe, edit,
resolve, validate, simulate, and — behind explicit human approval and the
guarded execution layer — run an experiment.  It cannot run shell commands,
edit Python, or reach the robot by any other path.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from langchain_core.tools import tool

from .execution import execute_live, get_robot_run_status, preflight
from .intent import intent_as_dict
from .machine import load_machine_profile, profile_summary
from .schema import SCHEMA_VERSION, SERSConfigError, config_as_dict, validate_experiment_config
from .state import REGISTRY, ExperimentSession, ExperimentStatus
from .summary import render_compact, render_review_plan
from .templates import describe_templates, template_names, template_payload
from .targets import TargetSpecError, resolve_targets


def _state(session: ExperimentSession) -> dict[str, Any]:
    return session.snapshot().model_dump(mode="json")


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


# ---------------------------------------------------------------------------
# Describing the laboratory
# ---------------------------------------------------------------------------


@tool
def describe_machine_profile(profile_path: str | None = None) -> dict[str, Any]:
    """Show the approved labware and calibrated geometry for the robot.

    Use this before proposing a deck so you never invent a labware name, a
    pipette mount, or an aspiration height. These values are laboratory-owned
    and cannot be changed from an experiment.
    """
    try:
        summary = profile_summary(load_machine_profile(profile_path))
    except SERSConfigError as exc:
        return _error(str(exc))
    summary["note"] = (
        "approved_labware is for your information only. Never copy a load name "
        "into a deck entry: a deck entry's 'kind' is one of plate, vial_rack, "
        "paper, tiprack, and the profile turns that into the right labware."
    )
    return {"ok": True, **summary}


@tool
def expand_paper_targets(targets: list[str]) -> dict[str, Any]:
    """Preview which paper wells a concise target specification covers.

    Accepts forms like 'A1', 'A1:C1', 'A1:C3', 'column 1', 'columns 1 and 2',
    'rows A-C', or 'A1, B4, F7'. Use it to confirm a layout with the user
    before committing it to the experiment.
    """
    try:
        wells = resolve_targets(targets)
    except TargetSpecError as exc:
        return _error(str(exc))
    return {"ok": True, "count": len(wells), "wells": wells}


# ---------------------------------------------------------------------------
# Creating and editing the experiment
# ---------------------------------------------------------------------------


@tool
def list_sers_templates() -> dict[str, Any]:
    """List the canonical starting patterns for a SERS experiment.

    Templates are optional. Each one is an ordinary experiment you then patch,
    not a separate kind of config. Use this to pick the simplest pattern that
    matches what the user described, then start from it and edit it.
    """
    return {"ok": True, "templates": describe_templates()}


@tool
def start_experiment_from_template(
    template: str,
    experiment_name: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Start a new experiment from one of the canonical templates.

    template must be one of: "dilution" (prepare conditions, no printing),
    "printing" (deposit a liquid onto paper, no dilution), or "workflow" (any
    ordered mix of dilution, print and wait - the right default when the
    experiment involves more than one kind of step).

    This gives you a valid starting experiment with sensible defaults. It is a
    scaffold, NOT the finished experiment: immediately follow it with
    update_experiment to match what the user actually asked for - their liquids,
    wells, dilution factors, targets, drop counts and timings - and add, remove
    or reorder steps as needed. Never present an unedited template as the plan.

    Use create_experiment instead when the request does not resemble any
    template.
    """
    try:
        payload = template_payload(
            template, experiment_name=experiment_name, description=description
        )
        session = ExperimentSession.create(payload)
    except SERSConfigError as exc:
        return _error(str(exc), approved_templates=template_names())
    REGISTRY.add(session)
    report = session.resolve_and_validate()
    result: dict[str, Any] = {
        "ok": report.ok,
        "started_from_template": template,
        "next": (
            "patch this scaffold with update_experiment so it matches the user's "
            "request, then show them the full plan"
        ),
        "state": _state(session),
        "validation": report.model_dump(mode="json"),
    }
    if session.resolved is not None:
        result["plan"] = render_compact(session.resolved)
    return result


@tool
def create_experiment(
    experiment_name: str,
    deck: list[dict],
    liquids: list[dict],
    steps: list[dict],
    description: str | None = None,
) -> dict[str, Any]:
    """Create a new experiment and immediately resolve and validate it.

    deck: [{"role": "vial_rack", "kind": "vial_rack"|"plate"|"paper"|"tiprack",
            "slot": 1-11}, ...]. Role names are yours to choose; the machine
    profile decides which physical labware each kind means.

    liquids: [{"name": "nanoparticles", "labware": "<deck role>", "well": "A1",
               "loaded_volume_ul": 5000, "minimum_remaining_volume_ul": 2500}, ...]

    steps run in the order given, and are one of:
      {"step_type": "dilution", "step_id": "np_30x", "label": "NP 30x",
       "source": "<liquid name>", "diluent": "<liquid name>",
       "destination": "<plate role>:A1",
       "dilution_factor": 30, "final_volume_ul": 150}
         - prefer dilution_factor + final_volume_ul and let the resolver do the
           arithmetic; stock_volume_ul + diluent_volume_ul is also accepted.
      {"step_type": "print", "step_id": "print_np_30x",
       "source": "<liquid name or an earlier dilution step_id>",
       "paper": "<paper role, optional if there is only one>",
       "targets": ["A1:C1"], "drop_volume_ul": 5, "drops_per_target": 3,
       "tip_strategy": "per_layer"|"per_paper"|"per_target"}
      {"step_type": "wait", "step_id": "dry", "duration_s": 1800,
       "reason": "dry the nanoparticle spots"}

    Returns the validation result and a compact plan. Show the user the full
    plan from summarize_experiment before asking them to approve it.
    """
    payload = {
        "experiment_name": experiment_name,
        "description": description,
        "deck": deepcopy(deck),
        "liquids": deepcopy(liquids),
        "steps": deepcopy(steps),
    }
    try:
        session = ExperimentSession.create(payload)
    except SERSConfigError as exc:
        return _error(str(exc))
    REGISTRY.add(session)
    report = session.resolve_and_validate()
    result: dict[str, Any] = {
        "ok": report.ok,
        "state": _state(session),
        "validation": report.model_dump(mode="json"),
    }
    if session.resolved is not None:
        result["plan"] = render_compact(session.resolved)
    return result


@tool
def update_experiment(
    update_steps: list[dict] | None = None,
    add_steps: list[dict] | None = None,
    remove_steps: list[str] | None = None,
    reorder_steps: list[str] | None = None,
    set_liquids: list[dict] | None = None,
    remove_liquids: list[str] | None = None,
    set_deck: list[dict] | None = None,
    experiment_name: str | None = None,
    description: str | None = None,
    tips: dict | None = None,
) -> dict[str, Any]:
    """Patch the current experiment in place, keeping everything else intact.

    This is how every revision is made. Change only what the user asked for.

    update_steps: [{"step_id": "np_30x", "dilution_factor": 50}, ...] - merges
      the named fields into the existing step and preserves its id and position.
      Works for any field: dilution_factor, final_volume_ul, targets,
      drops_per_target, drop_volume_ul, tip_strategy, duration_s, destination.
    add_steps: same shape as create_experiment steps, plus an optional
      "insert_after": "<step_id>".
    remove_steps / reorder_steps: by step_id.
    set_liquids: merge by "name", e.g.
      [{"name": "nanoparticles", "labware": "vial_rack", "well": "A1",
        "loaded_volume_ul": 5000, "minimum_remaining_volume_ul": 2500}]
    set_deck: merge by "role", e.g.
      [{"role": "working_plate", "kind": "plate", "slot": 1}]
      "kind" is exactly one of plate, vial_rack, paper, tiprack - never a
      labware load name. The machine profile decides the physical labware.

    Any change invalidates the resolved plan, the simulation, and both
    approvals, and produces a new configuration hash. That is intentional: a
    simulation may only ever authorize the configuration that produced it.
    """
    try:
        session = REGISTRY.get()
    except SERSConfigError as exc:
        return _error(str(exc))

    patch = {
        "update_steps": update_steps,
        "add_steps": add_steps,
        "remove_steps": remove_steps,
        "reorder_steps": reorder_steps,
        "set_liquids": set_liquids,
        "remove_liquids": remove_liquids,
        "set_deck": set_deck,
        "experiment_name": experiment_name,
        "description": description,
        "tips": tips,
    }
    try:
        changes = session.apply_patch(patch)
    except SERSConfigError as exc:
        return _error(str(exc), state=_state(session))
    if not changes:
        return {"ok": True, "changes": [], "note": "nothing changed", "state": _state(session)}

    report = session.resolve_and_validate()
    result: dict[str, Any] = {
        "ok": report.ok,
        "changes": changes,
        "invalidated": ["resolved plan", "simulation", "plan approval", "live approval"],
        "state": _state(session),
        "validation": report.model_dump(mode="json"),
    }
    if session.resolved is not None:
        result["plan"] = render_compact(session.resolved)
    return result


# ---------------------------------------------------------------------------
# Resolving, validating, reviewing
# ---------------------------------------------------------------------------


@tool
def validate_experiment() -> dict[str, Any]:
    """Re-resolve and validate the current experiment without simulating it.

    Runs the dilution arithmetic, target expansion, P20 limits, volume ledger,
    aspiration liquid-depth check, and tip availability.
    """
    try:
        session = REGISTRY.get()
    except SERSConfigError as exc:
        return _error(str(exc))
    report = session.resolve_and_validate()
    return {
        "ok": report.ok,
        "state": _state(session),
        "validation": report.model_dump(mode="json"),
    }


@tool
def summarize_experiment(compact: bool = False) -> dict[str, Any]:
    """Return the full human-readable plan the user reviews before approving.

    Show this to the user verbatim. Do not show them generated protocol code.
    """
    try:
        session = REGISTRY.get()
    except SERSConfigError as exc:
        return _error(str(exc))
    if session.resolved is None:
        session.resolve_and_validate()
    if session.resolved is None:
        return _error(
            "the experiment does not resolve yet",
            validation=session.validation.model_dump(mode="json") if session.validation else None,
        )
    plan = session.resolved
    return {
        "ok": True,
        "state": _state(session),
        "plan": render_compact(plan) if compact else render_review_plan(plan),
        "totals": plan.totals.model_dump(mode="json"),
    }


@tool
def approve_plan() -> dict[str, Any]:
    """Record the user's approval of the resolved plan (gate 1 of 2).

    Only call this after the user has seen the full plan and said yes. It
    authorizes simulation, never physical motion.
    """
    try:
        session = REGISTRY.get()
        session.approve_plan()
    except SERSConfigError as exc:
        return _error(str(exc))
    session.write_snapshot("validated")
    return {"ok": True, "state": _state(session)}


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


@tool
def simulate_experiment() -> dict[str, Any]:
    """Run the deterministic OT-2 simulation of the approved plan.

    Requires plan approval first. On success the simulation is bound to the
    current resolved hash; any later edit invalidates it.
    """
    try:
        session = REGISTRY.get()
        report = session.simulate()
    except SERSConfigError as exc:
        return _error(str(exc))
    session.write_snapshot("simulated" if report.passed else "simulation_failed")
    payload = report.model_dump(mode="json")
    payload.pop("tip_log", None)
    return {"ok": report.passed, "state": _state(session), "simulation": payload}


@tool
def get_simulation_report() -> dict[str, Any]:
    """Return the stored simulation report and whether it still applies."""
    try:
        session = REGISTRY.get()
    except SERSConfigError as exc:
        return _error(str(exc))
    if session.simulation is None:
        return _error("this experiment has not been simulated", state=_state(session))
    return {
        "ok": True,
        "state": _state(session),
        "still_valid": session.hash_is_current(),
        "simulation": session.simulation.model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# Physical execution
# ---------------------------------------------------------------------------


@tool
def prepare_live_execution() -> dict[str, Any]:
    """Check every physical-execution gate and report what still blocks a run.

    Moves nothing. Show the result to the user before asking for the final
    live-run approval.
    """
    try:
        session = REGISTRY.get()
    except SERSConfigError as exc:
        return _error(str(exc))
    report = preflight(session)
    return {"ok": report.ready, "state": _state(session), "preflight": report.model_dump(mode="json")}


@tool
def approve_live_execution(confirmation: str) -> dict[str, Any]:
    """Record the user's explicit approval to run on the physical OT-2 (gate 2).

    Only call this when the user has unambiguously said to run it on the real
    robot after seeing a passing simulation. Pass their own words as
    confirmation. General conversational agreement is not authorization.
    """
    try:
        session = REGISTRY.get()
        session.approve_live_execution(confirmation)
    except SERSConfigError as exc:
        return _error(str(exc))
    return {"ok": True, "state": _state(session), "confirmation": confirmation}


@tool
def execute_experiment(robot_host: str | None = None) -> dict[str, Any]:
    """Run the approved, simulated workflow on the physical OT-2. Moves the robot.

    Refuses unless every gate in prepare_live_execution passes, including the
    hash binding between the simulation and the current configuration.
    """
    try:
        session = REGISTRY.get()
        result = execute_live(session, robot_host=robot_host, wait_for_completion=False)
    except (SERSConfigError, RuntimeError) as exc:
        return _error(str(exc))
    return {"ok": True, "state": _state(session), "run": result}


@tool
def get_robot_run(run_id: str | None = None, robot_host: str | None = None) -> dict[str, Any]:
    """Poll the status of a physical robot run."""
    try:
        session = REGISTRY.get()
        identifier = run_id or session.robot_run_id
        if not identifier:
            return _error("no robot run has been started from this experiment")
        status = get_robot_run_status(identifier, robot_host)
    except (SERSConfigError, RuntimeError) as exc:
        return _error(str(exc))
    session.robot_run_status = status.get("status")
    # Close out this run's provenance record once the robot reaches a terminal
    # state, so the outcome of the physical run is on file and not just on screen.
    recorder = getattr(session, "provenance", None)
    record = getattr(session, "robot_run_record", None)
    if recorder is not None and record is not None and record.finished_at is None:
        if str(status.get("status")) in {"succeeded", "failed", "stopped"}:
            recorder.finish_robot_run(
                record, str(status["status"]), errors=status.get("errors"), run_log=status
            )
            recorder.write_manifest()
    return {"ok": True, "state": _state(session), "run": status}


# Config-stage tools are safe anywhere. Robot tools are separated so a
# simulation-only session can be built without them.
CONFIG_TOOLS = [
    describe_machine_profile,
    expand_paper_targets,
    list_sers_templates,
    start_experiment_from_template,
    create_experiment,
    update_experiment,
    validate_experiment,
    summarize_experiment,
    approve_plan,
    simulate_experiment,
    get_simulation_report,
]

ROBOT_TOOLS = [
    prepare_live_execution,
    approve_live_execution,
    execute_experiment,
    get_robot_run,
]

ALL_TOOLS = CONFIG_TOOLS + ROBOT_TOOLS


# ---------------------------------------------------------------------------
# Legacy direct-config interface
#
# These predate the intent/resolver split and operate on a hand-written
# ExperimentConfig. They are kept so configs/sers_cv_titration_demo.yaml and its
# tests keep working without the agent layer.
# ---------------------------------------------------------------------------


def _numpy_compatibility_shim() -> None:
    import numpy as np

    if not hasattr(np, "trapz") and hasattr(np, "trapezoid"):
        np.trapz = np.trapezoid  # type: ignore[attr-defined]


def _config_hash(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def generate_experiment_config(
    experiment_name: str,
    dilutions: list[dict[str, Any]],
    print_layers: list[dict[str, Any]],
    workflow: list[dict[str, str]] | None = None,
    deck_layout: dict[str, Any] | None = None,
    pipette: dict[str, Any] | None = None,
    tips: dict[str, Any] | None = None,
    description: str | None = None,
    waits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Legacy: build a raw ExperimentConfig without the intent layer."""
    if deck_layout is None or pipette is None:
        raise SERSConfigError(
            "generate_experiment_config needs an explicit deck_layout and pipette; "
            "prefer create_experiment, which takes them from the machine profile"
        )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "experiment_name": experiment_name,
        "description": description,
        "robot_type": "OT-2",
        "api_level": "2.15",
        "deck_layout": deepcopy(deck_layout),
        "pipette": deepcopy(pipette),
        "tips": deepcopy(tips or {"start_tip": "A1", "return_tips": False}),
        "dilutions": deepcopy(dilutions),
        "print_layers": deepcopy(print_layers),
        "waits": deepcopy(waits or []),
        "workflow": deepcopy(workflow or []),
        "safety": {
            "live_execution_allowed": False,
            "hardware_profile_verified": False,
            "required_laptop_role": "real-robot",
        },
    }
    return config_as_dict(validate_experiment_config(payload))


def execute_sers_workflow(config_dict: dict[str, Any], live: bool = False) -> dict[str, Any]:
    """Legacy: validate and simulate a raw ExperimentConfig. Never runs live."""
    from .orchestrator import run_unified_protocol

    try:
        config = validate_experiment_config(config_dict)
        normalized = config_as_dict(config)
    except Exception as exc:
        return {
            "status": "error",
            "mode": "live" if live else "simulate",
            "errors": [f"{type(exc).__name__}: {exc}"],
        }

    digest = _config_hash(normalized)
    if live:
        return {
            "status": "rejected",
            "mode": "live",
            "config_hash": digest,
            "errors": [
                "Live robot motion is not available through this legacy tool. Use the "
                "agent's guarded execution path, or scripts/run_sers_experiment.py "
                "--live from the verified real-robot execution host."
            ],
        }

    try:
        _numpy_compatibility_shim()
        from opentrons.simulate import get_protocol_api

        protocol = get_protocol_api(
            config.api_level, robot_type=config.robot_type, use_virtual_hardware=True
        )
        summary = run_unified_protocol(protocol, config)
        return {
            "status": "completed",
            "mode": "simulate",
            "config_hash": digest,
            "command_count": len(protocol.commands()),
            **summary,
        }
    except Exception as exc:
        return {
            "status": "error",
            "mode": "simulate",
            "config_hash": digest,
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
