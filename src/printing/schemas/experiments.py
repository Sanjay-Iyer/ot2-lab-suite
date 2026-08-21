"""Experiment-independent schemas for the general standard printing workflow.

Two strict models live here.

``PrintExperimentJobV1``
    Scientist-owned experimental intent plus the machine profile it runs on.
    It says *what* experiment is wanted in scientific language: declared liquids,
    ordered preparation and printing steps, repeat counts, and delays. It never
    contains robot motion, tip choices, chunked transfer volumes, or protocol code.

``ResolvedExperimentPlanV1``
    The canonical ordered physical actions a deterministic executor performs.
    Every ambiguity in the job is already resolved here: dilution volumes,
    pipette-sized sub-transfers, tip groups, aspiration heights, and droplet
    release parameters.

The pre-existing :mod:`.jobs` / :mod:`.plans` pair remains the schema for the
single-material design-printing families (v9 well grid and v12 four clover). It
permits exactly one material and has no preparation, mixing, or multi-liquid
vocabulary, so it cannot express a standard printing experiment. These models are
its experiment-level generalization and reuse the same strict conventions and the
same canonical serialization in :mod:`src.printing.canonical`.
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ..canonical import canonical_json_bytes, canonical_sha256


WELL_PATTERN = r"^[A-H](?:[1-9]|1[0-2])$"
ID_PATTERN = r"^[a-z][a-z0-9_]*$"
LOAD_NAME_PATTERN = r"^[a-z0-9._]+$"

ROW_LETTERS = "ABCDEFGH"


class StrictExperimentModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
        frozen=True,
    )


# --------------------------------------------------------------------------- #
# Machine-owned profile
# --------------------------------------------------------------------------- #


class FlowRatesV1(StrictExperimentModel):
    aspirate_ul_s: float = Field(gt=0)
    dispense_ul_s: float = Field(gt=0)


class PipetteProfileV1(StrictExperimentModel):
    name: str = Field(min_length=1)
    mount: Literal["left", "right"]
    minimum_volume_ul: float = Field(gt=0)
    maximum_volume_ul: float = Field(gt=0)
    flow_rates: FlowRatesV1

    @model_validator(mode="after")
    def _range_is_ordered(self) -> Self:
        if self.minimum_volume_ul >= self.maximum_volume_ul:
            raise ValueError("pipette minimum_volume_ul must be below maximum_volume_ul")
        return self


class LabwareProfileV1(StrictExperimentModel):
    """One deck position and the calibrated heights the executor must use."""

    role: str = Field(pattern=ID_PATTERN)
    kind: Literal["source", "preparation", "substrate", "tiprack"]
    load_name: str = Field(pattern=LOAD_NAME_PATTERN)
    namespace: str = Field(pattern=LOAD_NAME_PATTERN)
    version: int = Field(ge=1)
    slot: int = Field(ge=1, le=11)
    well_diameter_mm: float | None = Field(default=None, gt=0)
    aspirate_reference: Literal["bottom", "top"] = "bottom"
    aspirate_height_mm: float | None = None
    dispense_reference: Literal["bottom", "top"] = "top"
    dispense_height_mm: float | None = None

    @model_validator(mode="after")
    def _liquid_labware_is_calibrated(self) -> Self:
        if self.kind == "tiprack":
            return self
        if self.aspirate_height_mm is None or self.dispense_height_mm is None:
            raise ValueError(
                f"labware role {self.role!r} must declare aspirate and dispense heights"
            )
        if self.kind in {"source", "preparation"} and self.well_diameter_mm is None:
            raise ValueError(
                f"labware role {self.role!r} must declare well_diameter_mm "
                "for source-accessibility checks"
            )
        return self


class PrintReleaseProfileV1(StrictExperimentModel):
    """Physically validated droplet release behaviour for the substrate."""

    dispense_reference: Literal["bottom", "top"] = "bottom"
    dispense_height_mm: float = Field(ge=0)
    pre_air_chase_ul: float = Field(default=0.0, ge=0)
    trailing_air_gap_ul: float = Field(default=0.0, ge=0)
    air_gap_height_mm: float = Field(default=0.0, ge=0)
    push_out_ul: float = Field(default=0.0, ge=0)
    blow_out: bool = True
    post_dispense_delay_s: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _only_supported_air_handling_is_allowed(self) -> Self:
        if self.pre_air_chase_ul != 0:
            raise ValueError(
                "pre_air_chase_ul is not supported by resolved-experiment-plan/v1; "
                "use 0 until the action schema and executor explicitly implement it"
            )
        return self


class MachineProfileV1(StrictExperimentModel):
    """Everything the laboratory owns; no scientific meaning appears here."""

    robot_type: Literal["OT-2"] = "OT-2"
    api_level: Literal["2.15"] = "2.15"
    pipette: PipetteProfileV1
    labware: list[LabwareProfileV1] = Field(min_length=2)
    print_release: PrintReleaseProfileV1

    @model_validator(mode="after")
    def _deck_is_coherent(self) -> Self:
        roles = [item.role for item in self.labware]
        if len(roles) != len(set(roles)):
            raise ValueError("labware roles must be unique")
        slots = [item.slot for item in self.labware]
        if len(slots) != len(set(slots)):
            raise ValueError("labware deck slots must be unique")
        if sum(1 for item in self.labware if item.kind == "tiprack") != 1:
            raise ValueError("exactly one tiprack labware entry is required")
        if not any(item.kind == "substrate" for item in self.labware):
            raise ValueError("at least one substrate labware entry is required")
        return self

    def role(self, name: str) -> LabwareProfileV1:
        for item in self.labware:
            if item.role == name:
                return item
        raise KeyError(f"unknown labware role {name!r}")

    @property
    def tiprack(self) -> LabwareProfileV1:
        return next(item for item in self.labware if item.kind == "tiprack")


# --------------------------------------------------------------------------- #
# Science-owned experiment
# --------------------------------------------------------------------------- #


class WellLocationV1(StrictExperimentModel):
    labware: str = Field(pattern=ID_PATTERN)
    well: str = Field(pattern=WELL_PATTERN)


class LiquidSourceV1(StrictExperimentModel):
    """A liquid the scientist physically places on the deck before the run."""

    liquid_id: str = Field(pattern=ID_PATTERN)
    display_name: str = Field(min_length=1)
    location: WellLocationV1
    loaded_volume_ul: float = Field(gt=0)
    minimum_remaining_ul: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _reserve_is_available(self) -> Self:
        if self.minimum_remaining_ul >= self.loaded_volume_ul:
            raise ValueError(
                f"liquid {self.liquid_id!r} reserves at least as much as it loads"
            )
        return self


class MixSpecV1(StrictExperimentModel):
    cycles: int = Field(ge=1)
    volume_ul: float = Field(gt=0)


class _StepBase(StrictExperimentModel):
    id: str = Field(pattern=ID_PATTERN)
    description: str | None = None


class SerialDilutionStepV1(_StepBase):
    """N-point geometric dilution series retaining a usable volume in every well."""

    type: Literal["serial_dilution"] = "serial_dilution"
    source_liquid_id: str = Field(pattern=ID_PATTERN)
    diluent_liquid_id: str = Field(pattern=ID_PATTERN)
    destination_labware: str = Field(pattern=ID_PATTERN)
    destination_wells: list[str] = Field(min_length=1)
    fold: int = Field(ge=2)
    target_usable_volume_ul: float = Field(gt=0)
    mix: MixSpecV1 | None = None
    product_liquid_ids: list[str] | None = None

    @field_validator("destination_wells")
    @classmethod
    def _wells_are_distinct(cls, wells: list[str]) -> list[str]:
        upper = [well.upper() for well in wells]
        if len(upper) != len(set(upper)):
            raise ValueError("destination_wells must not repeat a well")
        return upper

    @model_validator(mode="after")
    def _series_is_coherent(self) -> Self:
        if self.source_liquid_id == self.diluent_liquid_id:
            raise ValueError("a dilution series cannot use its source as its diluent")
        if self.product_liquid_ids is not None:
            if len(self.product_liquid_ids) != len(self.destination_wells):
                raise ValueError(
                    "product_liquid_ids must name exactly one liquid per destination well"
                )
            if len(set(self.product_liquid_ids)) != len(self.product_liquid_ids):
                raise ValueError("product_liquid_ids must be unique")
        return self

    @property
    def factors(self) -> tuple[int, ...]:
        return tuple(self.fold**index for index in range(len(self.destination_wells)))

    def products(self) -> tuple[str, ...]:
        if self.product_liquid_ids is not None:
            return tuple(self.product_liquid_ids)
        return tuple(
            f"{self.id}_stock" if factor == 1 else f"{self.id}_1_{factor}x"
            for factor in self.factors
        )


class DilutionPointV1(StrictExperimentModel):
    well: str = Field(pattern=WELL_PATTERN)
    factor: float = Field(ge=1)
    total_volume_ul: float = Field(gt=0)
    product_liquid_id: str | None = Field(default=None, pattern=ID_PATTERN)


class DirectDilutionStepV1(_StepBase):
    """Independent dilutions made straight from one stock, with no cascade."""

    type: Literal["direct_dilution"] = "direct_dilution"
    source_liquid_id: str = Field(pattern=ID_PATTERN)
    diluent_liquid_id: str = Field(pattern=ID_PATTERN)
    destination_labware: str = Field(pattern=ID_PATTERN)
    points: list[DilutionPointV1] = Field(min_length=1)
    mix: MixSpecV1 | None = None

    @model_validator(mode="after")
    def _points_are_distinct(self) -> Self:
        wells = [point.well for point in self.points]
        if len(wells) != len(set(wells)):
            raise ValueError("direct dilution points must not repeat a well")
        if self.source_liquid_id == self.diluent_liquid_id:
            raise ValueError("a dilution cannot use its source as its diluent")
        return self

    def products(self) -> tuple[str, ...]:
        return tuple(
            point.product_liquid_id
            or (
                f"{self.id}_stock"
                if point.factor == 1
                else f"{self.id}_1_{point.factor:g}x"
            )
            for point in self.points
        )


class TransferStepV1(_StepBase):
    """Move one declared liquid into one well, split to pipette-sized chunks."""

    type: Literal["transfer"] = "transfer"
    liquid_id: str = Field(pattern=ID_PATTERN)
    source: WellLocationV1 | None = None
    destination: WellLocationV1
    volume_ul: float = Field(gt=0)
    result_liquid_id: str | None = Field(default=None, pattern=ID_PATTERN)
    mix_after: MixSpecV1 | None = None


class MixStepV1(_StepBase):
    type: Literal["mix"] = "mix"
    liquid_id: str = Field(pattern=ID_PATTERN)
    location: WellLocationV1
    cycles: int = Field(ge=1)
    volume_ul: float = Field(gt=0)


class SeriesPrintSourceV1(StrictExperimentModel):
    """Print one prepared series, one product per target, in declared order."""

    kind: Literal["series"] = "series"
    preparation_id: str = Field(pattern=ID_PATTERN)


class LiquidPrintSourceV1(StrictExperimentModel):
    """Print the same liquid onto every target."""

    kind: Literal["liquid"] = "liquid"
    liquid_id: str = Field(pattern=ID_PATTERN)


PrintSourceV1 = Annotated[
    SeriesPrintSourceV1 | LiquidPrintSourceV1,
    Field(discriminator="kind"),
]


class PrintStepV1(_StepBase):
    """Deposit droplets onto substrate wells, optionally repeated with spacing."""

    type: Literal["print"] = "print"
    source: PrintSourceV1
    substrate: str = Field(pattern=ID_PATTERN)
    targets: list[str] = Field(min_length=1)
    volume_ul: float = Field(gt=0)
    repeats: int = Field(default=1, ge=1)
    delay_after_pass_s: float = Field(default=0.0, ge=0)
    mix_before_aspirate: MixSpecV1 | None = None
    tip_policy: Literal["per_target", "per_pass", "per_step"] = "per_step"
    purpose: Literal["sample", "control"] = "sample"

    @field_validator("targets")
    @classmethod
    def _targets_are_distinct(cls, targets: list[str]) -> list[str]:
        upper = [target.upper() for target in targets]
        if len(upper) != len(set(upper)):
            raise ValueError("a print step must not repeat a target within itself")
        for target in upper:
            if target[0] not in ROW_LETTERS or not target[1:].isdigit():
                raise ValueError(f"invalid substrate target {target!r}")
        return upper

    @model_validator(mode="after")
    def _series_requires_isolated_targets(self) -> Self:
        if self.source.kind == "series" and self.tip_policy != "per_target":
            raise ValueError(
                "printing a series requires tip_policy='per_target' so different "
                "prepared liquids cannot cross-contaminate"
            )
        return self


class DelayStepV1(_StepBase):
    type: Literal["delay"] = "delay"
    duration_s: float = Field(ge=0)
    reason: str = Field(min_length=1)


ProcedureStepV1 = Annotated[
    SerialDilutionStepV1
    | DirectDilutionStepV1
    | TransferStepV1
    | MixStepV1
    | PrintStepV1
    | DelayStepV1,
    Field(discriminator="type"),
]


class ExperimentMetadataV1(StrictExperimentModel):
    experiment_id: str = Field(pattern=ID_PATTERN)
    title: str = Field(min_length=1)
    description: str | None = None
    notes: dict[str, str] = Field(default_factory=dict)


class ExperimentPoliciesV1(StrictExperimentModel):
    """Safety rules the resolver enforces, defaulting to the conservative choice."""

    require_drying_delay_between_deposits: bool = True


class ExperimentSpecV1(StrictExperimentModel):
    metadata: ExperimentMetadataV1
    policies: ExperimentPoliciesV1 = Field(default_factory=ExperimentPoliciesV1)
    liquids: list[LiquidSourceV1] = Field(min_length=1)
    procedure: list[ProcedureStepV1] = Field(min_length=1)

    @model_validator(mode="after")
    def _identifiers_are_unique(self) -> Self:
        liquid_ids = [liquid.liquid_id for liquid in self.liquids]
        if len(liquid_ids) != len(set(liquid_ids)):
            raise ValueError("liquid_id values must be unique")
        step_ids = [step.id for step in self.procedure]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("procedure step ids must be unique")
        return self


class PrintExperimentJobV1(StrictExperimentModel):
    """A complete, strictly validated standard printing experiment request."""

    schema_version: Literal["print-experiment-job/v1"] = "print-experiment-job/v1"
    job_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    machine: MachineProfileV1
    experiment: ExperimentSpecV1

    @classmethod
    def from_content(cls, **content: Any) -> Self:
        if "job_id" in content:
            raise ValueError("job_id is derived and cannot be supplied to from_content")
        provisional = cls.model_validate(
            {"job_id": "0" * 64, **content},
            context={"skip_job_id_validation": True},
        )
        payload = provisional.model_dump(mode="json", exclude={"job_id"})
        return cls.model_validate({"job_id": provisional.job_sha256(), **payload})

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"job_id"})

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_payload())

    def job_sha256(self) -> str:
        return canonical_sha256(self.canonical_payload())

    def liquid(self, liquid_id: str) -> LiquidSourceV1:
        for liquid in self.experiment.liquids:
            if liquid.liquid_id == liquid_id:
                return liquid
        raise KeyError(f"unknown liquid_id {liquid_id!r}")

    def step(self, step_id: str) -> Any:
        for step in self.experiment.procedure:
            if step.id == step_id:
                return step
        raise KeyError(f"unknown procedure step id {step_id!r}")

    @model_validator(mode="after")
    def _job_id_matches(self, info: ValidationInfo) -> Self:
        skip = bool(info.context and info.context.get("skip_job_id_validation", False))
        if not skip and self.job_id != self.job_sha256():
            raise ValueError("job_id does not match the canonical job SHA-256")
        return self


_JOB_ADAPTER = TypeAdapter(PrintExperimentJobV1)


def parse_print_experiment_job(payload: dict[str, Any]) -> PrintExperimentJobV1:
    return _JOB_ADAPTER.validate_python(payload)


def print_experiment_job_artifact_json(job: PrintExperimentJobV1) -> str:
    return (
        json.dumps(
            job.model_dump(mode="json"), indent=2, ensure_ascii=False, allow_nan=False
        )
        + "\n"
    )


# --------------------------------------------------------------------------- #
# Canonical resolved physical actions
# --------------------------------------------------------------------------- #


class PhysicalLocationV1(StrictExperimentModel):
    """An unambiguous physical point: labware role, well, reference, offset."""

    labware: str = Field(pattern=ID_PATTERN)
    well: str = Field(pattern=WELL_PATTERN)
    reference: Literal["bottom", "top"]
    z_mm: float


class LoadLabwareActionV1(StrictExperimentModel):
    sequence_index: int = Field(ge=1)
    action: Literal["LOAD_LABWARE"] = "LOAD_LABWARE"
    role: str = Field(pattern=ID_PATTERN)
    slot: int = Field(ge=1, le=11)
    load_name: str = Field(pattern=LOAD_NAME_PATTERN)
    namespace: str = Field(pattern=LOAD_NAME_PATTERN)
    version: int = Field(ge=1)


class LoadPipetteActionV1(StrictExperimentModel):
    sequence_index: int = Field(ge=1)
    action: Literal["LOAD_PIPETTE"] = "LOAD_PIPETTE"
    pipette: str = Field(min_length=1)
    mount: Literal["left", "right"]
    tiprack_role: str = Field(pattern=ID_PATTERN)
    minimum_volume_ul: float = Field(gt=0)
    maximum_volume_ul: float = Field(gt=0)
    flow_rates: FlowRatesV1


class TransferActionV1(StrictExperimentModel):
    sequence_index: int = Field(ge=1)
    action: Literal["TRANSFER"] = "TRANSFER"
    operation_id: str = Field(min_length=1)
    liquid_id: str = Field(pattern=ID_PATTERN)
    result_liquid_id: str = Field(pattern=ID_PATTERN)
    source: PhysicalLocationV1
    destination: PhysicalLocationV1
    volume_ul: float = Field(gt=0)
    chunk_index: int = Field(ge=1)
    chunk_count: int = Field(ge=1)
    pipette: str = Field(min_length=1)
    tip_group: str = Field(min_length=1)

    @model_validator(mode="after")
    def _chunk_is_within_count(self) -> Self:
        if self.chunk_index > self.chunk_count:
            raise ValueError("chunk_index cannot exceed chunk_count")
        return self


class MixActionV1(StrictExperimentModel):
    sequence_index: int = Field(ge=1)
    action: Literal["MIX"] = "MIX"
    operation_id: str = Field(min_length=1)
    liquid_id: str = Field(pattern=ID_PATTERN)
    location: PhysicalLocationV1
    cycles: int = Field(ge=1)
    volume_ul: float = Field(gt=0)
    pipette: str = Field(min_length=1)
    tip_group: str = Field(min_length=1)


class PrintActionV1(StrictExperimentModel):
    sequence_index: int = Field(ge=1)
    action: Literal["PRINT"] = "PRINT"
    operation_id: str = Field(min_length=1)
    condition_id: str = Field(min_length=1)
    liquid_id: str = Field(pattern=ID_PATTERN)
    source: PhysicalLocationV1
    destination: PhysicalLocationV1
    volume_ul: float = Field(gt=0)
    air_gap_ul: float = Field(ge=0)
    air_gap_height_mm: float = Field(ge=0)
    piston_dispense_ul: float = Field(gt=0)
    push_out_ul: float = Field(ge=0)
    blow_out: bool
    post_dispense_delay_s: float = Field(ge=0)
    pipette: str = Field(min_length=1)
    drop_index: int = Field(ge=1)
    repeat_count: int = Field(ge=1)
    tip_group: str = Field(min_length=1)

    @model_validator(mode="after")
    def _drop_is_within_repeats(self) -> Self:
        if self.drop_index > self.repeat_count:
            raise ValueError("drop_index cannot exceed repeat_count")
        return self


class DelayActionV1(StrictExperimentModel):
    sequence_index: int = Field(ge=1)
    action: Literal["DELAY"] = "DELAY"
    operation_id: str = Field(min_length=1)
    duration_s: float = Field(ge=0)
    reason: str = Field(min_length=1)


ResolvedActionV1 = Annotated[
    LoadLabwareActionV1
    | LoadPipetteActionV1
    | TransferActionV1
    | MixActionV1
    | PrintActionV1
    | DelayActionV1,
    Field(discriminator="action"),
]


class InitialLiquidV1(StrictExperimentModel):
    liquid_id: str = Field(pattern=ID_PATTERN)
    display_name: str = Field(min_length=1)
    location: PhysicalLocationV1
    loaded_volume_ul: float = Field(gt=0)
    minimum_remaining_ul: float = Field(ge=0)
    scientific_allocation_ul: float = Field(ge=0)


class PlanTotalsV1(StrictExperimentModel):
    action_count: int = Field(ge=1)
    transfer_count: int = Field(ge=0)
    mix_count: int = Field(ge=0)
    print_count: int = Field(ge=0)
    delay_count: int = Field(ge=0)
    configured_experimental_delay_s: float = Field(ge=0)
    printed_liquid_ul: float = Field(ge=0)
    tip_count: int = Field(ge=0)


class SourceAccessibilityV1(StrictExperimentModel):
    status: Literal["PASS"]
    checked_aspirate_or_mix_actions: int = Field(ge=0)
    minimum_nominal_submerged_margin_ul: float
    ending_volumes_ul: dict[str, float]
    geometry: dict[str, float]


class ResolvedExperimentPlanV1(StrictExperimentModel):
    """Canonical, ordered, unambiguous physical actions for one experiment."""

    schema_version: Literal["resolved-experiment-plan/v1"] = "resolved-experiment-plan/v1"
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    job_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    experiment_id: str = Field(pattern=ID_PATTERN)
    initial_liquids: list[InitialLiquidV1] = Field(min_length=1)
    preparation_math: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[ResolvedActionV1] = Field(min_length=1)
    totals: PlanTotalsV1
    source_accessibility: SourceAccessibilityV1

    @classmethod
    def from_content(cls, **content: Any) -> Self:
        if "plan_id" in content:
            raise ValueError("plan_id is derived and cannot be supplied to from_content")
        provisional = cls.model_validate(
            {"plan_id": "0" * 64, **content},
            context={"skip_plan_id_validation": True},
        )
        payload = provisional.model_dump(mode="json", exclude={"plan_id"})
        return cls.model_validate({"plan_id": provisional.plan_sha256(), **payload})

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"plan_id"})

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_payload())

    def plan_sha256(self) -> str:
        return canonical_sha256(self.canonical_payload())

    def action_dicts(self) -> list[dict[str, Any]]:
        return [action.model_dump(mode="json") for action in self.actions]

    @model_validator(mode="after")
    def _plan_is_sequenced(self, info: ValidationInfo) -> Self:
        for expected, action in enumerate(self.actions, start=1):
            if action.sequence_index != expected:
                raise ValueError(
                    f"actions must be numbered 1..n in order; found "
                    f"{action.sequence_index} at position {expected}"
                )
        counts = {
            "TRANSFER": self.totals.transfer_count,
            "MIX": self.totals.mix_count,
            "PRINT": self.totals.print_count,
            "DELAY": self.totals.delay_count,
        }
        for name, expected_count in counts.items():
            actual = sum(1 for action in self.actions if action.action == name)
            if actual != expected_count:
                raise ValueError(
                    f"totals.{name.lower()}_count is {expected_count} but the plan holds {actual}"
                )
        if self.totals.action_count != len(self.actions):
            raise ValueError("totals.action_count does not match the action list")
        declared = {liquid.liquid_id for liquid in self.initial_liquids}
        if len(declared) != len(self.initial_liquids):
            raise ValueError("initial_liquids must declare unique liquid ids")
        skip = bool(info.context and info.context.get("skip_plan_id_validation", False))
        if not skip and self.plan_id != self.plan_sha256():
            raise ValueError("plan_id does not match the canonical plan SHA-256")
        return self


_PLAN_ADAPTER = TypeAdapter(ResolvedExperimentPlanV1)


def parse_resolved_experiment_plan(payload: dict[str, Any]) -> ResolvedExperimentPlanV1:
    return _PLAN_ADAPTER.validate_python(payload)


def resolved_experiment_plan_artifact_json(plan: ResolvedExperimentPlanV1) -> str:
    return (
        json.dumps(
            plan.model_dump(mode="json"), indent=2, ensure_ascii=False, allow_nan=False
        )
        + "\n"
    )
