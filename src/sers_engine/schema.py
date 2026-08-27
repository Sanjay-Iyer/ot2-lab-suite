"""Strict declarative schema for SERS dilution and paper-printing workflows."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


SCHEMA_VERSION = "sers-experiment/v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
_WELL_RE = re.compile(r"^[A-H](?:[1-9]|1[0-2])$")


class SERSConfigError(ValueError):
    """The experiment cannot be represented or executed safely."""


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        allow_inf_nan=False,
    )


class LabwareSpec(StrictModel):
    slot: int = Field(ge=1, le=11)
    kind: Literal["plate", "vial_rack", "paper", "tiprack"]
    load_name: str = Field(min_length=1)
    namespace: str | None = None
    version: int | None = Field(default=None, ge=1)
    definition_path: str | None = None
    safe_max_volume_ul: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _definition_identity_is_complete(self) -> "LabwareSpec":
        if self.kind == "tiprack" and self.safe_max_volume_ul is not None:
            raise ValueError("tip racks cannot declare a liquid-volume limit")
        if self.kind == "paper" and self.safe_max_volume_ul is not None:
            raise ValueError("paper fixtures are substrates, not liquid-volume vessels")
        if self.definition_path is not None and not self.definition_path.strip():
            raise ValueError("definition_path cannot be blank")
        if self.definition_path is not None and (
            self.namespace is None or self.version is None
        ):
            raise ValueError(
                "filesystem labware definitions require pinned namespace and version"
            )
        return self


class DeckLayout(StrictModel):
    labware: dict[str, LabwareSpec] = Field(min_length=1)
    tip_racks: dict[str, LabwareSpec] = Field(min_length=1)

    @field_validator("labware", "tip_racks")
    @classmethod
    def _role_names(cls, value: dict[str, LabwareSpec]) -> dict[str, LabwareSpec]:
        for role in value:
            if not role or not role.replace("_", "").isalnum():
                raise ValueError(f"invalid labware role {role!r}")
        return value

    @model_validator(mode="after")
    def _unique_slots_and_kinds(self) -> "DeckLayout":
        overlap = set(self.labware) & set(self.tip_racks)
        if overlap:
            raise ValueError(f"roles appear in both labware and tip_racks: {sorted(overlap)}")
        for role, spec in self.labware.items():
            if spec.kind == "tiprack":
                raise ValueError(f"deck_layout.labware.{role} must not be a tiprack")
        for role, spec in self.tip_racks.items():
            if spec.kind != "tiprack":
                raise ValueError(f"deck_layout.tip_racks.{role} must have kind=tiprack")
        all_specs = {**self.labware, **self.tip_racks}
        by_slot: dict[int, str] = {}
        for role, spec in all_specs.items():
            previous = by_slot.get(spec.slot)
            if previous is not None:
                raise ValueError(
                    f"deck slot {spec.slot} is assigned to both {previous!r} and {role!r}"
                )
            by_slot[spec.slot] = role
        # A paper fixture is required only when something actually prints onto
        # one; a dilution-only workflow has no substrate to carry. That check
        # lives in ExperimentConfig, where the print layers are visible.
        return self


class PipetteConfig(StrictModel):
    name: Literal["p20_single_gen2"] = "p20_single_gen2"
    mount: Literal["left", "right"] = "right"
    tip_rack_roles: list[str] = Field(min_length=1)
    minimum_volume_ul: float = Field(default=1.0, gt=0, le=20)
    maximum_volume_ul: float = Field(default=20.0, gt=0, le=20)
    max_transfer_volume_ul: float = Field(default=18.0, gt=0, le=18)
    transfer_air_gap_ul: float = Field(default=1.5, ge=0)
    air_gap_height_mm: float = Field(default=5.0, ge=0)
    aspirate_flow_rate_ul_s: float = Field(default=3.0, gt=0)
    dispense_flow_rate_ul_s: float = Field(default=3.0, gt=0)

    @model_validator(mode="after")
    def _p20_capacity(self) -> "PipetteConfig":
        if self.minimum_volume_ul >= self.maximum_volume_ul:
            raise ValueError("minimum_volume_ul must be below maximum_volume_ul")
        if self.max_transfer_volume_ul < self.minimum_volume_ul:
            raise ValueError("max_transfer_volume_ul is below the pipette minimum")
        if self.max_transfer_volume_ul + self.transfer_air_gap_ul > self.maximum_volume_ul:
            raise ValueError(
                "max_transfer_volume_ul + transfer_air_gap_ul exceeds P20 capacity"
            )
        if len(self.tip_rack_roles) != len(set(self.tip_rack_roles)):
            raise ValueError("pipette.tip_rack_roles contains duplicates")
        return self


class TipPolicy(StrictModel):
    start_tip: str = "A1"
    return_tips: bool = False

    @field_validator("start_tip")
    @classmethod
    def _normalise_tip(cls, value: str) -> str:
        well = value.upper()
        if not _WELL_RE.fullmatch(well):
            raise ValueError(f"invalid tip well {value!r}")
        return well


class LiquidSource(StrictModel):
    labware: str = Field(min_length=1)
    well: str = Field(min_length=2)
    bottom_offset_mm: float = Field(gt=0, le=50)
    loaded_volume_ul: float | None = Field(default=None, gt=0)
    minimum_remaining_volume_ul: float = Field(default=0.0, ge=0)
    material: str | None = None

    @field_validator("well")
    @classmethod
    def _normalise_well(cls, value: str) -> str:
        well = value.upper()
        if not _WELL_RE.fullmatch(well):
            raise ValueError(f"invalid well {value!r}")
        return well


class LiquidDestination(StrictModel):
    """Where liquid is delivered, and separately where the tip mixes.

    Dispensing is expressed against either the well bottom or its top, mirroring
    ``configs/machines/*.yaml`` (``dispense_reference`` / ``dispense_height_mm``).
    Delivering against the top keeps the tip clear of the rising liquid column,
    so a tip returning to a shared stock vessel cannot carry analyte back into
    it.  Mixing must reach the liquid, so it stays bottom-referenced.
    """

    labware: str = Field(min_length=1)
    well: str = Field(min_length=2)
    dispense_reference: Literal["bottom", "top"] = "bottom"
    dispense_offset_mm: float = Field(ge=-50, le=50)
    mix_bottom_offset_mm: float = Field(default=2.0, gt=0, le=50)
    initial_volume_ul: float = Field(default=0.0, ge=0)

    @field_validator("well")
    @classmethod
    def _normalise_well(cls, value: str) -> str:
        well = value.upper()
        if not _WELL_RE.fullmatch(well):
            raise ValueError(f"invalid well {value!r}")
        return well

    @model_validator(mode="after")
    def _offset_matches_reference(self) -> "LiquidDestination":
        if self.dispense_reference == "bottom" and self.dispense_offset_mm <= 0:
            raise ValueError(
                "bottom-referenced dispense_offset_mm must be above the well floor"
            )
        if self.dispense_reference == "top" and self.dispense_offset_mm > 0:
            raise ValueError(
                "top-referenced dispense_offset_mm must be zero or negative "
                "(measured down from the well rim)"
            )
        return self


class DilutionOperation(StrictModel):
    operation_id: str = Field(min_length=1)
    source: LiquidSource
    diluent: LiquidSource
    destination: LiquidDestination
    stock_volume_ul: float = Field(gt=0)
    # 0 uL is a legal, meaningful value: a 1x condition is a straight stock
    # transfer into the working plate with no diluent leg at all.
    diluent_volume_ul: float = Field(ge=0)
    pre_mix_cycles: int = Field(default=0, ge=0)
    pre_mix_volume_ul: float = Field(default=0.0, ge=0)
    mix_cycles: int = Field(default=5, ge=0)
    mix_volume_ul: float = Field(default=15.0, ge=0)
    blow_out: bool = True

    @model_validator(mode="after")
    def _mix_pairs(self) -> "DilutionOperation":
        if bool(self.pre_mix_cycles) != bool(self.pre_mix_volume_ul):
            raise ValueError("pre_mix_cycles and pre_mix_volume_ul must both be zero or positive")
        if bool(self.mix_cycles) != bool(self.mix_volume_ul):
            raise ValueError("mix_cycles and mix_volume_ul must both be zero or positive")
        source_key = (self.source.labware, self.source.well)
        diluent_key = (self.diluent.labware, self.diluent.well)
        destination_key = (self.destination.labware, self.destination.well)
        if source_key == diluent_key:
            raise ValueError("stock and diluent must use distinct source wells")
        if destination_key in {source_key, diluent_key}:
            raise ValueError("dilution destination must differ from both source wells")
        return self


class PaperTarget(StrictModel):
    labware: str = Field(min_length=1)
    wells: list[str] = Field(min_length=1)

    @field_validator("wells")
    @classmethod
    def _normalise_wells(cls, value: list[str]) -> list[str]:
        wells = [well.upper() for well in value]
        invalid = [well for well in wells if not _WELL_RE.fullmatch(well)]
        if invalid:
            raise ValueError(f"invalid paper target well(s): {invalid}")
        if len(wells) != len(set(wells)):
            raise ValueError("paper target wells contain duplicates")
        return wells


class PrintLayer(StrictModel):
    layer_name: str = Field(min_length=1)
    source_location: LiquidSource
    drop_volume_ul: float = Field(gt=0)
    drops_per_target: int = Field(default=1, ge=1, le=50)
    paper_targets: list[PaperTarget] = Field(min_length=1)
    dispense_height_mm: float = Field(default=0.5, ge=0, le=10)
    air_gap_ul: float = Field(default=1.5, ge=0)
    air_gap_height_mm: float = Field(default=5.0, ge=0)
    push_out_ul: float = Field(default=3.0, ge=0)
    blow_out: bool = True
    touch_tip: bool = False
    tip_strategy: Literal["per_layer", "per_paper", "per_target"] = "per_layer"
    post_dispense_delay_s: float = Field(default=0.0, ge=0)
    drying_time_s: float = Field(default=0.0, ge=0)
    park_height_mm: float = Field(default=50.0, ge=0, le=100)

    @property
    def target_count(self) -> int:
        """How many distinct paper locations this layer touches."""
        return sum(len(target.wells) for target in self.paper_targets)

    @property
    def total_deposits(self) -> int:
        """Individual droplets released, counting repeats on the same spot."""
        return self.target_count * self.drops_per_target

    @model_validator(mode="after")
    def _distinct_target_fixtures(self) -> "PrintLayer":
        roles = [target.labware for target in self.paper_targets]
        if len(roles) != len(set(roles)):
            raise ValueError("a print layer may list each paper fixture only once")
        if self.touch_tip:
            raise ValueError(
                "touch_tip is not supported on flat paper fixtures; use a non-contact "
                "dispense and blow-out"
            )
        return self


class WaitStep(StrictModel):
    """A timed hold with the pipette parked clear of the deck.

    Drying is a first-class primitive rather than a property of a print layer,
    so an experiment can hold between any two operations.
    """

    wait_id: str = Field(min_length=1)
    duration_s: float = Field(gt=0)
    reason: str | None = None
    park_labware: str | None = None
    park_well: str | None = None
    park_height_mm: float = Field(default=50.0, ge=0, le=100)

    @field_validator("park_well")
    @classmethod
    def _normalise_well(cls, value: str | None) -> str | None:
        if value is None:
            return None
        well = value.upper()
        if not _WELL_RE.fullmatch(well):
            raise ValueError(f"invalid park well {value!r}")
        return well

    @model_validator(mode="after")
    def _park_is_complete(self) -> "WaitStep":
        if (self.park_labware is None) != (self.park_well is None):
            raise ValueError("park_labware and park_well must be given together")
        return self


class WorkflowStep(StrictModel):
    kind: Literal["dilution", "print_layer", "wait"]
    ref: str = Field(min_length=1)


class SafetyConfig(StrictModel):
    live_execution_allowed: bool = False
    hardware_profile_verified: bool = False
    required_laptop_role: Literal["real-robot"] = "real-robot"

    @model_validator(mode="after")
    def _live_requires_verified_hardware(self) -> "SafetyConfig":
        if self.live_execution_allowed and not self.hardware_profile_verified:
            raise ValueError(
                "live_execution_allowed requires hardware_profile_verified=true"
            )
        return self


class ExperimentConfig(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    experiment_name: str = Field(min_length=1)
    description: str | None = None
    robot_type: Literal["OT-2"] = "OT-2"
    api_level: Literal["2.15"] = "2.15"
    deck_layout: DeckLayout
    pipette: PipetteConfig
    tips: TipPolicy = Field(default_factory=TipPolicy)
    dilutions: list[DilutionOperation] = Field(default_factory=list)
    print_layers: list[PrintLayer] = Field(default_factory=list)
    waits: list[WaitStep] = Field(default_factory=list)
    workflow: list[WorkflowStep] = Field(default_factory=list)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)

    def ordered_workflow(self) -> list[WorkflowStep]:
        if self.workflow:
            return self.workflow
        return [
            *(
                WorkflowStep(kind="dilution", ref=step.operation_id)
                for step in self.dilutions
            ),
            *(
                WorkflowStep(kind="print_layer", ref=layer.layer_name)
                for layer in self.print_layers
            ),
            *(WorkflowStep(kind="wait", ref=hold.wait_id) for hold in self.waits),
        ]

    @model_validator(mode="after")
    def _cross_validate(self) -> "ExperimentConfig":
        for role in self.pipette.tip_rack_roles:
            if role not in self.deck_layout.tip_racks:
                raise ValueError(f"pipette references unknown tip rack role {role!r}")

        dilution_by_id = {step.operation_id: step for step in self.dilutions}
        if len(dilution_by_id) != len(self.dilutions):
            raise ValueError("dilution operation_id values must be unique")
        layer_by_name = {layer.layer_name: layer for layer in self.print_layers}
        if len(layer_by_name) != len(self.print_layers):
            raise ValueError("print layer_name values must be unique")
        wait_by_id = {hold.wait_id: hold for hold in self.waits}
        if len(wait_by_id) != len(self.waits):
            raise ValueError("wait_id values must be unique")
        if not dilution_by_id and not layer_by_name:
            raise ValueError("experiment must contain a dilution or print layer")

        roles = self.deck_layout.labware

        def require_liquid_role(role: str, label: str) -> LabwareSpec:
            spec = roles.get(role)
            if spec is None:
                raise ValueError(f"{label} references unknown labware role {role!r}")
            if spec.kind == "paper":
                raise ValueError(f"{label} cannot use paper fixture {role!r} as liquid labware")
            return spec

        for step in self.dilutions:
            require_liquid_role(step.source.labware, f"dilution {step.operation_id} source")
            require_liquid_role(step.diluent.labware, f"dilution {step.operation_id} diluent")
            destination_spec = require_liquid_role(
                step.destination.labware, f"dilution {step.operation_id} destination"
            )
            if destination_spec.kind != "plate":
                raise ValueError(
                    f"dilution {step.operation_id} destination must be plate labware"
                )
            if step.pre_mix_volume_ul > self.pipette.maximum_volume_ul:
                raise ValueError(f"dilution {step.operation_id} pre-mix exceeds P20 capacity")
            if step.mix_volume_ul > self.pipette.maximum_volume_ul:
                raise ValueError(f"dilution {step.operation_id} mix exceeds P20 capacity")
            for label, volume in (
                ("stock", step.stock_volume_ul),
                ("diluent", step.diluent_volume_ul),
                ("pre-mix", step.pre_mix_volume_ul),
                ("mix", step.mix_volume_ul),
            ):
                if volume and volume < self.pipette.minimum_volume_ul:
                    raise ValueError(
                        f"dilution {step.operation_id} {label} volume is below "
                        "the P20 minimum"
                    )

        if self.print_layers and not any(
            spec.kind == "paper" for spec in roles.values()
        ):
            raise ValueError(
                "this experiment prints, so deck_layout needs at least one paper fixture"
            )

        for layer in self.print_layers:
            require_liquid_role(layer.source_location.labware, f"print layer {layer.layer_name}")
            if layer.drop_volume_ul < self.pipette.minimum_volume_ul:
                raise ValueError(f"print layer {layer.layer_name} is below the P20 minimum")
            if layer.drop_volume_ul + layer.air_gap_ul > self.pipette.maximum_volume_ul:
                raise ValueError(
                    f"print layer {layer.layer_name} liquid + air gap exceeds P20 capacity"
                )
            for target in layer.paper_targets:
                target_spec = roles.get(target.labware)
                if target_spec is None:
                    raise ValueError(
                        f"print layer {layer.layer_name} references unknown paper role "
                        f"{target.labware!r}"
                    )
                if target_spec.kind != "paper":
                    raise ValueError(
                        f"print layer {layer.layer_name} target {target.labware!r} "
                        "is not paper labware"
                    )

        for hold in self.waits:
            if hold.park_labware is not None and hold.park_labware not in roles:
                raise ValueError(
                    f"wait {hold.wait_id} parks above unknown labware role "
                    f"{hold.park_labware!r}"
                )

        workflow = self.ordered_workflow()
        lookups = {
            "dilution": dilution_by_id,
            "print_layer": layer_by_name,
            "wait": wait_by_id,
        }
        for item in workflow:
            if item.ref not in lookups[item.kind]:
                raise ValueError(f"workflow references unknown {item.kind} {item.ref!r}")
        expected_refs = {
            *(('dilution', ref) for ref in dilution_by_id),
            *(('print_layer', ref) for ref in layer_by_name),
            *(('wait', ref) for ref in wait_by_id),
        }
        actual_refs = [(item.kind, item.ref) for item in workflow]
        if len(actual_refs) != len(expected_refs) or set(actual_refs) != expected_refs:
            raise ValueError(
                "workflow must reference every declared dilution, print layer, "
                "and wait exactly once"
            )

        # Simulate nominal liquid balances in the declared execution order.  This
        # catches depleted source wells and overfilled working wells before motion.
        volumes: dict[tuple[str, str], float] = {}

        def initialise(source: LiquidSource, label: str) -> tuple[str, str]:
            key = (source.labware, source.well)
            if key not in volumes:
                if source.loaded_volume_ul is None:
                    raise ValueError(
                        f"{label} needs loaded_volume_ul because no earlier workflow "
                        "step prepares that well"
                    )
                volumes[key] = source.loaded_volume_ul
                configured_cap = roles[source.labware].safe_max_volume_ul
                if (
                    configured_cap is not None
                    and source.loaded_volume_ul > configured_cap + 1e-9
                ):
                    raise ValueError(
                        f"{label} declares {source.loaded_volume_ul:g} uL, above the "
                        f"configured {configured_cap:g} uL safe limit"
                    )
            return key

        def consume(source: LiquidSource, amount: float, label: str) -> None:
            key = initialise(source, label)
            remaining = volumes[key] - amount
            if remaining + 1e-9 < source.minimum_remaining_volume_ul:
                raise ValueError(
                    f"{label} needs {amount:g} uL plus "
                    f"{source.minimum_remaining_volume_ul:g} uL reserve, but only "
                    f"{volumes[key]:g} uL is available"
                )
            volumes[key] = remaining

        for item in workflow:
            if item.kind == "dilution":
                step = dilution_by_id[item.ref]
                source_key = initialise(step.source, f"dilution {item.ref} stock")
                if (
                    step.pre_mix_cycles
                    and step.pre_mix_volume_ul > volumes[source_key] + 1e-9
                ):
                    raise ValueError(
                        f"dilution {item.ref} pre-mix volume exceeds stock present"
                    )
                consume(step.source, step.stock_volume_ul, f"dilution {item.ref} stock")
                consume(step.diluent, step.diluent_volume_ul, f"dilution {item.ref} diluent")
                destination_key = (step.destination.labware, step.destination.well)
                current = volumes.get(destination_key, step.destination.initial_volume_ul)
                final = current + step.stock_volume_ul + step.diluent_volume_ul
                destination_spec = roles[step.destination.labware]
                cap = destination_spec.safe_max_volume_ul
                if cap is None:
                    raise ValueError(
                        f"dilution destination {step.destination.labware!r} must declare "
                        "safe_max_volume_ul"
                    )
                if final > cap + 1e-9:
                    raise ValueError(
                        f"dilution {item.ref} would fill {step.destination.well} to "
                        f"{final:g} uL, above the configured {cap:g} uL safe limit"
                    )
                if step.mix_cycles and step.mix_volume_ul > final:
                    raise ValueError(f"dilution {item.ref} mix volume exceeds liquid present")
                volumes[destination_key] = final
            elif item.kind == "print_layer":
                layer = layer_by_name[item.ref]
                consume(
                    layer.source_location,
                    layer.total_deposits * layer.drop_volume_ul,
                    f"print layer {item.ref}",
                )
        return self

    def minimum_well_volumes(self) -> dict[tuple[str, str], float]:
        """Worst-case liquid left in every well that is aspirated from.

        This mirrors the balance ledger in :meth:`_cross_validate`, which
        enforces the volume limits.  It exists separately so the orchestrator
        can turn those volumes into real liquid heights once the labware
        geometry is loaded, and confirm the tip is actually submerged.
        """
        dilution_by_id = {step.operation_id: step for step in self.dilutions}
        layer_by_name = {layer.layer_name: layer for layer in self.print_layers}
        volumes: dict[tuple[str, str], float] = {}
        lowest: dict[tuple[str, str], float] = {}

        def seed(location: LiquidSource) -> tuple[str, str]:
            key = (location.labware, location.well)
            if key not in volumes:
                volumes[key] = float(location.loaded_volume_ul or 0.0)
                lowest[key] = volumes[key]
            return key

        def draw(location: LiquidSource, amount: float) -> None:
            key = seed(location)
            volumes[key] -= amount
            lowest[key] = min(lowest[key], volumes[key])

        for item in self.ordered_workflow():
            if item.kind == "dilution":
                step = dilution_by_id[item.ref]
                draw(step.source, step.stock_volume_ul)
                draw(step.diluent, step.diluent_volume_ul)
                key = (step.destination.labware, step.destination.well)
                volumes[key] = (
                    volumes.get(key, step.destination.initial_volume_ul)
                    + step.stock_volume_ul
                    + step.diluent_volume_ul
                )
                lowest[key] = min(lowest.get(key, volumes[key]), volumes[key])
            elif item.kind == "print_layer":
                layer = layer_by_name[item.ref]
                draw(layer.source_location, layer.total_deposits * layer.drop_volume_ul)
        return lowest

    def aspiration_points(self) -> list[tuple[str, str, float, str]]:
        """Every (labware role, well, bottom offset, label) the pipette draws from."""
        points: list[tuple[str, str, float, str]] = []
        for step in self.dilutions:
            points.append(
                (
                    step.source.labware,
                    step.source.well,
                    step.source.bottom_offset_mm,
                    f"dilution {step.operation_id} stock",
                )
            )
            points.append(
                (
                    step.diluent.labware,
                    step.diluent.well,
                    step.diluent.bottom_offset_mm,
                    f"dilution {step.operation_id} diluent",
                )
            )
        for layer in self.print_layers:
            points.append(
                (
                    layer.source_location.labware,
                    layer.source_location.well,
                    layer.source_location.bottom_offset_mm,
                    f"print layer {layer.layer_name} source",
                )
            )
        return points

    @property
    def tips_required(self) -> int:
        dilution_tips = sum(
            1 + int(step.diluent_volume_ul > 0) + int(step.mix_cycles > 0)
            for step in self.dilutions
        )
        print_tips = 0
        for layer in self.print_layers:
            if layer.tip_strategy == "per_layer":
                print_tips += 1
            elif layer.tip_strategy == "per_paper":
                print_tips += len(layer.paper_targets)
            else:
                print_tips += layer.target_count
        return dilution_tips + print_tips


def validate_experiment_config(config_dict: dict[str, Any]) -> ExperimentConfig:
    """Parse one dictionary and return its validated, normalized model."""
    try:
        return ExperimentConfig.model_validate(config_dict)
    except ValidationError as exc:
        raise SERSConfigError(str(exc)) from exc


def load_experiment_config(path: str | Path) -> ExperimentConfig:
    """Load and validate one YAML experiment file."""
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise SERSConfigError(f"experiment config not found: {config_path}")
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SERSConfigError(f"cannot read experiment config {config_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SERSConfigError(f"experiment config must contain a YAML mapping: {config_path}")
    return validate_experiment_config(payload)


def config_as_dict(config: ExperimentConfig | dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-compatible, fully normalized configuration dictionary."""
    model = config if isinstance(config, ExperimentConfig) else validate_experiment_config(config)
    return model.model_dump(mode="json")
