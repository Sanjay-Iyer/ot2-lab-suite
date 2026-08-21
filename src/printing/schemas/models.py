"""Pydantic models at the AI-to-deterministic printing boundary.

These models intentionally represent only parameters an agent may propose. Hardware,
deck, source identity, calibrated flow settings, validation tolerances, and live-run
authorization remain owned by registered configuration profiles and operator controls.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator


def _owned(owner: str, *, unit: str | None = None) -> dict[str, str]:
    metadata = {"owner": owner}
    if unit:
        metadata["unit"] = unit
    return metadata


AI_SELECTABLE = "AI-selectable"
CONFIG_CONTROLLED = "config-controlled"
DETERMINISTIC = "deterministically calculated"


class StrictPrintingModel(BaseModel):
    """Base for every model crossing the AI/digital boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @classmethod
    def parameter_ownership(cls) -> dict[str, dict[str, str]]:
        """Return field ownership/unit metadata for docs and tool inspection."""
        return {
            name: dict(field.json_schema_extra or {})
            for name, field in cls.model_fields.items()
        }


class PrintingFamily(str, Enum):
    STANDARD = "standard"
    DESIGN = "design"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(StrictPrintingModel):
    severity: Severity
    code: str
    field: str | None = None
    message: str
    suggested_fix: str | None = None


class ValidationReport(StrictPrintingModel):
    valid: bool
    workflow_name: str
    family: PrintingFamily
    design_name: str | None = None
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    calculated: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra=_owned(DETERMINISTIC),
    )


class CommonPrintingPatch(StrictPrintingModel):
    """The small set of experimental parameters shared by current families."""

    droplet_volume_ul: float | None = Field(
        default=None,
        gt=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="uL"),
    )


class StandardWellGridPatch(CommonPrintingPatch):
    """Allowed changes for v9 exact-well printing."""

    replicate_columns: list[int] | None = Field(
        default=None,
        min_length=1,
        json_schema_extra=_owned(AI_SELECTABLE),
    )
    layers_by_row: dict[str, int] | None = Field(
        default=None,
        json_schema_extra=_owned(AI_SELECTABLE),
    )
    rest_minutes: float | None = Field(
        default=None,
        ge=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="min"),
    )

    @field_validator("replicate_columns")
    @classmethod
    def _valid_columns(cls, columns: list[int] | None) -> list[int] | None:
        if columns is None:
            return None
        if any(isinstance(column, bool) or not 1 <= column <= 12 for column in columns):
            raise ValueError("replicate_columns entries must be integers from 1 through 12")
        if len(columns) != len(set(columns)):
            raise ValueError("replicate_columns must not contain duplicates")
        return columns

    @field_validator("layers_by_row")
    @classmethod
    def _valid_row_layers(cls, layers: dict[str, int] | None) -> dict[str, int] | None:
        if layers is None:
            return None
        normalized: dict[str, int] = {}
        for row, count in layers.items():
            name = str(row).upper()
            if name not in "ABCDEFGH" or len(name) != 1:
                raise ValueError(f"layers_by_row has invalid paper row {row!r}")
            if isinstance(count, bool) or count < 1:
                raise ValueError(f"layers_by_row.{name} must be an integer >= 1")
            normalized[name] = count
        if not normalized:
            raise ValueError("layers_by_row must not be empty")
        return normalized


class ComplementaryLayerPatch(CommonPrintingPatch):
    """Allowed changes for v10 by-row/by-column layer workflows."""

    layers: dict[str, int] | None = Field(
        default=None,
        json_schema_extra=_owned(AI_SELECTABLE),
    )
    rest_minutes: float | None = Field(
        default=None,
        ge=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="min"),
    )

    @field_validator("layers")
    @classmethod
    def _positive_layers(cls, layers: dict[str, int] | None) -> dict[str, int] | None:
        if layers is None:
            return None
        if not layers:
            raise ValueError("layers must not be empty")
        for location, count in layers.items():
            if isinstance(count, bool) or count < 1:
                raise ValueError(f"layers.{location} must be an integer >= 1")
        return layers


