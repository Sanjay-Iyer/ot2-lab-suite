"""Runtime skills of Agent NanoDrop's conversational router (the dye demo).

The router's procedures - how a message relates to the plan (edit semantics), how printing and dilutions are said,
which plan settings exist - live in skills/nanodrop-*/SKILL.md, the repository's single source of truth for agent
procedure (AI_AGENTS_SKILLS_OVERVIEW.md, Rule 1). The router's system prompt is its fixed output contract
(conversation.CORE_ROUTER_PROMPT) followed by these skills, in their declared order.

Every NanoDrop skill is loaded on every turn: they are few and short, and choosing among them would need either a
second model call or reading the scientist's words in Python - neither is worth it. A skill is selected by its
frontmatter (domain: dye_demo, agent: nanodrop); the other agents' loaders ignore it, and this one ignores theirs.
A malformed NanoDrop skill stops the router (ValueError) instead of silently running without it.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.agents.dye_demo.model import REPO

NANODROP_SKILLS_ROOT = REPO / "skills"
DOMAIN, AGENT = "dye_demo", "nanodrop"


@dataclass(frozen=True)
class NanoDropSkill:
    name: str
    description: str
    order: int
    scopes: tuple[str, ...]
    body: str
    path: Path


def _frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, ""
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError(f"skill {path} has unterminated YAML frontmatter") from exc
    metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"skill {path} frontmatter must be a mapping")
    return metadata, "\n".join(lines[end + 1:]).strip()


def _parse(path: Path) -> NanoDropSkill | None:
    metadata, body = _frontmatter(path)
    if metadata.get("domain") != DOMAIN or metadata.get("agent") != AGENT:
        return None
    name, description, order = metadata.get("name"), metadata.get("description"), metadata.get("order")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"NanoDrop skill {path} requires a non-empty name")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"NanoDrop skill {path} requires a non-empty description")
    if isinstance(order, bool) or not isinstance(order, int):
        raise ValueError(f"NanoDrop skill {path} requires an integer order")
    scopes = metadata.get("scopes", [])
    if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
        raise ValueError(f"NanoDrop skill {path} field 'scopes' must be a list of strings")
    if not body:
        raise ValueError(f"NanoDrop skill {path} requires a non-empty body")
    return NanoDropSkill(name.strip(), " ".join(description.split()), order, tuple(scopes), body, path)


def discover_nanodrop_skills(root: Path = NANODROP_SKILLS_ROOT) -> tuple[NanoDropSkill, ...]:
    """The router's skills, in their declared order."""
    skills = [skill for skill in (_parse(path) for path in sorted(Path(root).glob("*/SKILL.md"))) if skill]
    names = [skill.name for skill in skills]
    if len(names) != len(set(names)):
        raise ValueError("NanoDrop skill names must be unique")
    return tuple(sorted(skills, key=lambda skill: (skill.order, skill.name)))


def skills_prompt(skills: tuple[NanoDropSkill, ...]) -> str:
    """The skills as one prompt section: each with its name and what it is for, then its procedure."""
    return "\n\n".join(f"### SKILL {skill.name} - {skill.description}\n{skill.body}" for skill in skills)


@lru_cache(maxsize=1)
def loaded_skills() -> tuple[NanoDropSkill, ...]:
    """The skills read once per process (they are files of this repository, not settings)."""
    skills = discover_nanodrop_skills()
    if not skills:
        raise ValueError(f"no NanoDrop skills found under {NANODROP_SKILLS_ROOT} (skills/nanodrop-*/SKILL.md)")
    return skills
