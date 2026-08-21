"""Measured, AI-friendly templates for the regular 96-well V1 family."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from src.labware.schemas import WellPlate96SpecV1
from src.utils.paths import LABWARE_CONFIG_DIR


WELL_PLATE_96_TEMPLATE_DIR = LABWARE_CONFIG_DIR / "well_plate_96"


def list_well_plate_96_templates() -> tuple[str, ...]:
    if not WELL_PLATE_96_TEMPLATE_DIR.is_dir():
        return ()
    return tuple(sorted(path.stem for path in WELL_PLATE_96_TEMPLATE_DIR.glob("*.yaml")))


def resolve_well_plate_96_template(name: str) -> Path:
    """Resolve a bare allowlisted template name without arbitrary file access."""
    if Path(name).name != name or name in {"", ".", ".."}:
        raise ValueError("template must be a bare name from list_well_plate_96_templates")
    stem = name[:-5] if name.endswith(".yaml") else name
    path = WELL_PLATE_96_TEMPLATE_DIR / f"{stem}.yaml"
    if not path.is_file():
        raise FileNotFoundError(
            f"unknown 96-well template {name!r}; available: {list_well_plate_96_templates()}"
        )
    return path


def load_well_plate_96_template(name: str) -> WellPlate96SpecV1:
    path = resolve_well_plate_96_template(name)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")
    return WellPlate96SpecV1.model_validate(payload)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def derive_well_plate_96_template(
    name: str,
    patch: dict[str, Any],
) -> WellPlate96SpecV1:
    """Apply a concise nested patch, then revalidate the complete physical spec."""
    base = load_well_plate_96_template(name).model_dump(mode="json")
    return WellPlate96SpecV1.model_validate(_deep_merge(base, patch))