class ComplementaryColumnPatch(ComplementaryLayerPatch):
    """Layer map whose keys are destination columns 1-12."""

    @field_validator("layers")
    @classmethod
    def _column_keys(cls, layers: dict[str, int] | None) -> dict[str, int] | None:
        if layers is None:
            return None
        normalized: dict[str, int] = {}
        for key, value in layers.items():
            try:
                column = int(key)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"layers key {key!r} must be a column from 1 through 12") from exc
            if not 1 <= column <= 12:
                raise ValueError(f"layers key {key!r} must be a column from 1 through 12")
            normalized[str(column)] = value
        return normalized


class ComplementaryRowPatch(ComplementaryLayerPatch):
    """Layer map whose keys are destination rows A-H."""

    @field_validator("layers")
    @classmethod
    def _row_keys(cls, layers: dict[str, int] | None) -> dict[str, int] | None:
        if layers is None:
            return None
        normalized = {str(key).upper(): value for key, value in layers.items()}
        invalid = [key for key in normalized if len(key) != 1 or key not in "ABCDEFGH"]
        if invalid:
            raise ValueError(f"layers keys must be rows A-H; got {', '.join(invalid)}")
        return normalized


class ComplementaryQuickPatch(CommonPrintingPatch):
    """Allowed changes for v10c initial-plus-extra printing."""

    initial_layers: int | None = Field(
        default=None,
        ge=1,
        json_schema_extra=_owned(AI_SELECTABLE),
    )
    extra_layers: dict[str, int] | None = Field(
        default=None,
        json_schema_extra=_owned(AI_SELECTABLE),
    )
    rest_minutes: float | None = Field(
        default=None,
        ge=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="min"),
    )

    @field_validator("extra_layers")
    @classmethod
    def _valid_extra_layers(cls, layers: dict[str, int] | None) -> dict[str, int] | None:
        if layers is None:
            return None
        for well, count in layers.items():
            name = str(well).upper()
            if not name or name[0] not in "ABCDEFGH" or not name[1:].isdigit():
                raise ValueError(f"extra_layers has invalid paper well {well!r}")
            column = int(name[1:])
            if not 1 <= column <= 12:
                raise ValueError(f"extra_layers has invalid paper well {well!r}")
            if isinstance(count, bool) or count < 1:
                raise ValueError(f"extra_layers.{name} must be an integer >= 1")
        return {str(well).upper(): count for well, count in layers.items()}


class CombinedOverlayPatch(CommonPrintingPatch):
    """Allowed experiment-level changes for the two-source v11 overlay."""

    between_parts_delay_minutes: float | None = Field(
        default=None,
        ge=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="min"),
    )


class XYOffset(StrictPrintingModel):
    x_mm: float = Field(json_schema_extra=_owned(AI_SELECTABLE, unit="mm"))
    y_mm: float = Field(json_schema_extra=_owned(AI_SELECTABLE, unit="mm"))


class XYOverride(StrictPrintingModel):
    x_mm: float | None = Field(
        default=None,
        json_schema_extra=_owned(AI_SELECTABLE, unit="mm"),
    )
    y_mm: float | None = Field(
        default=None,
        json_schema_extra=_owned(AI_SELECTABLE, unit="mm"),
    )

    @model_validator(mode="after")
    def _at_least_one_axis(self) -> "XYOverride":
        if self.x_mm is None and self.y_mm is None:
            raise ValueError("a droplet override must set x_mm or y_mm")
        return self


