"""
Structured, validated parameters for custom labware generation.

This is the contract between an LLM (or a human editing YAML) and the
deterministic generator. The AI reasons about *what the user wants* and fills
in this schema; :mod:`src.labware.geometry` and :mod:`src.labware.builder`
then compute every coordinate. The AI never writes 96 x/y pairs by hand.

The legacy flat models retain field names from ``configs/labware/*.yaml`` so
existing configs load unchanged. ``WellPlate96SpecV1`` is the newer, nested
AI-facing contract and adapts explicitly into that proven rectangular-grid core.

Layering
--------
``WellPlate96SpecV1``   — bounded nested public contract for exactly 8 x 12.
``CommonLabwareSpec``   — legacy flat fields shared by existing generators.
                          (identity, well
                          geometry, outer footprint, tip-rack flags).
``RectangularGridSpec`` — adds the evenly-spaced grid that the
                          ``rectangular_grid`` family generates from.

Future families can register their own strict public contract and adapt to
reusable geometry/building primitives without changing the specialist agent.

Guardrail
---------
Physical geometry has **no defaults**. ``rows``, ``cols``, spacings, offsets,
well depth, volume and the outer footprint are all required, so neither the
agent nor a half-written YAML can silently inherit a dimension nobody measured.
Only policy/identity fields (namespace, version, quirks, ...) carry defaults.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, ClassVar, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Opentrons schema-2 "safeString": loadName / namespace are lowercase only.
SAFE_STRING = re.compile(r"^[a-z0-9._]+$")

# Enumerations lifted from the opentrons_shared_data labware schema 2.
PlateFormat = Literal["96Standard", "384Standard", "trough", "irregular", "trash"]
DisplayCategory = Literal[
    "tipRack", "tubeRack", "reservoir", "trash", "wellPlate",
    "aluminumBlock", "adapter", "other", "lid", "system",
]
WellShape = Literal["circular", "rectangular"]
WellBottomShape = Literal["flat", "u", "v"]

# Geometry comparisons use a tolerance much smaller than the 0.01 mm output
# precision. It absorbs floating-point representation noise without hiding a
# physically meaningful fit or overlap error.
GEOMETRY_TOLERANCE_MM = 1e-6


class _StrictModel(BaseModel):
    """Shared typo and assignment guard for the AI-facing V1 schema."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class WellPlate96IdentityV1(_StrictModel):
    load_name: str = Field(description="Opentrons loadName; lowercase [a-z0-9._] only")
    display_name: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    namespace: str = "custom_beta"
    description: str = ""
    manufacturer: Optional[str] = None

    @field_validator("load_name", "namespace")
    @classmethod
    def _safe_string(cls, value: str) -> str:
        if not SAFE_STRING.match(value):
            raise ValueError(
                f"{value!r} is not a valid Opentrons safeString — use lowercase "
                "letters, digits, '.' and '_' only."
            )
        return value


class WellPlate96RegularityV1(_StrictModel):
    same_well_shape_and_size: Literal[True] = True
    evenly_spaced_rows: Literal[True] = True
    evenly_spaced_columns: Literal[True] = True
    exclude_from_position_check: bool = False


class WellPlate96FootprintV1(_StrictModel):
    length_mm: float = Field(gt=0, description="Total labware length along Opentrons +X")
    width_mm: float = Field(gt=0, description="Total labware width along Opentrons +Y")
    height_mm: float = Field(gt=0, description="Total labware height along Opentrons +Z")


class WellPlate96GridV1(_StrictModel):
    """The fixed public grid for this family; arbitrary counts are unsupported."""

    rows: Literal[8] = 8
    columns: Literal[12] = 12


class CircularWellV1(_StrictModel):
    shape: Literal["circular"]
    volume_ul: float = Field(gt=0)
    diameter_mm: float = Field(gt=0)
    bottom_shape: Literal["flat", "round", "v_bottom"]
    depth_mm: float = Field(gt=0)
    well_bottom_z_mm: float = Field(
        ge=0,
        description="Height of the inside well floor above the labware bottom",
    )


