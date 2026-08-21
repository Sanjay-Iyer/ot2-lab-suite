"""Stage 3 structured contract between the Printing Agent and deterministic code."""

from __future__ import annotations

from copy import deepcopy
from enum import Enum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .job_compiler import (
    PrintJobCompilationError,
    PrintJobPhysicalValidationError,
    compile_print_job,
)
from .references import (
    PrintingReferenceError,
    resolve_clover_placement_preset,
    resolve_registered_material,
    resolve_registered_substrate,
)
from .schemas.jobs import (
    CloverOrderingIntentV1,
    CloverReplicationV1,
    FourCloverCenterV1,
    FourCloverPatternV1,
    PrintJobV1,
    StandardOrderingIntentV1,
    WellPatternV1,
    WellReplicationV1,
)
from .schemas.models import FourCloverGeometry
from .schemas.plans import ResolvedPrintPlanV1, XYPointV1


class StrictAgentContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
        frozen=True,
    )


class AgentFourCloverPatternV1(StrictAgentContractModel):
    """Agent input with registry-resolvable geometry and center placement."""

    type: Literal["four_clover"] = "four_clover"
    geometry: FourCloverGeometry | None = None
    centers: list[FourCloverCenterV1] | None = Field(default=None, min_length=1)
    replicates: int = Field(default=1, ge=1)
    placement_preset: Literal["standard"] = "standard"
    layers: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _explicit_centers_match_replicates(self) -> Self:
        if self.centers is not None and len(self.centers) != self.replicates:
            raise ValueError("explicit clover centers must equal replicates")
        return self


AgentPrintPatternV1 = Annotated[
    WellPatternV1 | AgentFourCloverPatternV1,
    Field(discriminator="type"),
]


class PrintJobDraftV1(StrictAgentContractModel):
    """Scientific fields the model may submit; hashes and full references are absent."""

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


class PrintJobModificationV1(StrictAgentContractModel):
    """Bounded scientific changes applied by constructing a new canonical job."""

    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    substrate_id: str | None = None
    material_id: str | None = None
    volume_ul: float | None = None
    layers_by_row: dict[str, int] | None = None
    clover_geometry: FourCloverGeometry | None = None
    clover_centers: list[FourCloverCenterV1] | None = Field(default=None, min_length=1)
    clover_replicates: int | None = Field(default=None, ge=1)
    clover_placement_preset: Literal["standard"] = "standard"
    clover_layers: int | None = Field(default=None, ge=1)
    ordering_mode: (
        Literal[
            "layer_then_row_then_column",
            "clover_by_clover",
            "position_by_position",
        ]
        | None
    ) = None
    inter_layer_rest_minutes: float | None = Field(default=None, ge=0)
    metadata_updates: dict[str, str] = Field(default_factory=dict)


class PrintingErrorStage(str, Enum):
    INTERPRETATION = "interpretation"
    SCHEMA = "schema_validation"
    REFERENCE = "reference_resolution"
    COMPILER = "deterministic_compiler"
    PHYSICAL = "physical_plan_validation"
    SIMULATION = "simulation"


class PrintingAgentErrorV1(StrictAgentContractModel):
    stage: PrintingErrorStage
    code: str
    message: str
    details: list[str] = Field(default_factory=list)


class CloverPreviewV1(StrictAgentContractModel):
    name: str
    reference_well: str
    paper_center_mm: XYPointV1
    points_mm: dict[Literal["D1", "D2", "D3", "D4"], XYPointV1]


class ResolvedPlanPreviewV1(StrictAgentContractModel):
    pattern_type: Literal["well_selection", "four_clover"]
    target_wells: list[str] = Field(default_factory=list)
    layers_by_row: dict[str, int] = Field(default_factory=dict)
    clovers: list[CloverPreviewV1] = Field(default_factory=list)
    deposit_count: int = Field(ge=1)
    total_liquid_ul: float = Field(gt=0)
    replicate_count: int | None = Field(default=None, ge=1)
    design_instance_count: int | None = Field(default=None, ge=1)
    order_mode: str


class PrintingAgentResultV1(StrictAgentContractModel):
    status: Literal["success", "needs_clarification", "unsupported", "error"]
    job: PrintJobV1 | None = None
    job_id: str | None = None
    job_summary: str | None = None
    plan: ResolvedPrintPlanV1 | None = None
    plan_id: str | None = None
    plan_summary: str | None = None
    preview: ResolvedPlanPreviewV1 | None = None
    validation: Literal["PASS", "FAIL", "NOT_RUN"]
    warnings: list[str] = Field(default_factory=list)
    error: PrintingAgentErrorV1 | None = None


