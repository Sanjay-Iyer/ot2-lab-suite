"""
src/core/materials.py
=====================
Materials / vial-source schema + validation for the vial->dilution->paper-print
workflow. A *material* is a labelled liquid stored in one vial of a custom rack
(water, ethanol, a nanoparticle stock, a wash solution, ...). Steps reference
materials by name, so the Python protocol never hardcodes "water" or "ethanol" —
they are configured here.

This module:
  * defines the `Material` schema,
  * builds a labware index from labware/*.json (loadName -> wells, depth, filename
    match) so vial names and the filename==loadName rule can be validated,
  * validates a materials block with **conservative volume accounting**: a
    material's initial volume must cover every aspiration of it plus air gaps,
    overage, and a dead-volume allowance.

The active rack's real loadName is `tuberack_3dprint_20ml_8vials_v2` (verified on
disk); an un-versioned `tuberack_3dprint_20ml_8vials` will fail the labware check
with a "did you mean" hint.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.core.print_groups import Issue  # reuse one Issue type across validators

# Known roles (free-form roles are allowed but warned about, to catch typos).
KNOWN_ROLES = {"solvent", "stock", "reagent", "wash", "dye", "other"}

# Default unusable volume left in a 20 mL vial (tip cannot reach the very bottom).
DEFAULT_DEAD_VOLUME_UL = 1000.0
LABWARE_DIR = Path(__file__).resolve().parent.parent.parent / "labware"


class MaterialsConfigError(ValueError):
    """Raised when a materials block is invalid (has ERROR issues)."""


class Material(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    labware: str                      # loadName of the rack holding this material
    vial: str                         # well name in that rack, e.g. "A1"
    initial_volume_ul: float = Field(gt=0)
    aspirate_height_mm: Optional[float] = None
    dead_volume_ul: float = Field(default=DEFAULT_DEAD_VOLUME_UL, ge=0)
    allow_shared_vial: bool = False   # opt-in to co-locating two materials in one vial


# ── Labware index (loadName -> geometry, filename match) ──────────────────────

@dataclass
class LabwareInfo:
    load_name: str
    filename: str
    filename_matches_loadname: bool
    wells: list[str]
    well_depth_mm: dict[str, float]


def build_labware_index(labware_dir: Path | str = LABWARE_DIR) -> dict[str, LabwareInfo]:
    """Index labware/*.json by internal loadName, recording the filename match."""
    index: dict[str, LabwareInfo] = {}
    for path in Path(labware_dir).glob("*.json"):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        load_name = d.get("parameters", {}).get("loadName")
        if not load_name:
            continue
        wells = d.get("wells", {})
        index[load_name] = LabwareInfo(
            load_name=load_name,
            filename=path.name,
            filename_matches_loadname=(path.stem == load_name),
            wells=list(wells.keys()),
            well_depth_mm={w: float(v.get("depth", 0.0)) for w, v in wells.items()},
        )
    return index


def parse_materials(config: Mapping[str, Any]) -> dict[str, Material]:
    """Parse the `materials:` block into {name: Material}. Empty if absent."""
    raw = config.get("materials") or {}
    return {name: Material(**body) for name, body in raw.items()}


def _suggest_labware(name: str, index: Mapping[str, LabwareInfo]) -> str:
    for load_name in index:
        if load_name.startswith(name) or name.startswith(load_name.rsplit("_v", 1)[0]):
            return f" Did you mean '{load_name}'?"
    return ""


def validate_materials(
    materials: Mapping[str, Material],
    consumed_ul: Optional[Mapping[str, float]] = None,
    referenced_names: Optional[set[str]] = None,
    labware_index: Optional[Mapping[str, LabwareInfo]] = None,
) -> list[Issue]:
    """Validate the materials block. Returns issues (errors + warnings).

    Parameters
    ----------
    materials       : parsed {name: Material}.
    consumed_ul     : {name: total uL aspirated} from the planner (transfers +
                      air gaps + overage). Volume sufficiency is checked against it.
    referenced_names: material names referenced by steps; unknown ones -> error.
    labware_index   : from build_labware_index(); built on demand if omitted.
    """
    index = labware_index if labware_index is not None else build_labware_index()
    issues: list[Issue] = []
    consumed_ul = consumed_ul or {}

    # Unknown material names referenced by steps.
    for ref in (referenced_names or set()):
        if ref not in materials:
            issues.append(Issue("error", "materials",
                                f"step references unknown material '{ref}' "
                                f"(defined: {sorted(materials)})."))

    seen_vials: dict[tuple[str, str], str] = {}
    for name, m in materials.items():
        where = f"materials.{name}"

        # Role sanity (warn on unknown roles to catch typos).
        if m.role not in KNOWN_ROLES:
            issues.append(Issue("warning", where,
                                f"role '{m.role}' is not one of {sorted(KNOWN_ROLES)} "
                                f"(allowed, but check for a typo)."))

        # Labware existence + filename==loadName consistency.
        info = index.get(m.labware)
        if info is None:
            issues.append(Issue("error", where,
                                f"labware '{m.labware}' has no JSON in labware/ with that "
                                f"loadName.{_suggest_labware(m.labware, index)}"))
            continue
        if not info.filename_matches_loadname:
            issues.append(Issue("error", where,
                                f"labware file '{info.filename}' does not match its internal "
                                f"loadName '{info.load_name}' (Opentrons requires filename==loadName)."))

        # Vial validity.
        if m.vial not in info.wells:
            issues.append(Issue("error", where,
                                f"vial '{m.vial}' is not a well of '{m.labware}' "
                                f"(valid: {info.wells})."))
        else:
            # Aspiration height sanity vs. the vial depth.
            depth = info.well_depth_mm.get(m.vial, 0.0)
            if m.aspirate_height_mm is not None:
                if m.aspirate_height_mm < 0:
                    issues.append(Issue("error", where,
                                        f"aspirate_height_mm {m.aspirate_height_mm} is negative."))
                elif m.aspirate_height_mm > depth:
                    issues.append(Issue("error", where,
                                        f"aspirate_height_mm {m.aspirate_height_mm} exceeds vial "
                                        f"depth {depth} mm (tip would be above the vial)."))

        # Duplicate vial assignment (two materials in one physical vial).
        key = (m.labware, m.vial)
        if key in seen_vials and not m.allow_shared_vial:
            issues.append(Issue("error", where,
                                f"vial {m.labware}:{m.vial} is already assigned to material "
                                f"'{seen_vials[key]}'. Set allow_shared_vial: true if intended."))
        seen_vials.setdefault(key, name)

        # Conservative volume accounting: consumed + dead volume must fit.
        need = float(consumed_ul.get(name, 0.0)) + m.dead_volume_ul
        if need > m.initial_volume_ul:
            issues.append(Issue("error", where,
                                f"insufficient volume: needs ~{need:g} uL (consumed "
                                f"{consumed_ul.get(name, 0.0):g} + dead {m.dead_volume_ul:g}) "
                                f"but only {m.initial_volume_ul:g} uL configured."))

    return issues


def raise_if_errors(issues: list[Issue]) -> None:
    errs = [i for i in issues if i.severity == "error"]
    if errs:
        raise MaterialsConfigError(
            "Invalid materials configuration:\n  " + "\n  ".join(str(i) for i in errs))