class FourCloverGeometry(StrictPrintingModel):
    """One real geometry form accepted by the existing four-clover resolver."""

    half_width_mm: float | None = Field(
        default=None,
        gt=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="mm"),
    )
    half_height_mm: float | None = Field(
        default=None,
        gt=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="mm"),
    )
    droplet_overrides: dict[Literal["d1", "d2", "d3", "d4"], XYOverride] | None = Field(
        default=None, json_schema_extra=_owned(AI_SELECTABLE)
    )
    d1: XYOffset | None = Field(default=None, json_schema_extra=_owned(AI_SELECTABLE))
    d2: XYOffset | None = Field(default=None, json_schema_extra=_owned(AI_SELECTABLE))
    d3: XYOffset | None = Field(default=None, json_schema_extra=_owned(AI_SELECTABLE))
    d4: XYOffset | None = Field(default=None, json_schema_extra=_owned(AI_SELECTABLE))

    @model_validator(mode="after")
    def _one_complete_geometry_form(self) -> "FourCloverGeometry":
        explicit = [self.d1, self.d2, self.d3, self.d4]
        has_explicit = any(value is not None for value in explicit)
        has_symmetric = self.half_width_mm is not None or self.half_height_mm is not None
        if has_explicit:
            if not all(value is not None for value in explicit):
                raise ValueError("explicit geometry requires d1, d2, d3, and d4")
            if has_symmetric or self.droplet_overrides:
                raise ValueError("explicit and symmetric geometry forms cannot be mixed")
        elif self.half_width_mm is None or self.half_height_mm is None:
            raise ValueError("geometry requires both half_width_mm and half_height_mm")
        return self


class FourCloverManualCenter(StrictPrintingModel):
    name: str = Field(min_length=1, json_schema_extra=_owned(AI_SELECTABLE))
    reference_well: str = Field(
        pattern=r"^[A-Ha-h](?:[1-9]|1[0-2])$",
        json_schema_extra=_owned(AI_SELECTABLE),
    )
    x_offset_mm: float = Field(
        default=0.0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="mm"),
    )
    y_offset_mm: float = Field(
        default=0.0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="mm"),
    )
    geometry: FourCloverGeometry | None = Field(default=None, json_schema_extra=_owned(AI_SELECTABLE))
    layers: int | None = Field(default=None, ge=1, json_schema_extra=_owned(AI_SELECTABLE))
    pre_air_chase_ul: float | None = Field(
        default=None,
        ge=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="uL"),
    )

    @field_validator("reference_well")
    @classmethod
    def _uppercase_well(cls, value: str) -> str:
        return value.upper()


class FourCloverGrid(StrictPrintingModel):
    anchor_well: str = Field(
        pattern=r"^[A-Ha-h](?:[1-9]|1[0-2])$",
        json_schema_extra=_owned(AI_SELECTABLE),
    )
    rows: int = Field(ge=1, json_schema_extra=_owned(AI_SELECTABLE))
    columns: int = Field(ge=1, json_schema_extra=_owned(AI_SELECTABLE))
    x_pitch_mm: float = Field(ge=0, json_schema_extra=_owned(AI_SELECTABLE, unit="mm"))
    y_pitch_mm: float = Field(ge=0, json_schema_extra=_owned(AI_SELECTABLE, unit="mm"))
    x_offset_mm: float | None = Field(default=None, json_schema_extra=_owned(AI_SELECTABLE, unit="mm"))
    y_offset_mm: float | None = Field(default=None, json_schema_extra=_owned(AI_SELECTABLE, unit="mm"))
    name_prefix: str | None = Field(default=None, min_length=1, json_schema_extra=_owned(AI_SELECTABLE))
    row_direction: Literal["-y", "+y"] | None = Field(default=None, json_schema_extra=_owned(AI_SELECTABLE))
    column_direction: Literal["+x", "-x"] | None = Field(default=None, json_schema_extra=_owned(AI_SELECTABLE))
    geometry_overrides: dict[str, FourCloverGeometry] = Field(default_factory=dict, json_schema_extra=_owned(AI_SELECTABLE))
    layer_overrides: dict[str, int] = Field(default_factory=dict, json_schema_extra=_owned(AI_SELECTABLE))
    pre_air_chase_overrides: dict[str, float] = Field(default_factory=dict, json_schema_extra=_owned(AI_SELECTABLE, unit="uL"))

    @field_validator("anchor_well")
    @classmethod
    def _uppercase_anchor(cls, value: str) -> str:
        return value.upper()

    @field_validator("layer_overrides")
    @classmethod
    def _positive_layer_overrides(cls, values: dict[str, int]) -> dict[str, int]:
        if any(isinstance(value, bool) or value < 1 for value in values.values()):
            raise ValueError("layer_overrides values must be integers >= 1")
        return values

    @field_validator("pre_air_chase_overrides")
    @classmethod
    def _nonnegative_chase_overrides(cls, values: dict[str, float]) -> dict[str, float]:
        if any(value < 0 for value in values.values()):
            raise ValueError("pre_air_chase_overrides values must be >= 0 uL")
        return values

    @model_validator(mode="after")
    def _pitch_when_repeated(self) -> "FourCloverGrid":
        if self.columns > 1 and self.x_pitch_mm <= 0:
            raise ValueError("x_pitch_mm must be > 0 when columns > 1")
        if self.rows > 1 and self.y_pitch_mm <= 0:
            raise ValueError("y_pitch_mm must be > 0 when rows > 1")
        return self


