"""Single Stage 3 registry for printing substrates, materials, and profiles."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Literal

from .config import REPO_ROOT
from .schemas.jobs import FourCloverCenterV1, LabwareReferenceV1, MaterialReferenceV1


PatternType = Literal["well_selection", "four_clover"]


class PrintingReferenceError(ValueError):
    """A named scientific reference is unknown or incompatible."""


@dataclass(frozen=True)
class PrintingProfile:
    pattern_type: PatternType
    workflow_name: str
    default_material_id: str
    substrate_id: str


@dataclass(frozen=True)
class RegisteredMaterial:
    material_id: str
    display_name: str
    pattern_type: PatternType
    workflow_name: str
    is_default: bool = True


@dataclass(frozen=True)
class RegisteredSubstrate:
    substrate_id: str
    load_name: str
    namespace: str
    version: int
    template_id: str
    aliases: tuple[str, ...]
    is_default: bool = True


_SUBSTRATES = (
    RegisteredSubstrate(
        substrate_id="paper_print_96_flat",
        load_name="paper_print_96_flat",
        namespace="custom_beta",
        version=1,
        template_id="well_plate_96/paper_print_96_flat_v1",
        aliases=(
            "standard paper plate",
            "our standard paper plate",
            "standard paper",
            "paper plate",
        ),
    ),
)

_MATERIALS = (
    RegisteredMaterial("sample", "Sample", "well_selection", "plate_well_direct_v9"),
    RegisteredMaterial("BP", "BP", "four_clover", "four_clover_air_chase"),
)

_PROFILES = (
    PrintingProfile(
        "well_selection", "plate_well_direct_v9", "sample", "paper_print_96_flat"
    ),
    PrintingProfile(
        "four_clover", "four_clover_air_chase", "BP", "paper_print_96_flat"
    ),
)


def labware_definition_sha256(definition: dict[str, Any]) -> str:
    """Use the semantic definition digest emitted by the Labware Specialist."""
    payload = json.dumps(definition, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def list_registered_substrates() -> tuple[RegisteredSubstrate, ...]:
    return _SUBSTRATES


def list_registered_materials() -> tuple[RegisteredMaterial, ...]:
    return _MATERIALS


def profile_for_pattern(pattern_type: str) -> PrintingProfile:
    try:
        return next(
            profile for profile in _PROFILES if profile.pattern_type == pattern_type
        )
    except StopIteration as exc:
        raise PrintingReferenceError(
            f"unsupported printing pattern {pattern_type!r}; V1 supports well_selection and four_clover"
        ) from exc


def resolve_registered_substrate(name: str | None = None) -> LabwareReferenceV1:
    requested = _normalized(name or "paper_print_96_flat")
    registered = next(
        (
            item
            for item in _SUBSTRATES
            if requested == _normalized(item.substrate_id)
            or requested == _normalized(item.load_name)
            or requested in {_normalized(alias) for alias in item.aliases}
        ),
        None,
    )
    if registered is None:
        available = ", ".join(item.substrate_id for item in _SUBSTRATES)
        raise PrintingReferenceError(
            f"unknown substrate reference {name!r}; registered substrates: {available}"
        )
    path = REPO_ROOT / "labware" / f"{registered.load_name}.json"
    if not path.is_file():
        raise PrintingReferenceError(
            f"registered substrate definition is missing: {path.name}"
        )
    definition = json.loads(path.read_text(encoding="utf-8"))
    return LabwareReferenceV1(
        load_name=registered.load_name,
        namespace=registered.namespace,
        version=registered.version,
        definition_sha256=labware_definition_sha256(definition),
        template_id=registered.template_id,
    )


def resolve_registered_material(
    material_id: str | None,
    *,
    pattern_type: str,
) -> MaterialReferenceV1:
    profile = profile_for_pattern(pattern_type)
    requested = material_id or profile.default_material_id
    material = next(
        (
            item
            for item in _MATERIALS
            if item.material_id.lower() == requested.lower()
            and item.pattern_type == pattern_type
        ),
        None,
    )
    if material is None:
        available = ", ".join(
            item.material_id for item in _MATERIALS if item.pattern_type == pattern_type
        )
        raise PrintingReferenceError(
            f"unknown material reference {requested!r} for {pattern_type}; available: {available}"
        )
    return MaterialReferenceV1(
        material_id=material.material_id,
        display_name=material.display_name,
    )


def resolve_clover_placement_preset(
    replicate_count: int,
    *,
    preset: str = "standard",
) -> list[FourCloverCenterV1]:
    if preset != "standard":
        raise PrintingReferenceError(
            f"unknown clover placement preset {preset!r}; available: standard"
        )
    if replicate_count == 1:
        return [
            FourCloverCenterV1(
                name="air_chase_5ul",
                reference_well="E6",
                x_offset_mm=4.5,
                y_offset_mm=4.5,
            )
        ]
    if replicate_count == 3:
        return [
            FourCloverCenterV1(
                name=f"clover_{index}",
                reference_well=well,
                x_offset_mm=4.5,
                y_offset_mm=4.5,
            )
            for index, well in enumerate(("E3", "E6", "E9"), 1)
        ]
    raise PrintingReferenceError(
        "the standard clover placement preset supports 1 or 3 replicates; "
        "supply explicit centers for another count"
    )
