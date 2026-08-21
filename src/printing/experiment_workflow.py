"""Deterministic Stage 4 approval, build, and simulation workflow.

No transition in this module contacts a robot.  The terminal state is
``READY_FOR_EXECUTION`` after local simulation of the exact approved artifact.
"""
from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .artifacts import (
    ARTIFACT_DIR,
    BuildArtifact,
    SimulationResult,
    build_prepared_artifact,
    prepare_printing_request,
    simulate_built_artifact,
)
from .canonical import canonical_sha256
from .config import REPO_ROOT
from .experiment_configs import (
    ExperimentConfigError,
    ExperimentConfigArtifactV1,
    ExperimentConfigPhysicalValidationError,
    ExperimentConfigReferenceError,
    ExperimentConfigSchemaError,
    ExperimentConfigSummaryV1,
    PrintingExperimentDraftV1,
    PrintingExperimentRevisionV1,
    create_printing_experiment_config,
    load_printing_experiment_config,
    revise_printing_experiment_config,
)
from .job_compiler import compile_print_job, printing_request_from_job
from .plans import resolved_plan_artifact_json
from .schemas import ResolvedPrintPlanV1, parse_resolved_print_plan_json


class WorkflowTransitionError(ValueError):
    """A lifecycle operation was attempted before its prerequisites."""


class ExperimentLifecycle(str, Enum):
    REQUEST_RECEIVED = "REQUEST_RECEIVED"
    CONFIG_DRAFTED = "CONFIG_DRAFTED"
    CONFIG_VALIDATED = "CONFIG_VALIDATED"
    CONFIG_INVALID = "CONFIG_INVALID"
    PLAN_PRESENTED = "PLAN_PRESENTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    USER_REQUESTED_CHANGES = "USER_REQUESTED_CHANGES"
    APPROVED = "APPROVED"
    PLAN_REJECTED = "PLAN_REJECTED"
    RESOLVED = "RESOLVED"
    PROTOCOL_BUILT = "PROTOCOL_BUILT"
    SIMULATED = "SIMULATED"
    SIMULATION_FAILED = "SIMULATION_FAILED"
    READY_FOR_EXECUTION = "READY_FOR_EXECUTION"


class WorkflowErrorStage(str, Enum):
    CONFIG_SCHEMA = "config_schema_validation"
    REFERENCE = "reference_resolution"
    PHYSICAL = "physical_plan_validation"
    SIMULATION = "simulation"


_ALLOWED_TRANSITIONS: dict[ExperimentLifecycle, frozenset[ExperimentLifecycle]] = {
    ExperimentLifecycle.REQUEST_RECEIVED: frozenset({ExperimentLifecycle.CONFIG_DRAFTED}),
    ExperimentLifecycle.CONFIG_DRAFTED: frozenset(
        {ExperimentLifecycle.CONFIG_VALIDATED, ExperimentLifecycle.CONFIG_INVALID}
    ),
    ExperimentLifecycle.CONFIG_VALIDATED: frozenset({ExperimentLifecycle.PLAN_PRESENTED}),
    ExperimentLifecycle.PLAN_PRESENTED: frozenset({ExperimentLifecycle.AWAITING_APPROVAL}),
    ExperimentLifecycle.AWAITING_APPROVAL: frozenset(
        {
            ExperimentLifecycle.APPROVED,
            ExperimentLifecycle.USER_REQUESTED_CHANGES,
            ExperimentLifecycle.PLAN_REJECTED,
        }
    ),
    ExperimentLifecycle.APPROVED: frozenset(
        {ExperimentLifecycle.RESOLVED, ExperimentLifecycle.USER_REQUESTED_CHANGES}
    ),
    ExperimentLifecycle.USER_REQUESTED_CHANGES: frozenset(
        {ExperimentLifecycle.CONFIG_DRAFTED}
    ),
    ExperimentLifecycle.RESOLVED: frozenset({ExperimentLifecycle.PROTOCOL_BUILT}),
    ExperimentLifecycle.PROTOCOL_BUILT: frozenset(
        {ExperimentLifecycle.SIMULATED, ExperimentLifecycle.SIMULATION_FAILED}
    ),
    ExperimentLifecycle.SIMULATED: frozenset({ExperimentLifecycle.READY_FOR_EXECUTION}),
    ExperimentLifecycle.CONFIG_INVALID: frozenset(),
    ExperimentLifecycle.PLAN_REJECTED: frozenset(),
    ExperimentLifecycle.SIMULATION_FAILED: frozenset(),
    ExperimentLifecycle.READY_FOR_EXECUTION: frozenset(),
}


class StrictWorkflowModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
        frozen=True,
    )


