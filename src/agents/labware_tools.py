"""
Labware creation tools for the OT-2 LangChain agent.

Discovery / inspection:
  list_labware_families       — labware families that are actually implemented
  list_labware_presets        — built-in starting layouts
  list_labware_configs        — list YAML configs in configs/labware/
  list_generated_labware      — list JSON definitions in labware/
  describe_labware_config     — human-readable summary of a config

Generation:
  create_labware_config       — write a new YAML config from user-specified params
  generate_labware_from_config— render a YAML config → validated Opentrons JSON
  generate_custom_labware     — structured params → validated Opentrons JSON, one call
  derive_custom_labware       — inherit an existing definition's geometry, then
                                copy it under a new identity or regenerate it
                                with explicit overrides
  validate_labware_definition — re-run every validation layer on a JSON on disk

The agent supplies *parameters*; src/labware/ computes every coordinate and
validates the result. No tool here lets a model write well coordinates directly.
"""

import json
import hashlib
import sys
import yaml
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, Dict, Optional

from langchain.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from src.utils.paths import LABWARE_CONFIG_DIR, LABWARE_OUTPUT_DIR, PROJECT_ROOT

# Import generator internals so we don't shell-out
sys.path.insert(0, str(PROJECT_ROOT))
from scripts.generate_labware import LabwareSpec, make_labware, spec_from_yaml, _SPEC_FIELDS
from src.labware.builder import LabwareOutputExistsError, read_labware_json
from src.labware.families import get_family, list_families
from src.labware.geometry import generate_rectangular_grid
from src.labware.pipeline import GenerationResult, generate_from_config, generate_labware
from src.labware.schemas import RectangularGridSpec, WellPlate96SpecV1
from src.labware.templates import (
    GEOMETRY_FIELDS,
    TemplateNotFoundError,
    derive_spec,
    list_templates,
)
from src.labware.validation import validate_all
from src.labware.well_plate_96_templates import (
    list_well_plate_96_templates as list_well_plate_96_template_names,
    load_well_plate_96_template,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_config(name_or_path: str) -> Path:
    """Accept a bare name ('my_plate'), filename ('my_plate.yaml'), or full path."""
    p = Path(name_or_path)
    if p.is_absolute() and p.exists():
        return p
    # Try as-is relative to cwd
    if p.exists():
        return p.resolve()
    # Try inside LABWARE_CONFIG_DIR
    candidate = LABWARE_CONFIG_DIR / (name_or_path if name_or_path.endswith(".yaml") else f"{name_or_path}.yaml")
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"Labware config '{name_or_path}' not found. "
        f"Expected it in {LABWARE_CONFIG_DIR} or as an absolute path."
    )


def _format_result(result: GenerationResult, next_steps: str = "") -> str:
    """Render a GenerationResult as the agent-readable string these tools return."""
    if not result.success:
        lines = [
            f"FAILED to generate '{result.load_name}' — nothing was written.",
            f"  validation : {result.validation.summary()}",
        ]
        lines += [f"  error      : {e}" for e in result.errors]
        return "\n".join(lines)

    lines = [
        f"Labware JSON generated: {result.output_path}  ({result.write_status})",
        f"  load_name  : {result.load_name}",
        f"  family     : {result.labware_family}",
        f"  positions  : {result.position_count}",
        f"  validation : {result.validation.summary()}",
    ]
    for warning in result.validation.warnings:
        lines.append(f"  WARNING    : {warning.code}: {warning.message}")
    definition = result.definition or {}
    lines.append(
        f"\nTo use in a protocol: load_name='{result.load_name}', "
        f"namespace='{definition.get('namespace')}', version={definition.get('version')}"
    )
    if next_steps:
        lines.append(next_steps)
    return "\n".join(lines)


