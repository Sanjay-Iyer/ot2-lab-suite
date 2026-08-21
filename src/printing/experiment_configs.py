"""Persistent, scientist-facing Stage 4 printing experiment configurations.

The YAML handled here is the approval artifact.  It records scientific meaning
and logical labware positions, while trusted workflow YAML and Python continue to
own hardware parameters and robot motion.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from .canonical import canonical_json_bytes, canonical_sha256
from .config import REPO_ROOT
from .job_compiler import printing_request_from_job
from .references import resolve_registered_material, resolve_registered_substrate
from .references import PrintingReferenceError
from .artifacts import prepare_printing_request
from .schemas.jobs import (
    CloverOrderingIntentV1,
    CloverReplicationV1,
    FourCloverCenterV1,
    FourCloverPatternV1,
    MaterialReferenceV1,
    PrintJobV1,
    StandardOrderingIntentV1,
    WellPatternV1,
    WellReplicationV1,
)
from .schemas.models import FourCloverGeometry, PrintingFamily


EXPERIMENT_DIR = REPO_ROOT / "configs" / "experiments"
TEMPLATE_DIR = REPO_ROOT / "configs" / "templates" / "printing"
WELL_PATTERN = r"^[A-Ha-h](?:[1-9]|1[0-2])$"
MATERIAL_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_.-]*$"


class ExperimentConfigError(ValueError):
    """Base error for persistent experiment configuration operations."""


class ExperimentConfigSchemaError(ExperimentConfigError):
    """The human-facing YAML or deterministic draft is structurally invalid."""


class ExperimentConfigReferenceError(ExperimentConfigError):
    """A template, material, substrate, or file reference cannot be resolved."""


class ExperimentConfigPhysicalValidationError(ExperimentConfigError):
    """Scientific intent cannot pass the trusted workflow's physical constraints."""


class StrictExperimentModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
        frozen=True,
    )


class ExperimentIdentityV1(StrictExperimentModel):
    name: str = Field(min_length=1)
    description: str | None = None
    version: int = Field(ge=1)
    parent_config_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    template_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*/v[1-9][0-9]*$")


class ExperimentWorkflowReferenceV1(StrictExperimentModel):
    family: PrintingFamily
    name: str = Field(min_length=1)
    design_name: str | None = None

    @model_validator(mode="after")
    def _family_matches_design(self) -> Self:
        if self.family == PrintingFamily.STANDARD and self.design_name is not None:
            raise ValueError("standard workflows cannot declare design_name")
        if self.family == PrintingFamily.DESIGN and not self.design_name:
            raise ValueError("design workflows require design_name")
        return self


class ExperimentSubstrateV1(StrictExperimentModel):
    labware_id: str = Field(min_length=1)


class ExperimentMaterialV1(StrictExperimentModel):
    material_id: str = Field(pattern=MATERIAL_ID_PATTERN)
    display_name: str = Field(min_length=1)


class StandardConditionV1(StrictExperimentModel):
    name: str = Field(min_length=1)
    drops_per_position: int = Field(ge=1)
    wells: list[str] = Field(min_length=1)

    @field_validator("wells", mode="before")
    @classmethod
    def _uppercase_wells(cls, values: Any) -> Any:
        if isinstance(values, list):
            return [str(value).upper() for value in values]
        return values

    @field_validator("wells")
    @classmethod
    def _validate_wells(cls, values: list[str]) -> list[str]:
        if any(re.fullmatch(WELL_PATTERN, value) is None for value in values):
            raise ValueError("condition wells must be valid paper positions A1-H12")
        if len(values) != len(set(values)):
            raise ValueError("condition wells must not contain duplicates")
        rows = {value[0] for value in values}
        if len(rows) != 1:
            raise ValueError("each standard condition must occupy exactly one paper row")
        return values


