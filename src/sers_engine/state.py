"""Stateful experiment session: patch, resolve, validate, approve, snapshot.

The single rule this module exists to enforce: **any edit that can change what
the robot does invalidates every downstream approval.**  A simulation result may
only ever authorize the exact configuration that produced it.
"""

from __future__ import annotations

import json
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .intent import (
    DilutionStep,
    PrintStep,
    SERSExperimentV1,
    WaitStep,
    intent_as_dict,
    validate_intent,
)
from .provenance.models import now_iso
from .resolver import ResolvedWorkflowV1
from .schema import REPO_ROOT, SERSConfigError
from .simulation import SimulationReport, simulate_resolved
from .validator import ValidationReport, validate_experiment

SNAPSHOT_ROOT = REPO_ROOT / "runs" / "sers"

_STEP_TYPES = {"dilution": DilutionStep, "print": PrintStep, "wait": WaitStep}


class ExperimentStatus(str, Enum):
    """Where the current experiment sits between conversation and robot motion."""

    DRAFT = "DRAFT"
    INVALID = "INVALID"
    VALIDATED = "VALIDATED"
    APPROVED_FOR_SIMULATION = "APPROVED_FOR_SIMULATION"
    SIMULATED = "SIMULATED"
    SIMULATION_FAILED = "SIMULATION_FAILED"
    APPROVED_FOR_LIVE = "APPROVED_FOR_LIVE"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class SessionSnapshot(BaseModel):
    """Everything a caller needs to see the session without touching internals."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    experiment_name: str
    status: ExperimentStatus
    revision: int
    config_hash: str | None = None
    resolved_hash: str | None = None
    simulated_hash: str | None = None
    plan_approved: bool = False
    live_execution_approved: bool = False
    validation_status: str | None = None
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    simulation_status: str | None = None
    robot_run_id: str | None = None
    robot_run_status: str | None = None
    last_change: str | None = None
    snapshot_dir: str | None = None
    provenance_dir: str | None = None


class ExperimentSession:
    """One conversational experiment, with its approvals bound to its hash."""

    def __init__(self, experiment: SERSExperimentV1) -> None:
        # The provenance recorder observes this session; it never changes it.
        # Binding it here means both the agent path and the manual runner are
        # recorded without either of them having to remember to ask.
        from .provenance import active_session

        self.provenance = active_session()
        self.experiment = experiment
        self.status = ExperimentStatus.DRAFT
        self.revision = 0
        self.resolved: ResolvedWorkflowV1 | None = None
        self.validation: ValidationReport | None = None
        self.simulation: SimulationReport | None = None
        self.simulated_hash: str | None = None
        self.plan_approved = False
        self.live_execution_approved = False
        self.robot_run_id: str | None = None
        self.robot_run_status: str | None = None
        # The open provenance record for the physical run in progress, if any.
        self.robot_run_record: Any | None = None
        # The operator's own words authorizing physical motion, kept verbatim.
        self.live_approval_text: str | None = None
        self.last_change: str | None = "created"
        self.history: list[str] = []
        self.snapshot_dir: Path | None = None

    # ---- construction ------------------------------------------------------
    @classmethod
    def create(cls, payload: dict[str, Any]) -> "ExperimentSession":
        payload = dict(payload)
        payload.setdefault("experiment_id", f"exp_{uuid.uuid4().hex[:8]}")
        return cls(validate_intent(payload))

    # ---- invalidation ------------------------------------------------------
    def _invalidate(self, reason: str) -> None:
        """Drop every downstream artifact. Called on any change to the experiment."""
        had_simulation = self.simulation is not None
        self.resolved = None
        self.validation = None
        self.simulation = None
        self.simulated_hash = None
        self.plan_approved = False
        self.live_execution_approved = False
        self.robot_run_id = None
        self.robot_run_status = None
        self.robot_run_record = None
        self.live_approval_text = None
        self.status = ExperimentStatus.DRAFT
        self.revision += 1
        self.last_change = reason
        self.history.append(f"rev {self.revision}: {reason}")
        if self.provenance is not None:
            self.provenance.record_invalidation(self, reason, had_simulation)

    # ---- editing -----------------------------------------------------------
    def apply_patch(self, patch: dict[str, Any]) -> list[str]:
        """Merge a structured patch into the experiment, preserving step ids.

        Returns the list of changes made.  Raises :class:`SERSConfigError` and
        leaves the session untouched if the result would be incoherent.
        """
        payload = intent_as_dict(self.experiment)
        changes: list[str] = []

        for field in ("experiment_name", "description", "machine_profile"):
            if field in patch and patch[field] is not None:
                if payload.get(field) != patch[field]:
                    payload[field] = patch[field]
                    changes.append(f"{field} -> {patch[field]!r}")

        if patch.get("tips"):
            payload["tips"] = {**payload.get("tips", {}), **patch["tips"]}
            changes.append(f"tips -> {patch['tips']}")

        for assignment in patch.get("set_deck", []) or []:
            if not isinstance(assignment, dict) or "role" not in assignment:
                raise SERSConfigError(
                    "each set_deck entry needs a 'role', e.g. "
                    '{"role": "working_plate", "kind": "plate", "slot": 1}; got '
                    f"{assignment!r}"
                )
            role = assignment["role"]
            existing = next((item for item in payload["deck"] if item["role"] == role), None)
            if existing is None:
                payload["deck"].append(assignment)
                changes.append(f"deck +{role} (slot {assignment.get('slot')})")
            else:
                before = dict(existing)
                existing.update(assignment)
                if before != existing:
                    changes.append(f"deck {role}: {before['slot']} -> {existing['slot']}")

        for role in patch.get("remove_deck", []) or []:
            payload["deck"] = [item for item in payload["deck"] if item["role"] != role]
            changes.append(f"deck -{role}")

        for liquid in patch.get("set_liquids", []) or []:
            if not isinstance(liquid, dict) or "name" not in liquid:
                raise SERSConfigError(
                    "each set_liquids entry needs a 'name', e.g. "
                    '{"name": "water", "labware": "vial_rack", "well": "A2", '
                    '"loaded_volume_ul": 15000}; got '
                    f"{liquid!r}"
                )
            name = liquid["name"]
            existing = next((item for item in payload["liquids"] if item["name"] == name), None)
            if existing is None:
                payload["liquids"].append(liquid)
                changes.append(f"liquid +{name}")
            else:
                before = dict(existing)
                existing.update(liquid)
                if before != existing:
                    changes.append(f"liquid {name} updated")

        for name in patch.get("remove_liquids", []) or []:
            payload["liquids"] = [item for item in payload["liquids"] if item["name"] != name]
            changes.append(f"liquid -{name}")

        for update in patch.get("update_steps", []) or []:
            if not isinstance(update, dict) or not update.get("step_id"):
                raise SERSConfigError(
                    "each update_steps entry needs a 'step_id' naming the step to "
                    f"change; got {update!r}"
                )
            step_id = update.get("step_id")
            existing = next(
                (item for item in payload["steps"] if item["step_id"] == step_id), None
            )
            if existing is None:
                raise SERSConfigError(
                    f"cannot update unknown step {step_id!r}; existing steps are "
                    f"{[item['step_id'] for item in payload['steps']]}"
                )
            before = dict(existing)
            fields = {key: value for key, value in update.items() if key != "step_id"}
            # Switching a dilution between a scientific target and explicit
            # volumes must not leave both halves set. Both tests read the
            # caller's original keys, because clearing one half would otherwise
            # look like a request to clear the other.
            requested = set(fields)
            if {"dilution_factor", "final_volume_ul"} & requested:
                fields.setdefault("stock_volume_ul", None)
                fields.setdefault("diluent_volume_ul", None)
            if {"stock_volume_ul", "diluent_volume_ul"} & requested:
                fields.setdefault("dilution_factor", None)
                fields.setdefault("final_volume_ul", None)
            existing.update(fields)
            if before != existing:
                touched = ", ".join(
                    f"{key}={value!r}" for key, value in fields.items() if before.get(key) != value
                )
                changes.append(f"step {step_id}: {touched or 'updated'}")

        for step in patch.get("add_steps", []) or []:
            step = dict(step)
            step.setdefault("step_id", f"step_{uuid.uuid4().hex[:6]}")
            index = step.pop("insert_after", None)
            if index is None:
                payload["steps"].append(step)
            else:
                position = next(
                    (
                        offset + 1
                        for offset, item in enumerate(payload["steps"])
                        if item["step_id"] == index
                    ),
                    len(payload["steps"]),
                )
                payload["steps"].insert(position, step)
            changes.append(f"step +{step['step_id']} ({step.get('step_type')})")

        for step_id in patch.get("remove_steps", []) or []:
            payload["steps"] = [item for item in payload["steps"] if item["step_id"] != step_id]
            changes.append(f"step -{step_id}")

        order = patch.get("reorder_steps")
        if order:
            by_id = {item["step_id"]: item for item in payload["steps"]}
            missing = [step_id for step_id in order if step_id not in by_id]
            if missing:
                raise SERSConfigError(f"reorder_steps names unknown steps {missing}")
            remainder = [item for item in payload["steps"] if item["step_id"] not in set(order)]
            payload["steps"] = [by_id[step_id] for step_id in order] + remainder
            changes.append(f"reordered -> {order}")

        if not changes:
            return []

        # Validate before committing so a rejected patch cannot corrupt the session.
        updated = validate_intent(payload)
        self.experiment = updated
        self._invalidate("; ".join(changes))
        return changes

    # ---- pipeline ----------------------------------------------------------
    def resolve_and_validate(self) -> ValidationReport:
        report, plan = validate_experiment(self.experiment)
        self.validation = report
        self.resolved = plan
        self.status = ExperimentStatus.VALIDATED if report.ok else ExperimentStatus.INVALID
        if self.provenance is not None:
            # Every revision is snapshotted here, the one place where the
            # experiment, its resolution and its validation all exist at once.
            self.provenance.record_revision(self, report, plan)
        return report

    def approve_plan(self) -> None:
        if self.status != ExperimentStatus.VALIDATED or self.resolved is None:
            raise SERSConfigError(
                f"the plan cannot be approved from status {self.status.value}; "
                "resolve and validate it first"
            )
        self.plan_approved = True
        self.status = ExperimentStatus.APPROVED_FOR_SIMULATION
        self.history.append(f"rev {self.revision}: plan approved")
        if self.provenance is not None:
            self.provenance.record_plan_approval(self)

    def simulate(self) -> SimulationReport:
        if self.resolved is None:
            raise SERSConfigError("nothing to simulate; resolve the experiment first")
        if not self.plan_approved:
            raise SERSConfigError(
                "simulation needs plan approval first (review the resolved workflow, "
                "then approve it)"
            )
        if self.provenance is not None:
            self.provenance.record_simulation_start(self)
        report = simulate_resolved(self.resolved)
        self.simulation = report
        if report.passed:
            self.simulated_hash = report.resolved_hash
            self.status = ExperimentStatus.SIMULATED
        else:
            self.simulated_hash = None
            self.status = ExperimentStatus.SIMULATION_FAILED
        self.history.append(f"rev {self.revision}: simulation {report.status}")
        if self.provenance is not None:
            self.provenance.record_simulation(self, report)
        return report

    def approve_live_execution(self, confirmation: str | None = None) -> None:
        if self.status != ExperimentStatus.SIMULATED or self.resolved is None:
            raise SERSConfigError(
                f"live execution cannot be approved from status {self.status.value}; "
                "a passing simulation of the current configuration is required"
            )
        if self.simulated_hash != self.resolved.resolved_hash:
            raise SERSConfigError(
                "the experiment changed after simulation; re-simulate before approving"
            )
        self.live_execution_approved = True
        self.status = ExperimentStatus.APPROVED_FOR_LIVE
        self.history.append(f"rev {self.revision}: live execution approved")
        if confirmation:
            self.live_approval_text = confirmation
            self.history.append(f"live approval quoted: {confirmation!r}")
        if self.provenance is not None:
            self.provenance.record_live_approval(self, confirmation)

    def hash_is_current(self) -> bool:
        """True when the simulation on file describes today's configuration."""
        return (
            self.resolved is not None
            and self.simulated_hash is not None
            and self.simulated_hash == self.resolved.resolved_hash
        )

    # ---- reporting ---------------------------------------------------------
    def snapshot(self) -> SessionSnapshot:
        return SessionSnapshot(
            experiment_id=self.experiment.experiment_id,
            experiment_name=self.experiment.experiment_name,
            status=self.status,
            revision=self.revision,
            config_hash=self.resolved.config_hash if self.resolved else None,
            resolved_hash=self.resolved.resolved_hash if self.resolved else None,
            simulated_hash=self.simulated_hash,
            plan_approved=self.plan_approved,
            live_execution_approved=self.live_execution_approved,
            validation_status=self.validation.status if self.validation else None,
            validation_errors=list(self.validation.errors) if self.validation else [],
            validation_warnings=list(self.validation.warnings) if self.validation else [],
            simulation_status=self.simulation.status if self.simulation else None,
            robot_run_id=self.robot_run_id,
            robot_run_status=self.robot_run_status,
            last_change=self.last_change,
            snapshot_dir=str(self.snapshot_dir) if self.snapshot_dir else None,
            provenance_dir=(
                str(self.provenance.directory) if self.provenance is not None else None
            ),
        )

    def write_snapshot(self, label: str) -> Path:
        """Persist a reproducible record of the session at one milestone."""
        directory = (
            SNAPSHOT_ROOT
            / self.experiment.experiment_id
            / f"rev{self.revision:02d}_{label}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "experiment.yaml").write_text(
            yaml.safe_dump(intent_as_dict(self.experiment), sort_keys=False),
            encoding="utf-8",
        )
        if self.resolved is not None:
            (directory / "resolved_workflow.json").write_text(
                json.dumps(self.resolved.model_dump(mode="json"), indent=2), encoding="utf-8"
            )
            (directory / "execution_config.yaml").write_text(
                yaml.safe_dump(self.resolved.execution_config, sort_keys=False),
                encoding="utf-8",
            )
        if self.validation is not None:
            (directory / "validation.json").write_text(
                json.dumps(self.validation.model_dump(mode="json"), indent=2), encoding="utf-8"
            )
        if self.simulation is not None:
            (directory / "simulation.json").write_text(
                json.dumps(self.simulation.model_dump(mode="json"), indent=2), encoding="utf-8"
            )
        (directory / "session.json").write_text(
            json.dumps(
                {
                    **self.snapshot().model_dump(mode="json"),
                    "label": label,
                    "timestamp": now_iso(),
                    "history": self.history,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self.snapshot_dir = directory
        return directory


class SessionRegistry:
    """Process-local store of conversational experiments, keyed by id."""

    def __init__(self) -> None:
        self._sessions: dict[str, ExperimentSession] = {}
        self._current: str | None = None

    def add(self, session: ExperimentSession) -> ExperimentSession:
        self._sessions[session.experiment.experiment_id] = session
        self._current = session.experiment.experiment_id
        return session

    def get(self, experiment_id: str | None = None) -> ExperimentSession:
        key = experiment_id or self._current
        if key is None or key not in self._sessions:
            raise SERSConfigError(
                "no current experiment; create one first with create_experiment"
            )
        return self._sessions[key]

    def current_id(self) -> str | None:
        return self._current

    def list_ids(self) -> list[str]:
        return list(self._sessions)

    def clear(self) -> None:
        self._sessions.clear()
        self._current = None


REGISTRY = SessionRegistry()