class WorkflowTransitionV1(StrictWorkflowModel):
    from_state: ExperimentLifecycle
    to_state: ExperimentLifecycle
    reason: str = Field(min_length=1)


class ApprovalRecordV1(StrictWorkflowModel):
    decision: Literal["APPROVE", "REJECT"]
    statement: str = Field(min_length=1)
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExperimentWorkflowStateV1(StrictWorkflowModel):
    schema_version: Literal["printing-experiment-workflow/v1"] = (
        "printing-experiment-workflow/v1"
    )
    workflow_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_text: str = Field(min_length=1)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lifecycle_state: ExperimentLifecycle
    transitions: list[WorkflowTransitionV1] = Field(default_factory=list)
    config_path: str | None = None
    config_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    config_version: int | None = Field(default=None, ge=1)
    parent_config_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    config_summary: ExperimentConfigSummaryV1 | None = None
    print_job_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    approval: ApprovalRecordV1 | None = None
    approved_config_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    resolved_plan_path: str | None = None
    resolved_plan_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    build_artifact: BuildArtifact | None = None
    simulation_result: SimulationResult | None = None
    error_stage: WorkflowErrorStage | None = None
    errors: list[str] = Field(default_factory=list)


def allowed_transitions(state: ExperimentLifecycle | str) -> tuple[ExperimentLifecycle, ...]:
    return tuple(sorted(_ALLOWED_TRANSITIONS[ExperimentLifecycle(state)], key=lambda item: item.value))


def transition_workflow(
    state: ExperimentWorkflowStateV1,
    target: ExperimentLifecycle,
    *,
    reason: str,
) -> ExperimentWorkflowStateV1:
    if target not in _ALLOWED_TRANSITIONS[state.lifecycle_state]:
        allowed = ", ".join(item.value for item in allowed_transitions(state.lifecycle_state)) or "none"
        raise WorkflowTransitionError(
            f"invalid workflow transition {state.lifecycle_state.value} -> {target.value}; "
            f"allowed: {allowed}"
        )
    record = WorkflowTransitionV1(
        from_state=state.lifecycle_state,
        to_state=target,
        reason=reason,
    )
    return state.model_copy(
        update={
            "lifecycle_state": target,
            "transitions": [*state.transitions, record],
        }
    )


def receive_experiment_request(request_text: str) -> ExperimentWorkflowStateV1:
    request = request_text.strip()
    if not request:
        raise ValueError("request_text must not be empty")
    request_sha = hashlib.sha256(request.encode("utf-8")).hexdigest()
    workflow_id = canonical_sha256(
        {"schema_version": "printing-experiment-workflow/v1", "request_sha256": request_sha}
    )
    return ExperimentWorkflowStateV1(
        workflow_id=workflow_id,
        request_text=request,
        request_sha256=request_sha,
        lifecycle_state=ExperimentLifecycle.REQUEST_RECEIVED,
    )


def _attach_config(
    state: ExperimentWorkflowStateV1,
    artifact: ExperimentConfigArtifactV1,
) -> ExperimentWorkflowStateV1:
    return state.model_copy(
        update={
            "config_path": artifact.path,
            "config_sha256": artifact.config_sha256,
            "config_version": artifact.config.experiment.version,
            "parent_config_sha256": artifact.config.experiment.parent_config_sha256,
            "config_summary": artifact.summary,
            "print_job_sha256": artifact.job.job_id,
            "approval": None,
            "approved_config_sha256": None,
            "resolved_plan_path": None,
            "resolved_plan_sha256": None,
            "build_artifact": None,
            "simulation_result": None,
            "error_stage": None,
            "errors": [],
        }
    )


def _present_config(
    state: ExperimentWorkflowStateV1,
    artifact: ExperimentConfigArtifactV1,
) -> ExperimentWorkflowStateV1:
    state = _attach_config(state, artifact)
    state = transition_workflow(state, ExperimentLifecycle.CONFIG_VALIDATED, reason="strict YAML, reference, and trusted-workflow validation passed")
    state = transition_workflow(state, ExperimentLifecycle.PLAN_PRESENTED, reason="scientist-facing YAML plan summary generated")
    return transition_workflow(state, ExperimentLifecycle.AWAITING_APPROVAL, reason="explicit scientist decision required")