def _structured_result(result: GenerationResult) -> str:
    """Stable machine-readable result for the bounded V1 agent tool."""
    definition_bytes = (
        json.dumps(result.definition, sort_keys=True, ensure_ascii=False).encode("utf-8")
        if result.definition is not None
        else b""
    )
    payload = {
        "success": result.success,
        "family": result.labware_family,
        "load_name": result.load_name,
        "position_count": result.position_count,
        "output_path": str(result.output_path) if result.output_path else None,
        "write_status": result.write_status,
        "definition_sha256": hashlib.sha256(definition_bytes).hexdigest() if definition_bytes else None,
        "validation": {
            "layers": dict(result.validation.layers),
            "issues": [
                {
                    "layer": issue.layer,
                    "severity": issue.severity,
                    "code": issue.code,
                    "message": issue.message,
                }
                for issue in result.validation.issues
            ],
        },
        "errors": list(result.errors),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


class Generate96WellLabwareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: WellPlate96SpecV1
    overwrite: bool = False


class Load96WellTemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_name: str = Field(min_length=1)


def _spec_to_yaml_dict(spec: LabwareSpec) -> Dict[str, Any]:
    """Convert a LabwareSpec to an ordered dict suitable for YAML output."""
    d: Dict[str, Any] = {}
    for f in dataclass_fields(spec):
        val = getattr(spec, f.name)
        d[f.name] = val
    return d


_COMMON_LAYOUTS: Dict[str, Dict[str, Any]] = {
    "6-well":  {"rows": 2, "cols": 3, "x_offset": 24.76, "y_offset": 65.52,
                "x_spacing": 39.12, "y_spacing": 39.12, "diameter": 35.0,
                "depth": 17.4, "total_liquid_volume": 16800, "plate_format": "irregular"},
    "12-well": {"rows": 3, "cols": 4, "x_offset": 24.88, "y_offset": 68.74,
                "x_spacing": 26.0,  "y_spacing": 26.0,  "diameter": 22.1,
                "depth": 17.5, "total_liquid_volume": 6000,  "plate_format": "irregular"},
    "24-well": {"rows": 4, "cols": 6, "x_offset": 17.05, "y_offset": 76.0,
                "x_spacing": 19.3,  "y_spacing": 19.3,  "diameter": 15.62,
                "depth": 17.4, "total_liquid_volume": 3400,  "plate_format": "irregular"},
    "48-well": {"rows": 6, "cols": 8, "x_offset": 18.16, "y_offset": 75.52,
                "x_spacing": 13.08, "y_spacing": 13.08, "diameter": 11.56,
                "depth": 17.4, "total_liquid_volume": 1600,  "plate_format": "irregular"},
    "96-well": {"rows": 8, "cols": 12, "x_offset": 14.38, "y_offset": 74.24,
                "x_spacing": 9.0,   "y_spacing": 9.0,   "diameter": 6.86,
                "depth": 10.67,"total_liquid_volume": 360,   "plate_format": "96Standard"},
    "384-well":{"rows": 16, "cols": 24, "x_offset": 11.75, "y_offset": 79.0,
                "x_spacing": 4.5,   "y_spacing": 4.5,   "diameter": 3.3,
                "depth": 11.43,"total_liquid_volume": 115,   "plate_format": "384Standard"},
    "reservoir-12-well": {
                "rows": 1, "cols": 12, "x_offset": 13.94, "y_offset": 42.74,
                "x_spacing": 9.0, "y_spacing": 0.0, "x_length": 8.2, "y_length": 71.2,
                "shape": "rectangular", "diameter": None,
                "depth": 39.22, "total_liquid_volume": 21000, "plate_format": "trough",
                "display_category": "reservoir"},
    # Geometry from Opentrons' published opentrons_15_tuberack_falcon_15ml_conical.
    # The previous estimate (x_offset 20.75, diameter 15.16) overhung the SBS
    # footprint by 0.57 mm at column 5 and now fails the footprint check.
    "tube-rack-15ml": {
                "rows": 3, "cols": 5, "x_offset": 13.88, "y_offset": 67.74,
                "x_spacing": 25.0, "y_spacing": 25.0, "diameter": 14.9,
                "depth": 117.5, "total_liquid_volume": 15000, "plate_format": "irregular",
                "display_category": "tubeRack", "well_bottom_shape": "v",
                "x_dimension": 127.76, "y_dimension": 85.48, "z_dimension": 124.35},
    "tube-rack-1.5ml": {
                "rows": 4, "cols": 6, "x_offset": 14.38, "y_offset": 74.24,
                "x_spacing": 20.0, "y_spacing": 20.0, "diameter": 9.0,
                "depth": 39.2, "total_liquid_volume": 1500, "plate_format": "irregular",
                "display_category": "tubeRack",
                "x_dimension": 127.76, "y_dimension": 85.48, "z_dimension": 45.0},
}


# ── Tools ─────────────────────────────────────────────────────────────────────

@tool
def list_labware_configs() -> str:
    """
    Lists all labware YAML configuration files in configs/labware/.
    Returns names that can be passed to describe_labware_config or generate_labware_from_config.
    """
    LABWARE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(LABWARE_CONFIG_DIR.glob("*.yaml"))
    if not files:
        return "No labware configs found in configs/labware/. Use create_labware_config to create one."
    lines = ["Labware configs in configs/labware/:"]
    for f in files:
        lines.append(f"  - {f.stem}  ({f.name})")
    lines.append(f"\nPass any name (without .yaml) to describe_labware_config or generate_labware_from_config.")
    return "\n".join(lines)


@tool
def list_generated_labware() -> str:
    """
    Lists all generated Opentrons labware JSON definition files in labware/.
    These are ready to import into the Opentrons app or reference in protocols.
    """
    LABWARE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(LABWARE_OUTPUT_DIR.glob("*.json"))
    if not files:
        return "No generated labware JSON files found in labware/. Use generate_labware_from_config to create one."
    lines = ["Generated labware definitions in labware/:"]
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            meta = data.get("metadata", {})
            display = meta.get("displayName", "?")
            category = meta.get("displayCategory", "?")
            params = data.get("parameters", {})
            load_name = params.get("loadName", f.stem)
            well_count = sum(len(col) for col in data.get("ordering", []))
            lines.append(f"  - {load_name}  [{category}, {well_count} wells] — \"{display}\"")
        except Exception:
            lines.append(f"  - {f.name}")
    return "\n".join(lines)


@tool
def list_labware_presets() -> str:
    """
    Lists built-in labware layout presets that can be used as starting points
    when calling create_labware_config. Pass the preset name as the 'preset' key.
    """
    lines = ["Built-in labware presets (use as 'preset' in create_labware_config):"]
    descriptions = {
        "6-well":            "6-well plate, ~16.8 mL/well, circular",
        "12-well":           "12-well plate, ~6 mL/well, circular",
        "24-well":           "24-well plate, ~3.4 mL/well, circular",
        "48-well":           "48-well plate, ~1.6 mL/well, circular",
        "96-well":           "96-well flat-bottom plate, 360 µL/well, SBS standard",
        "384-well":          "384-well plate, 115 µL/well, SBS standard",
        "reservoir-12-well": "12-column trough reservoir, 21 mL/column, rectangular",
        "tube-rack-15ml":    "15 × 15 mL falcon tube rack (3×5)",
        "tube-rack-1.5ml":   "24 × 1.5 mL Eppendorf tube rack (4×6)",
    }
    for name, desc in descriptions.items():
        lines.append(f"  - {name:25s}  {desc}")
    return "\n".join(lines)


@tool
def describe_labware_config(config_name: str) -> str:
    """
    Reads a labware YAML config and returns a human-readable summary of all parameters.
    config_name: bare name (e.g. 'corning_96_wellplate_360ul') or filename or full path.
    """
    try:
        path = _resolve_config(config_name)
    except FileNotFoundError as e:
        return str(e)

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        lines = [f"Labware config: {path.name}", "=" * 50]
        lines.append(f"  load_name        : {data.get('load_name', '(not set)')}")
        lines.append(f"  display_name     : {data.get('display_name', '(not set)')}")
        lines.append(f"  brand            : {data.get('brand', 'Custom')}")
        lines.append(f"  display_category : {data.get('display_category', 'wellPlate')}")
        lines.append(f"  plate_format     : {data.get('plate_format', 'irregular')}")
        rows = data.get("rows", "?")
        cols = data.get("cols", "?")
        try:
            total = int(rows) * int(cols)
        except Exception:
            total = "?"
        lines.append(f"  grid             : {rows} rows × {cols} cols = {total} wells")
        lines.append(f"  well_shape       : {data.get('shape', 'circular')}")
        if data.get("shape", "circular") == "circular":
            lines.append(f"  diameter         : {data.get('diameter', '?')} mm")
        else:
            lines.append(f"  x_length         : {data.get('x_length', '?')} mm")
            lines.append(f"  y_length         : {data.get('y_length', '?')} mm")
        lines.append(f"  depth            : {data.get('depth', '?')} mm")
        lines.append(f"  volume/well      : {data.get('total_liquid_volume', '?')} µL")
        lines.append(f"  well_bottom      : {data.get('well_bottom_shape', 'flat')}")
        lines.append(f"  footprint        : {data.get('x_dimension', 127.76)} × {data.get('y_dimension', 85.48)} × {data.get('z_dimension', '?')} mm")
        lines.append(f"  is_tiprack       : {data.get('is_tiprack', False)}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading config '{config_name}': {e}"


@tool
def create_labware_config(params: dict) -> str:
    """
    Creates a new labware YAML config file in configs/labware/ from user-specified parameters.

    Required keys in params:
      - load_name (str): unique identifier, lowercase letters/digits/underscores/dots only
      - display_name (str): human-readable name shown in the Opentrons app

    Optional keys (use a preset to fill in the rest, or override individually):
      - preset (str): one of the preset names from list_labware_presets() — fills in
                      grid layout, well geometry, and spacing automatically
      - rows (int), cols (int)
      - x_offset, y_offset, x_spacing, y_spacing (floats, mm)
      - shape: 'circular' or 'rectangular'
      - diameter (float, mm) — required if shape=circular
      - x_length, y_length (float, mm) — required if shape=rectangular
      - depth (float, mm)
      - total_liquid_volume (float, µL)
      - well_bottom_shape: 'flat', 'u', or 'v'
      - x_dimension, y_dimension, z_dimension (float, mm) — outer footprint
      - brand, brand_ids, namespace, version, display_category, plate_format
      - is_tiprack (bool), tip_length (float), tip_overlap (float)
      - is_magnetic_module_compatible (bool), quirks (list)
      - well_z (float or null) — leave null to auto-compute

    Example:
      {"load_name": "my_24well_plate", "display_name": "My 24-Well Plate",
       "preset": "24-well", "depth": 20.0, "total_liquid_volume": 4000}
    """
    # Extract and remove the preset key before passing to LabwareSpec
    preset_name = params.pop("preset", None)

    # Build base from preset if given
    base: Dict[str, Any] = {}
    if preset_name:
        preset_name = preset_name.lower().strip()
        if preset_name not in _COMMON_LAYOUTS:
            return (
                f"Unknown preset '{preset_name}'. "
                f"Valid presets: {', '.join(_COMMON_LAYOUTS.keys())}. "
                f"Call list_labware_presets() to see descriptions."
            )
        base.update(_COMMON_LAYOUTS[preset_name])

    # User params override preset
    base.update(params)

    # Validate required fields
    if "load_name" not in base:
        return "Error: 'load_name' is required in params."
    if "display_name" not in base:
        return "Error: 'display_name' is required in params."

    # Filter to only LabwareSpec fields (drop anything extra/unknown)
    unknown = set(base.keys()) - _SPEC_FIELDS
    if unknown:
        return (
            f"Error: Unknown parameter(s): {sorted(unknown)}. "
            f"Valid fields: {sorted(_SPEC_FIELDS)}. "
            f"Call list_labware_presets() or check the _template.yaml for valid keys."
        )

    # Attempt to build a LabwareSpec to validate
    try:
        spec = LabwareSpec(**base)
    except TypeError as e:
        return f"Error building labware spec: {e}"

    # Write YAML
    LABWARE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LABWARE_CONFIG_DIR / f"{spec.load_name}.yaml"

    yaml_dict = _spec_to_yaml_dict(spec)
    yaml_str = yaml.dump(yaml_dict, sort_keys=False, allow_unicode=True, default_flow_style=False)

    out_path.write_text(yaml_str, encoding="utf-8")

    rows, cols = spec.rows, spec.cols
    return (
        f"Labware config created: {out_path}\n"
        f"  load_name    : {spec.load_name}\n"
        f"  display_name : {spec.display_name}\n"
        f"  grid         : {rows} rows × {cols} cols = {rows * cols} wells\n"
        f"  well shape   : {spec.shape}, depth={spec.depth} mm, volume={spec.total_liquid_volume} µL\n\n"
        f"Next step: call generate_labware_from_config('{spec.load_name}') to produce the JSON definition."
    )


@tool
def generate_labware_from_config(config_name: str, overwrite: bool = False) -> str:
    """
    Generates a validated Opentrons schemaVersion-2 labware JSON definition from
    a YAML config. The JSON is written to labware/<load_name>.json and is ready
    to import into the Opentrons app or reference in protocols.

    Every validation layer (schema, geometry, json, opentrons) must pass before
    anything is written. If a layer fails, no file is created and the failure is
    reported.

    config_name: bare name (e.g. 'my_24well_plate'), filename, or full path.
    overwrite:   set True ONLY when the user has explicitly asked to replace an
                 existing definition. Default False refuses to change a labware
                 JSON that hardware may already be calibrated against.
    """
    try:
        config_path = _resolve_config(config_name)
    except FileNotFoundError as e:
        return str(e)

    try:
        result = generate_from_config(str(config_path), overwrite=overwrite)
    except (ValueError, TypeError) as e:
        return f"Config validation error in '{config_name}': {e}"

    return _format_result(
        result,
        next_steps=f"To import: upload {result.output_path} in the Opentrons App under Labware > Import.",
    )


@tool
def list_labware_families() -> str:
    """
    Lists the labware families this system can actually generate, with the
    parameters each one requires.

    Use this before promising the user any particular kind of labware — only
    families listed here are implemented. Anything else has to be built first.
    """
    lines = ["Implemented public Labware Specialist families (V1):"]
    for family in list_families(agent_visible_only=True):
        lines.append(f"\n  {family.name}")
        lines.append(f"    {family.description}")
        lines.append(f"    required parameters: {', '.join(family.required_fields)}")
        if family.example_configs:
            lines.append(f"    example configs    : {', '.join(family.example_configs)}")
    lines.append(
        "\nNo other family is available. Do not claim support for labware whose "
        "family is not in this list."
    )
    return "\n".join(lines)


@tool
def list_96_well_templates() -> str:
    """List measured/configured templates accepted by the V1 96-well workflow."""
    names = list_well_plate_96_template_names()
    return json.dumps({"family": "well_plate_96", "templates": list(names)}, indent=2)


@tool
def list_registered_labware_templates(family: str) -> str:
    """List configured templates for one public registered labware family."""
    try:
        registered = get_family(family)
    except ValueError as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)
    if not registered.agent_visible:
        return json.dumps({"success": False, "error": f"family {family!r} is not agent-visible"}, indent=2)
    directory = LABWARE_CONFIG_DIR / family
    templates = sorted(path.stem for path in directory.glob("*.yaml")) if directory.is_dir() else []
    return json.dumps({"family": family, "templates": templates}, indent=2)


@tool(args_schema=Load96WellTemplateInput)
def load_96_well_template(template_name: str) -> str:
    """Load a validated 96-well template as the complete AI-facing V1 spec."""
    try:
        spec = load_well_plate_96_template(template_name)
    except (FileNotFoundError, ValueError) as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)
    return spec.model_dump_json(indent=2)