class StandardConditionsLayoutV1(StrictExperimentModel):
    kind: Literal["well_conditions"] = "well_conditions"
    conditions: list[StandardConditionV1] = Field(min_length=1)

    @model_validator(mode="after")
    def _rectangular_non_overlapping_layout(self) -> Self:
        names = [condition.name for condition in self.conditions]
        if len(names) != len(set(names)):
            raise ValueError("condition names must be unique")
        wells = [well for condition in self.conditions for well in condition.wells]
        if len(wells) != len(set(wells)):
            raise ValueError("conditions must not share paper positions")
        rows = [condition.wells[0][0] for condition in self.conditions]
        if len(rows) != len(set(rows)):
            raise ValueError("standard conditions must use distinct paper rows")
        columns = [[int(well[1:]) for well in condition.wells] for condition in self.conditions]
        if any(item != columns[0] for item in columns[1:]):
            raise ValueError(
                "the proven v9 workflow requires every condition to use the same replicate columns"
            )
        return self


class FourCloverLayoutV1(StrictExperimentModel):
    kind: Literal["four_clover"] = "four_clover"
    geometry: FourCloverGeometry
    centers: list[FourCloverCenterV1] = Field(min_length=1)
    layers: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _unique_centers(self) -> Self:
        names = [center.name for center in self.centers]
        if len(names) != len(set(names)):
            raise ValueError("clover center names must be unique")
        return self


ExperimentLayoutV1 = Annotated[
    StandardConditionsLayoutV1 | FourCloverLayoutV1,
    Field(discriminator="kind"),
]


class ExperimentPrintingV1(StrictExperimentModel):
    droplet_volume_ul: float = Field(gt=0)
    layout: ExperimentLayoutV1
    ordering_mode: Literal[
        "layer_then_row_then_column",
        "clover_by_clover",
        "position_by_position",
    ]
    inter_layer_rest_minutes: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _ordering_matches_layout(self) -> Self:
        if self.layout.kind == "well_conditions":
            if self.ordering_mode != "layer_then_row_then_column":
                raise ValueError("well conditions require layer_then_row_then_column ordering")
        elif self.ordering_mode == "layer_then_row_then_column":
            raise ValueError("four-clover layouts require a clover ordering mode")
        return self


class PrintingExperimentConfigV1(StrictExperimentModel):
    schema_version: Literal["printing-experiment/v1"] = "printing-experiment/v1"
    experiment: ExperimentIdentityV1
    workflow: ExperimentWorkflowReferenceV1
    substrate: ExperimentSubstrateV1
    material: ExperimentMaterialV1
    printing: ExperimentPrintingV1

    @model_validator(mode="after")
    def _workflow_matches_layout(self) -> Self:
        expected = (
            PrintingFamily.STANDARD
            if self.printing.layout.kind == "well_conditions"
            else PrintingFamily.DESIGN
        )
        if self.workflow.family != expected:
            raise ValueError("workflow family does not match the experiment layout")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def config_sha256(self) -> str:
        return canonical_sha256(self.canonical_payload())


class TemplateDefaultsV1(StrictExperimentModel):
    substrate_id: str = Field(min_length=1)
    material_id: str = Field(pattern=MATERIAL_ID_PATTERN)
    material_display_name: str = Field(min_length=1)
    droplet_volume_ul: float = Field(gt=0)
    ordering_mode: Literal[
        "layer_then_row_then_column",
        "clover_by_clover",
        "position_by_position",
    ]
    inter_layer_rest_minutes: float | None = Field(default=None, ge=0)


class TemplateConstraintsV1(StrictExperimentModel):
    allowed_layout: Literal["well_conditions", "four_clover"]
    maximum_positions: int = Field(ge=1)


class PrintingExperimentTemplateV1(StrictExperimentModel):
    schema_version: Literal["printing-experiment-template/v1"] = (
        "printing-experiment-template/v1"
    )
    template_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*/v[1-9][0-9]*$")
    description: str = Field(min_length=1)
    workflow: ExperimentWorkflowReferenceV1
    capability_config_reference: str = Field(min_length=1)
    defaults: TemplateDefaultsV1
    constraints: TemplateConstraintsV1