def draft_experiment_workflow(
    request_text: str,
    draft: PrintingExperimentDraftV1 | dict[str, Any],
    *,
    template_id: str,
    output_name: str,
    output_dir: str | Path | None = None,
) -> ExperimentWorkflowStateV1:
    state = receive_experiment_request(request_text)
    state = transition_workflow(
        state,
        ExperimentLifecycle.CONFIG_DRAFTED,
        reason="deterministic experiment configuration draft constructed",
    )
    try:
        artifact = create_printing_experiment_config(
            draft,
            template_id=template_id,
            output_name=output_name,
            output_dir=output_dir,
        )
    except (ValidationError, ExperimentConfigError) as exc:
        if isinstance(exc, ExperimentConfigReferenceError):
            stage = WorkflowErrorStage.REFERENCE
        elif isinstance(exc, ExperimentConfigPhysicalValidationError):
            stage = WorkflowErrorStage.PHYSICAL
        else:
            stage = WorkflowErrorStage.CONFIG_SCHEMA
        invalid = transition_workflow(
            state,
            ExperimentLifecycle.CONFIG_INVALID,
            reason="experiment configuration validation failed",
        )
        return invalid.model_copy(
            update={"error_stage": stage, "errors": [str(exc)]}
        )
    return _present_config(state, artifact)


def approve_experiment_workflow(
    state: ExperimentWorkflowStateV1,
    *,
    statement: str,
) -> ExperimentWorkflowStateV1:
    normalized = statement.strip().lower()
    if not any(
        token in normalized
        for token in ("approve", "approved", "yes", "run", "proceed", "confirm")
    ):
        raise WorkflowTransitionError(
            "approval statement must explicitly approve, confirm, or authorize proceeding"
        )
    if state.config_sha256 is None:
        raise WorkflowTransitionError("cannot approve without a validated config")
    approved = transition_workflow(state, ExperimentLifecycle.APPROVED, reason="scientist explicitly approved the displayed YAML")
    record = ApprovalRecordV1(
        decision="APPROVE",
        statement=statement,
        config_sha256=state.config_sha256,
    )
    return approved.model_copy(
        update={"approval": record, "approved_config_sha256": state.config_sha256}
    )


def reject_experiment_workflow(
    state: ExperimentWorkflowStateV1,
    *,
    statement: str,
) -> ExperimentWorkflowStateV1:
    if state.config_sha256 is None:
        raise WorkflowTransitionError("cannot reject without a displayed config")
    rejected = transition_workflow(state, ExperimentLifecycle.PLAN_REJECTED, reason="scientist rejected the displayed YAML")
    return rejected.model_copy(
        update={
            "approval": ApprovalRecordV1(
                decision="REJECT",
                statement=statement,
                config_sha256=state.config_sha256,
            )
        }
    )


def revise_experiment_workflow(
    state: ExperimentWorkflowStateV1,
    changes: PrintingExperimentRevisionV1 | dict[str, Any],
    *,
    output_dir: str | Path | None = None,
) -> ExperimentWorkflowStateV1:
    if state.config_path is None:
        raise WorkflowTransitionError("cannot revise before a config exists")
    state = transition_workflow(
        state,
        ExperimentLifecycle.USER_REQUESTED_CHANGES,
        reason="scientist requested a scientific configuration change",
    )
    state = transition_workflow(
        state,
        ExperimentLifecycle.CONFIG_DRAFTED,
        reason="deterministic child configuration draft constructed; prior artifact retained",
    )
    try:
        artifact = revise_printing_experiment_config(
            state.config_path,
            changes,
            output_dir=output_dir,
        )
    except (ValidationError, ExperimentConfigError) as exc:
        if isinstance(exc, ExperimentConfigReferenceError):
            stage = WorkflowErrorStage.REFERENCE
        elif isinstance(exc, ExperimentConfigPhysicalValidationError):
            stage = WorkflowErrorStage.PHYSICAL
        else:
            stage = WorkflowErrorStage.CONFIG_SCHEMA
        invalid = transition_workflow(
            state,
            ExperimentLifecycle.CONFIG_INVALID,
            reason="revised experiment configuration validation failed",
        )
        return invalid.model_copy(
            update={"error_stage": stage, "errors": [str(exc)]}
        )
    return _present_config(state, artifact)


def _verified_config(state: ExperimentWorkflowStateV1) -> ExperimentConfigArtifactV1:
    if state.config_path is None or state.config_sha256 is None:
        raise WorkflowTransitionError("workflow has no config artifact")
    artifact = load_printing_experiment_config(state.config_path)
    if artifact.config_sha256 != state.config_sha256:
        raise WorkflowTransitionError(
            "experiment config changed after workflow validation; create a new version"
        )
    return artifact


def _artifact_directory(state: ExperimentWorkflowStateV1, output_dir: str | Path | None) -> Path:
    return Path(output_dir or (ARTIFACT_DIR / state.workflow_id[:16])).resolve()


def _config_reference(path: str) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(resolved)


