"""Scoped runtime loading for Labware Specialist procedural skills."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.utils.paths import PROJECT_ROOT


LABWARE_SKILLS_ROOT = PROJECT_ROOT / "skills"


@dataclass(frozen=True)
class LabwareSkillSpec:
    name: str
    description: str
    directory: Path
    body: str
    families: tuple[str, ...]
    references: tuple[str, ...]


def _parse(path: Path) -> LabwareSkillSpec | None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError(f"labware skill {path} has unterminated YAML frontmatter") from exc
    frontmatter = yaml.safe_load("\n".join(lines[1:end])) or {}
    routing = frontmatter.get("metadata", {})
    if not isinstance(routing, dict):
        raise ValueError(f"labware skill {path} metadata must be a mapping")
    if routing.get("domain") != "labware" or routing.get("agent") != "custom_labware":
        return None
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    body = "\n".join(lines[end + 1 :]).strip()
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"labware skill {path} requires a non-empty name")
    if not isinstance(description, str) or not description.strip() or not body:
        raise ValueError(f"labware skill {path} requires a description and body")

    def strings(key: str) -> tuple[str, ...]:
        value = routing.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"labware skill {path} field {key!r} must be a list of strings")
        return tuple(value)

    references = strings("references")
    for reference in references:
        resolved = (path.parent / reference).resolve()
        if path.parent.resolve() not in resolved.parents or not resolved.is_file():
            raise ValueError(f"labware skill reference is not an in-directory file: {reference!r}")
    return LabwareSkillSpec(
        name=name.strip(),
        description=description.strip(),
        directory=path.parent.resolve(),
        body=body,
        families=strings("families"),
        references=references,
    )


def discover_labware_skills(root: Path = LABWARE_SKILLS_ROOT) -> tuple[LabwareSkillSpec, ...]:
    skills = tuple(filter(None, (_parse(path) for path in sorted(root.glob("*/SKILL.md")))))
    names = [skill.name for skill in skills]
    if len(names) != len(set(names)):
        raise ValueError("labware runtime skill names must be unique")
    return skills


def get_labware_skill(name: str) -> LabwareSkillSpec:
    try:
        return next(skill for skill in discover_labware_skills() if skill.name == name)
    except StopIteration as exc:
        available = ", ".join(skill.name for skill in discover_labware_skills())
        raise KeyError(f"unknown labware skill {name!r}; available: {available}") from exc


def labware_skill_index() -> str:
    return "\n".join(f"- {skill.name}: {skill.description}" for skill in discover_labware_skills())


def select_labware_skills(family: str) -> tuple[str, ...]:
    return tuple(skill.name for skill in discover_labware_skills() if family in skill.families)


def load_labware_skill_content(name: str, reference: str | None = None) -> str:
    skill = get_labware_skill(name)
    if reference is None:
        return skill.body
    if reference not in skill.references:
        raise ValueError(f"reference {reference!r} is not declared by labware skill {name!r}")
    return (skill.directory / reference).read_text(encoding="utf-8")
