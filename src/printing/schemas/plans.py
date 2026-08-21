"""Strict canonical schemas for fully resolved printing execution plans.

These models sit after workflow-specific scientific/configuration resolution and
before any future protocol-generation adapter.  They contain data, never generated
Python or arbitrary model reasoning.
"""
from __future__ import annotations

import json
import math
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from ..canonical import canonical_json_bytes, canonical_sha256


WELL_PATTERN = r"^[A-H](?:[1-9]|1[0-2])$"
ID_PATTERN = r"^[a-z][a-z0-9_-]*$"


class StrictPlanModel(BaseModel):
    """Immutable, finite-number, unknown-field-rejecting plan model."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
        frozen=True,
    )


class XYPointV1(StrictPlanModel):
    x_mm: float
    y_mm: float


class PlanProvenanceV1(StrictPlanModel):
    resolved_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_experiment_config_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description=(
            "Canonical PrintingExperimentConfigV1 identity approved by the "
            "scientist. This evidence-chain link is excluded from physical identity."
        ),
    )
    source_experiment_config_reference: str | None = Field(default=None, min_length=1)
    source_job_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description=(
            "PrintJobV1 identity that led to this plan. This evidence-chain link "
            "is intentionally excluded from the physical plan identity."
        ),
    )
    source_request_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    source_config_reference: str
    source_protocol: str
    source_protocol_family: Literal["plate_well_direct_v9", "four_clover_v12"]
    source_builder_version: int = Field(ge=1)
    adapter_id: Literal[
        "plate-well-direct-v9-to-plan/v1",
        "four-clover-v12-to-plan/v1",
    ]


class SourceDefinitionV1(StrictPlanModel):
    source_id: str = Field(pattern=ID_PATTERN)
    material_id: str = Field(min_length=1)
    labware_role: str = Field(min_length=1)
    labware_name: str = Field(min_length=1)
    deck_slot: int = Field(ge=1, le=11)
    well: str = Field(pattern=WELL_PATTERN)
    aspirate_height_mm: float = Field(gt=0)
    park_height_mm: float = Field(ge=0)


class DestinationLabwareV1(StrictPlanModel):
    destination_labware_id: str = Field(pattern=ID_PATTERN)
    labware_role: str = Field(min_length=1)
    labware_name: str = Field(min_length=1)
    deck_slot: int = Field(ge=1, le=11)
    dispense_height_mm: float = Field(ge=0)


class PipetteV1(StrictPlanModel):
    name: str = Field(min_length=1)
    mount: Literal["left", "right"]
    max_volume_ul: float = Field(gt=0)


class TipStrategyV1(StrictPlanModel):
    rack_labware_name: str = Field(min_length=1)
    rack_deck_slot: int = Field(ge=1, le=11)
    tip_well: str = Field(pattern=WELL_PATTERN)
    return_tip: bool
    held_for_complete_run: bool


class FlowRatesV1(StrictPlanModel):
    aspirate_ul_s: float = Field(gt=0)
    dispense_ul_s: float = Field(gt=0)


class MachineConfigurationV1(StrictPlanModel):
    """Resolved machine-owned references, separate from deposit provenance."""

    robot_type: Literal["OT-2"] = "OT-2"
    api_level: Literal["2.15"] = "2.15"
    pipette: PipetteV1
    tip_strategy: TipStrategyV1
    flow_rates: FlowRatesV1
    sources: list[SourceDefinitionV1] = Field(min_length=1)
    destination_labware: DestinationLabwareV1


class WellDestinationV1(StrictPlanModel):
    kind: Literal["well"] = "well"
    well: str = Field(pattern=WELL_PATTERN)
    row: Literal["A", "B", "C", "D", "E", "F", "G", "H"]
    column: int = Field(ge=1, le=12)
    paper_xy_mm: XYPointV1
    deck_xy_mm: XYPointV1

    @model_validator(mode="after")
    def _well_matches_row_and_column(self) -> Self:
        if self.well != f"{self.row}{self.column}":
            raise ValueError("destination well must equal row + column")
        return self


class CloverCoordinateDestinationV1(StrictPlanModel):
    kind: Literal["coordinate"] = "coordinate"
    reference_well: str = Field(pattern=WELL_PATTERN)
    reference_well_paper_xy_mm: XYPointV1
    reference_well_deck_xy_mm: XYPointV1
    center_translation_mm: XYPointV1
    paper_center_xy_mm: XYPointV1
    deck_center_xy_mm: XYPointV1
    point_offset_mm: XYPointV1
    paper_xy_mm: XYPointV1
    deck_xy_mm: XYPointV1

    @model_validator(mode="after")
    def _coordinates_are_consistent(self) -> Self:
        expected_paper_center = (
            self.reference_well_paper_xy_mm.x_mm + self.center_translation_mm.x_mm,
            self.reference_well_paper_xy_mm.y_mm + self.center_translation_mm.y_mm,
        )
        actual_paper_center = (
            self.paper_center_xy_mm.x_mm,
            self.paper_center_xy_mm.y_mm,
        )
        expected_deck_center = (
            self.reference_well_deck_xy_mm.x_mm + self.center_translation_mm.x_mm,
            self.reference_well_deck_xy_mm.y_mm + self.center_translation_mm.y_mm,
        )
        actual_deck_center = (
            self.deck_center_xy_mm.x_mm,
            self.deck_center_xy_mm.y_mm,
        )
        expected_paper = (
            self.paper_center_xy_mm.x_mm + self.point_offset_mm.x_mm,
            self.paper_center_xy_mm.y_mm + self.point_offset_mm.y_mm,
        )
        actual_paper = (self.paper_xy_mm.x_mm, self.paper_xy_mm.y_mm)
        expected_deck = (
            self.deck_center_xy_mm.x_mm + self.point_offset_mm.x_mm,
            self.deck_center_xy_mm.y_mm + self.point_offset_mm.y_mm,
        )
        actual_deck = (self.deck_xy_mm.x_mm, self.deck_xy_mm.y_mm)
        if not all(
            math.isclose(actual, expected, abs_tol=1e-9)
            for actual, expected in zip(actual_paper_center, expected_paper_center)
        ):
            raise ValueError(
                "paper center must equal reference well + center translation"
            )
        if not all(
            math.isclose(actual, expected, abs_tol=1e-9)
            for actual, expected in zip(actual_deck_center, expected_deck_center)
        ):
            raise ValueError(
                "deck center must equal reference well + center translation"
            )
        if not all(
            math.isclose(actual, expected, abs_tol=1e-9)
            for actual, expected in zip(actual_paper, expected_paper)
        ):
            raise ValueError("paper coordinate must equal center + point offset")
        if not all(
            math.isclose(actual, expected, abs_tol=1e-9)
            for actual, expected in zip(actual_deck, expected_deck)
        ):
            raise ValueError("deck coordinate must equal center + point offset")
        return self


ResolvedDestinationV1 = Annotated[
    WellDestinationV1 | CloverCoordinateDestinationV1,
    Field(discriminator="kind"),
]


class WellGridProvenanceV1(StrictPlanModel):
    kind: Literal["well_grid"] = "well_grid"
    layer_index: int = Field(ge=1)
    row: Literal["A", "B", "C", "D", "E", "F", "G", "H"]
    column: int = Field(ge=1, le=12)
    replicate_index: int = Field(ge=1)


class FourCloverProvenanceV1(StrictPlanModel):
    kind: Literal["four_clover"] = "four_clover"
    layer_index: int = Field(ge=1)
    clover_index: int = Field(ge=1)
    clover_name: str = Field(min_length=1)
    design_point: Literal["D1", "D2", "D3", "D4"]


DepositProvenanceV1 = Annotated[
    WellGridProvenanceV1 | FourCloverProvenanceV1,
    Field(discriminator="kind"),
]


class DepositionV1(StrictPlanModel):
    liquid_volume_ul: float = Field(gt=0)
    pre_air_chase_ul: float = Field(ge=0)
    trailing_air_gap_ul: float = Field(ge=0)
    air_gap_height_mm: float = Field(ge=0)
    piston_dispense_ul: float = Field(gt=0)
    push_out_ul: float = Field(ge=0)
    blow_out: bool

    @model_validator(mode="after")
    def _piston_matches_components(self) -> Self:
        expected = (
            self.pre_air_chase_ul
            + self.liquid_volume_ul
            + self.trailing_air_gap_ul
        )
        if not math.isclose(self.piston_dispense_ul, expected, abs_tol=1e-9):
            raise ValueError(
                "piston_dispense_ul must equal pre-air + liquid + trailing air"
            )
        return self


class DepositTimingV1(StrictPlanModel):
    delay_before_s: float = Field(default=0.0, ge=0)
    post_dispense_delay_s: float = Field(default=0.0, ge=0)
    rest_after_s: float = Field(default=0.0, ge=0)


class DepositInstructionV1(StrictPlanModel):
    sequence_index: int = Field(ge=1)
    source_id: str = Field(pattern=ID_PATTERN)
    destination_labware_id: str = Field(pattern=ID_PATTERN)
    destination: ResolvedDestinationV1
    deposition: DepositionV1
    provenance: DepositProvenanceV1
    timing: DepositTimingV1

    @model_validator(mode="after")
    def _destination_matches_provenance(self) -> Self:
        if self.destination.kind == "well" and self.provenance.kind != "well_grid":
            raise ValueError("well destinations require well_grid provenance")
        if (
            self.destination.kind == "coordinate"
            and self.provenance.kind != "four_clover"
        ):
            raise ValueError("coordinate destinations require four_clover provenance")
        return self


class StandardPlanTimingV1(StrictPlanModel):
    kind: Literal["standard_layer_passes"] = "standard_layer_passes"
    post_dispense_delay_s: float = Field(ge=0)
    inter_pass_rest_s: float = Field(ge=0)
    total_rest_s: float = Field(ge=0)


class CloverPlanTimingV1(StrictPlanModel):
    kind: Literal["four_clover"] = "four_clover"
    inter_drop_delay_s: float = Field(ge=0)
    inter_layer_delay_s: float = Field(ge=0)
    inter_clover_delay_s: float = Field(ge=0)


ResolvedPlanTimingV1 = Annotated[
    StandardPlanTimingV1 | CloverPlanTimingV1,
    Field(discriminator="kind"),
]


class PlanTotalsV1(StrictPlanModel):
    deposit_count: int = Field(ge=1)
    total_liquid_ul: float = Field(gt=0)
    total_air_ul: float = Field(ge=0)
    total_piston_dispense_ul: float = Field(gt=0)
    total_delay_s: float = Field(ge=0)
    source_count: int = Field(ge=1)
    layer_count: int = Field(ge=1)
    replicate_count: int | None = Field(default=None, ge=1)
    clover_count: int | None = Field(default=None, ge=1)


class ResolvedPrintPlanV1(StrictPlanModel):
    """Canonical ordered description of an already resolved printing experiment."""

    schema_version: Literal["resolved-print-plan/v1"] = "resolved-print-plan/v1"
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflow_id: str = Field(min_length=1)
    provenance: PlanProvenanceV1
    machine: MachineConfigurationV1
    order_mode: Literal[
        "layer_then_row_then_column",
        "clover_by_clover",
        "position_by_position",
    ]
    timing: ResolvedPlanTimingV1
    deposits: list[DepositInstructionV1] = Field(min_length=1)
    totals: PlanTotalsV1

    @classmethod
    def from_content(cls, **content: Any) -> Self:
        """Validate content, derive its identity, then validate the sealed plan."""
        if "plan_id" in content:
            raise ValueError("plan_id is derived and cannot be supplied to from_content")
        provisional = cls.model_validate(
            {"plan_id": "0" * 64, **content},
            context={"skip_plan_id_validation": True},
        )
        payload = provisional.model_dump(mode="json", exclude={"plan_id"})
        return cls.model_validate({"plan_id": provisional.plan_sha256(), **payload})

    def canonical_payload(self) -> dict[str, Any]:
        """Return the resolved physical hash payload.

        ``source_job_sha256`` links two independently sealed artifacts. It is not
        itself a physical operation and therefore does not perturb the existing
        Stage 1 plan identity.
        """
        payload = self.model_dump(mode="json", exclude={"plan_id"})
        payload["provenance"].pop("source_job_sha256", None)
        payload["provenance"].pop("source_experiment_config_sha256", None)
        payload["provenance"].pop("source_experiment_config_reference", None)
        return payload

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_payload())

    def canonical_json(self) -> str:
        return self.canonical_bytes().decode("utf-8")

    def plan_sha256(self) -> str:
        return canonical_sha256(self.canonical_payload())

    @model_validator(mode="after")
    def _validate_plan(self, info: ValidationInfo) -> Self:
        sequences = [deposit.sequence_index for deposit in self.deposits]
        expected_sequences = list(range(1, len(self.deposits) + 1))
        if sequences != expected_sequences:
            raise ValueError("deposit sequence indexes must be continuous, unique, and ordered")

        source_ids = [source.source_id for source in self.machine.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source definitions must have unique source_id values")
        known_sources = set(source_ids)
        known_destination = self.machine.destination_labware.destination_labware_id
        for deposit in self.deposits:
            if deposit.source_id not in known_sources:
                raise ValueError(f"deposit references unknown source_id {deposit.source_id!r}")
            if deposit.destination_labware_id != known_destination:
                raise ValueError("deposit references an unknown destination labware")
            if deposit.deposition.piston_dispense_ul > self.machine.pipette.max_volume_ul:
                raise ValueError("deposit piston dispense exceeds pipette capacity")
            if deposit.deposition.push_out_ul > self.machine.pipette.max_volume_ul:
                raise ValueError("deposit push-out exceeds pipette capacity")

        layers = {deposit.provenance.layer_index for deposit in self.deposits}
        layer_count = max(layers)
        if layers != set(range(1, layer_count + 1)):
            raise ValueError("layer indexes must be continuous starting at 1")

        well_deposits = [
            deposit for deposit in self.deposits if deposit.provenance.kind == "well_grid"
        ]
        clover_deposits = [
            deposit for deposit in self.deposits if deposit.provenance.kind == "four_clover"
        ]
        replicate_count = (
            len({deposit.provenance.replicate_index for deposit in well_deposits})
            if well_deposits
            else None
        )
        clover_count = (
            len({deposit.provenance.clover_index for deposit in clover_deposits})
            if clover_deposits
            else None
        )
        calculated = {
            "deposit_count": len(self.deposits),
            "total_liquid_ul": sum(
                deposit.deposition.liquid_volume_ul for deposit in self.deposits
            ),
            "total_air_ul": sum(
                deposit.deposition.pre_air_chase_ul
                + deposit.deposition.trailing_air_gap_ul
                for deposit in self.deposits
            ),
            "total_piston_dispense_ul": sum(
                deposit.deposition.piston_dispense_ul for deposit in self.deposits
            ),
            "total_delay_s": sum(
                deposit.timing.delay_before_s
                + deposit.timing.post_dispense_delay_s
                + deposit.timing.rest_after_s
                for deposit in self.deposits
            ),
            "source_count": len(self.machine.sources),
            "layer_count": layer_count,
            "replicate_count": replicate_count,
            "clover_count": clover_count,
        }
        supplied = self.totals.model_dump()
        for field, expected in calculated.items():
            actual = supplied[field]
            if isinstance(expected, float):
                if not math.isclose(actual, expected, abs_tol=1e-9):
                    raise ValueError(f"totals.{field} does not equal the deposits")
            elif actual != expected:
                raise ValueError(f"totals.{field} does not equal the deposits")

        skip_hash = bool(
            info.context and info.context.get("skip_plan_id_validation", False)
        )
        if not skip_hash and self.plan_id != self.plan_sha256():
            raise ValueError("plan_id does not match the canonical plan SHA-256")
        return self


def parse_resolved_print_plan_json(data: str | bytes) -> ResolvedPrintPlanV1:
    """Strict JSON round-trip entry point used by artifacts and handoff tests."""
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    payload = json.loads(data)
    return ResolvedPrintPlanV1.model_validate(payload)