def construct_print_job(draft: PrintJobDraftV1) -> PrintJobV1:
    """Resolve registry facts and deterministically seal one scientific job."""
    substrate = resolve_registered_substrate(draft.substrate_id)
    material = resolve_registered_material(
        draft.material_id,
        pattern_type=draft.pattern.type,
    )
    if isinstance(draft.pattern, WellPatternV1):
        pattern: WellPatternV1 | FourCloverPatternV1 = draft.pattern
        replication = WellReplicationV1(replicates=len(pattern.columns))
        mode = draft.ordering_mode or "layer_then_row_then_column"
        ordering = StandardOrderingIntentV1(
            mode=mode,
            inter_layer_rest_minutes=(
                0.25
                if draft.inter_layer_rest_minutes is None
                else draft.inter_layer_rest_minutes
            ),
        )
    else:
        geometry = draft.pattern.geometry or FourCloverGeometry(
            half_width_mm=2.0,
            half_height_mm=2.0,
        )
        centers = draft.pattern.centers or resolve_clover_placement_preset(
            draft.pattern.replicates,
            preset=draft.pattern.placement_preset,
        )
        pattern = FourCloverPatternV1(
            geometry=geometry,
            centers=centers,
            layers=draft.pattern.layers,
        )
        replication = CloverReplicationV1(replicates=len(centers))
        ordering = CloverOrderingIntentV1(
            mode=draft.ordering_mode or "clover_by_clover"
        )

    return PrintJobV1.from_content(
        schema_version="print-job/v1",
        name=draft.name,
        description=draft.description,
        substrate=substrate,
        materials=[material],
        pattern=pattern,
        deposition={
            "material_id": material.material_id,
            "volume_ul": draft.volume_ul,
        },
        replication=replication,
        ordering_intent=ordering,
        metadata=draft.metadata,
    )


def preview_resolved_print_plan(plan: ResolvedPrintPlanV1) -> ResolvedPlanPreviewV1:
    """Derive a concise scientific preview exclusively from resolved plan data."""
    first = plan.deposits[0]
    if first.provenance.kind == "well_grid":
        targets: list[str] = []
        layers: dict[str, int] = {}
        for deposit in plan.deposits:
            target = deposit.destination.well
            if target not in targets:
                targets.append(target)
            row = deposit.provenance.row
            layers[row] = max(layers.get(row, 0), deposit.provenance.layer_index)
        return ResolvedPlanPreviewV1(
            pattern_type="well_selection",
            target_wells=targets,
            layers_by_row=layers,
            deposit_count=plan.totals.deposit_count,
            total_liquid_ul=plan.totals.total_liquid_ul,
            replicate_count=plan.totals.replicate_count,
            order_mode=plan.order_mode,
        )

    clover_records: dict[int, dict[str, Any]] = {}
    for deposit in plan.deposits:
        provenance = deposit.provenance
        destination = deposit.destination
        record = clover_records.setdefault(
            provenance.clover_index,
            {
                "name": provenance.clover_name,
                "reference_well": destination.reference_well,
                "paper_center_mm": destination.paper_center_xy_mm,
                "points_mm": {},
            },
        )
        record["points_mm"].setdefault(
            provenance.design_point,
            destination.paper_xy_mm,
        )
    return ResolvedPlanPreviewV1(
        pattern_type="four_clover",
        clovers=[
            CloverPreviewV1.model_validate(item) for item in clover_records.values()
        ],
        deposit_count=plan.totals.deposit_count,
        total_liquid_ul=plan.totals.total_liquid_ul,
        design_instance_count=plan.totals.clover_count,
        order_mode=plan.order_mode,
    )


def _success(job: PrintJobV1, plan: ResolvedPrintPlanV1) -> PrintingAgentResultV1:
    preview = preview_resolved_print_plan(plan)
    plan_summary = (
        f"Resolved plan\nDeposits: {plan.totals.deposit_count}\n"
        f"Total liquid: {plan.totals.total_liquid_ul:g} uL\n"
        f"Validated: PASS\nPlan ID: {plan.plan_id}"
    )
    return PrintingAgentResultV1(
        status="success",
        job=job,
        job_id=job.job_id,
        job_summary=job.summary(),
        plan=plan,
        plan_id=plan.plan_id,
        plan_summary=plan_summary,
        preview=preview,
        validation="PASS",
    )


def _error(
    stage: PrintingErrorStage, code: str, exc: Exception
) -> PrintingAgentResultV1:
    return PrintingAgentResultV1(
        status="error",
        validation="FAIL",
        error=PrintingAgentErrorV1(
            stage=stage,
            code=code,
            message=str(exc),
        ),
    )


