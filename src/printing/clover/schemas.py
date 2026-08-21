"""Scientist-facing schema for generalized four-clover printing experiments.

This is the four-clover counterpart of
:mod:`src.printing.schemas.experiments`. It describes WHAT a scientist wants
printed, never HOW the robot does it:

    what belongs here                 what may never appear here
      the liquid and which vial         deck slot numbers
      where the patterns go             calibrated aspiration heights
      how far apart the droplets are    air gap / push out / blow out
      the droplet volume                flow rates
      how many layers, and the rests    pipette identity or tip choice
      the print order                   labware load names or namespaces
                                        droplet XY coordinates
                                        Python of any kind

Everything in the right-hand column is laboratory-owned and lives in a registered
machine profile. The absolute droplet coordinates are computed later, by the
frozen geometry engine inside
``src/protocols/printing/02_printing_four_clover.py``. Neither a scientist nor an
agent supplies them.
"""
from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from ..canonical import canonical_json_bytes, canonical_sha256


SCHEMA_VERSION = "four-clover-experiment-job/v1"
PLAN_SCHEMA_VERSION = "resolved-clover-plan/v1"

#: Paper positions the substrate labware actually provides.
PAPER_WELL_PATTERN = r"^[A-H](?:[1-9]|1[0-2])$"
#: Vial positions the registered 20 mL rack provides.
VIAL_WELL_PATTERN = r"^[AB][1-4]$"
IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_]*$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"

DROPLET_KEYS = ("d1", "d2", "d3", "d4")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── the scientist's experiment ────────────────────────────────────────────────────

class CloverMetadataV1(_Strict):
    experiment_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    notes: dict[str, str] = Field(default_factory=dict)


class CloverSourceV1(_Strict):
    """The one liquid a human pours into the vial rack before the run."""

    liquid_id: str = Field(pattern=IDENTIFIER_PATTERN, max_length=60)
    display_name: str = Field(min_length=1, max_length=120)
    well: str = Field(pattern=VIAL_WELL_PATTERN)
    loaded_volume_ul: float = Field(gt=0, le=20000)
    minimum_remaining_ul: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _reserve_below_load(self) -> Self:
        if self.minimum_remaining_ul >= self.loaded_volume_ul:
            raise ValueError(
                f"source {self.liquid_id!r} reserves at least as much as it loads"
            )
        return self


class CloverGeometryV1(_Strict):
    """Half-offsets from the pattern centre, the executor's own convention.

    ``half_width_mm`` is the distance from the centre to a droplet, so two
    opposing droplets end up TWICE that far apart. A clover described as "4 mm
    separation" has ``half_width_mm: 2.0``.
    """

    half_width_mm: float = Field(gt=0, le=40)
    half_height_mm: float = Field(gt=0, le=40)


class CloverPlacementV1(_Strict):
    """One four-droplet pattern, positioned relative to a paper well."""

    name: str = Field(pattern=IDENTIFIER_PATTERN, max_length=60)
    reference_well: str = Field(pattern=PAPER_WELL_PATTERN)
    x_offset_mm: float = Field(default=0.0, ge=-60, le=60)
    y_offset_mm: float = Field(default=0.0, ge=-60, le=60)
    #: Omit to use the experiment's ``default_geometry``.
    geometry: CloverGeometryV1 | None = None
    #: Omit to use the experiment's ``printing.layers``.
    layers: int | None = Field(default=None, ge=1, le=50)


class CloverPrintingV1(_Strict):
    """How the droplets are laid down. No air handling, no heights, no motions."""

    droplet_volume_ul: float = Field(gt=0, le=20)
    #: Repeated depositions of the WHOLE pattern.
    layers: int = Field(default=1, ge=1, le=50)
    inter_drop_delay_s: float = Field(default=0.0, ge=0, le=7200)
    inter_layer_delay_s: float = Field(default=0.0, ge=0, le=7200)
    inter_clover_delay_s: float = Field(default=0.0, ge=0, le=7200)
    order: Literal["clover_by_clover", "position_by_position"] = "clover_by_clover"


class CloverExperimentSpecV1(_Strict):
    metadata: CloverMetadataV1
    source: CloverSourceV1
    printing: CloverPrintingV1
    default_geometry: CloverGeometryV1
    clovers: list[CloverPlacementV1] = Field(min_length=1, max_length=96)

    @model_validator(mode="after")
    def _names_are_unique(self) -> Self:
        names = [clover.name for clover in self.clovers]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"clover names must be unique; repeated: {', '.join(duplicates)}"
            )
        return self


# ── the laboratory's hardware ─────────────────────────────────────────────────────

