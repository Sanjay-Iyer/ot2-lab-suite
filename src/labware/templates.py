"""
Template loading and inheritance for custom labware.

A *template* is simply an existing labware YAML config under
``configs/labware/``. Deriving from one is how a new definition gets geometry
that somebody actually measured, instead of geometry a model invented.

Two distinct operations, deliberately kept separate:

``copy``        Same physical object, new identity. Only identity/metadata may
                be overridden; any geometry override is rejected. Coordinates
                are recomputed from the same inputs, so they come out identical.

``regenerate``  Deliberately different physical object. Geometry overrides are
                allowed and every dependent coordinate is recalculated.

The distinction matters because "make another one called X" and "make one like
this but with wider spacing" are different requests, and silently treating the
first as the second is how a calibrated definition drifts.

Templates are never mutated — every load returns a fresh dict.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.labware.schemas import CommonLabwareSpec, RectangularGridSpec, WellPlate96SpecV1
from src.utils.paths import LABWARE_CONFIG_DIR

# Keys a config may carry that are not spec fields.
NON_SPEC_KEYS = {"output_dir"}

# Overriding any of these changes the physical object, so they are refused in
# ``copy`` mode.
GEOMETRY_FIELDS = frozenset({
    "rows", "cols",
    "x_offset", "y_offset", "x_spacing", "y_spacing",
    "shape", "diameter", "x_length", "y_length",
    "depth", "well_z", "total_liquid_volume",
    "x_dimension", "y_dimension", "z_dimension",
    "is_tiprack", "tip_length", "tip_overlap",
})

# Safe to change when copying: they describe the record, not the object.
IDENTITY_FIELDS = frozenset({
    "load_name", "display_name", "brand", "brand_ids",
    "namespace", "version", "display_category", "plate_format",
    "well_bottom_shape", "is_magnetic_module_compatible", "quirks",
})


class TemplateNotFoundError(FileNotFoundError):
    """Raised when a named template config does not exist."""


def list_templates(config_dir: Optional[Path] = None) -> List[str]:
    """Names of every labware config usable as a template."""
    directory = Path(config_dir) if config_dir else LABWARE_CONFIG_DIR
    if not directory.exists():
        return []
    return sorted(
        p.stem for p in directory.glob("*.yaml") if not p.name.startswith("_")
    )


def resolve_config_path(name_or_path: str, config_dir: Optional[Path] = None) -> Path:
    """Accept a bare name, a filename, or a path, and return the config path."""
    directory = Path(config_dir) if config_dir else LABWARE_CONFIG_DIR

    candidate = Path(name_or_path)
    if candidate.exists() and candidate.is_file():
        return candidate.resolve()

    filename = name_or_path if name_or_path.endswith(".yaml") else f"{name_or_path}.yaml"
    in_config_dir = directory / filename
    if in_config_dir.exists():
        return in_config_dir

    available = list_templates(directory)
    raise TemplateNotFoundError(
        f"labware config {name_or_path!r} not found in {directory}. "
        f"Available: {available or '(none)'}"
    )


def load_template_dict(name_or_path: str, config_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Read a config to a plain dict. The file on disk is never modified."""
    path = resolve_config_path(name_or_path, config_dir)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping of field names to values.")
    return copy.deepcopy(data)


def spec_from_dict(data: Dict[str, Any]) -> CommonLabwareSpec | WellPlate96SpecV1:
    """Validate a legacy flat config or a family-discriminated V1 config."""
    payload = {k: v for k, v in data.items() if k not in NON_SPEC_KEYS}
    if payload.get("family") == "well_plate_96":
        return WellPlate96SpecV1(**payload)
    return RectangularGridSpec(**payload)


def load_spec(
    name_or_path: str,
    config_dir: Optional[Path] = None,
) -> CommonLabwareSpec | WellPlate96SpecV1:
    """Load and validate one labware config."""
    return spec_from_dict(load_template_dict(name_or_path, config_dir))


def derive_spec(
    template: str,
    overrides: Dict[str, Any],
    mode: str = "regenerate",
    config_dir: Optional[Path] = None,
) -> RectangularGridSpec:
    """Build a new spec from ``template`` plus ``overrides``.

    ``mode="copy"``       identity-only changes; geometry overrides are refused.
    ``mode="regenerate"`` geometry may change and coordinates are recalculated.

    A new ``load_name`` is always required: two definitions sharing a loadName
    would collide in ``labware/`` and in the robot's custom-labware store.
    """
    if mode not in ("copy", "regenerate"):
        raise ValueError(f"mode must be 'copy' or 'regenerate', got {mode!r}")

    base = load_template_dict(template, config_dir)
    base.pop("output_dir", None)

    unknown = set(overrides) - set(RectangularGridSpec.model_fields)
    if unknown:
        raise ValueError(
            f"unknown override field(s): {sorted(unknown)}. "
            f"Valid fields: {sorted(RectangularGridSpec.model_fields)}"
        )

    if mode == "copy":
        geometry_overrides = sorted(set(overrides) & GEOMETRY_FIELDS)
        if geometry_overrides:
            raise ValueError(
                f"mode='copy' keeps the physical geometry of {template!r} unchanged, but "
                f"{geometry_overrides} would alter it. Use mode='regenerate' if you really "
                f"want a different physical object, or drop those overrides to make a copy."
            )

    new_load_name = overrides.get("load_name")
    if not new_load_name:
        raise ValueError(
            f"deriving from {template!r} requires a new `load_name` override — "
            f"reusing '{base.get('load_name')}' would collide with the original definition."
        )
    if new_load_name == base.get("load_name"):
        raise ValueError(
            f"`load_name` override {new_load_name!r} is identical to the template's. "
            f"Choose a distinct name so the new definition does not replace the original."
        )

    merged = {**base, **overrides}

    # A derived definition is a NEW labware identity, not a revision of the
    # template, so its version restarts at 1 unless the caller said otherwise.
    # (Bumping `version` is for revising a definition that keeps its loadName.)
    if "version" not in overrides:
        merged["version"] = 1

    return spec_from_dict(merged)


def spec_to_yaml(
    spec: CommonLabwareSpec | WellPlate96SpecV1,
    header: Optional[str] = None,
) -> str:
    """Render a spec back out as a labware YAML config."""
    body = yaml.dump(
        spec.to_config_dict(),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return f"{header.rstrip()}\n\n{body}" if header else body