def resolve_approved_experiment(
    state: ExperimentWorkflowStateV1,
    *,
    output_dir: str | Path | None = None,
) -> ExperimentWorkflowStateV1:
    if state.lifecycle_state != ExperimentLifecycle.APPROVED:
        raise WorkflowTransitionError("only an APPROVED experiment can be resolved")
    artifact = _verified_config(state)
    if state.approved_config_sha256 != artifact.config_sha256:
        raise WorkflowTransitionError("approval does not seal the current config SHA-256")
    plan = compile_print_job(
        artifact.job,
        experiment_config_sha256=artifact.config_sha256,
        experiment_config_reference=_config_reference(artifact.path),
    )
    directory = _artifact_directory(state, output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    plan_path = directory / f"resolved_plan_{plan.plan_id[:12]}.json"
    plan_path.write_text(resolved_plan_artifact_json(plan), encoding="utf-8", newline="\n")
    resolved = transition_workflow(state, ExperimentLifecycle.RESOLVED, reason="approved YAML compiled to ResolvedPrintPlanV1")
    return resolved.model_copy(
        update={
            "resolved_plan_path": str(plan_path),
            "resolved_plan_sha256": plan.plan_id,
        }
    )


def _verified_plan(state: ExperimentWorkflowStateV1) -> ResolvedPrintPlanV1:
    if state.resolved_plan_path is None or state.resolved_plan_sha256 is None:
        raise WorkflowTransitionError("workflow has no resolved plan artifact")
    plan = parse_resolved_print_plan_json(Path(state.resolved_plan_path).read_bytes())
    if plan.plan_id != state.resolved_plan_sha256:
        raise WorkflowTransitionError("resolved plan identity does not match workflow state")
    return plan


def build_approved_experiment(
    state: ExperimentWorkflowStateV1,
    *,
    output_dir: str | Path | None = None,
) -> ExperimentWorkflowStateV1:
    if state.lifecycle_state != ExperimentLifecycle.RESOLVED:
        raise WorkflowTransitionError("only a RESOLVED experiment can be built")
    config = _verified_config(state)
    plan = _verified_plan(state)
    if plan.provenance.source_experiment_config_sha256 != config.config_sha256:
        raise WorkflowTransitionError("resolved plan is not linked to the approved config")
    if plan.provenance.source_job_sha256 != config.job.job_id:
        raise WorkflowTransitionError("resolved plan is not linked to the config's PrintJobV1")
    prepared = prepare_printing_request(printing_request_from_job(config.job))
    artifact = build_prepared_artifact(
        prepared,
        exercise_motion=True,
        output_dir=_artifact_directory(state, output_dir),
        source_experiment_config_sha256=config.config_sha256,
        source_job_sha256=config.job.job_id,
        source_plan_sha256=plan.plan_id,
    )
    built = transition_workflow(state, ExperimentLifecycle.PROTOCOL_BUILT, reason="existing deterministic Python protocol built with sealed provenance")
    return built.model_copy(update={"build_artifact": artifact})


def simulate_approved_experiment(
    state: ExperimentWorkflowStateV1,
    *,
    record: bool = True,
) -> ExperimentWorkflowStateV1:
    if state.lifecycle_state != ExperimentLifecycle.PROTOCOL_BUILT or state.build_artifact is None:
        raise WorkflowTransitionError("only a PROTOCOL_BUILT experiment can be simulated")
    result = simulate_built_artifact(state.build_artifact, record=record)
    if result.status != "PASS":
        failed = transition_workflow(state, ExperimentLifecycle.SIMULATION_FAILED, reason="local OT-2 simulation failed")
        return failed.model_copy(
            update={
                "simulation_result": result,
                "error_stage": WorkflowErrorStage.SIMULATION,
                "errors": [result.output_tail],
            }
        )
    simulated = transition_workflow(state, ExperimentLifecycle.SIMULATED, reason="exact approved protocol artifact passed local OT-2 simulation")
    simulated = simulated.model_copy(update={"simulation_result": result})
    return transition_workflow(simulated, ExperimentLifecycle.READY_FOR_EXECUTION, reason="approval, resolution, build, and simulation prerequisites satisfied")


def advance_approved_experiment_to_ready(
    state: ExperimentWorkflowStateV1,
    *,
    output_dir: str | Path | None = None,
    record_simulation: bool = True,
) -> ExperimentWorkflowStateV1:
    """Run only the deterministic post-approval local path; never contacts a robot."""
    state = resolve_approved_experiment(state, output_dir=output_dir)
    state = build_approved_experiment(state, output_dir=output_dir)
    return simulate_approved_experiment(state, record=record_simulation)
