"""Strict loading for four-clover experiment jobs and their machine profiles.

YAML is never executed and never reaches the robot. It is parsed into a plain
mapping, validated by :class:`FourCloverExperimentJobV1`, and only the validated
model continues downstream.

This mirrors :mod:`src.printing.standard.loader` deliberately: the two printing
families should feel identical to anyone editing configuration by hand.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..config import resolve_repo_path
from .schemas import FourCloverExperimentJobV1


MACHINE_PROFILE_DIR = "configs/machines"
DEFAULT_MACHINE_PROFILE = "configs/machines/ot2_four_clover_printing_p20_v1.yaml"


class CloverJobLoadError(ValueError):
    """A configuration file is not a valid four-clover experiment job."""


def load_machine_profile(reference: str | Path) -> dict[str, Any]:
    """Load one laboratory-owned four-clover machine profile as a mapping."""
    path = resolve_repo_path(reference)
    registered_dir = resolve_repo_path(MACHINE_PROFILE_DIR)
    if path.parent != registered_dir or path.suffix.lower() != ".yaml":
        raise CloverJobLoadError(
            "machine_profile must reference a registered laboratory profile in "
            f"{MACHINE_PROFILE_DIR}: {reference}"
        )
    if not path.is_file():
        raise CloverJobLoadError(f"machine profile not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or "machine" not in loaded:
        raise CloverJobLoadError(
            f"machine profile must be a mapping with a 'machine' section: {path}"
        )
    machine = loaded["machine"]
    if not isinstance(machine, dict):
        raise CloverJobLoadError(f"machine profile 'machine' must be a mapping: {path}")
    return deepcopy(machine)


def load_experiment_job_mapping(payload: dict[str, Any]) -> FourCloverExperimentJobV1:
    """Validate an already-parsed mapping and derive its canonical identity."""
    if not isinstance(payload, dict):
        raise CloverJobLoadError("a four-clover experiment job must be a mapping")
    data = deepcopy(payload)
    declared_job_id = data.pop("job_id", None)
    profile_reference = data.pop("machine_profile", None)
    if "machine" in data:
        raise CloverJobLoadError(
            "four-clover experiment YAML may not contain an inline 'machine' "
            "section; reference a registered laboratory-owned machine_profile"
        )
    if profile_reference is None:
        raise CloverJobLoadError(
            "four-clover experiment YAML must reference a registered "
            "laboratory-owned machine_profile"
        )
    data["machine"] = load_machine_profile(profile_reference)
    try:
        job = FourCloverExperimentJobV1.from_content(**data)
    except ValidationError as exc:
        raise CloverJobLoadError(str(exc)) from exc
    except TypeError as exc:
        raise CloverJobLoadError(f"unexpected top-level key: {exc}") from exc
    if declared_job_id is not None and declared_job_id != job.job_id:
        raise CloverJobLoadError(
            "declared job_id does not match the canonical content hash "
            f"({declared_job_id} != {job.job_id})"
        )
    return job


def load_experiment_job(reference: str | Path) -> FourCloverExperimentJobV1:
    """Load one repository-relative YAML four-clover experiment job."""
    path = resolve_repo_path(reference)
    if not path.is_file():
        raise CloverJobLoadError(f"four-clover experiment job not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise CloverJobLoadError(f"experiment job must be a mapping: {path}")
    return load_experiment_job_mapping(loaded)


def load_manual_executor_config(reference: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load a hand-written flat executor config, the manual fallback format.

    Returns ``(config, run_modes)``. This is the shape of the validated ground
    truth ``configs/experiments/02_printing_four_clover.yaml``: the machine values
    are written out in full rather than referenced, because that file must stay
    runnable with no agent, no profile indirection, and no schema layer.
    """
    path = resolve_repo_path(reference)
    if not path.is_file():
        raise CloverJobLoadError(f"clover config not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise CloverJobLoadError(f"clover config must be a mapping: {path}")
    loaded = deepcopy(loaded)

    reference_path = loaded.pop("destination_config", None)
    if reference_path:
        shared_path = resolve_repo_path(reference_path)
        if not shared_path.is_file():
            raise CloverJobLoadError(f"destination_config not found: {shared_path}")
        shared = yaml.safe_load(shared_path.read_text(encoding="utf-8")) or {}
        loaded["destination"] = shared.get("destination", shared)
    if "destination" not in loaded:
        raise CloverJobLoadError(f"{path} has no 'destination' section")

    # Shared source selection: a `source.type` of vial_rack / well_plate resolves
    # the deck labware, slot and aspiration height from src.printing.source_config,
    # exactly as the standard printing workflow does. Configs that still spell the
    # source out by hand (deck.source + source.well) keep working untouched.
    source = loaded.get("source") or {}
    if isinstance(source, dict) and source.get("type"):
        from ..source_config import SourceConfigError, resolve_source

        try:
            resolved_source = resolve_source(source)
        except SourceConfigError as exc:
            raise CloverJobLoadError(f"{path}: {exc}") from exc
        loaded.setdefault("deck", {})["source"] = resolved_source["deck_spec"]
        source = dict(source)
        source["wells"] = resolved_source["wells"]
        source["well"] = resolved_source["wells"][0]
        source["aspirate_height_mm"] = resolved_source["aspirate_height_mm"]
        source.setdefault("park_height_mm", 5.0)
        loaded["source"] = source
        loaded.setdefault("safety", {})["expected_source_slot"] = (
            resolved_source["deck_spec"]["slot"]
        )

    run_modes = loaded.pop("run_modes", {}) or {}
    return loaded, run_modes
