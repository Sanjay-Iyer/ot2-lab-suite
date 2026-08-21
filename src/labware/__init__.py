"""
Custom Opentrons labware generation.

    natural language  ->  Custom Labware Agent
                      ->  validated spec        (schemas.py)
                      ->  deterministic geometry(geometry.py)
                      ->  definition assembly   (builder.py)
                      ->  layered validation    (validation.py)
                      ->  labware/<loadName>.json

The AI decides *what* to build; this package decides *where every coordinate
goes*. No model ever writes a well coordinate by hand.

Entry points:
    ``pipeline.generate_from_config(name)``  — YAML config -> validated JSON
    ``templates.derive_spec(...)``           — inherit an existing definition
    ``families.list_families()``             — what can actually be generated
"""

from src.labware.builder import (
    LabwareOutputExistsError,
    build_labware_definition,
    build_rectangular_grid_labware,
    definition_output_path,
    read_labware_json,
    serialize_labware,
    write_labware_json,
)
from src.labware.families import LABWARE_FAMILIES, LabwareFamily, get_family, list_families, match_agent_family
from src.labware.geometry import (
    GridPosition,
    column_major_ordering,
    flatten_ordering,
    generate_rectangular_grid,
    row_letters,
)
from src.labware.pipeline import GenerationResult, generate_from_config, generate_labware
from src.labware.schemas import CommonLabwareSpec, RectangularGridSpec, WellPlate96SpecV1
from src.labware.templates import (
    TemplateNotFoundError,
    derive_spec,
    list_templates,
    load_spec,
    load_template_dict,
    resolve_config_path,
    spec_from_dict,
    spec_to_yaml,
)
from src.labware.validation import (
    ValidationIssue,
    ValidationReport,
    validate_against_opentrons,
    validate_all,
    validate_geometry,
    validate_json_document,
)

__all__ = [
    "CommonLabwareSpec",
    "GenerationResult",
    "GridPosition",
    "LABWARE_FAMILIES",
    "LabwareFamily",
    "LabwareOutputExistsError",
    "RectangularGridSpec",
    "WellPlate96SpecV1",
    "TemplateNotFoundError",
    "ValidationIssue",
    "ValidationReport",
    "build_labware_definition",
    "build_rectangular_grid_labware",
    "column_major_ordering",
    "definition_output_path",
    "derive_spec",
    "flatten_ordering",
    "generate_from_config",
    "generate_labware",
    "generate_rectangular_grid",
    "get_family",
    "list_families",
    "list_templates",
    "match_agent_family",
    "load_spec",
    "load_template_dict",
    "read_labware_json",
    "resolve_config_path",
    "row_letters",
    "serialize_labware",
    "spec_from_dict",
    "spec_to_yaml",
    "validate_against_opentrons",
    "validate_all",
    "validate_geometry",
    "validate_json_document",
    "write_labware_json",
]