@tool
def load_registered_labware_template(family: str, template_name: str) -> str:
    """Load and validate one allowlisted template through its registered schema."""
    try:
        registered = get_family(family)
        if not registered.agent_visible:
            raise ValueError(f"family {family!r} is not agent-visible")
        if Path(template_name).name != template_name:
            raise ValueError("template_name must be a bare name returned by the template list")
        path = LABWARE_CONFIG_DIR / family / f"{template_name.removesuffix('.yaml')}.yaml"
        if not path.is_file():
            raise FileNotFoundError(f"unknown template {template_name!r} for family {family!r}")
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        spec = registered.spec_model.model_validate(payload)
    except (FileNotFoundError, TypeError, ValueError) as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)
    return spec.model_dump_json(indent=2)


def _run_registered_generation(
    spec: WellPlate96SpecV1 | dict[str, Any],
    overwrite: bool,
) -> str:
    try:
        family_name = spec.get("family") if isinstance(spec, dict) else spec.family
        family = get_family(str(family_name))
        if not family.agent_visible:
            raise ValueError(f"family {family_name!r} is not agent-visible")
        validated = family.spec_model.model_validate(spec)
        result = generate_labware(validated, overwrite=overwrite)
    except Exception as exc:
        return json.dumps(
            {
                "success": False,
                "family": spec.get("family") if isinstance(spec, dict) else getattr(spec, "family", None),
                "error": f"schema or generation failure: {exc}",
                "artifact_written": False,
            },
            indent=2,
        )
    return _structured_result(result)


