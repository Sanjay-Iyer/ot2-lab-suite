"""Safe loading of existing printing configs without relocating historical files."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_repo_path(reference: str | Path) -> Path:
    """Resolve a repository-relative path and reject traversal outside the repo."""
    path = Path(reference)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"path must resolve inside the repository: {reference}") from exc
    return path


def load_printing_config(reference: str | Path) -> dict[str, Any]:
    """Load one config and resolve its optional shared destination mapping."""
    path = resolve_repo_path(reference)
    if not path.is_file():
        raise FileNotFoundError(f"printing config not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"printing config must be a mapping: {path}")
    config = deepcopy(loaded)
    destination_reference = config.pop("destination_config", None)
    if destination_reference:
        destination_path = resolve_repo_path(destination_reference)
        if not destination_path.is_file():
            raise FileNotFoundError(f"destination config not found: {destination_path}")
        shared = yaml.safe_load(destination_path.read_text(encoding="utf-8")) or {}
        if not isinstance(shared, dict):
            raise ValueError(f"destination config must be a mapping: {destination_path}")
        destination = shared.get("destination", shared)
        if not isinstance(destination, dict):
            raise ValueError("destination config must contain a destination mapping")
        config["destination"] = deepcopy(destination)
    return config
