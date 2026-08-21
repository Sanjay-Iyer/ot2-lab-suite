"""Strict YAML loading for standard printing experiment jobs.

YAML is never executed and never reaches the robot. It is parsed into a plain
mapping, validated by :class:`PrintExperimentJobV1`, and only the validated model
continues downstream.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..config import resolve_repo_path
from ..schemas.experiments import PrintExperimentJobV1


MACHINE_PROFILE_DIR = "configs/machines"


class ExperimentJobLoadError(ValueError):
    """A configuration file is not a valid standard printing experiment job."""


def list_machine_profiles() -> list[str]:
    """Repository-relative paths of every registered machine profile."""
    directory = resolve_repo_path(MACHINE_PROFILE_DIR)
    if not directory.is_dir():
        return []
    return sorted(
        f"{MACHINE_PROFILE_DIR}/{path.name}" for path in directory.glob("*.yaml")
    )


def load_machine_profile(reference: str | Path) -> dict[str, Any]:
    """Load one laboratory-owned machine profile as a plain mapping.

    An experiment configuration references a profile rather than restating deck
    slots and calibrated heights, so proposing an experiment can never silently
    change hardware geometry.
    """
    path = resolve_repo_path(reference)
    registered_dir = resolve_repo_path(MACHINE_PROFILE_DIR)
    if path.parent != registered_dir or path.suffix.lower() != ".yaml":
        raise ExperimentJobLoadError(
            "machine_profile must reference a registered laboratory profile in "
            f"{MACHINE_PROFILE_DIR}: {reference}"
        )
    if not path.is_file():
        raise ExperimentJobLoadError(f"machine profile not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or "machine" not in loaded:
        raise ExperimentJobLoadError(
            f"machine profile must be a mapping with a 'machine' section: {path}"
        )
    machine = loaded["machine"]
    if not isinstance(machine, dict):
        raise ExperimentJobLoadError(f"machine profile 'machine' must be a mapping: {path}")
    return deepcopy(machine)


def load_experiment_job_mapping(payload: dict[str, Any]) -> PrintExperimentJobV1:
    """Validate an already-parsed mapping and derive its canonical identity."""
    if not isinstance(payload, dict):
        raise ExperimentJobLoadError("an experiment job must be a mapping")
    data = deepcopy(payload)
    declared_job_id = data.pop("job_id", None)
    profile_reference = data.pop("machine_profile", None)
    if "machine" in data:
        raise ExperimentJobLoadError(
            "experiment YAML may not contain an inline 'machine' section; reference "
            "a registered laboratory-owned machine_profile"
        )
    if profile_reference is None:
        raise ExperimentJobLoadError(
            "experiment YAML must reference a registered laboratory-owned "
            "machine_profile"
        )
    data["machine"] = load_machine_profile(profile_reference)
    try:
        job = PrintExperimentJobV1.from_content(**data)
    except ValidationError as exc:
        raise ExperimentJobLoadError(str(exc)) from exc
    except TypeError as exc:
        raise ExperimentJobLoadError(f"unexpected top-level key: {exc}") from exc
    if declared_job_id is not None and declared_job_id != job.job_id:
        raise ExperimentJobLoadError(
            "declared job_id does not match the canonical content hash "
            f"({declared_job_id} != {job.job_id})"
        )
    return job


def load_experiment_job(reference: str | Path) -> PrintExperimentJobV1:
    """Load one repository-relative YAML experiment job."""
    path = resolve_repo_path(reference)
    if not path.is_file():
        raise ExperimentJobLoadError(f"experiment job not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ExperimentJobLoadError(f"experiment job must be a mapping: {path}")
    return load_experiment_job_mapping(loaded)
