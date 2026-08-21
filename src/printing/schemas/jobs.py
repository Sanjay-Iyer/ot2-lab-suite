"""Strict scientist-facing schema for Stage 2 printing intent.

``PrintJobV1`` says what experiment is wanted. It deliberately contains no
deck slots, source wells, pipette settings, air handling, calibrated heights,
protocol code, or other machine-owned implementation details.
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationInfo, field_validator, model_validator

from ..canonical import canonical_json_bytes, canonical_sha256
from .models import FourCloverGeometry


WELL_PATTERN = r"^[A-H](?:[1-9]|1[0-2])$"
MATERIAL_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_.-]*$"


class StrictJobModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
        frozen=True,
    )


class LabwareReferenceV1(StrictJobModel):
    """Stable reference to a validated definition; geometry remains external."""

    load_name: str = Field(pattern=r"^[a-z0-9._]+$")
    namespace: str = Field(pattern=r"^[a-z0-9._]+$")
    version: int = Field(ge=1)
    definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_id: str = Field(min_length=1)


class MaterialReferenceV1(StrictJobModel):
    material_id: str = Field(pattern=MATERIAL_ID_PATTERN)
    display_name: str | None = Field(default=None, min_length=1)


class DepositionIntentV1(StrictJobModel):
    material_id: str = Field(pattern=MATERIAL_ID_PATTERN)
    volume_ul: float = Field(gt=0)


class WellPatternV1(StrictJobModel):
    type: Literal["well_selection"] = "well_selection"
    rows: list[Literal["A", "B", "C", "D", "E", "F", "G", "H"]] = Field(
        min_length=1
    )
    columns: list[int] = Field(min_length=1)
    layers_by_row: dict[str, int]

    @field_validator("rows", mode="before")
    @classmethod
    def _uppercase_rows(cls, rows: Any) -> Any:
        return [str(row).upper() for row in rows] if isinstance(rows, list) else rows

    @field_validator("columns")
    @classmethod
    def _valid_columns(cls, columns: list[int]) -> list[int]:
        if any(isinstance(column, bool) or not 1 <= column <= 12 for column in columns):
            raise ValueError("columns entries must be integers from 1 through 12")
        if len(columns) != len(set(columns)):
            raise ValueError("columns must not contain duplicates")
        return columns

    @field_validator("layers_by_row", mode="before")
    @classmethod
    def _normalize_layers(cls, layers: Any) -> Any:
        if not isinstance(layers, dict):
            return layers
        normalized: dict[str, Any] = {}
        for row, count in layers.items():
            name = str(row).upper()
            if name in normalized:
                raise ValueError("layers_by_row contains duplicate normalized rows")
            normalized[name] = count
        return normalized

    @model_validator(mode="after")
    def _coherent_selection(self) -> Self:
        if len(self.rows) != len(set(self.rows)):
            raise ValueError("rows must not contain duplicates")
        if set(self.layers_by_row) != set(self.rows):
            raise ValueError("layers_by_row must define exactly the selected rows")
        for row, count in self.layers_by_row.items():
            if row not in "ABCDEFGH" or len(row) != 1:
                raise ValueError(f"layers_by_row has invalid row {row!r}")
            if isinstance(count, bool) or count < 1:
                raise ValueError(f"layers_by_row.{row} must be an integer >= 1")
        return self


class FourCloverCenterV1(StrictJobModel):
    """One design instance placed using the proven v12 reference-well convention."""

    name: str = Field(min_length=1)
    reference_well: str = Field(pattern=r"^[A-Ha-h](?:[1-9]|1[0-2])$")
    x_offset_mm: float = 0.0
    y_offset_mm: float = 0.0
    layers: int | None = Field(default=None, ge=1)

    @field_validator("reference_well")
    @classmethod
    def _uppercase_well(cls, value: str) -> str:
        return value.upper()


class FourCloverPatternV1(StrictJobModel):
    type: Literal["four_clover"] = "four_clover"
    geometry: FourCloverGeometry
    centers: list[FourCloverCenterV1] = Field(min_length=1)
    layers: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _unique_centers(self) -> Self:
        names = [center.name for center in self.centers]
        if len(names) != len(set(names)):
            raise ValueError("clover center names must be unique")
        return self


PrintPatternV1 = Annotated[
    WellPatternV1 | FourCloverPatternV1,
    Field(discriminator="type"),
]


class WellReplicationV1(StrictJobModel):
    kind: Literal["well_replicates"] = "well_replicates"
    replicates: int = Field(ge=1)


class CloverReplicationV1(StrictJobModel):
    kind: Literal["design_replicates"] = "design_replicates"
    replicates: int = Field(ge=1)


ReplicationIntentV1 = Annotated[
    WellReplicationV1 | CloverReplicationV1,
    Field(discriminator="kind"),
]


class StandardOrderingIntentV1(StrictJobModel):
    mode: Literal["layer_then_row_then_column"] = "layer_then_row_then_column"
    inter_layer_rest_minutes: float = Field(default=0.0, ge=0)


class CloverOrderingIntentV1(StrictJobModel):
    mode: Literal["clover_by_clover", "position_by_position"]


OrderingIntentV1 = Annotated[
    StandardOrderingIntentV1 | CloverOrderingIntentV1,
    Field(discriminator="mode"),
]


class PrintJobV1(StrictJobModel):
    """Canonical high-level scientific printing experiment."""

    schema_version: Literal["print-job/v1"] = "print-job/v1"
    job_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(min_length=1)
    description: str | None = None
    substrate: LabwareReferenceV1
    materials: list[MaterialReferenceV1] = Field(min_length=1, max_length=1)
    pattern: PrintPatternV1
    deposition: DepositionIntentV1
    replication: ReplicationIntentV1
    ordering_intent: OrderingIntentV1
    metadata: dict[str, str] = Field(default_factory=dict)

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
        """Scientific identity excluding labels and inspection-only metadata."""
        return self.model_dump(
            mode="json",
            exclude={"job_id", "name", "description", "metadata"},
        )

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_payload())

    def canonical_json(self) -> str:
        return self.canonical_bytes().decode("utf-8")

    def job_sha256(self) -> str:
        return canonical_sha256(self.canonical_payload())

    def summary(self) -> str:
        material = next(
            item for item in self.materials if item.material_id == self.deposition.material_id
        )
        lines = [
            f"PrintJob: {self.name}",
            f"Substrate: {self.substrate.load_name}",
            f"Material: {material.display_name or material.material_id}",
            f"Volume: {self.deposition.volume_ul:g} uL",
        ]
        if self.pattern.type == "well_selection":
            targets = [f"{row}{column}" for row in self.pattern.rows for column in self.pattern.columns]
            lines.append(f"Targets: {', '.join(targets)}")
            lines.append(
                "Layers: "
                + ", ".join(
                    f"{row}={self.pattern.layers_by_row[row]}" for row in self.pattern.rows
                )
            )
            lines.append(f"Replicates: {self.replication.replicates}")
        else:
            lines.append(f"Clovers: {len(self.pattern.centers)}")
            lines.append("Points per clover: 4")
            lines.append(f"Layers: {self.pattern.layers}")
        return "\n".join(lines)

    @model_validator(mode="after")
    def _validate_scientific_intent(self, info: ValidationInfo) -> Self:
        material_ids = [material.material_id for material in self.materials]
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("materials must have unique material_id values")
        if self.deposition.material_id not in material_ids:
            raise ValueError("deposition references an unknown material_id")

        if self.pattern.type == "well_selection":
            if self.replication.kind != "well_replicates":
                raise ValueError("well selections require well_replicates")
            if self.replication.replicates != len(self.pattern.columns):
                raise ValueError("well replicate count must equal selected replicate columns")
            if self.ordering_intent.mode != "layer_then_row_then_column":
                raise ValueError("well selections require layer_then_row_then_column ordering")
        else:
            if self.replication.kind != "design_replicates":
                raise ValueError("four-clover patterns require design_replicates")
            if self.replication.replicates != len(self.pattern.centers):
                raise ValueError("clover replicate count must equal the number of centers")
            if self.ordering_intent.mode not in {
                "clover_by_clover",
                "position_by_position",
            }:
                raise ValueError("unsupported four-clover ordering intent")

        skip_hash = bool(info.context and info.context.get("skip_job_id_validation", False))
        if not skip_hash and self.job_id != self.job_sha256():
            raise ValueError("job_id does not match the canonical job SHA-256")
        return self


_PRINT_JOB_ADAPTER = TypeAdapter(PrintJobV1)


def parse_print_job_json(data: str | bytes) -> PrintJobV1:
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return _PRINT_JOB_ADAPTER.validate_python(json.loads(data))


def print_job_artifact_json(job: PrintJobV1) -> str:
    return json.dumps(
        job.model_dump(mode="json"),
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
