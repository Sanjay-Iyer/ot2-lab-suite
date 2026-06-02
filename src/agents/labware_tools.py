"""
Labware creation tools for the OT-2 LangChain agent.

Tools exposed:
  list_labware_configs        — list YAML configs in configs/labware/
  list_generated_labware      — list JSON definitions in labware/
  describe_labware_config     — human-readable summary of a config
  create_labware_config       — write a new YAML config from user-specified params
  generate_labware_from_config— render a YAML config → Opentrons JSON definition
"""

import json
import sys
import yaml
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, Dict, Optional

from langchain.tools import tool

from src.utils.paths import LABWARE_CONFIG_DIR, LABWARE_OUTPUT_DIR, PROJECT_ROOT

# Import generator internals so we don't shell-out
sys.path.insert(0, str(PROJECT_ROOT))
from scripts.generate_labware import LabwareSpec, make_labware, spec_from_yaml, _SPEC_FIELDS


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
    "tube-rack-15ml": {
                "rows": 3, "cols": 5, "x_offset": 20.75, "y_offset": 68.63,
                "x_spacing": 25.0, "y_spacing": 25.0, "diameter": 15.16,
                "depth": 117.97, "total_liquid_volume": 15000, "plate_format": "irregular",
                "display_category": "tubeRack",
                "x_dimension": 127.76, "y_dimension": 85.48, "z_dimension": 130.0},
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
def generate_labware_from_config(config_name: str) -> str:
    """
    Generates an Opentrons schemaVersion-2 labware JSON definition from a YAML config.
    The JSON is written to labware/<load_name>.json and is ready to import into
    the Opentrons app or reference in protocols.

    config_name: bare name (e.g. 'my_24well_plate'), filename, or full path.
    """
    try:
        config_path = _resolve_config(config_name)
    except FileNotFoundError as e:
        return str(e)

    try:
        spec = spec_from_yaml(config_path)
    except (ValueError, TypeError) as e:
        return f"Config validation error in '{config_name}': {e}"

    try:
        definition = make_labware(spec)
    except (ValueError, TypeError) as e:
        return f"Labware generation error: {e}"

    LABWARE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LABWARE_OUTPUT_DIR / f"{spec.load_name}.json"
    out_path.write_text(
        json.dumps(definition, indent=4, ensure_ascii=False),
        encoding="utf-8"
    )

    well_count = sum(len(col) for col in definition["ordering"])
    return (
        f"Labware JSON generated: {out_path}\n"
        f"  load_name    : {spec.load_name}\n"
        f"  display_name : {spec.display_name}\n"
        f"  wells        : {well_count} ({spec.rows} rows × {spec.cols} cols)\n"
        f"  well shape   : {spec.shape}, depth={spec.depth} mm, volume={spec.total_liquid_volume} µL\n"
        f"  footprint    : {spec.x_dimension} × {spec.y_dimension} × {spec.z_dimension} mm\n\n"
        f"To use in a protocol: load_name='{spec.load_name}', namespace='{spec.namespace}', version={spec.version}\n"
        f"To import: upload {out_path} in the Opentrons App under Labware > Import."
    )
