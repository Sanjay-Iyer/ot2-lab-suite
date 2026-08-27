"""Guarded physical execution on the OT-2.

Every gate here is independent, and the agent has no path around any of them.
The binding that matters most: a run is only ever started for the exact resolved
hash that a passing simulation produced.

Transport is the repository's standard HTTP API runner pattern (port 31950,
upload -> create run -> play -> poll), the same one used by
``scripts/run_vial_print_robot.py``.  No SSH is involved.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .emitter import write_protocol
from .schema import SERSConfigError

HEADERS = {"opentrons-version": "*"}
TERMINAL_STATUSES = {"succeeded", "failed", "stopped"}

# Each of these must be set on the real robot laptop before live motion.
ENV_LAPTOP_ROLE = "OT2_LAPTOP_ROLE"
ENV_ROBOT_READY = "OT2_ROBOT_READY"
ENV_PLATE_CONFIRMED = "SERS_PLATE_ASPIRATE_CONFIRMED"
REQUIRED_CONDA_ENV = "llm"


class ExecutionGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    detail: str


class PreflightReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "blocked"]
    experiment_id: str
    resolved_hash: str | None = None
    simulated_hash: str | None = None
    gates: list[ExecutionGate] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)
    hardware_confirmations: list[str] = Field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.status == "ready"


def _gate(gates: list[ExecutionGate], name: str, passed: bool, detail: str) -> None:
    gates.append(ExecutionGate(name=name, passed=passed, detail=detail))


def preflight(session: Any, robot_host: str | None = None) -> PreflightReport:
    """Check every live-execution gate without moving anything."""
    gates: list[ExecutionGate] = []
    plan = session.resolved

    _gate(
        gates,
        "resolved plan exists",
        plan is not None,
        "the experiment resolves to a concrete workflow"
        if plan is not None
        else "no resolved workflow; resolve and validate first",
    )
    simulation = session.simulation
    _gate(
        gates,
        "simulation passed",
        bool(simulation and simulation.passed),
        f"simulation {simulation.status}" if simulation else "the workflow was never simulated",
    )
    _gate(
        gates,
        "hash binding",
        session.hash_is_current(),
        "the simulated configuration is the current configuration"
        if session.hash_is_current()
        else "the experiment changed after simulation; re-simulate before running",
    )
    _gate(
        gates,
        "human live approval",
        bool(session.live_execution_approved),
        "an explicit live-run approval is on file"
        if session.live_execution_approved
        else "no explicit live-run approval",
    )

    role = os.environ.get(ENV_LAPTOP_ROLE, "")
    _gate(
        gates,
        "real-robot laptop",
        role == "real-robot",
        f"{ENV_LAPTOP_ROLE}={role or '(unset)'}; must be 'real-robot'",
    )
    conda = os.environ.get("CONDA_DEFAULT_ENV", "")
    _gate(
        gates,
        "conda environment",
        conda == REQUIRED_CONDA_ENV,
        f"CONDA_DEFAULT_ENV={conda or '(unset)'}; must be '{REQUIRED_CONDA_ENV}'",
    )
    ready = os.environ.get(ENV_ROBOT_READY, "").lower()
    _gate(
        gates,
        "deck physically checked",
        ready == "confirmed",
        f"{ENV_ROBOT_READY}={ready or '(unset)'}; set it to 'confirmed' after checking "
        "deck, tips, liquids, and clearances",
    )

    # The machine profile records that the BRAND plate's 0.2 mm aspirate height
    # was inherited from the Corning well and never physically re-validated.
    # That is a hardware fact this code cannot establish, so it is surfaced as a
    # confirmation the operator must make rather than a value anything invents.
    confirmations: list[str] = []
    uses_plate = bool(plan) and any(
        "working" in role or spec.get("kind") == "plate"
        for role, spec in plan.execution_config["deck_layout"]["labware"].items()
    )
    if uses_plate:
        plate_ok = os.environ.get(ENV_PLATE_CONFIRMED, "").lower() == "confirmed"
        _gate(
            gates,
            "plate aspirate height confirmed",
            plate_ok,
            f"{ENV_PLATE_CONFIRMED}={os.environ.get(ENV_PLATE_CONFIRMED) or '(unset)'}; "
            "the BRAND 781662 0.2 mm aspirate height is inherited from the Corning "
            "plate and has never been physically re-validated. Run Labware Position "
            "Check, then set this to 'confirmed'.",
        )
        if not plate_ok:
            confirmations.append(
                "BRAND 781662 aspirate height (0.2 mm) needs Labware Position Check "
                "before it is trusted on real hardware"
            )

    reachable, detail = _robot_reachable(robot_host)
    _gate(gates, "robot reachable", reachable, detail)

    # An unlogged run cannot be reconstructed for a paper, an audit, or a
    # repeat, so a run that could not be recorded is refused rather than run.
    # This observes the record only; every gate above is unchanged.
    from .provenance import live_execution_readiness

    logged, log_detail = live_execution_readiness(session)
    _gate(gates, "provenance record complete", logged, log_detail)

    blocking = [gate.name for gate in gates if not gate.passed]
    return PreflightReport(
        status="blocked" if blocking else "ready",
        experiment_id=session.experiment.experiment_id,
        resolved_hash=plan.resolved_hash if plan else None,
        simulated_hash=session.simulated_hash,
        gates=gates,
        blocking=blocking,
        hardware_confirmations=confirmations,
    )


def _robot_reachable(robot_host: str | None) -> tuple[bool, str]:
    try:
        from src.lab.robot_connection import health, resolve_host

        host = robot_host or resolve_host(None)
        payload = health(host)
        name = payload.get("name") or payload.get("robot_model") or "OT-2"
        return True, f"{name} responding at {host}"
    except Exception as exc:
        return False, f"no robot on the network: {type(exc).__name__}: {exc}"


def _request(method: str, host: str, path: str, **kwargs: Any) -> dict[str, Any]:
    import requests

    from src.lab.robot_connection import base_url

    response = requests.request(
        method, f"{base_url(host)}{path}", headers=HEADERS, timeout=30, **kwargs
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    if response.status_code >= 400:
        raise RuntimeError(
            f"{method} {path} failed with HTTP {response.status_code}:\n"
            f"{json.dumps(payload, indent=2)}"
        )
    return payload


def execute_live(
    session: Any,
    robot_host: str | None = None,
    poll_seconds: float = 5.0,
    wait_for_completion: bool = True,
) -> dict[str, Any]:
    """Upload and play the exact simulated workflow. This moves the robot."""
    report = preflight(session, robot_host)
    if not report.ready:
        raise SERSConfigError(
            "live execution refused; unmet gates: " + ", ".join(report.blocking)
        )

    import requests

    from src.lab.robot_connection import base_url, resolve_host

    host = robot_host or resolve_host(None)
    plan = session.resolved
    directory = session.write_snapshot("live")
    protocol_path = write_protocol(plan, directory)

    with protocol_path.open("rb") as handle:
        response = requests.post(
            f"{base_url(host)}/protocols",
            headers=HEADERS,
            files={"files": (protocol_path.name, handle, "text/x-python")},
            timeout=120,
        )
    payload = response.json() if response.content else {}
    if response.status_code >= 400:
        raise RuntimeError(
            f"protocol upload failed with HTTP {response.status_code}:\n"
            f"{json.dumps(payload, indent=2)}"
        )
    protocol_id = payload.get("data", {}).get("id")
    if not protocol_id:
        raise RuntimeError(f"upload response missing data.id:\n{json.dumps(payload, indent=2)}")

    run_payload = _request("POST", host, "/runs", json={"data": {"protocolId": protocol_id}})
    run_id = run_payload.get("data", {}).get("id")
    if not run_id:
        raise RuntimeError(f"create-run response missing data.id:\n{json.dumps(run_payload, indent=2)}")

    session.robot_run_id = run_id
    session.robot_run_status = "queued"
    session.status = type(session.status).RUNNING

    _request("POST", host, f"/runs/{run_id}/actions", json={"data": {"actionType": "play"}})
    session.robot_run_status = "running"

    # Each physical execution gets its own record. A replicate of the same
    # approved experiment never overwrites the run before it.
    recorder = getattr(session, "provenance", None)
    run_record = None
    if recorder is not None:
        run_record = recorder.start_robot_run(
            session,
            robot_host=host,
            robot_run_id=run_id,
            robot_protocol_id=protocol_id,
            protocol_path=protocol_path,
            approval_text=_approval_text(session),
        )

    result: dict[str, Any] = {
        "status": "running",
        "robot_host": host,
        "protocol_id": protocol_id,
        "robot_run_id": run_id,
        "resolved_hash": plan.resolved_hash,
        "protocol_file": str(protocol_path),
        "snapshot_dir": str(directory),
    }
    if run_record is not None:
        result["provenance_run_record"] = str(
            recorder.directory / "robot_runs" / f"run_{run_record.run_index:03d}.json"
        )
        session.robot_run_record = run_record
    if not wait_for_completion:
        return result

    final = _monitor(host, run_id, poll_seconds)
    session.robot_run_status = final
    session.status = (
        type(session.status).COMPLETE if final == "succeeded" else type(session.status).FAILED
    )
    result["status"] = final
    (Path(directory) / "robot_run.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if recorder is not None and run_record is not None:
        details = get_robot_run_status(run_id, host)
        recorder.finish_robot_run(
            run_record, final, errors=details.get("errors"), run_log=details
        )
        recorder.write_manifest()
    return result


def _approval_text(session: Any) -> str | None:
    """The operator's own words authorizing this run, verbatim."""
    return getattr(session, "live_approval_text", None)


def _monitor(host: str, run_id: str, poll_seconds: float) -> str:
    while True:
        data = _request("GET", host, f"/runs/{run_id}").get("data", {})
        status = str(data.get("status", "unknown"))
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(poll_seconds)


def get_robot_run_status(run_id: str, robot_host: str | None = None) -> dict[str, Any]:
    """Poll one run without starting anything."""
    from src.lab.robot_connection import resolve_host

    host = robot_host or resolve_host(None)
    data = _request("GET", host, f"/runs/{run_id}").get("data", {})
    return {
        "robot_run_id": run_id,
        "status": data.get("status"),
        "current_command": (data.get("current") or {}).get("commandId"),
        "errors": data.get("errors", []),
    }