class RectangularWellV1(_StrictModel):
    shape: Literal["rectangular"]
    volume_ul: float = Field(gt=0)
    x_size_mm: float = Field(gt=0)
    y_size_mm: float = Field(gt=0)
    bottom_shape: Literal["flat", "round", "v_bottom"]
    depth_mm: float = Field(gt=0)
    well_bottom_z_mm: float = Field(
        ge=0,
        description="Height of the inside well floor above the labware bottom",
    )


WellPlate96WellV1 = Annotated[
    Union[CircularWellV1, RectangularWellV1],
    Field(discriminator="shape"),
]


class WellPlate96SpacingV1(_StrictModel):
    x_spacing_mm: float = Field(gt=0, description="Column center-to-center pitch")
    y_spacing_mm: float = Field(gt=0, description="Row center-to-center pitch")


class WellPlate96OffsetV1(_StrictModel):
    x_offset_mm: float = Field(
        ge=0,
        description="A1 center from the labware's left edge along +X",
    )
    y_offset_mm: float = Field(
        ge=0,
        description="A1 center from the front edge along +Y; A is the back row",
    )


class WellPlate96StackingV1(_StrictModel):
    mode: Literal["none"] = "none"


class WellPlate96SpecV1(_StrictModel):
    """Strict AI-facing contract for one regular 8 x 12 plate.

    Python identifiers cannot begin with a digit, so this class is named
    ``WellPlate96SpecV1`` while its JSON Schema title is the requested
    ``96WellPlateSpecV1``.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        title="96WellPlateSpecV1",
    )

    family: Literal["well_plate_96"] = "well_plate_96"
    schema_version: Literal[1] = 1
    identity: WellPlate96IdentityV1
    regularity: WellPlate96RegularityV1 = Field(default_factory=WellPlate96RegularityV1)
    footprint: WellPlate96FootprintV1
    grid: WellPlate96GridV1 = Field(default_factory=WellPlate96GridV1)
    well: WellPlate96WellV1
    spacing: WellPlate96SpacingV1
    offset: WellPlate96OffsetV1
    stacking: WellPlate96StackingV1 = Field(default_factory=WellPlate96StackingV1)

    @model_validator(mode="after")
    def _check_physical_geometry(self) -> "WellPlate96SpecV1":
        half_x = self.well_footprint_x / 2.0
        half_y = self.well_footprint_y / 2.0
        tol = GEOMETRY_TOLERANCE_MM

        if self.well.well_bottom_z_mm + self.well.depth_mm > self.footprint.height_mm + tol:
            raise ValueError(
                "well_bottom_z_mm + depth_mm exceeds footprint.height_mm; "
                "the well opening would be above the labware."
            )
        if self.spacing.x_spacing_mm < self.well_footprint_x - tol:
            raise ValueError("x_spacing_mm is smaller than the well X size; columns overlap.")
        if self.spacing.y_spacing_mm < self.well_footprint_y - tol:
            raise ValueError("y_spacing_mm is smaller than the well Y size; rows overlap.")

        left = self.offset.x_offset_mm - half_x
        right = self.offset.x_offset_mm + 11 * self.spacing.x_spacing_mm + half_x
        back = self.offset.y_offset_mm + half_y
        front = self.offset.y_offset_mm - 7 * self.spacing.y_spacing_mm - half_y
        if left < -tol:
            raise ValueError("A1 extends past the left edge of the footprint.")
        if right > self.footprint.length_mm + tol:
            raise ValueError("column 12 extends past the right edge of the footprint.")
        if back > self.footprint.width_mm + tol:
            raise ValueError("row A extends past the back edge of the footprint.")
        if front < -tol:
            raise ValueError("row H extends past the front edge of the footprint.")
        return self

    @property
    def position_count(self) -> int:
        return 96

    @property
    def rows(self) -> int:
        return 8

    @property
    def cols(self) -> int:
        return 12

    @property
    def load_name(self) -> str:
        return self.identity.load_name

    @property
    def well_footprint_x(self) -> float:
        return self.well.diameter_mm if self.well.shape == "circular" else self.well.x_size_mm

    @property
    def well_footprint_y(self) -> float:
        return self.well.diameter_mm if self.well.shape == "circular" else self.well.y_size_mm

    def to_rectangular_grid_spec(self) -> "RectangularGridSpec":
        """Adapt the concise V1 contract to the reusable legacy core."""
        bottom = {"flat": "flat", "round": "u", "v_bottom": "v"}[self.well.bottom_shape]
        shape_fields: Dict[str, Any]
        if self.well.shape == "circular":
            shape_fields = {"shape": "circular", "diameter": self.well.diameter_mm}
        else:
            shape_fields = {
                "shape": "rectangular",
                "x_length": self.well.x_size_mm,
                "y_length": self.well.y_size_mm,
            }
        quirks = (
            ["excludeFromLabwarePositionCheck"]
            if self.regularity.exclude_from_position_check
            else []
        )
        return RectangularGridSpec(
            load_name=self.identity.load_name,
            display_name=self.identity.display_name,
            brand=self.identity.manufacturer or "Custom",
            namespace=self.identity.namespace,
            version=self.identity.version,
            display_category="wellPlate",
            plate_format="96Standard",
            depth=self.well.depth_mm,
            total_liquid_volume=self.well.volume_ul,
            well_bottom_shape=bottom,
            well_z=self.well.well_bottom_z_mm,
            x_dimension=self.footprint.length_mm,
            y_dimension=self.footprint.width_mm,
            z_dimension=self.footprint.height_mm,
            rows=8,
            cols=12,
            x_offset=self.offset.x_offset_mm,
            y_offset=self.offset.y_offset_mm,
            x_spacing=self.spacing.x_spacing_mm,
            y_spacing=self.spacing.y_spacing_mm,
            quirks=quirks,
            **shape_fields,
        )

    def to_config_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


class CommonLabwareSpec(BaseModel):
    """Parameters shared by every labware family."""

    # ``forbid`` is the typo guard: ``well_diameter`` instead of ``diameter``
    # fails loudly at load time instead of silently generating default geometry.
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    #: Family key this spec belongs to; overridden by each subclass.
    family: ClassVar[str] = "common"

    # ── Identity ──────────────────────────────────────────────────
    load_name: str = Field(description="Opentrons loadName; lowercase [a-z0-9._] only")
    display_name: str = Field(min_length=1, description="Human-readable name shown in the app")
    brand: str = "Custom"
    brand_ids: List[str] = Field(default_factory=list)
    namespace: str = "custom_beta"
    version: int = Field(default=1, ge=1)
    display_category: DisplayCategory = "wellPlate"

    # ── Layout format ─────────────────────────────────────────────
    plate_format: PlateFormat = "irregular"

    # ── Well geometry (required — never invented) ─────────────────
    shape: WellShape = "circular"
    diameter: Optional[float] = Field(default=None, gt=0, description="mm; required when shape=circular")
    x_length: Optional[float] = Field(default=None, gt=0, description="mm; required when shape=rectangular")
    y_length: Optional[float] = Field(default=None, gt=0, description="mm; required when shape=rectangular")
    depth: float = Field(gt=0, description="mm from well top down to the well floor")
    total_liquid_volume: float = Field(gt=0, description="microlitres per well")
    well_bottom_shape: WellBottomShape = "flat"

    # z of the well's centre-bottom above the labware floor. None auto-computes
    # to (z_dimension - depth); set it explicitly when you have a measurement
    # that differs (paper_print_96_flat pins 6.0, not the computed 13.9).
    well_z: Optional[float] = Field(default=None, ge=0)

    # ── Outer footprint (required) ────────────────────────────────
    x_dimension: float = Field(gt=0, description="mm; overall length (x)")
    y_dimension: float = Field(gt=0, description="mm; overall width (y)")
    z_dimension: float = Field(gt=0, description="mm; overall height (z)")

    # ── Tip rack / misc ───────────────────────────────────────────
    is_tiprack: bool = False
    tip_length: Optional[float] = Field(default=None, gt=0)
    tip_overlap: Optional[float] = Field(default=None, ge=0)
    is_magnetic_module_compatible: bool = False
    quirks: List[str] = Field(default_factory=list)

    # ── Validators ────────────────────────────────────────────────
    @field_validator("load_name", "namespace")
    @classmethod
    def _safe_string(cls, value: str) -> str:
        if not SAFE_STRING.match(value):
            raise ValueError(
                f"{value!r} is not a valid Opentrons safeString — allowed characters "
                "are lowercase letters, digits, '.' and '_' (no spaces, hyphens, or capitals)."
            )
        return value

    @model_validator(mode="after")
    def _check_shape_fields(self) -> "CommonLabwareSpec":
        if self.shape == "circular":
            if self.diameter is None:
                raise ValueError("shape='circular' requires `diameter` (mm).")
            if self.x_length is not None or self.y_length is not None:
                raise ValueError("shape='circular' must not set `x_length` / `y_length`.")
        else:
            if self.x_length is None or self.y_length is None:
                raise ValueError("shape='rectangular' requires `x_length` and `y_length` (mm).")
            if self.diameter is not None:
                raise ValueError("shape='rectangular' must not set `diameter`.")
        return self

    @model_validator(mode="after")
    def _check_tiprack(self) -> "CommonLabwareSpec":
        if self.is_tiprack and self.tip_length is None:
            raise ValueError("is_tiprack=True requires `tip_length` (mm).")
        if not self.is_tiprack and self.tip_length is not None:
            raise ValueError("`tip_length` is only meaningful when is_tiprack=True.")
        return self

    # ── Derived values ────────────────────────────────────────────
    @property
    def resolved_well_z(self) -> float:
        """Well centre-bottom height above the labware floor, in mm."""
        if self.well_z is not None:
            return self.well_z
        return round(self.z_dimension - self.depth, 2)

    @property
    def well_footprint_x(self) -> float:
        """Full width of one well in x (mm) — used for containment checks."""
        return float(self.diameter if self.shape == "circular" else self.x_length)

    @property
    def well_footprint_y(self) -> float:
        """Full depth of one well in y (mm) — used for containment checks."""
        return float(self.diameter if self.shape == "circular" else self.y_length)

    def to_config_dict(self) -> Dict[str, Any]:
        """Flat dict suitable for writing back out as a labware YAML config."""
        return self.model_dump()


class RectangularGridSpec(CommonLabwareSpec):
    """An evenly spaced ``rows`` x ``cols`` grid of identical positions.

    Covers flat printing substrates, standard well plates, troughs and simple
    racks — anything whose positions sit on a single regular pitch.
    """

    family: ClassVar[str] = "rectangular_grid"

    rows: int = Field(gt=0, le=676, description="Number of rows -> letters A, B, C ...")
    cols: int = Field(gt=0, description="Number of columns -> numbers 1, 2, 3 ...")

    x_offset: float = Field(ge=0, description="mm; x centre of column 1")
    y_offset: float = Field(ge=0, description="mm; y centre of row A (the BACK row)")
    x_spacing: float = Field(ge=0, description="mm; centre-to-centre between columns")
    y_spacing: float = Field(ge=0, description="mm; centre-to-centre between rows")

    @model_validator(mode="after")
    def _check_spacing_needed(self) -> "RectangularGridSpec":
        # Zero spacing is legal on a single row/column (a 1x12 trough has
        # y_spacing 0) but would stack every position on one point otherwise.
        if self.cols > 1 and self.x_spacing <= 0:
            raise ValueError(
                f"x_spacing must be > 0 when cols > 1 (got {self.x_spacing} with cols={self.cols}) "
                "— every column would land on the same x."
            )
        if self.rows > 1 and self.y_spacing <= 0:
            raise ValueError(
                f"y_spacing must be > 0 when rows > 1 (got {self.y_spacing} with rows={self.rows}) "
                "— every row would land on the same y."
            )
        return self

    @property
    def position_count(self) -> int:
        return self.rows * self.cols