@tool(args_schema=Generate96WellLabwareInput)
def generate_96_well_labware(
    spec: WellPlate96SpecV1 | dict[str, Any],
    overwrite: bool = False,
) -> str:
    """Validate one regular 8x12 spec, generate 96 wells, validate, and save JSON.

    This is intentionally not a generic file writer. It accepts only the
    family-discriminated ``96WellPlateSpecV1`` contract and always runs the
    registered deterministic generation and validation pipeline before write.
    """
    return _run_registered_generation(spec, overwrite)


@tool(args_schema=Generate96WellLabwareInput)
def generate_registered_labware(
    spec: WellPlate96SpecV1 | dict[str, Any],
    overwrite: bool = False,
) -> str:
    """Generate a public registered family through its strict discriminated schema.

    The V1 tool schema contains only ``well_plate_96``. A future family extends
    this tool's discriminated input union and the registry; the specialist agent
    and deterministic pipeline remain unchanged.
    """
    return _run_registered_generation(spec, overwrite)


@tool
def generate_custom_labware(params: dict, overwrite: bool = False) -> str:
    """
    Generates a custom Opentrons labware definition from structured parameters
    in one call: validates the parameters, computes every position coordinate
    deterministically, builds the JSON, validates it, and writes it.

    Use this when the user has given (or confirmed) complete geometry. If they
    want an existing labware's geometry, use derive_custom_labware instead —
    it inherits measured values rather than requiring them to be restated.

    Required keys in params (family 'rectangular_grid'):
      - load_name (str)           : lowercase letters/digits/underscores/dots only
      - display_name (str)        : human-readable name
      - rows (int), cols (int)    : grid size; rows become A, B, C...
      - x_offset, y_offset (mm)   : centre of A1 (A1 is the BACK-LEFT position)
      - x_spacing, y_spacing (mm) : centre-to-centre pitch
      - depth (mm)                : well depth
      - total_liquid_volume (µL)
      - x_dimension, y_dimension, z_dimension (mm) : outer footprint

    Plus well geometry — either:
      - shape='circular' with diameter (mm), or
      - shape='rectangular' with x_length and y_length (mm)

    Optional: brand, brand_ids, namespace, version, display_category,
    plate_format, well_bottom_shape, well_z, is_tiprack, tip_length,
    tip_overlap, is_magnetic_module_compatible, quirks.

    There are NO defaults for physical geometry. If the user has not supplied a
    dimension and no template provides it, this call fails and names what is
    missing — do not invent a value to make it pass.

    overwrite: set True only when the user explicitly asked to replace an
               existing definition.
    """
    if not isinstance(params, dict):
        return "Error: `params` must be a dict of labware parameters."

    params = dict(params)
    params.pop("family", None)  # only one family today; the registry selects it

    try:
        spec = RectangularGridSpec(**params)
    except Exception as e:
        return (
            f"Parameter validation failed — nothing was generated:\n{e}\n\n"
            "Ask the user for any missing physical dimension rather than choosing one. "
            "If they want to reuse an existing labware's geometry, call "
            "derive_custom_labware with that labware as the template."
        )

    try:
        result = generate_labware(spec, overwrite=overwrite)
    except (ValueError, TypeError) as e:
        return f"Labware generation error: {e}"

    return _format_result(result)


