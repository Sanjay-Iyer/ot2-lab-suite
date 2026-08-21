"""
The one entry point that runs the whole custom-labware pipeline.

    validated spec
        -> geometry generation      (deterministic coordinates)
        -> definition assembly      (Opentrons schema-2 dict)
        -> layered validation       (geometry / json / opentrons)
        -> write                    (only if every layer passed)

Validation happens BEFORE the file is written, so a definition that fails any
layer never lands on disk. Callers get a structured
:class:`GenerationResult` rather than a bare path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.labware.builder import (
    LabwareOutputExistsError,
    definition_output_path,
    write_labware_json,
)
from src.labware.families import get_family
from src.labware.geometry import GridPosition
from pydantic import BaseModel
from src.labware.validation import ValidationReport, validate_all


@dataclass
class GenerationResult:
    """What a generation attempt produced."""

    success: bool
    labware_family: str
    load_name: str
    position_count: int
    validation: ValidationReport
    definition: Optional[Dict[str, Any]] = None
    output_path: Optional[Path] = None
    write_status: Optional[str] = None  # created | unchanged | overwritten | not_written
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'FAILED'}: {self.load_name} "
            f"[{self.labware_family}, {self.position_count} positions]"
        ]
        if self.output_path:
            lines.append(f"  output   : {self.output_path}  ({self.write_status})")
        lines.append(f"  validation: {self.validation.summary()}")
        for err in self.errors:
            lines.append(f"  error    : {err}")
        return "\n".join(lines)


def generate_labware(
    spec: BaseModel,
    output_dir: Optional[Path] = None,
    overwrite: bool = False,
    write: bool = True,
) -> GenerationResult:
    """Run the full pipeline for one spec.

    Set ``write=False`` for a dry run: everything is built and validated, but
    nothing touches the filesystem.
    """
    family = get_family(spec.family)

    positions: List[GridPosition] = list(family.positions(spec))
    definition = family.build(spec)
    output_path = definition_output_path(spec, output_dir)

    expected_count = getattr(spec, "position_count", None)
    report = validate_all(
        spec,
        positions,
        definition,
        expected_filename=output_path.name,
        expected_count=expected_count,
    )

    result = GenerationResult(
        success=False,
        labware_family=family.name,
        load_name=spec.load_name,
        position_count=len(positions),
        validation=report,
        definition=definition,
        output_path=output_path,
        write_status="not_written",
    )

    if not report.ok:
        result.errors = [str(issue) for issue in report.errors]
        return result

    if not write:
        result.success = True
        result.write_status = "not_written"
        return result

    try:
        result.write_status = write_labware_json(definition, output_path, overwrite=overwrite)
    except LabwareOutputExistsError as exc:
        result.errors = [str(exc)]
        return result
    except OSError as exc:
        result.errors = [f"could not write {output_path}: {exc}"]
        return result

    result.success = True
    return result


def generate_from_config(
    config_name: str,
    output_dir: Optional[Path] = None,
    overwrite: bool = False,
    write: bool = True,
    config_dir: Optional[Path] = None,
) -> GenerationResult:
    """Load a labware YAML config and run the pipeline over it."""
    from src.labware.templates import load_spec

    return generate_labware(
        load_spec(config_name, config_dir),
        output_dir=output_dir,
        overwrite=overwrite,
        write=write,
    )