class FourCloverPatch(CommonPrintingPatch):
    """AI-selectable four-clover fields; config-owned safety limits are absent."""

    default_geometry: FourCloverGeometry | None = Field(default=None, json_schema_extra=_owned(AI_SELECTABLE))
    manual_centers: list[FourCloverManualCenter] | None = Field(default=None, min_length=1, json_schema_extra=_owned(AI_SELECTABLE))
    grid: FourCloverGrid | None = Field(default=None, json_schema_extra=_owned(AI_SELECTABLE))
    layers: int | None = Field(default=None, ge=1, json_schema_extra=_owned(AI_SELECTABLE))
    pre_air_chase_ul: float | None = Field(
        default=None,
        ge=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="uL"),
    )
    dispense_height_mm: float | None = Field(
        default=None,
        gt=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="mm"),
    )
    inter_drop_delay_s: float | None = Field(
        default=None,
        ge=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="s"),
    )
    inter_layer_delay_s: float | None = Field(
        default=None,
        ge=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="s"),
    )
    inter_clover_delay_s: float | None = Field(
        default=None,
        ge=0,
        json_schema_extra=_owned(AI_SELECTABLE, unit="s"),
    )
    order_mode: Literal["clover_by_clover", "position_by_position"] | None = Field(
        default=None, json_schema_extra=_owned(AI_SELECTABLE)
    )

    @model_validator(mode="after")
    def _one_center_mode(self) -> "FourCloverPatch":
        if self.manual_centers is not None and self.grid is not None:
            raise ValueError("manual_centers and grid are mutually exclusive")
        if self.manual_centers:
            names = [center.name for center in self.manual_centers]
            if len(names) != len(set(names)):
                raise ValueError("manual center names must be unique")
        return self


class StandardPrintingRequest(StrictPrintingModel):
    family: Literal[PrintingFamily.STANDARD] = PrintingFamily.STANDARD
    workflow_name: str = Field(min_length=1, json_schema_extra=_owned(AI_SELECTABLE))
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Validated against the selected workflow's registered patch model.",
        json_schema_extra=_owned(AI_SELECTABLE),
    )


class DesignPrintingRequest(StrictPrintingModel):
    family: Literal[PrintingFamily.DESIGN] = PrintingFamily.DESIGN
    workflow_name: str = Field(min_length=1, json_schema_extra=_owned(AI_SELECTABLE))
    design_name: str = Field(min_length=1, json_schema_extra=_owned(AI_SELECTABLE))
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Validated against the selected design/workflow patch model.",
        json_schema_extra=_owned(AI_SELECTABLE),
    )


PrintingRequest: TypeAlias = Annotated[
    StandardPrintingRequest | DesignPrintingRequest,
    Field(discriminator="family"),
]
_PRINTING_REQUEST_ADAPTER = TypeAdapter(PrintingRequest)


def parse_printing_request(payload: dict[str, Any]) -> PrintingRequest:
    """Parse family selection; workflow registration validates its parameter patch."""
    return _PRINTING_REQUEST_ADAPTER.validate_python(payload)