class CloverDeckSlotV1(_Strict):
    slot: int = Field(ge=1, le=11)
    load_name: str = Field(min_length=1)
    namespace: str | None = None
    version: int | None = Field(default=None, ge=1)


class CloverDeckV1(_Strict):
    source: CloverDeckSlotV1
    paper: CloverDeckSlotV1
    tiprack_p20: CloverDeckSlotV1

    @model_validator(mode="after")
    def _slots_are_unique(self) -> Self:
        slots = [self.source.slot, self.paper.slot, self.tiprack_p20.slot]
        if len(set(slots)) != len(slots):
            raise ValueError(f"deck slots must be unique, got {slots}")
        return self


class CloverPipetteV1(_Strict):
    name: str = Field(min_length=1)
    mount: Literal["left", "right"]


class CloverSourceHandlingV1(_Strict):
    kind: str = Field(min_length=1)
    aspirate_height_mm: float = Field(gt=0, le=60)
    park_height_mm: float = Field(ge=0, le=60)


class CloverPrintReleaseV1(_Strict):
    dispense_height_mm: float = Field(ge=0, le=40)
    pre_air_chase_ul: float = Field(default=0.0, ge=0, le=20)
    air_gap_ul: float = Field(default=0.0, ge=0, le=20)
    air_gap_height_mm: float = Field(default=5.0, ge=0, le=40)
    push_out_ul: float = Field(default=0.0, ge=0, le=20)
    blow_out: bool = True


class CloverPaperBoundsV1(_Strict):
    x_dimension_mm: float = Field(gt=0)
    y_dimension_mm: float = Field(gt=0)
    grid_inset_x_mm: float = Field(ge=0)
    grid_inset_y_mm: float = Field(ge=0)
    boundary_mode: Literal["grid", "labware"] = "grid"
    edge_margin_mm: float = Field(ge=0)


class CloverValidationV1(_Strict):
    mode: Literal["warn", "error"] = "warn"
    min_intra_clover_distance_mm: float = Field(default=0.0, ge=0)
    min_inter_clover_distance_mm: float = Field(default=0.0, ge=0)
    droplet_radius_mm: float = Field(default=0.0, ge=0)
    allow_duplicate_droplet_positions: bool = False


class CloverTipsV1(_Strict):
    return_tips: bool = True
    print_tip: str = Field(pattern=r"^[A-H](?:[1-9]|1[0-2])$")


class CloverFlowRatesV1(_Strict):
    aspirate_ul_s: float = Field(gt=0, le=100)
    dispense_ul_s: float = Field(gt=0, le=100)


class CloverSafetyV1(_Strict):
    p20_max_volume_ul: float = Field(gt=0, le=1000)
    expected_source_slot: int = Field(ge=1, le=11)


class CloverMachineV1(_Strict):
    """Laboratory-owned hardware. Referenced by an experiment, never restated."""

    robot_type: Literal["OT-2"] = "OT-2"
    api_level: Literal["2.15"] = "2.15"
    protocol_version: int = Field(ge=1)
    deck: CloverDeckV1
    pipette: CloverPipetteV1
    source_handling: CloverSourceHandlingV1
    print_release: CloverPrintReleaseV1
    paper_bounds: CloverPaperBoundsV1
    validation: CloverValidationV1
    tips: CloverTipsV1
    flow_rates: CloverFlowRatesV1
    safety: CloverSafetyV1

    @model_validator(mode="after")
    def _source_slot_matches_safety(self) -> Self:
        if self.deck.source.slot != self.safety.expected_source_slot:
            raise ValueError(
                f"deck.source.slot {self.deck.source.slot} does not match "
                f"safety.expected_source_slot {self.safety.expected_source_slot}"
            )
        return self


# ── the complete job ──────────────────────────────────────────────────────────────

class FourCloverExperimentJobV1(_Strict):
    """One validated four-clover experiment, identified by its content hash."""

    schema_version: Literal["four-clover-experiment-job/v1"] = SCHEMA_VERSION
    job_id: str = Field(pattern=SHA256_PATTERN)
    machine: CloverMachineV1
    experiment: CloverExperimentSpecV1

    @model_validator(mode="after")
    def _job_id_matches_content(self, info: ValidationInfo) -> Self:
        if bool(info.context and info.context.get("skip_job_id_validation", False)):
            return self
        if self.job_id != self.job_sha256():
            raise ValueError(
                "job_id does not match the canonical content hash of this job"
            )
        return self

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


# ── the resolved plan ─────────────────────────────────────────────────────────────

class ResolvedDropletV1(_Strict):
    key: Literal["d1", "d2", "d3", "d4"]
    offset_x_mm: float
    offset_y_mm: float
    x_mm: float
    y_mm: float
    z_mm: float


