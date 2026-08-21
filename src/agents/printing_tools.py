"""High-level, simulation-only AI tools for modern printing workflows."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field
import yaml

from src.printing.artifacts import (
    build_prepared_artifact,
    prepare_printing_request,
    simulate_prepared_request,
)
from src.printing.designs import get_design, list_designs
from src.printing.agent_contract import (
    AgentPrintPatternV1,
    PrintJobDraftV1,
    PrintJobModificationV1,
    create_and_compile_draft,
    interpretation_result,
    modify_and_compile_job,
)
from src.printing.references import (
    list_registered_materials as registered_material_records,
    list_registered_substrates as registered_substrate_records,
)
from src.printing.schemas import PrintJobV1, PrintingFamily
from src.printing.experiment_configs import (
    PrintingExperimentDraftV1,
    PrintingExperimentRevisionV1,
    describe_experiment_config,
)
from src.printing.experiment_workflow import (
    ExperimentWorkflowStateV1,
    advance_approved_experiment_to_ready,
    approve_experiment_workflow,
    draft_experiment_workflow,
    reject_experiment_workflow,
    revise_experiment_workflow,
)
from src.printing.workflows import get_workflow, list_workflows
from src.printing.standard import equivalence as standard_equivalence, builder as standard_builder
from src.printing.standard.loader import load_experiment_job_mapping
from src.printing.standard.resolver import resolve_experiment_job
from src.printing.standard.review import render_plan_review
from src.printing.config import REPO_ROOT
from src.printing.schemas.experiments import ExperimentSpecV1
from src.printing.clover import builder as clover_builder
from src.printing.clover.loader import (
    load_experiment_job_mapping as load_clover_job_mapping,
)
from src.printing.clover.resolver import resolve_experiment_job as resolve_clover_job
from src.printing.clover.review import render_clover_coordinates, render_clover_review
from src.printing.clover.schemas import CloverExperimentSpecV1


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowNameInput(ToolInput):
    workflow_name: str = Field(min_length=1)


class PrintingRequestInput(ToolInput):
    family: PrintingFamily
    workflow_name: str = Field(min_length=1)
    design_name: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        payload["family"] = self.family.value
        return payload


class CreatePrintJobInput(ToolInput):
    name: str = Field(min_length=1)
    description: str | None = None
    substrate_id: str | None = None
    material_id: str | None = None
    pattern: AgentPrintPatternV1
    volume_ul: float
    ordering_mode: (
        Literal[
            "layer_then_row_then_column",
            "clover_by_clover",
            "position_by_position",
        ]
        | None
    ) = None
    inter_layer_rest_minutes: float | None = Field(default=None, ge=0)
    metadata: dict[str, str] = Field(default_factory=dict)


class ModifyPrintJobInput(ToolInput):
    existing_job: PrintJobV1
    changes: PrintJobModificationV1


class ReportPrintingIssueInput(ToolInput):
    status: Literal["needs_clarification", "unsupported", "error"]
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: list[str] = Field(default_factory=list)


class DraftPrintingExperimentInput(ToolInput):
    request_text: str = Field(min_length=1)
    draft: PrintingExperimentDraftV1
    template_id: Literal[
        "standard_paper_printing/v1",
        "four_clover_printing/v1",
    ]
    output_name: str = Field(min_length=1)


class ExistingExperimentConfigInput(ToolInput):
    path: str = Field(min_length=1)


class RevisePrintingExperimentInput(ToolInput):
    workflow_state: ExperimentWorkflowStateV1
    changes: PrintingExperimentRevisionV1


class DecidePrintingExperimentInput(ToolInput):
    workflow_state: ExperimentWorkflowStateV1
    statement: str = Field(min_length=1)


class PrepareApprovedExperimentInput(ToolInput):
    workflow_state: ExperimentWorkflowStateV1


class StandardExperimentProposalV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["print-experiment-job/v1"] = "print-experiment-job/v1"
    machine_profile: Literal[
        "configs/machines/ot2_standard_printing_p20_v1.yaml"
    ]
    experiment: ExperimentSpecV1


class StandardExperimentConfigInput(ToolInput):
    """High-level scientist configuration; never resolved robot actions."""

    experiment_config: StandardExperimentProposalV1


class CreateStandardExperimentConfigInput(StandardExperimentConfigInput):
    output_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


class StandardExperimentValidationResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["PASS"] = "PASS"
    schema_version: Literal["print-experiment-job/v1"]
    experiment_id: str
    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    machine_profile: str
    canonical_config_yaml: str


class StandardExperimentConfigArtifactV1(StandardExperimentValidationResultV1):
    path: str
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class StandardExperimentResolutionResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["PASS"] = "PASS"
    experiment_id: str
    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    physical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    setup_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structural_topology_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    totals: dict[str, int | float]


class StandardExperimentPreviewResultV1(StandardExperimentResolutionResultV1):
    review: str


class StandardExperimentApprovalSealV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    statement: str = Field(min_length=1)
    seal: str = Field(pattern=r"^[0-9a-f]{64}$")


class SimulateApprovedStandardExperimentInput(StandardExperimentConfigInput):
    approval: StandardExperimentApprovalSealV1


class StandardExperimentSimulationResultV1(StandardExperimentResolutionResultV1):
    status: Literal["READY_FOR_EXECUTION"] = "READY_FOR_EXECUTION"
    protocol_path: str
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    simulation: Literal["PASS"] = "PASS"
    print_count: int = Field(ge=0)


# AI-generated configurations are written here, never over a hand-validated
# ground truth in configs/experiments/.
GENERATED_CONFIG_DIR = Path(REPO_ROOT) / "configs" / "generated"
STANDARD_EXPERIMENT_PROPOSAL_DIR = GENERATED_CONFIG_DIR
FOUR_CLOVER_PROPOSAL_DIR = GENERATED_CONFIG_DIR


def _standard_approval_key() -> bytes:
    encoded = os.environ.get("OT2_STANDARD_EXPERIMENT_APPROVAL_KEY", "").strip()
    if not encoded:
        raise RuntimeError(
            "trusted workflow must configure OT2_STANDARD_EXPERIMENT_APPROVAL_KEY"
        )
    try:
        key = bytes.fromhex(encoded)
    except ValueError as exc:
        raise RuntimeError(
            "OT2_STANDARD_EXPERIMENT_APPROVAL_KEY must be hex encoded"
        ) from exc
    if len(key) < 32:
        raise RuntimeError(
            "OT2_STANDARD_EXPERIMENT_APPROVAL_KEY must contain at least 32 bytes"
        )
    return key


def _validated_standard_config(
    config: StandardExperimentProposalV1 | dict[str, Any],
) -> tuple[Any, StandardExperimentValidationResultV1]:
    proposal = StandardExperimentProposalV1.model_validate(config)
    normalized = proposal.model_dump(mode="json")
    job = load_experiment_job_mapping(normalized)
    profile = proposal.machine_profile
    result = StandardExperimentValidationResultV1(
        schema_version=job.schema_version,
        experiment_id=job.experiment.metadata.experiment_id,
        job_sha256=job.job_id,
        machine_profile=profile,
        canonical_config_yaml=yaml.safe_dump(
            normalized,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ),
    )
    return job, result


def seal_standard_experiment_approval(
    experiment_config: StandardExperimentProposalV1 | dict[str, Any], statement: str
) -> StandardExperimentApprovalSealV1:
    """Non-tool boundary used by trusted UI/workflow code after explicit approval."""
    lowered = statement.strip().lower()
    if re.search(r"\b(?:do not|don't|not approved|reject|cancel|stop)\b", lowered):
        raise ValueError("approval statement contains a rejection or negation")
    if not re.search(r"\b(?:i approve|approved|confirm approval)\b", lowered):
        raise ValueError("explicit approval statement is required")
    job, _ = _validated_standard_config(experiment_config)
    exact_statement = statement.strip()
    message = f"{job.job_id}\n{exact_statement}".encode("utf-8")
    approval = StandardExperimentApprovalSealV1(
        job_sha256=job.job_id,
        statement=exact_statement,
        seal=hmac.new(_standard_approval_key(), message, hashlib.sha256).hexdigest(),
    )
    return approval


def _verify_standard_approval(job_sha256: str, approval: StandardExperimentApprovalSealV1) -> None:
    if approval.job_sha256 != job_sha256:
        raise ValueError("approval seal does not authorize this exact experiment job")
    message = f"{approval.job_sha256}\n{approval.statement}".encode("utf-8")
    expected = hmac.new(_standard_approval_key(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, approval.seal):
        raise ValueError("approval seal does not authorize this exact experiment job")


def _payload(
    family: PrintingFamily,
    workflow_name: str,
    design_name: str | None,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    return PrintingRequestInput(
        family=family,
        workflow_name=workflow_name,
        design_name=design_name,
        parameters=parameters,
    ).payload()


@tool
def list_printing_workflows() -> str:
    """List functioning modern printing workflows, families, designs, and lifecycle."""
    return json.dumps(
        [
            {
                "workflow_name": spec.name,
                "family": spec.family.value,
                "design_name": spec.design_name,
                "lifecycle": spec.lifecycle.value,
                "is_default": spec.is_default,
                "description": spec.description,
            }
            for spec in list_workflows()
        ],
        indent=2,
    )


@tool(args_schema=WorkflowNameInput)
def describe_printing_workflow(workflow_name: str) -> str:
    """Describe one workflow and its exact AI-selectable parameter schema with units."""
    spec = get_workflow(workflow_name)
    return json.dumps(
        {
            "workflow_name": spec.name,
            "family": spec.family.value,
            "design_name": spec.design_name,
            "description": spec.description,
            "parameter_schema": spec.patch_model.model_json_schema(),
            "config_reference": str(spec.default_config),
        },
        indent=2,
    )


@tool
def list_printing_designs() -> str:
    """List registered continuous-coordinate designs that have working generators."""
    return json.dumps(
        [
            {
                "design_name": design.name,
                "description": design.description,
                "parameter_schema": design.patch_model.model_json_schema(),
            }
            for design in list_designs()
        ],
        indent=2,
    )


@tool
def list_printing_capabilities() -> str:
    """List persistent YAML capabilities, strict job families, and approval boundary."""
    return json.dumps(
        {
            "experiment_schema": "printing-experiment/v1",
            "validation_schema": "print-job/v1",
            "templates": [
                "standard_paper_printing/v1",
                "four_clover_printing/v1",
            ],
            "approval_boundary": "AWAITING_APPROVAL -> APPROVED",
            "terminal_local_state": "READY_FOR_EXECUTION",
            "patterns": {
                "well_selection": {
                    "scientific_fields": [
                        "rows",
                        "columns",
                        "layers_by_row",
                        "volume_ul",
                    ],
                    "ordering": ["layer_then_row_then_column"],
                },
                "four_clover": {
                    "scientific_fields": [
                        "geometry",
                        "centers or registered placement preset",
                        "replicates",
                        "layers",
                        "volume_ul",
                    ],
                    "ordering": ["clover_by_clover", "position_by_position"],
                    "default_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
                    "placement_presets": {"standard": [1, 3]},
                },
            },
            "unsupported_patterns": ["ring", "line", "circle", "arbitrary_points"],
        },
        indent=2,
    )


@tool
def list_registered_substrates() -> str:
    """List scientist-selectable substrate names without asking the model for hashes."""
    return json.dumps(
        [
            {
                "substrate_id": item.substrate_id,
                "load_name": item.load_name,
                "aliases": list(item.aliases),
                "template_id": item.template_id,
                "is_default": item.is_default,
            }
            for item in registered_substrate_records()
        ],
        indent=2,
    )


@tool
def list_registered_materials() -> str:
    """List logical material IDs and compatible scientific pattern families."""
    return json.dumps(
        [
            {
                "material_id": item.material_id,
                "display_name": item.display_name,
                "pattern_type": item.pattern_type,
                "is_default": item.is_default,
            }
            for item in registered_material_records()
        ],
        indent=2,
    )


@tool(args_schema=CreatePrintJobInput)
def create_and_compile_print_job(
    name: str,
    pattern: AgentPrintPatternV1,
    volume_ul: float,
    description: str | None = None,
    substrate_id: str | None = None,
    material_id: str | None = None,
    ordering_mode: str | None = None,
    inter_layer_rest_minutes: float | None = None,
    metadata: dict[str, str] | None = None,
) -> str:
    """Construct PrintJobV1, derive hashes/references, compile, and return a preview."""
    result = create_and_compile_draft(
        PrintJobDraftV1(
            name=name,
            description=description,
            substrate_id=substrate_id,
            material_id=material_id,
            pattern=pattern,
            volume_ul=volume_ul,
            ordering_mode=ordering_mode,
            inter_layer_rest_minutes=inter_layer_rest_minutes,
            metadata=metadata or {},
        )
    )
    return result.model_dump_json(indent=2)


@tool(args_schema=ModifyPrintJobInput)
def modify_and_compile_print_job(
    existing_job: PrintJobV1,
    changes: PrintJobModificationV1,
) -> str:
    """Create and compile a new immutable PrintJobV1 from scientific changes."""
    return modify_and_compile_job(existing_job, changes).model_dump_json(indent=2)


@tool(args_schema=ReportPrintingIssueInput)
def report_printing_request_issue(
    status: Literal["needs_clarification", "unsupported", "error"],
    code: str,
    message: str,
    details: list[str] | None = None,
) -> str:
    """Return a structured interpretation result when no job should be constructed."""
    return interpretation_result(
        status=status,
        code=code,
        message=message,
        details=details,
    ).model_dump_json(indent=2)


@tool
def list_standard_printing_experiment_capabilities() -> str:
    """Describe the generalized validated experiment vocabulary and safety boundary."""
    return json.dumps(
        {
            "job_schema": "print-experiment-job/v1",
            "plan_schema": "resolved-experiment-plan/v1",
            "machine_profiles": [
                "configs/machines/ot2_standard_printing_p20_v1.yaml"
            ],
            "scientific_steps": [
                "transfer",
                "mix",
                "direct_dilution",
                "serial_dilution",
                "print",
                "delay",
            ],
            "agent_facing_operations": [
                "create_config",
                "validate_config",
                "resolve_summary",
                "inspect_layout",
                "simulate_approved_config",
            ],
            "internal_only": [
                "split_transfer",
                "aspirate",
                "dispense",
                "move",
                "execute_print",
            ],
            "approval_boundary": "simulation requires an externally sealed approval",
            "terminal_state": "READY_FOR_EXECUTION",
            "live_execution": False,
        },
        indent=2,
    )


@tool(args_schema=CreateStandardExperimentConfigInput)
def create_standard_printing_experiment_config(
    experiment_config: StandardExperimentProposalV1, output_name: str
) -> str:
    """Validate and persist one immutable proposed generalized experiment YAML."""
    _, validated = _validated_standard_config(experiment_config)
    data = validated.canonical_config_yaml.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    directory = STANDARD_EXPERIMENT_PROPOSAL_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{output_name}_{digest[:12]}.yaml"
    if path.exists() and path.read_bytes() != data:
        raise ValueError("immutable proposal path already exists with different content")
    path.write_bytes(data)
    try:
        artifact_path = str(path.relative_to(REPO_ROOT))
    except ValueError:  # test/injected stores may intentionally live outside the repo
        artifact_path = str(path)
    return StandardExperimentConfigArtifactV1(
        **validated.model_dump(mode="json"),
        path=artifact_path,
        file_sha256=digest,
    ).model_dump_json(indent=2)


@tool(args_schema=StandardExperimentConfigInput)
def validate_standard_printing_experiment(
    experiment_config: StandardExperimentProposalV1,
) -> str:
    """Validate a proposed high-level standard experiment and registered profile."""
    _, result = _validated_standard_config(experiment_config)
    return result.model_dump_json(indent=2)


@tool(args_schema=StandardExperimentConfigInput)
def resolve_standard_printing_experiment(
    experiment_config: StandardExperimentProposalV1,
) -> str:
    """Resolve validated scientific intent and return hashes/totals, never motions."""
    job, _ = _validated_standard_config(experiment_config)
    plan = resolve_experiment_job(job)
    result = StandardExperimentResolutionResultV1(
        experiment_id=plan.experiment_id,
        job_sha256=job.job_id,
        plan_sha256=plan.plan_id,
        physical_sha256=standard_equivalence.physical_sha256(plan),
        setup_sha256=standard_equivalence.setup_sha256(plan),
        structural_topology_sha256=standard_equivalence.structural_sha256(plan),
        totals=plan.totals.model_dump(mode="json"),
    )
    return result.model_dump_json(indent=2)


@tool(args_schema=StandardExperimentConfigInput)
def inspect_standard_printing_layout(
    experiment_config: StandardExperimentProposalV1,
) -> str:
    """Render a scientist-readable setup/layout/operation preview before approval."""
    job, _ = _validated_standard_config(experiment_config)
    plan = resolve_experiment_job(job)
    result = StandardExperimentPreviewResultV1(
        experiment_id=plan.experiment_id,
        job_sha256=job.job_id,
        plan_sha256=plan.plan_id,
        physical_sha256=standard_equivalence.physical_sha256(plan),
        setup_sha256=standard_equivalence.setup_sha256(plan),
        structural_topology_sha256=standard_equivalence.structural_sha256(plan),
        totals=plan.totals.model_dump(mode="json"),
        review=render_plan_review(plan, job),
    )
    return result.model_dump_json(indent=2)


@tool(args_schema=SimulateApprovedStandardExperimentInput)
def simulate_approved_standard_printing_experiment(
    experiment_config: StandardExperimentProposalV1,
    approval: StandardExperimentApprovalSealV1,
) -> str:
    """Build and locally simulate only an externally sealed exact experiment."""
    job, _ = _validated_standard_config(experiment_config)
    _verify_standard_approval(job.job_id, approval)
    plan = resolve_experiment_job(job)
    artifact = standard_builder.build_standard_protocol(plan)
    passed, _, _ = standard_builder.simulate_standard_protocol(
        artifact.protocol_path, expected_sha256=artifact.protocol_sha256
    )
    if not passed:  # pragma: no cover - simulator raises on failure
        raise RuntimeError("local OT-2 simulation failed")
    return StandardExperimentSimulationResultV1(
        experiment_id=plan.experiment_id,
        job_sha256=job.job_id,
        plan_sha256=plan.plan_id,
        physical_sha256=standard_equivalence.physical_sha256(plan),
        setup_sha256=standard_equivalence.setup_sha256(plan),
        structural_topology_sha256=standard_equivalence.structural_sha256(plan),
        totals=plan.totals.model_dump(mode="json"),
        protocol_path=str(artifact.protocol_path.relative_to(REPO_ROOT)),
        protocol_sha256=artifact.protocol_sha256,
        print_count=plan.totals.print_count,
    ).model_dump_json(indent=2)


@tool(args_schema=DraftPrintingExperimentInput)
def draft_printing_experiment(
    request_text: str,
    draft: PrintingExperimentDraftV1,
    template_id: str,
    output_name: str,
) -> str:
    """Create, validate, save, and present a new YAML; stop at AWAITING_APPROVAL."""
    state = draft_experiment_workflow(
        request_text,
        draft,
        template_id=template_id,
        output_name=output_name,
    )
    return state.model_dump_json(indent=2)


@tool(args_schema=ExistingExperimentConfigInput)
def describe_printing_experiment(path: str) -> str:
    """Strictly load and summarize an existing persistent experiment YAML."""
    return describe_experiment_config(path).model_dump_json(indent=2)


@tool(args_schema=RevisePrintingExperimentInput)
def revise_printing_experiment(
    workflow_state: ExperimentWorkflowStateV1,
    changes: PrintingExperimentRevisionV1,
) -> str:
    """Create a child YAML version and return to AWAITING_APPROVAL."""
    return revise_experiment_workflow(workflow_state, changes).model_dump_json(indent=2)


@tool(args_schema=DecidePrintingExperimentInput)
def approve_printing_experiment(
    workflow_state: ExperimentWorkflowStateV1,
    statement: str,
) -> str:
    """Seal exactly the displayed config SHA after explicit scientist approval."""
    return approve_experiment_workflow(
        workflow_state,
        statement=statement,
    ).model_dump_json(indent=2)


@tool(args_schema=DecidePrintingExperimentInput)
def reject_printing_experiment(
    workflow_state: ExperimentWorkflowStateV1,
    statement: str,
) -> str:
    """Reject the displayed YAML; no plan, build, or simulation can follow."""
    return reject_experiment_workflow(
        workflow_state,
        statement=statement,
    ).model_dump_json(indent=2)


@tool(args_schema=PrepareApprovedExperimentInput)
def prepare_approved_printing_experiment(
    workflow_state: ExperimentWorkflowStateV1,
) -> str:
    """Resolve, build, and locally simulate an APPROVED YAML; never contacts a robot."""
    return advance_approved_experiment_to_ready(workflow_state).model_dump_json(indent=2)


@tool(args_schema=PrintingRequestInput)
def validate_printing_request(
    family: PrintingFamily,
    workflow_name: str,
    parameters: dict[str, Any],
    design_name: str | None = None,
) -> str:
    """Validate structured printing parameters deterministically before any build."""
    prepared = prepare_printing_request(
        _payload(family, workflow_name, design_name, parameters)
    )
    return prepared.validation.model_dump_json(indent=2)


@tool(args_schema=PrintingRequestInput)
def preview_design_coordinates(
    family: PrintingFamily,
    workflow_name: str,
    parameters: dict[str, Any],
    design_name: str | None = None,
) -> str:
    """Resolve design coordinates after validation; does not build or contact a robot."""
    prepared = prepare_printing_request(
        _payload(family, workflow_name, design_name, parameters)
    )
    if not prepared.validation.valid:
        return prepared.validation.model_dump_json(indent=2)
    if family != PrintingFamily.DESIGN or not design_name:
        raise ValueError(
            "coordinate preview requires family='design' and a design_name"
        )
    preview = get_design(design_name).generate(prepared.config)
    return json.dumps(preview, indent=2)


@tool(args_schema=PrintingRequestInput)
def build_printing_protocol(
    family: PrintingFamily,
    workflow_name: str,
    parameters: dict[str, Any],
    design_name: str | None = None,
) -> str:
    """Build an exact plan-only local artifact after deterministic validation."""
    prepared = prepare_printing_request(
        _payload(family, workflow_name, design_name, parameters)
    )
    if not prepared.validation.valid:
        return prepared.validation.model_dump_json(indent=2)
    artifact = build_prepared_artifact(prepared, exercise_motion=False)
    return artifact.model_dump_json(indent=2)


@tool(args_schema=PrintingRequestInput)
def simulate_printing_protocol(
    family: PrintingFamily,
    workflow_name: str,
    parameters: dict[str, Any],
    design_name: str | None = None,
) -> str:
    """Build and locally simulate the exact request with its motion path exercised."""
    prepared = prepare_printing_request(
        _payload(family, workflow_name, design_name, parameters)
    )
    if not prepared.validation.valid:
        return prepared.validation.model_dump_json(indent=2)
    return simulate_prepared_request(prepared).model_dump_json(indent=2)


PRINTING_TOOLS = [
    list_printing_workflows,
    describe_printing_workflow,
    list_printing_designs,
    validate_printing_request,
    preview_design_coordinates,
    build_printing_protocol,
    simulate_printing_protocol,
]


# Stage 3 primary agent boundary. The older workflow/config tools above remain
# callable for compatibility but are intentionally absent from the Printing Agent.
PRINT_JOB_TOOLS = [
    list_printing_capabilities,
    list_registered_substrates,
    list_registered_materials,
    create_and_compile_print_job,
    modify_and_compile_print_job,
    report_printing_request_issue,
]


PRINT_EXPERIMENT_TOOLS = [
    list_printing_capabilities,
    list_registered_substrates,
    list_registered_materials,
    draft_printing_experiment,
    describe_printing_experiment,
    revise_printing_experiment,
    approve_printing_experiment,
    reject_printing_experiment,
    prepare_approved_printing_experiment,
    report_printing_request_issue,
]


STANDARD_PRINT_EXPERIMENT_TOOLS = [
    list_standard_printing_experiment_capabilities,
    create_standard_printing_experiment_config,
    validate_standard_printing_experiment,
    resolve_standard_printing_experiment,
    inspect_standard_printing_layout,
    simulate_approved_standard_printing_experiment,
    report_printing_request_issue,
]


# --------------------------------------------------------------------------- #
# Four-clover experiment surface
#
# The same shape as the standard experiment surface above: the agent supplies
# scientific parameters, deterministic code validates them, resolves the
# coordinates with the frozen geometry engine, and renders the review. The agent
# never writes YAML text, never writes Python, and never computes a coordinate.
# --------------------------------------------------------------------------- #


class FourCloverProposalV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["four-clover-experiment-job/v1"] = (
        "four-clover-experiment-job/v1"
    )
    machine_profile: Literal[
        "configs/machines/ot2_four_clover_printing_p20_v1.yaml"
    ]
    experiment: CloverExperimentSpecV1


class FourCloverConfigInput(ToolInput):
    """High-level scientist configuration; never resolved droplet coordinates."""

    experiment_config: FourCloverProposalV1


class CreateFourCloverConfigInput(FourCloverConfigInput):
    output_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


class FourCloverValidationResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["PASS"] = "PASS"
    schema_version: Literal["four-clover-experiment-job/v1"]
    experiment_id: str
    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    machine_profile: str
    canonical_config_yaml: str


class FourCloverConfigArtifactV1(FourCloverValidationResultV1):
    path: str
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FourCloverResolutionResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["PASS"] = "PASS"
    experiment_id: str
    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    physical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    totals: dict[str, int | float]


class FourCloverPreviewResultV1(FourCloverResolutionResultV1):
    absolute_dispense_mm: float
    review: str
    resolved_coordinates: str


class FourCloverSimulationResultV1(FourCloverResolutionResultV1):
    simulation: Literal["PASS"] = "PASS"
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    deposit_count: int = Field(ge=0)
    resolved_coordinates: str


def _validated_clover_config(
    config: FourCloverProposalV1 | dict[str, Any],
) -> tuple[Any, FourCloverValidationResultV1]:
    proposal = FourCloverProposalV1.model_validate(config)
    normalized = proposal.model_dump(mode="json", exclude_none=True)
    job = load_clover_job_mapping(normalized)
    result = FourCloverValidationResultV1(
        schema_version=job.schema_version,
        experiment_id=job.experiment.metadata.experiment_id,
        job_sha256=job.job_id,
        machine_profile=proposal.machine_profile,
        canonical_config_yaml=yaml.safe_dump(
            normalized,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ),
    )
    return job, result


@tool
def list_four_clover_experiment_capabilities() -> str:
    """Describe the four-clover experiment vocabulary and its safety boundary."""
    return json.dumps(
        {
            "job_schema": "four-clover-experiment-job/v1",
            "plan_schema": "resolved-clover-plan/v1",
            "template": (
                "configs/templates/printing/02_printing_four_clover.template.yaml"
            ),
            "machine_profiles": [
                "configs/machines/ot2_four_clover_printing_p20_v1.yaml"
            ],
            "executor": "src/protocols/printing/02_printing_four_clover.py",
            "scientific_fields": [
                "metadata",
                "source",
                "printing.droplet_volume_ul",
                "printing.layers",
                "printing.inter_drop_delay_s",
                "printing.inter_layer_delay_s",
                "printing.inter_clover_delay_s",
                "printing.order",
                "default_geometry.half_width_mm",
                "default_geometry.half_height_mm",
                "clovers[].reference_well",
                "clovers[].x_offset_mm",
                "clovers[].y_offset_mm",
                "clovers[].geometry",
                "clovers[].layers",
            ],
            "machine_owned": [
                "deck slots",
                "labware load names and namespaces",
                "pipette identity and limits",
                "aspiration and park heights",
                "dispense standoff",
                "pre air chase, air gap, push out, blow out",
                "flow rates",
                "printable area bounds",
                "tip selection and return policy",
            ],
            "internal_only": [
                "droplet d1..d4 coordinates",
                "aspirate",
                "dispense",
                "move",
            ],
            "unsupported": [
                "dilution",
                "mixing",
                "more than one source liquid",
                "non-clover patterns such as rings or lines",
            ],
            "geometry_convention": (
                "half_width_mm and half_height_mm are offsets FROM THE CENTRE; "
                "opposing droplets end up twice that far apart, so a 3 mm "
                "separation is half_width_mm 1.5"
            ),
            "live_execution": False,
        },
        indent=2,
    )


@tool(args_schema=CreateFourCloverConfigInput)
def create_four_clover_experiment_config(
    experiment_config: FourCloverProposalV1, output_name: str
) -> str:
    """Validate and persist one immutable proposed four-clover experiment YAML."""
    _, validated = _validated_clover_config(experiment_config)
    data = validated.canonical_config_yaml.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    directory = FOUR_CLOVER_PROPOSAL_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{output_name}_{digest[:12]}.yaml"
    if path.exists() and path.read_bytes() != data:
        raise ValueError(
            "immutable proposal path already exists with different content"
        )
    path.write_bytes(data)
    try:
        artifact_path = str(path.relative_to(REPO_ROOT))
    except ValueError:  # test/injected stores may live outside the repo
        artifact_path = str(path)
    return FourCloverConfigArtifactV1(
        **validated.model_dump(mode="json"),
        path=artifact_path,
        file_sha256=digest,
    ).model_dump_json(indent=2)


@tool(args_schema=FourCloverConfigInput)
def validate_four_clover_experiment(experiment_config: FourCloverProposalV1) -> str:
    """Validate a proposed four-clover experiment against the registered profile."""
    _, result = _validated_clover_config(experiment_config)
    return result.model_dump_json(indent=2)


@tool(args_schema=FourCloverConfigInput)
def preview_four_clover_experiment(experiment_config: FourCloverProposalV1) -> str:
    """Resolve the patterns and render the four coordinates of every clover."""
    job, _ = _validated_clover_config(experiment_config)
    plan = resolve_clover_job(job)
    return FourCloverPreviewResultV1(
        experiment_id=plan.experiment_id,
        job_sha256=job.job_id,
        plan_sha256=plan.plan_id,
        physical_sha256=plan.physical_sha256(),
        totals=plan.totals.model_dump(mode="json"),
        absolute_dispense_mm=plan.absolute_dispense_mm,
        review=render_clover_review(plan),
        resolved_coordinates=render_clover_coordinates(plan),
    ).model_dump_json(indent=2)


@tool(args_schema=FourCloverConfigInput)
def simulate_four_clover_experiment(experiment_config: FourCloverProposalV1) -> str:
    """Locally simulate the frozen executor carrying this exact configuration.

    Simulation is local and read-only: it proves the plan is physically
    executable. It does not produce an upload-ready artifact and does not reach
    READY_FOR_EXECUTION - a human does that with
    ``scripts/run_printing_workflow.py``.
    """
    job, _ = _validated_clover_config(experiment_config)
    plan = resolve_clover_job(job)
    artifact = clover_builder.build_clover_protocol(plan)
    passed, output = clover_builder.simulate_clover_protocol(
        artifact.protocol_path, expected_sha256=artifact.protocol_sha256
    )
    if not passed:
        raise RuntimeError(f"local OT-2 simulation failed:\n{output[-2000:]}")
    return FourCloverSimulationResultV1(
        experiment_id=plan.experiment_id,
        job_sha256=job.job_id,
        plan_sha256=plan.plan_id,
        physical_sha256=plan.physical_sha256(),
        totals=plan.totals.model_dump(mode="json"),
        protocol_sha256=artifact.protocol_sha256,
        deposit_count=plan.totals.deposit_count,
        resolved_coordinates=render_clover_coordinates(plan),
    ).model_dump_json(indent=2)


FOUR_CLOVER_EXPERIMENT_TOOLS = [
    list_four_clover_experiment_capabilities,
    create_four_clover_experiment_config,
    validate_four_clover_experiment,
    preview_four_clover_experiment,
    simulate_four_clover_experiment,
    report_printing_request_issue,
]


#: One Printing Agent covers both families; the request selects the workflow.
def _printing_experiment_tools() -> list:
    seen: set[str] = set()
    ordered = []
    for item in (
        load_printing_experiment_template,
        *STANDARD_PRINT_EXPERIMENT_TOOLS,
        *FOUR_CLOVER_EXPERIMENT_TOOLS,
    ):
        if item.name in seen:
            continue
        seen.add(item.name)
        ordered.append(item)
    return ordered


#: The only two files the template reader may return. Not a filesystem tool.
PRINTING_EXPERIMENT_TEMPLATES = {
    "standard": "configs/templates/printing/01_printing_standard.template.yaml",
    "four_clover": "configs/templates/printing/02_printing_four_clover.template.yaml",
}


class LoadPrintingTemplateInput(ToolInput):
    workflow_family: Literal["standard", "four_clover"]


@tool(args_schema=LoadPrintingTemplateInput)
def load_printing_experiment_template(workflow_family: str) -> str:
    """Return the generalized configuration template for one workflow family.

    This is an allowlist of exactly two files, not filesystem access. The template
    teaches the configuration language; it is a placeholder example, never an
    approved experiment, and its values must not be copied into a real request.
    """
    reference = PRINTING_EXPERIMENT_TEMPLATES[workflow_family]
    return (Path(REPO_ROOT) / reference).read_text(encoding="utf-8")


PRINTING_EXPERIMENT_TOOLS = _printing_experiment_tools()