def create_and_compile_draft(
    draft: PrintJobDraftV1 | dict[str, Any],
) -> PrintingAgentResultV1:
    """Structured, error-categorized construction and deterministic compilation."""
    try:
        validated = PrintJobDraftV1.model_validate(draft)
        job = construct_print_job(validated)
        return _success(job, compile_print_job(job))
    except ValidationError as exc:
        return _error(PrintingErrorStage.SCHEMA, "invalid_print_job", exc)
    except PrintingReferenceError as exc:
        return _error(PrintingErrorStage.REFERENCE, "unknown_reference", exc)
    except PrintJobPhysicalValidationError as exc:
        return _error(PrintingErrorStage.PHYSICAL, "invalid_physical_plan", exc)
    except PrintJobCompilationError as exc:
        return _error(PrintingErrorStage.COMPILER, "compilation_failed", exc)
    except (TypeError, ValueError) as exc:
        return _error(PrintingErrorStage.COMPILER, "compilation_failed", exc)


def _draft_from_existing(
    existing: PrintJobV1,
    changes: PrintJobModificationV1,
) -> PrintJobDraftV1:
    material_id = existing.deposition.material_id
    substrate_id = existing.substrate.load_name
    volume = existing.deposition.volume_ul
    name = existing.name
    description = existing.description
    metadata = deepcopy(existing.metadata)
    metadata.update(changes.metadata_updates)

    if changes.material_id is not None:
        material_id = changes.material_id
    if changes.substrate_id is not None:
        substrate_id = changes.substrate_id
    if changes.volume_ul is not None:
        volume = changes.volume_ul
    if changes.name is not None:
        name = changes.name
    if changes.description is not None:
        description = changes.description

    if existing.pattern.type == "well_selection":
        if any(
            value is not None
            for value in (
                changes.clover_geometry,
                changes.clover_centers,
                changes.clover_replicates,
                changes.clover_layers,
            )
        ):
            raise ValueError("clover modifications cannot be applied to a well job")
        layers = dict(existing.pattern.layers_by_row)
        if changes.layers_by_row is not None:
            layers.update(
                {
                    str(row).upper(): count
                    for row, count in changes.layers_by_row.items()
                }
            )
        pattern: AgentPrintPatternV1 = WellPatternV1(
            rows=list(existing.pattern.rows),
            columns=list(existing.pattern.columns),
            layers_by_row=layers,
        )
        mode = changes.ordering_mode or existing.ordering_intent.mode
        rest = (
            changes.inter_layer_rest_minutes
            if changes.inter_layer_rest_minutes is not None
            else existing.ordering_intent.inter_layer_rest_minutes
        )
    else:
        if changes.layers_by_row is not None:
            raise ValueError(
                "row-layer modifications cannot be applied to a clover job"
            )
        centers = changes.clover_centers
        replicates = changes.clover_replicates or len(existing.pattern.centers)
        if centers is None and changes.clover_replicates is None:
            centers = list(existing.pattern.centers)
        pattern = AgentFourCloverPatternV1(
            geometry=changes.clover_geometry or existing.pattern.geometry,
            centers=centers,
            replicates=replicates,
            placement_preset=changes.clover_placement_preset,
            layers=changes.clover_layers or existing.pattern.layers,
        )
        mode = changes.ordering_mode or existing.ordering_intent.mode
        rest = None

    return PrintJobDraftV1(
        name=name,
        description=description,
        substrate_id=substrate_id,
        material_id=material_id,
        pattern=pattern,
        volume_ul=volume,
        ordering_mode=mode,
        inter_layer_rest_minutes=rest,
        metadata=metadata,
    )


def modify_and_compile_job(
    existing_job: PrintJobV1 | dict[str, Any],
    changes: PrintJobModificationV1 | dict[str, Any],
) -> PrintingAgentResultV1:
    """Construct a new immutable job from a sealed job plus scientific changes."""
    try:
        existing = PrintJobV1.model_validate(existing_job)
        modification = PrintJobModificationV1.model_validate(changes)
        draft = _draft_from_existing(existing, modification)
    except ValidationError as exc:
        return _error(PrintingErrorStage.SCHEMA, "invalid_modification", exc)
    except (TypeError, ValueError) as exc:
        return _error(PrintingErrorStage.SCHEMA, "incompatible_modification", exc)
    return create_and_compile_draft(draft)


def interpretation_result(
    *,
    status: Literal["needs_clarification", "unsupported", "error"],
    code: str,
    message: str,
    details: list[str] | None = None,
) -> PrintingAgentResultV1:
    return PrintingAgentResultV1(
        status=status,
        validation="NOT_RUN",
        error=PrintingAgentErrorV1(
            stage=PrintingErrorStage.INTERPRETATION,
            code=code,
            message=message,
            details=details or [],
        ),
    )
