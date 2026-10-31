"""Trusted Stage 8 workflow for generalized standard-printing experiments.

The LLM-facing agent may propose, validate, resolve, and preview configurations.
It cannot call the approval functions in this module. A trusted UI or application
layer must record the scientist's decision before the exact configuration can be
simulated. This laptop intentionally stops at ``READY_FOR_EXECUTION``.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml

from src.printing.config import REPO_ROOT
from src.printing.standard.loader import load_experiment_job_mapping

from .printing_tools import (
    StandardExperimentApprovalSealV1,
    StandardExperimentConfigArtifactV1,
    StandardExperimentPreviewResultV1,
    StandardExperimentProposalV1,
    StandardExperimentResolutionResultV1,
    StandardExperimentSimulationResultV1,
    StandardExperimentValidationResultV1,
    create_standard_printing_experiment_config,
    inspect_standard_printing_layout,
    resolve_standard_printing_experiment,
    seal_standard_experiment_approval,
    simulate_approved_standard_printing_experiment,
    validate_standard_printing_experiment,
)


class StandardExperimentLifecycle(str, Enum):
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    PLAN_REJECTED = "PLAN_REJECTED"
    READY_FOR_EXECUTION = "READY_FOR_EXECUTION"


class StandardExperimentWorkflowStateV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "standard-experiment-workflow/v1"
    lifecycle_state: StandardExperimentLifecycle
    request_text: str = Field(min_length=1)
    experiment_config: StandardExperimentProposalV1
    artifact: StandardExperimentConfigArtifactV1
    validation: StandardExperimentValidationResultV1
    resolution: StandardExperimentResolutionResultV1
    preview: StandardExperimentPreviewResultV1
    approval: StandardExperimentApprovalSealV1 | None = None
    simulation: StandardExperimentSimulationResultV1 | None = None
    decision_statement: str | None = None
    history: tuple[str, ...]

    @model_validator(mode="after")
    def _lifecycle_has_required_evidence(self):
        config_payload = self.experiment_config.model_dump(mode="json")
        config_job_sha256 = load_experiment_job_mapping(config_payload).job_id
        job_hashes = {
            config_job_sha256,
            self.artifact.job_sha256,
            self.validation.job_sha256,
            self.resolution.job_sha256,
            self.preview.job_sha256,
        }
        if len(job_hashes) != 1:
            raise ValueError("workflow config and displayed artifacts do not describe one exact job")
        artifact_config = StandardExperimentProposalV1.model_validate(
            yaml.safe_load(self.artifact.canonical_config_yaml)
        )
        validation_config = StandardExperimentProposalV1.model_validate(
            yaml.safe_load(self.validation.canonical_config_yaml)
        )
        if artifact_config != self.experiment_config or validation_config != self.experiment_config:
            raise ValueError("displayed canonical configuration differs from workflow config")
        artifact_bytes = self.artifact.canonical_config_yaml.encode("utf-8")
        if hashlib.sha256(artifact_bytes).hexdigest() != self.artifact.file_sha256:
            raise ValueError("proposal artifact digest does not match its canonical YAML")
        artifact_path = Path(self.artifact.path)
        if not artifact_path.is_absolute():
            artifact_path = Path(REPO_ROOT) / artifact_path
        if not artifact_path.is_file() or artifact_path.read_bytes() != artifact_bytes:
            raise ValueError("persisted proposal artifact is absent or differs from review")
        if self.resolution.plan_sha256 != self.preview.plan_sha256:
            raise ValueError("resolved plan and displayed preview do not match")
        if (
            self.resolution.physical_sha256 != self.preview.physical_sha256
            or self.resolution.setup_sha256 != self.preview.setup_sha256
            or self.resolution.structural_topology_sha256
            != self.preview.structural_topology_sha256
            or self.resolution.totals != self.preview.totals
        ):
            raise ValueError("resolved evidence and displayed preview do not match")
        if self.lifecycle_state == StandardExperimentLifecycle.AWAITING_APPROVAL:
            if self.approval is not None or self.simulation is not None:
                raise ValueError("awaiting-approval state cannot contain approval or simulation")
        elif self.lifecycle_state == StandardExperimentLifecycle.APPROVED:
            if self.approval is None or self.simulation is not None:
                raise ValueError("approved state requires approval and no simulation")
            if self.approval.job_sha256 != config_job_sha256:
                raise ValueError("approval does not bind the displayed exact job")
        elif self.lifecycle_state == StandardExperimentLifecycle.PLAN_REJECTED:
            if self.approval is not None or self.simulation is not None:
                raise ValueError("rejected state cannot contain approval or simulation")
        elif self.lifecycle_state == StandardExperimentLifecycle.READY_FOR_EXECUTION:
            if self.approval is None or self.simulation is None:
                raise ValueError("ready state requires approval and passed simulation")
            if self.approval.job_sha256 != config_job_sha256:
                raise ValueError("approval does not bind the displayed exact job")
            if (
                self.simulation.job_sha256 != config_job_sha256
                or self.simulation.plan_sha256 != self.resolution.plan_sha256
                or self.simulation.physical_sha256 != self.resolution.physical_sha256
                or self.simulation.setup_sha256 != self.resolution.setup_sha256
                or self.simulation.structural_topology_sha256
                != self.resolution.structural_topology_sha256
                or self.simulation.totals != self.resolution.totals
            ):
                raise ValueError("simulation evidence does not match the approved plan")
        return self


class StandardExperimentWorkflowTransitionError(ValueError):
    """Raised when trusted workflow code attempts an unsafe lifecycle change."""


def _config_payload(config: StandardExperimentProposalV1) -> dict:
    return config.model_dump(mode="json")


def _validated_transition(
    state: StandardExperimentWorkflowStateV1, **updates
) -> StandardExperimentWorkflowStateV1:
    """Reject unchecked ``model_copy`` mutations before and after every transition."""
    current = StandardExperimentWorkflowStateV1.model_validate(
        state.model_dump(mode="json")
    )
    payload = current.model_dump(mode="json")
    payload.update(updates)
    return StandardExperimentWorkflowStateV1.model_validate(payload)


def present_standard_experiment_for_approval(
    request_text: str,
    experiment_config: StandardExperimentProposalV1 | dict,
    *,
    output_name: str,
) -> StandardExperimentWorkflowStateV1:
    """Persist, validate, resolve, and preview a proposal, then stop for approval."""
    config = StandardExperimentProposalV1.model_validate(experiment_config)
    payload = _config_payload(config)
    artifact = StandardExperimentConfigArtifactV1.model_validate_json(
        create_standard_printing_experiment_config.invoke(
            {"experiment_config": payload, "output_name": output_name}
        )
    )
    validation = StandardExperimentValidationResultV1.model_validate_json(
        validate_standard_printing_experiment.invoke({"experiment_config": payload})
    )
    resolution = StandardExperimentResolutionResultV1.model_validate_json(
        resolve_standard_printing_experiment.invoke({"experiment_config": payload})
    )
    preview = StandardExperimentPreviewResultV1.model_validate_json(
        inspect_standard_printing_layout.invoke({"experiment_config": payload})
    )
    return StandardExperimentWorkflowStateV1(
        lifecycle_state=StandardExperimentLifecycle.AWAITING_APPROVAL,
        request_text=request_text,
        experiment_config=config,
        artifact=artifact,
        validation=validation,
        resolution=resolution,
        preview=preview,
        history=(
            "proposal persisted as immutable YAML",
            "PrintExperimentJobV1 validation passed",
            "ResolvedExperimentPlanV1 resolution passed",
            "scientist-readable preview presented",
            "awaiting explicit scientist approval",
        ),
    )


def approve_standard_experiment_workflow(
    state: StandardExperimentWorkflowStateV1, statement: str
) -> StandardExperimentWorkflowStateV1:
    """Trusted non-tool boundary: bind explicit approval to this exact job."""
    state = StandardExperimentWorkflowStateV1.model_validate(
        state.model_dump(mode="json")
    )
    if state.lifecycle_state != StandardExperimentLifecycle.AWAITING_APPROVAL:
        raise StandardExperimentWorkflowTransitionError(
            f"cannot approve from {state.lifecycle_state.value}"
        )
    approval = seal_standard_experiment_approval(state.experiment_config, statement)
    return _validated_transition(
        state,
        lifecycle_state=StandardExperimentLifecycle.APPROVED,
        approval=approval,
        decision_statement=approval.statement,
        history=(*state.history, "scientist approved the exact displayed job"),
    )


def reject_standard_experiment_workflow(
    state: StandardExperimentWorkflowStateV1, statement: str
) -> StandardExperimentWorkflowStateV1:
    """Trusted non-tool boundary: reject a displayed proposal permanently."""
    state = StandardExperimentWorkflowStateV1.model_validate(
        state.model_dump(mode="json")
    )
    if state.lifecycle_state != StandardExperimentLifecycle.AWAITING_APPROVAL:
        raise StandardExperimentWorkflowTransitionError(
            f"cannot reject from {state.lifecycle_state.value}"
        )
    if not statement.strip():
        raise ValueError("a rejection statement is required")
    return _validated_transition(
        state,
        lifecycle_state=StandardExperimentLifecycle.PLAN_REJECTED,
        decision_statement=statement.strip(),
        history=(*state.history, "scientist rejected the displayed job"),
    )


def simulate_approved_standard_experiment_workflow(
    state: StandardExperimentWorkflowStateV1,
) -> StandardExperimentWorkflowStateV1:
    """Build and simulate the exact approved plan; never authorize live motion."""
    state = StandardExperimentWorkflowStateV1.model_validate(
        state.model_dump(mode="json")
    )
    if state.lifecycle_state != StandardExperimentLifecycle.APPROVED:
        raise StandardExperimentWorkflowTransitionError(
            f"cannot simulate from {state.lifecycle_state.value}"
        )
    if state.approval is None:  # guarded by the model; retained for fail-closed use
        raise StandardExperimentWorkflowTransitionError("approved state has no seal")
    simulation = StandardExperimentSimulationResultV1.model_validate_json(
        simulate_approved_standard_printing_experiment.invoke(
            {
                "experiment_config": _config_payload(state.experiment_config),
                "approval": state.approval.model_dump(mode="json"),
            }
        )
    )
    return _validated_transition(
        state,
        lifecycle_state=StandardExperimentLifecycle.READY_FOR_EXECUTION,
        simulation=simulation,
        history=(
            *state.history,
            "exact approved plan built by 01_printing_standard.py",
            "local OT-2 simulation passed",
            "READY_FOR_EXECUTION (no live execution performed)",
        ),
    )
