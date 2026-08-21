"""Shared fixtures for the standard printing workflow tests.

Tests build jobs from the *registered* machine profile rather than a private
copy, so a change to calibrated laboratory geometry is caught here instead of
silently diverging from what the robot would do.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

import pytest

from src.printing.schemas.experiments import PrintExperimentJobV1
from src.printing.standard.loader import load_machine_profile


MACHINE_PROFILE_REFERENCE = "configs/machines/ot2_standard_printing_p20_v1.yaml"


@pytest.fixture(scope="session")
def registered_machine() -> dict[str, Any]:
    """The laboratory-owned OT-2 standard printing profile, as a mapping."""
    return load_machine_profile(MACHINE_PROFILE_REFERENCE)


@pytest.fixture
def machine(registered_machine: dict[str, Any]) -> dict[str, Any]:
    """A mutable copy of the registered profile for per-test variation."""
    return deepcopy(registered_machine)


@pytest.fixture
def make_job() -> Callable[..., PrintExperimentJobV1]:
    """Build a validated job from a machine mapping and an experiment mapping."""

    def _make(machine: dict[str, Any], experiment: dict[str, Any]) -> PrintExperimentJobV1:
        return PrintExperimentJobV1.from_content(
            machine=deepcopy(machine), experiment=deepcopy(experiment)
        )

    return _make


@pytest.fixture
def single_source_experiment() -> dict[str, Any]:
    """The smallest useful experiment: one declared liquid, one printed drop.

    Deliberately unrelated to any real study; it exists to prove the generic path
    works with no dilution, no mixing, and no delays.
    """
    return {
        "metadata": {
            "experiment_id": "minimal_single_drop",
            "title": "Single drop from a vial",
        },
        "liquids": [
            {
                "liquid_id": "marker_dye",
                "display_name": "Marker dye",
                "location": {"labware": "vial_rack", "well": "A1"},
                "loaded_volume_ul": 4000.0,
                "minimum_remaining_ul": 2600.0,
            }
        ],
        "procedure": [
            {
                "id": "one_row",
                "type": "print",
                "source": {"kind": "liquid", "liquid_id": "marker_dye"},
                "substrate": "paper",
                "targets": ["A1", "B1"],
                "volume_ul": 5.0,
            }
        ],
    }