@tool
def derive_custom_labware(params: dict, overwrite: bool = False) -> str:
    """
    Creates a new labware definition based on an existing one, inheriting its
    measured geometry. This is the right tool for "make another one like X".

    Required keys in params:
      - template (str)  : name of an existing labware config (see list_labware_configs)
      - load_name (str) : the NEW load name; must differ from the template's
      - mode (str)      : 'copy' or 'regenerate'

    mode='copy'
        Same physical object, new identity. Only identity/metadata fields may be
        overridden (display_name, brand, brand_ids, namespace, version,
        display_category, plate_format, well_bottom_shape, quirks). Coordinates
        come out identical to the template's. Passing any geometry override in
        this mode is rejected rather than silently applied.

    mode='regenerate'
        Deliberately different physical object. Geometry overrides are allowed
        (rows, cols, x_offset, y_offset, x_spacing, y_spacing, diameter,
        x_length, y_length, depth, well_z, total_liquid_volume, x_dimension,
        y_dimension, z_dimension) and every dependent coordinate is recalculated
        and re-validated — including whether the wells still fit the footprint.

    Any other key in params is treated as an override of that field.
    The template file itself is never modified.

    Example:
      {"template": "paper_print_96_flat", "load_name": "paper_test_01",
       "display_name": "Paper Test 01", "mode": "copy"}
    """
    if not isinstance(params, dict):
        return "Error: `params` must be a dict."

    params = dict(params)
    template = params.pop("template", None)
    mode = params.pop("mode", "regenerate")

    if not template:
        available = list_templates()
        return (
            "Error: 'template' is required — name the existing labware whose geometry "
            f"should be inherited. Available: {available or '(none)'}"
        )
    if mode not in ("copy", "regenerate"):
        return f"Error: 'mode' must be 'copy' or 'regenerate', got {mode!r}."

    try:
        spec = derive_spec(template, params, mode=mode)
    except TemplateNotFoundError as e:
        return str(e)
    except Exception as e:
        return f"Could not derive from '{template}' in mode='{mode}':\n{e}"

    try:
        result = generate_labware(spec, overwrite=overwrite)
    except (ValueError, TypeError) as e:
        return f"Labware generation error: {e}"

    changed = sorted(set(params) & GEOMETRY_FIELDS)
    note = (
        f"\nGeometry regenerated after overriding: {', '.join(changed)}"
        if changed else
        f"\nGeometry copied unchanged from '{template}'."
    )
    return _format_result(result) + note


