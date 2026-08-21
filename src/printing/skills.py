"""Scoped runtime loading for printing procedural knowledge.

The Printing Agent receives only a compact name/description index. A selected skill
body is loaded on demand, and optional references are restricted to files explicitly
declared beneath that skill directory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import REPO_ROOT
from .schemas import PrintingFamily


PRINTING_SKILLS_ROOT = REPO_ROOT / "skills"


@dataclass(frozen=True)
class PrintingSkillSpec:
    name: str
    description: str
    directory: Path
    body: str
    families: tuple[PrintingFamily, ...]
    designs: tuple[str, ...]
    references: tuple[str, ...]


def _frontmatter(text: str, *, path: Path) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"printing skill {path} is missing YAML frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError(f"printing skill {path} has unterminated YAML frontmatter") from exc
    metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"printing skill {path} frontmatter must be a mapping")
    return metadata, "\n".join(lines[end + 1 :]).strip()


def _as_string_list(metadata: dict[str, Any], key: str, *, path: Path) -> tuple[str, ...]:
    value = metadata.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"printing skill {path} field {key!r} must be a list of strings")
    return tuple(item.strip() for item in value)


def _parse_skill(path: Path) -> PrintingSkillSpec | None:
    metadata, body = _frontmatter(path.read_text(encoding="utf-8"), path=path)
    if metadata.get("domain") != "printing" or metadata.get("agent") != "printing":
        return None
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"printing skill {path} requires a non-empty name")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"printing skill {path} requires a non-empty description")
    if not body:
        raise ValueError(f"printing skill {path} requires a non-empty body")
    families = tuple(
        PrintingFamily(value)
        for value in _as_string_list(metadata, "families", path=path)
    )
    references = _as_string_list(metadata, "references", path=path)
    for reference in references:
        resolved = (path.parent / reference).resolve()
        if path.parent.resolve() not in resolved.parents or not resolved.is_file():
            raise ValueError(
                f"printing skill {name!r} reference must be an existing file beneath its directory: {reference!r}"
            )
    return PrintingSkillSpec(
        name=name.strip(),
        description=description.strip(),
        directory=path.parent.resolve(),
        body=body,
        families=families,
        designs=_as_string_list(metadata, "designs", path=path),
        references=references,
    )


def discover_printing_skills(root: Path = PRINTING_SKILLS_ROOT) -> tuple[PrintingSkillSpec, ...]:
    """Discover only skills explicitly scoped to the Printing Agent."""
    discovered: list[PrintingSkillSpec] = []
    for path in sorted(root.glob("*/SKILL.md")):
        spec = _parse_skill(path)
        if spec is not None:
            discovered.append(spec)
    names = [spec.name for spec in discovered]
    if len(names) != len(set(names)):
        raise ValueError("printing skill names must be unique")
    return tuple(discovered)


def get_printing_skill(name: str) -> PrintingSkillSpec:
    try:
        return next(spec for spec in discover_printing_skills() if spec.name == name)
    except StopIteration as exc:
        available = ", ".join(spec.name for spec in discover_printing_skills())
        raise KeyError(f"unknown printing skill {name!r}; available: {available}") from exc


def printing_skill_index() -> str:
    """Compact prompt index; bodies remain unloaded until the agent selects one."""
    return "\n".join(f"- {spec.name}: {spec.description}" for spec in discover_printing_skills())


def select_printing_skills(
    family: PrintingFamily | str,
    *,
    design_name: str | None = None,
) -> tuple[str, ...]:
    """Select general family knowledge plus any matching design specialization."""
    selected_family = PrintingFamily(family)
    selected: list[str] = []
    for spec in discover_printing_skills():
        if selected_family not in spec.families:
            continue
        if spec.designs and design_name not in spec.designs:
            continue
        selected.append(spec.name)
    return tuple(selected)


def select_standard_experiment_skills() -> tuple[str, ...]:
    """The isolated generalized skill set; legacy v9 design skills stay excluded."""
    name = "standard-printing-experiment"
    get_printing_skill(name)
    return (name,)


def load_printing_skill_content(name: str, reference: str | None = None) -> str:
    """Load a selected body or one allowlisted, in-directory reference file."""
    spec = get_printing_skill(name)
    if reference is None:
        return spec.body
    if reference not in spec.references:
        raise ValueError(f"reference {reference!r} is not declared by printing skill {name!r}")
    path = (spec.directory / reference).resolve()
    if spec.directory not in path.parents:
        raise ValueError("printing skill reference escapes its skill directory")
    return path.read_text(encoding="utf-8")