class ResolvedCloverV1(_Strict):
    name: str
    reference_well: str
    center_offset_x_mm: float
    center_offset_y_mm: float
    center_x_mm: float
    center_y_mm: float
    geometry_source: Literal["default", "override"]
    layers: int
    droplets: list[ResolvedDropletV1] = Field(min_length=4, max_length=4)


class ResolvedCloverTotalsV1(_Strict):
    clover_count: int = Field(ge=1)
    droplets_per_clover: Literal[4] = 4
    layer_total: int = Field(ge=1)
    deposit_count: int = Field(ge=1)
    printed_liquid_ul: float = Field(ge=0)
    execution_steps: int = Field(ge=1)
    tip_count: Literal[1] = 1
    configured_delay_s: float = Field(ge=0)


class ResolvedCloverSourceV1(_Strict):
    status: Literal["PASS"] = "PASS"
    liquid_id: str
    well: str
    loaded_volume_ul: float
    required_volume_ul: float
    remaining_volume_ul: float
    minimum_remaining_ul: float
    submersion_volume_ul: float
    submerged_margin_ul: float


class ResolvedCloverPlanV1(_Strict):
    """Everything physical about the run, fully determined and inspectable."""

    schema_version: Literal["resolved-clover-plan/v1"] = PLAN_SCHEMA_VERSION
    plan_id: str = Field(pattern=SHA256_PATTERN)
    job_id: str = Field(pattern=SHA256_PATTERN)
    experiment_id: str
    order: Literal["clover_by_clover", "position_by_position"]
    droplet_volume_ul: float = Field(gt=0)
    paper_surface_mm: float
    dispense_standoff_mm: float
    absolute_dispense_mm: float
    piston_load_ul: float = Field(gt=0)
    usable_box: dict[str, float]
    clovers: list[ResolvedCloverV1] = Field(min_length=1)
    execution_order: list[dict[str, Any]]
    totals: ResolvedCloverTotalsV1
    source: ResolvedCloverSourceV1
    minimum_intra_clover_distance_mm: float | None = None
    minimum_inter_clover_distance_mm: float | None = None
    warnings: list[str] = Field(default_factory=list)
    #: The exact mapping handed to the deterministic executor.
    executor_config: dict[str, Any]

    @model_validator(mode="after")
    def _plan_id_matches_content(self, info: ValidationInfo) -> Self:
        if bool(info.context and info.context.get("skip_plan_id_validation", False)):
            return self
        if self.plan_id != self.plan_sha256():
            raise ValueError(
                "plan_id does not match the canonical content hash of this plan"
            )
        return self

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

    def plan_sha256(self) -> str:
        return canonical_sha256(self.canonical_payload())

    def physical_payload(self) -> dict[str, Any]:
        """Only what the robot physically does, for ground-truth comparison.

        Deliberately excludes ids, titles, notes, clover names, and the executor
        mapping, so two configurations that describe the same experiment in
        different words still compare equal.
        """
        return {
            "order": self.order,
            "droplet_volume_ul": round(self.droplet_volume_ul, 6),
            "absolute_dispense_mm": round(self.absolute_dispense_mm, 6),
            "piston_load_ul": round(self.piston_load_ul, 6),
            "source_well": self.source.well,
            "required_volume_ul": round(self.source.required_volume_ul, 6),
            "totals": {
                "clover_count": self.totals.clover_count,
                "layer_total": self.totals.layer_total,
                "deposit_count": self.totals.deposit_count,
                "execution_steps": self.totals.execution_steps,
                "tip_count": self.totals.tip_count,
                "configured_delay_s": round(self.totals.configured_delay_s, 6),
            },
            "delays": {
                "inter_drop_s": round(
                    float(self.executor_config["printing"]["inter_drop_delay_s"]), 6
                ),
                "inter_layer_s": round(
                    float(self.executor_config["printing"]["inter_layer_delay_s"]), 6
                ),
                "inter_clover_s": round(
                    float(self.executor_config["printing"]["inter_clover_delay_s"]), 6
                ),
            },
            "tips": {
                "print_tip": str(self.executor_config["tips"]["p20"]["print_tip"]),
                "return_tips": bool(self.executor_config["tips"]["return_tips"]),
            },
            # Coordinates sorted so the order the patterns were listed in does not
            # change the fingerprint - only where the droplets actually land does.
            "droplets": sorted(
                [
                    round(droplet.x_mm, 6),
                    round(droplet.y_mm, 6),
                    round(droplet.z_mm, 6),
                    clover.layers,
                ]
                for clover in self.clovers
                for droplet in clover.droplets
            ),
        }

    def physical_sha256(self) -> str:
        return canonical_sha256(self.physical_payload())