class PrintingExperimentDraftV1(StrictExperimentModel):
    name: str = Field(min_length=1)
    description: str | None = None
    material_id: str | None = Field(default=None, pattern=MATERIAL_ID_PATTERN)
    material_display_name: str | None = Field(default=None, min_length=1)
    substrate_id: str | None = Field(default=None, min_length=1)
    droplet_volume_ul: float | None = Field(default=None, gt=0)
    layout: ExperimentLayoutV1
    ordering_mode: Literal[
        "layer_then_row_then_column",
        "clover_by_clover",
        "position_by_position",
    ] | None = None
    inter_layer_rest_minutes: float | None = Field(default=None, ge=0)


class PrintingExperimentRevisionV1(StrictExperimentModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    material_id: str | None = Field(default=None, pattern=MATERIAL_ID_PATTERN)
    material_display_name: str | None = Field(default=None, min_length=1)
    substrate_id: str | None = Field(default=None, min_length=1)
    droplet_volume_ul: float | None = Field(default=None, gt=0)
    layout: ExperimentLayoutV1 | None = None
    ordering_mode: Literal[
        "layer_then_row_then_column",
        "clover_by_clover",
        "position_by_position",
    ] | None = None
    inter_layer_rest_minutes: float | None = Field(default=None, ge=0)


class ExperimentConfigSummaryV1(StrictExperimentModel):
    experiment_name: str
    workflow_name: str
    substrate: str
    material: str
    droplet_volume_ul: float
    condition_to_wells: dict[str, list[str]] = Field(default_factory=dict)
    drops_by_condition: dict[str, int] = Field(default_factory=dict)
    unique_locations: int = Field(ge=1)
    total_deposition_events: int = Field(ge=1)
    total_liquid_ul: float = Field(gt=0)
    config_version: int = Field(ge=1)
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_config_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    validation: Literal["PASS"] = "PASS"

    def as_text(self, *, path: str | None = None) -> str:
        lines = [
            f"Experiment: {self.experiment_name}",
            f"Workflow: {self.workflow_name}",
            f"Substrate: {self.substrate}",
            f"Material: {self.material}",
            f"Droplet volume: {self.droplet_volume_ul:g} uL",
        ]
        for name, wells in self.condition_to_wells.items():
            drops = self.drops_by_condition[name]
            lines.append(f"{name} ({drops} drop{'s' if drops != 1 else ''}): {', '.join(wells)}")
        lines.extend(
            [
                f"Unique locations: {self.unique_locations}",
                f"Total deposition events: {self.total_deposition_events}",
                f"Total liquid: {self.total_liquid_ul:g} uL",
                f"Configuration: {path}" if path else "",
                f"Config SHA-256: {self.config_sha256}",
                "Validation: PASS",
            ]
        )
        return "\n".join(line for line in lines if line)


class ExperimentConfigArtifactV1(StrictExperimentModel):
    path: str
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config: PrintingExperimentConfigV1
    job: PrintJobV1
    summary: ExperimentConfigSummaryV1
    validation: Literal["PASS"] = "PASS"


_CONFIG_ADAPTER = TypeAdapter(PrintingExperimentConfigV1)
_TEMPLATE_FILES = {
    "standard_paper_printing/v1": TEMPLATE_DIR / "standard_paper_printing.yaml",
    "four_clover_printing/v1": TEMPLATE_DIR / "four_clover_printing.yaml",
}


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExperimentConfigReferenceError(f"configuration file does not exist: {path}") from exc
    except yaml.YAMLError as exc:
        raise ExperimentConfigSchemaError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExperimentConfigSchemaError(f"YAML document must be a mapping: {path}")
    return payload


def load_experiment_template(template_id: str) -> PrintingExperimentTemplateV1:
    try:
        path = _TEMPLATE_FILES[template_id]
    except KeyError as exc:
        raise ExperimentConfigReferenceError(
            f"unknown printing experiment template {template_id!r}; available: {', '.join(sorted(_TEMPLATE_FILES))}"
        ) from exc
    template = PrintingExperimentTemplateV1.model_validate(_load_yaml_mapping(path))
    capability = (REPO_ROOT / template.capability_config_reference).resolve()
    if REPO_ROOT.resolve() not in capability.parents or not capability.is_file():
        raise ExperimentConfigReferenceError(
            f"template capability config is not a repository file: {template.capability_config_reference}"
        )
    return template


def experiment_config_to_print_job(config: PrintingExperimentConfigV1) -> PrintJobV1:
    """Resolve references and validate the YAML through the strict PrintJobV1 contract."""
    substrate = resolve_registered_substrate(config.substrate.labware_id)
    registered_material = resolve_registered_material(
        config.material.material_id,
        pattern_type=(
            "well_selection"
            if config.printing.layout.kind == "well_conditions"
            else "four_clover"
        ),
    )
    material = MaterialReferenceV1(
        material_id=registered_material.material_id,
        display_name=config.material.display_name,
    )
    layout = config.printing.layout
    if isinstance(layout, StandardConditionsLayoutV1):
        rows = [condition.wells[0][0] for condition in layout.conditions]
        columns = [int(well[1:]) for well in layout.conditions[0].wells]
        pattern = WellPatternV1(
            rows=rows,
            columns=columns,
            layers_by_row={
                condition.wells[0][0]: condition.drops_per_position
                for condition in layout.conditions
            },
        )
        replication = WellReplicationV1(replicates=len(columns))
        ordering = StandardOrderingIntentV1(
            mode="layer_then_row_then_column",
            inter_layer_rest_minutes=config.printing.inter_layer_rest_minutes or 0.0,
        )
    else:
        pattern = FourCloverPatternV1(
            geometry=layout.geometry,
            centers=layout.centers,
            layers=layout.layers,
        )
        replication = CloverReplicationV1(replicates=len(layout.centers))
        ordering = CloverOrderingIntentV1(mode=config.printing.ordering_mode)
    return PrintJobV1.from_content(
        schema_version="print-job/v1",
        name=config.experiment.name,
        description=config.experiment.description,
        substrate=substrate,
        materials=[material],
        pattern=pattern,
        deposition={
            "material_id": material.material_id,
            "volume_ul": config.printing.droplet_volume_ul,
        },
        replication=replication,
        ordering_intent=ordering,
        metadata={
            "experiment_config_schema": config.schema_version,
            "template_id": config.experiment.template_id,
        },
    )


def _validated_job(config: PrintingExperimentConfigV1) -> PrintJobV1:
    try:
        return experiment_config_to_print_job(config)
    except PrintingReferenceError as exc:
        raise ExperimentConfigReferenceError(str(exc)) from exc


def _summary(config: PrintingExperimentConfigV1) -> ExperimentConfigSummaryV1:
    layout = config.printing.layout
    if isinstance(layout, StandardConditionsLayoutV1):
        mapping = {condition.name: list(condition.wells) for condition in layout.conditions}
        drops = {condition.name: condition.drops_per_position for condition in layout.conditions}
        locations = sum(len(condition.wells) for condition in layout.conditions)
        events = sum(
            len(condition.wells) * condition.drops_per_position
            for condition in layout.conditions
        )
    else:
        mapping = {center.name: [center.reference_well] for center in layout.centers}
        drops = {center.name: 4 * layout.layers for center in layout.centers}
        locations = len(layout.centers) * 4
        events = locations * layout.layers
    return ExperimentConfigSummaryV1(
        experiment_name=config.experiment.name,
        workflow_name=config.workflow.name,
        substrate=config.substrate.labware_id,
        material=config.material.display_name,
        droplet_volume_ul=config.printing.droplet_volume_ul,
        condition_to_wells=mapping,
        drops_by_condition=drops,
        unique_locations=locations,
        total_deposition_events=events,
        total_liquid_ul=events * config.printing.droplet_volume_ul,
        config_version=config.experiment.version,
        config_sha256=config.config_sha256(),
        parent_config_sha256=config.experiment.parent_config_sha256,
    )


def _validate_trusted_constraints(job: PrintJobV1) -> None:
    try:
        prepared = prepare_printing_request(printing_request_from_job(job))
    except Exception as exc:
        raise ExperimentConfigPhysicalValidationError(str(exc)) from exc
    if not prepared.validation.valid:
        details = "; ".join(issue.message for issue in prepared.validation.errors)
        raise ExperimentConfigPhysicalValidationError(details or "trusted workflow validation failed")


def _template_sha(template: PrintingExperimentTemplateV1) -> str:
    return canonical_sha256(template.model_dump(mode="json"))


def _artifact(path: Path, config: PrintingExperimentConfigV1) -> ExperimentConfigArtifactV1:
    template = load_experiment_template(config.experiment.template_id)
    if config.workflow != template.workflow:
        raise ExperimentConfigReferenceError(
            "experiment workflow does not match its registered template"
        )
    layout = config.printing.layout
    positions = (
        sum(len(condition.wells) for condition in layout.conditions)
        if isinstance(layout, StandardConditionsLayoutV1)
        else len(layout.centers) * 4
    )
    if positions > template.constraints.maximum_positions:
        raise ExperimentConfigPhysicalValidationError(
            f"experiment requires {positions} positions but template allows "
            f"{template.constraints.maximum_positions}"
        )
    job = _validated_job(config)
    _validate_trusted_constraints(job)
    data = path.read_bytes()
    return ExperimentConfigArtifactV1(
        path=str(path),
        config_sha256=config.config_sha256(),
        file_sha256=hashlib.sha256(data).hexdigest(),
        template_sha256=_template_sha(template),
        config=config,
        job=job,
        summary=_summary(config),
    )


def load_printing_experiment_config(reference: str | Path) -> ExperimentConfigArtifactV1:
    path = Path(reference).resolve()
    try:
        config = _CONFIG_ADAPTER.validate_python(_load_yaml_mapping(path))
    except ExperimentConfigError:
        raise
    except Exception as exc:
        raise ExperimentConfigSchemaError(str(exc)) from exc
    return _artifact(path, config)


def describe_experiment_config(reference: str | Path) -> ExperimentConfigSummaryV1:
    return load_printing_experiment_config(reference).summary


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not stem:
        raise ExperimentConfigSchemaError("output_name must contain letters or digits")
    return re.sub(r"_v[1-9][0-9]*$", "", stem)


def _next_path(directory: Path, stem: str, minimum_version: int) -> tuple[Path, int]:
    version = minimum_version
    while True:
        candidate = directory / f"{stem}_v{version}.yaml"
        if not candidate.exists():
            return candidate, version
        version += 1


def _write_config(path: Path, config: PrintingExperimentConfigV1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        config.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def create_printing_experiment_config(
    draft: PrintingExperimentDraftV1 | dict[str, Any],
    *,
    template_id: str,
    output_name: str,
    output_dir: str | Path | None = None,
) -> ExperimentConfigArtifactV1:
    """Instantiate, validate, and save a new versioned YAML without overwriting templates."""
    template = load_experiment_template(template_id)
    validated = PrintingExperimentDraftV1.model_validate(draft)
    if validated.layout.kind != template.constraints.allowed_layout:
        raise ExperimentConfigSchemaError(
            f"template {template_id!r} requires {template.constraints.allowed_layout!r} layout"
        )
    positions = (
        sum(len(condition.wells) for condition in validated.layout.conditions)
        if isinstance(validated.layout, StandardConditionsLayoutV1)
        else len(validated.layout.centers) * 4
    )
    if positions > template.constraints.maximum_positions:
        raise ExperimentConfigPhysicalValidationError(
            f"experiment requires {positions} positions but template allows "
            f"{template.constraints.maximum_positions}"
        )
    directory = Path(output_dir or EXPERIMENT_DIR).resolve()
    path, version = _next_path(directory, _safe_stem(output_name), 1)
    config = PrintingExperimentConfigV1(
        experiment=ExperimentIdentityV1(
            name=validated.name,
            description=validated.description,
            version=version,
            template_id=template.template_id,
        ),
        workflow=template.workflow,
        substrate=ExperimentSubstrateV1(
            labware_id=validated.substrate_id or template.defaults.substrate_id
        ),
        material=ExperimentMaterialV1(
            material_id=validated.material_id or template.defaults.material_id,
            display_name=(
                validated.material_display_name or template.defaults.material_display_name
            ),
        ),
        printing=ExperimentPrintingV1(
            droplet_volume_ul=(
                validated.droplet_volume_ul or template.defaults.droplet_volume_ul
            ),
            layout=validated.layout,
            ordering_mode=validated.ordering_mode or template.defaults.ordering_mode,
            inter_layer_rest_minutes=(
                validated.inter_layer_rest_minutes
                if validated.inter_layer_rest_minutes is not None
                else template.defaults.inter_layer_rest_minutes
            ),
        ),
    )
    job = _validated_job(config)
    _validate_trusted_constraints(job)
    _write_config(path, config)
    return _artifact(path, config)


def revise_printing_experiment_config(
    reference: str | Path,
    changes: PrintingExperimentRevisionV1 | dict[str, Any],
    *,
    output_dir: str | Path | None = None,
) -> ExperimentConfigArtifactV1:
    """Create a child config version; the parent file is never mutated."""
    original = load_printing_experiment_config(reference)
    revision = PrintingExperimentRevisionV1.model_validate(changes)
    old = original.config
    directory = Path(output_dir or Path(original.path).parent).resolve()
    path, version = _next_path(
        directory,
        _safe_stem(Path(original.path).stem),
        old.experiment.version + 1,
    )
    config = PrintingExperimentConfigV1(
        experiment=ExperimentIdentityV1(
            name=revision.name or old.experiment.name,
            description=(
                revision.description
                if revision.description is not None
                else old.experiment.description
            ),
            version=version,
            parent_config_sha256=original.config_sha256,
            template_id=old.experiment.template_id,
        ),
        workflow=old.workflow,
        substrate=ExperimentSubstrateV1(
            labware_id=revision.substrate_id or old.substrate.labware_id
        ),
        material=ExperimentMaterialV1(
            material_id=revision.material_id or old.material.material_id,
            display_name=revision.material_display_name or old.material.display_name,
        ),
        printing=ExperimentPrintingV1(
            droplet_volume_ul=revision.droplet_volume_ul or old.printing.droplet_volume_ul,
            layout=revision.layout or old.printing.layout,
            ordering_mode=revision.ordering_mode or old.printing.ordering_mode,
            inter_layer_rest_minutes=(
                revision.inter_layer_rest_minutes
                if revision.inter_layer_rest_minutes is not None
                else old.printing.inter_layer_rest_minutes
            ),
        ),
    )
    job = _validated_job(config)
    _validate_trusted_constraints(job)
    _write_config(path, config)
    return _artifact(path, config)


def canonical_experiment_yaml(config: PrintingExperimentConfigV1) -> str:
    """Return deterministic human-readable YAML; identity uses canonical JSON bytes."""
    return yaml.safe_dump(
        config.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


def canonical_experiment_bytes(config: PrintingExperimentConfigV1) -> bytes:
    return canonical_json_bytes(config.canonical_payload())
