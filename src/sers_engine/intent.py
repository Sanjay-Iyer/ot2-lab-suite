"""SERSExperimentV1 - the scientific intent contract the agent edits.

This layer holds what a scientist decides: which liquids, where they sit, what
dilutions to make, what to print where, and in what order.  It deliberately does
NOT hold calibrated geometry -- aspiration heights, dispense references, air-gap
mechanics and flow rates all come from the laboratory machine profile.

An intent document is turned into an executable ExperimentConfig by
sers_engine.resolver.  Nothing in this module touches the robot.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .machine import DEFAULT_PROFILE
from .schema import SERSConfigError
from .targets import TargetSpecError, resolve_targets

INTENT_SCHEMA_VERSION = "sers-experiment-intent/v1"

_WELL_RE = re.compile(r"^[A-H](?:[1-9]|1[0-2])$")
_LOCATION_RE = re.compile(r"^([A-Za-z0-9_]+)\s*:\s*([A-Ha-h](?:[1-9]|1[0-2]))$")


class _Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        allow_inf_nan=False,
    )


def _normalise_well(value: str) -> str:
    well = str(value).upper().strip()
    if not _WELL_RE.fullmatch(well):
        raise ValueError(f"invalid well {value!r}; use A1 through H12")
    return well


def parse_location(text: str) -> tuple[str, str]:
    """Split 'working_plate:A1' into its deck role and well."""
    match = _LOCATION_RE.match(str(text).strip())
    if not match:
        raise ValueError(
            f"invalid location {text!r}; use '<deck role>:<well>', e.g. working_plate:A1"
        )
    return match.group(1), match.group(2).upper()


class DeckAssignment(_Strict):
    """One piece of labware on one deck slot.

    The load name is not stated here: the machine profile decides which physical
    labware is approved for each kind, so an experiment cannot substitute an
    unvalidated plate or vial rack.
    """

    role: str = Field(min_length=1)
    kind: Literal["plate", "vial_rack", "paper", "tiprack"]
    slot: int = Field(ge=1, le=11)

    @field_validator("role")
    @classmethod
    def _role_is_identifier(cls, value: str) -> str:
        if not value.replace("_", "").isalnum():
            raise ValueError(
                f"invalid deck role {value!r}; use letters, digits and underscores"
            )
        return value


class LiquidDeclaration(_Strict):
    """A named liquid the operator physically loads before the run."""

    name: str = Field(min_length=1)
    labware: str = Field(min_length=1)
    well: str
    loaded_volume_ul: float = Field(gt=0)
    minimum_remaining_volume_ul: float = Field(default=0.0, ge=0)
    description: str | None = None

    @field_validator("well")
    @classmethod
    def _well(cls, value: str) -> str:
        return _normalise_well(value)


class DilutionStep(_Strict):
    """Prepare one condition in one working-plate well.

    Give either a scientific target (dilution_factor + final_volume_ul) or
    explicit stock_volume_ul + diluent_volume_ul.  The resolver does the
    arithmetic; the agent is never asked to.
    """

    step_type: Literal["dilution"] = "dilution"
    step_id: str = Field(min_length=1)
    label: str | None = None
    source: str = Field(min_length=1)
    diluent: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    dilution_factor: float | None = Field(default=None, ge=1)
    final_volume_ul: float | None = Field(default=None, gt=0)
    stock_volume_ul: float | None = Field(default=None, gt=0)
    diluent_volume_ul: float | None = Field(default=None, ge=0)
    mix_cycles: int = Field(default=5, ge=0)
    mix_volume_ul: float = Field(default=15.0, ge=0)
    pre_mix_cycles: int = Field(default=0, ge=0)
    pre_mix_volume_ul: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _one_way_of_specifying(self) -> "DilutionStep":
        parse_location(self.destination)
        by_target = self.dilution_factor is not None and self.final_volume_ul is not None
        by_volume = self.stock_volume_ul is not None and self.diluent_volume_ul is not None
        if by_target and by_volume:
            raise ValueError(
                f"dilution {self.step_id}: give either dilution_factor + final_volume_ul "
                "or stock_volume_ul + diluent_volume_ul, not both"
            )
        if not by_target and not by_volume:
            raise ValueError(
                f"dilution {self.step_id}: needs dilution_factor + final_volume_ul "
                "(preferred) or explicit stock_volume_ul + diluent_volume_ul"
            )
        if bool(self.mix_cycles) != bool(self.mix_volume_ul):
            raise ValueError(
                f"dilution {self.step_id}: mix_cycles and mix_volume_ul must both be "
                "zero or both positive"
            )
        if bool(self.pre_mix_cycles) != bool(self.pre_mix_volume_ul):
            raise ValueError(
                f"dilution {self.step_id}: pre_mix_cycles and pre_mix_volume_ul must "
                "both be zero or both positive"
            )
        return self

    @property
    def specified_by_target(self) -> bool:
        return self.dilution_factor is not None and self.final_volume_ul is not None


class PrintStep(_Strict):
    """Deposit one liquid onto explicit paper locations."""

    step_type: Literal["print"] = "print"
    step_id: str = Field(min_length=1)
    label: str | None = None
    source: str = Field(min_length=1)
    paper: str | None = None
    targets: list[str] = Field(min_length=1)
    drop_volume_ul: float = Field(gt=0)
    drops_per_target: int = Field(default=1, ge=1, le=50)
    tip_strategy: Literal["per_layer", "per_paper", "per_target"] = "per_layer"
    dispense_height_mm: float | None = None
    post_dispense_delay_s: float | None = Field(default=None, ge=0)

    @field_validator("targets")
    @classmethod
    def _targets_are_readable(cls, value: list[str]) -> list[str]:
        # Expand here as well as in the resolver so an unreadable target is
        # rejected at the moment it is proposed, not several steps later.
        try:
            resolve_targets(value)
        except TargetSpecError as exc:
            raise ValueError(str(exc)) from exc
        return value


class WaitStep(_Strict):
    """Hold the run for a fixed time, pipette parked clear."""

    step_type: Literal["wait"] = "wait"
    step_id: str = Field(min_length=1)
    label: str | None = None
    duration_s: float = Field(gt=0)
    reason: str | None = None


Step = Annotated[Union[DilutionStep, PrintStep, WaitStep], Field(discriminator="step_type")]


class TipPolicyIntent(_Strict):
    start_tip: str = "A1"
    return_tips: bool = False

    @field_validator("start_tip")
    @classmethod
    def _tip(cls, value: str) -> str:
        return _normalise_well(value)


class SERSExperimentV1(_Strict):
    """One coherent experiment: deck, liquids, and an ordered list of primitives."""

    schema_version: Literal[INTENT_SCHEMA_VERSION] = INTENT_SCHEMA_VERSION
    experiment_id: str = Field(min_length=1)
    experiment_name: str = Field(min_length=1)
    description: str | None = None
    machine_profile: str = DEFAULT_PROFILE
    deck: list[DeckAssignment] = Field(min_length=1)
    liquids: list[LiquidDeclaration] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    tips: TipPolicyIntent = Field(default_factory=TipPolicyIntent)

    # ---- lookups -----------------------------------------------------------
    @property
    def deck_by_role(self) -> dict[str, DeckAssignment]:
        return {item.role: item for item in self.deck}

    @property
    def liquid_by_name(self) -> dict[str, LiquidDeclaration]:
        return {item.name: item for item in self.liquids}

    @property
    def step_by_id(self) -> dict[str, Any]:
        return {item.step_id: item for item in self.steps}

    def roles_of_kind(self, kind: str) -> list[str]:
        return [item.role for item in self.deck if item.kind == kind]

    # ---- structural validation --------------------------------------------
    @model_validator(mode="after")
    def _coherent(self) -> "SERSExperimentV1":
        roles = self.deck_by_role
        if len(roles) != len(self.deck):
            raise ValueError("deck roles must be unique")
        by_slot: dict[int, list[str]] = {}
        for item in self.deck:
            by_slot.setdefault(item.slot, []).append(item.role)
        clashes = {slot: roles for slot, roles in by_slot.items() if len(roles) > 1}
        if clashes:
            detail = "; ".join(
                f"slot {slot} is assigned to {' and '.join(repr(r) for r in roles)}"
                for slot, roles in sorted(clashes.items())
            )
            raise ValueError(
                f"{detail}. Each slot holds one piece of labware - change the "
                "existing role's slot rather than adding a second role on top of it"
            )
        if not self.roles_of_kind("tiprack"):
            raise ValueError("the deck needs at least one tiprack")

        names = [item.name for item in self.liquids]
        if len(names) != len(set(names)):
            raise ValueError("liquid names must be unique")
        for liquid in self.liquids:
            spec = roles.get(liquid.labware)
            if spec is None:
                raise ValueError(
                    f"liquid {liquid.name!r} sits on unknown deck role {liquid.labware!r}"
                )
            if spec.kind in {"paper", "tiprack"}:
                raise ValueError(
                    f"liquid {liquid.name!r} cannot be declared on {spec.kind} "
                    f"{liquid.labware!r}"
                )

        ids = [item.step_id for item in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step_id values must be unique")

        seen: set[str] = set()
        for step in self.steps:
            if isinstance(step, DilutionStep):
                self._check_source(step.source, seen, f"dilution {step.step_id} source")
                self._check_source(step.diluent, seen, f"dilution {step.step_id} diluent")
                role, _ = parse_location(step.destination)
                spec = roles.get(role)
                if spec is None:
                    raise ValueError(
                        f"dilution {step.step_id} targets unknown deck role {role!r}"
                    )
                if spec.kind != "plate":
                    raise ValueError(
                        f"dilution {step.step_id} must land in a plate, not "
                        f"{spec.kind} {role!r}"
                    )
            elif isinstance(step, PrintStep):
                self._check_source(step.source, seen, f"print {step.step_id} source")
                paper_roles = self.roles_of_kind("paper")
                if step.paper is None:
                    if len(paper_roles) != 1:
                        raise ValueError(
                            f"print {step.step_id} must name a paper fixture; the deck "
                            f"has {len(paper_roles)}"
                        )
                elif step.paper not in paper_roles:
                    raise ValueError(
                        f"print {step.step_id} targets {step.paper!r}, which is not a "
                        "paper fixture"
                    )
            seen.add(step.step_id)
        return self

    def _check_source(self, reference: str, prepared: set[str], label: str) -> None:
        """A source is a declared liquid, an earlier step's product, or role:well."""
        if reference in self.liquid_by_name:
            return
        if reference in prepared:
            step = self.step_by_id[reference]
            if not isinstance(step, DilutionStep):
                raise ValueError(
                    f"{label} points at {reference!r}, which prepares no liquid"
                )
            return
        if reference in self.step_by_id:
            raise ValueError(
                f"{label} points at step {reference!r}, which runs later in the experiment"
            )
        try:
            role, _ = parse_location(reference)
        except ValueError as exc:
            raise ValueError(
                f"{label} {reference!r} is not a declared liquid, an earlier dilution, "
                "or a '<deck role>:<well>' location"
            ) from exc
        spec = self.deck_by_role.get(role)
        if spec is None:
            raise ValueError(f"{label} uses unknown deck role {role!r}")
        if spec.kind in {"paper", "tiprack"}:
            raise ValueError(f"{label} cannot aspirate from {spec.kind} {role!r}")


def validate_intent(payload: dict[str, Any]) -> SERSExperimentV1:
    """Parse one intent mapping, raising SERSConfigError on failure."""
    try:
        return SERSExperimentV1.model_validate(payload)
    except ValidationError as exc:
        raise SERSConfigError(str(exc)) from exc


def intent_as_dict(experiment: SERSExperimentV1 | dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-compatible, fully normalized intent mapping."""
    model = (
        experiment
        if isinstance(experiment, SERSExperimentV1)
        else validate_intent(experiment)
    )
    return model.model_dump(mode="json")
