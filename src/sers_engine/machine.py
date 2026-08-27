"""Authoritative laboratory machine profile.

Calibrated hardware behaviour lives in ``configs/machines/*.yaml`` and is
laboratory-owned.  The conversational agent chooses liquids, wells, volumes,
targets, and ordering; this module supplies how the tip approaches glass and
paper.  Nothing here may be overridden from an experiment except the paper
release height, and only inside the envelope the profile itself declares.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .schema import REPO_ROOT, SERSConfigError

DEFAULT_PROFILE = "configs/machines/ot2_sers_p20_v1.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FlowRates(_Strict):
    aspirate_ul_s: float = Field(gt=0)
    dispense_ul_s: float = Field(gt=0)


class ProfilePipette(_Strict):
    name: str
    mount: Literal["left", "right"]
    minimum_volume_ul: float = Field(gt=0)
    maximum_volume_ul: float = Field(gt=0)
    max_transfer_volume_ul: float = Field(gt=0)
    flow_rates: FlowRates


class ProfileLabware(_Strict):
    role_kind: Literal["plate", "vial_rack", "paper", "tiprack"]
    load_name: str
    namespace: str | None = None
    version: int | None = None
    definition_path: str | None = None
    well_diameter_mm: float | None = Field(default=None, gt=0)
    safe_max_volume_ul: float | None = Field(default=None, gt=0)
    aspirate_reference: Literal["bottom", "top"] | None = None
    aspirate_height_mm: float | None = None
    dispense_reference: Literal["bottom", "top"] | None = None
    dispense_height_mm: float | None = None


class PrintRelease(_Strict):
    dispense_reference: Literal["bottom", "top"]
    dispense_height_mm: float
    trailing_air_gap_ul: float = Field(ge=0)
    air_gap_height_mm: float = Field(ge=0)
    push_out_ul: float = Field(ge=0)
    blow_out: bool
    post_dispense_delay_s: float = Field(ge=0)


class TunableRange(_Strict):
    minimum: float
    maximum: float
    default: float

    @model_validator(mode="after")
    def _ordered(self) -> "TunableRange":
        if not self.minimum <= self.default <= self.maximum:
            raise ValueError("default must sit inside the tunable range")
        return self


class ExperimentTunable(_Strict):
    paper_dispense_height_mm: TunableRange


class Mixing(_Strict):
    plate_mix_bottom_offset_mm: float = Field(gt=0)


class MachineBlock(_Strict):
    robot_type: Literal["OT-2"]
    api_level: str
    pipette: ProfilePipette
    labware: list[ProfileLabware] = Field(min_length=1)
    print_release: PrintRelease
    experiment_tunable: ExperimentTunable
    mixing: Mixing


class MachineProfile(_Strict):
    profile_id: str
    machine: MachineBlock
    source_path: str | None = None

    def labware_for(self, kind: str) -> ProfileLabware:
        """The single approved labware definition for one deck role kind."""
        matches = [item for item in self.machine.labware if item.role_kind == kind]
        if not matches:
            raise SERSConfigError(
                f"machine profile {self.profile_id!r} approves no labware of kind {kind!r}; "
                "this needs human hardware confirmation, not an invented value"
            )
        if len(matches) > 1:
            raise SERSConfigError(
                f"machine profile {self.profile_id!r} lists {len(matches)} labware entries "
                f"for kind {kind!r}; exactly one must be approved"
            )
        return matches[0]

    @property
    def approved_kinds(self) -> list[str]:
        return sorted({item.role_kind for item in self.machine.labware})

    def clamp_paper_height(self, requested: float | None) -> tuple[float, str | None]:
        """Return (height, warning) for a requested paper release height."""
        window = self.machine.experiment_tunable.paper_dispense_height_mm
        if requested is None:
            return window.default, None
        if window.minimum <= requested <= window.maximum:
            return float(requested), None
        clamped = min(max(requested, window.minimum), window.maximum)
        return clamped, (
            f"requested paper dispense height {requested:g} mm is outside the "
            f"profile's validated {window.minimum:g}-{window.maximum:g} mm envelope; "
            f"clamped to {clamped:g} mm"
        )


@lru_cache(maxsize=8)
def load_machine_profile(path: str | None = None) -> MachineProfile:
    """Load and validate one laboratory machine profile."""
    relative = path or DEFAULT_PROFILE
    profile_path = Path(relative)
    if not profile_path.is_absolute():
        profile_path = REPO_ROOT / profile_path
    profile_path = profile_path.resolve()
    if not profile_path.is_file():
        raise SERSConfigError(f"machine profile not found: {profile_path}")
    try:
        payload = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SERSConfigError(f"cannot read machine profile {profile_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SERSConfigError(f"machine profile must be a YAML mapping: {profile_path}")
    payload["source_path"] = str(relative)
    try:
        return MachineProfile.model_validate(payload)
    except ValidationError as exc:
        raise SERSConfigError(f"invalid machine profile {profile_path}: {exc}") from exc


def profile_summary(profile: MachineProfile) -> dict[str, Any]:
    """Compact, JSON-safe description for agent tools and review output."""
    pipette = profile.machine.pipette
    return {
        "profile_id": profile.profile_id,
        "source_path": profile.source_path,
        "robot_type": profile.machine.robot_type,
        "api_level": profile.machine.api_level,
        "pipette": f"{pipette.name} on {pipette.mount} mount",
        "approved_labware": {
            item.role_kind: item.load_name for item in profile.machine.labware
        },
        "geometry": {
            item.role_kind: {
                "aspirate": f"{item.aspirate_reference}({item.aspirate_height_mm})",
                "dispense": f"{item.dispense_reference}({item.dispense_height_mm})",
            }
            for item in profile.machine.labware
            if item.aspirate_reference is not None
        },
    }