@tool
def validate_labware_definition(labware_name: str) -> str:
    """
    Re-runs every validation layer against a labware JSON already on disk in
    labware/. Use this to check a definition that was created elsewhere (for
    example by the Opentrons Labware Creator web tool) or to confirm a file is
    still sound.

    labware_name: bare load name (e.g. 'paper_print_96_flat'), filename, or path.

    Reports each layer separately: json (document structure) and opentrons
    (schema 2 + the robot's own verify_definition). These are different
    questions — and none of them is physical verification on an OT-2.
    """
    name = labware_name if labware_name.endswith(".json") else f"{labware_name}.json"
    path = Path(labware_name)
    if not path.exists():
        path = LABWARE_OUTPUT_DIR / name
    if not path.exists():
        available = sorted(p.stem for p in LABWARE_OUTPUT_DIR.glob("*.json"))
        return f"Labware '{labware_name}' not found in {LABWARE_OUTPUT_DIR}. Available: {available}"

    try:
        definition = read_labware_json(path)
    except (ValueError, UnicodeDecodeError) as e:
        return f"{path.name}: not readable as JSON — {e}"

    from src.labware.validation import (
        ValidationReport,
        validate_against_opentrons,
        validate_json_document,
    )

    report = ValidationReport()
    validate_json_document(definition, report, expected_filename=path.name)
    validate_against_opentrons(definition, report)

    well_count = sum(len(col) for col in definition.get("ordering", []))
    lines = [
        f"Validation of {path}:",
        f"  loadName   : {definition.get('parameters', {}).get('loadName')}",
        f"  positions  : {well_count}",
        f"  layers     : {report.summary()}",
        "",
        "Note: this is local validation only. It does not confirm the definition "
        "matches the physical object on the deck — that needs an OT-2 check.",
    ]
    return "\n".join(lines)
