"""Config-driven SERS dilution and paper-printing engine.

Two layers, deliberately separated:

    SERSExperimentV1   scientific intent - what the experiment IS
        |  resolver + validator (deterministic Python)
    ResolvedWorkflowV1 exact physical operations - what the robot DOES

The conversational agent edits the first and never the second.  Opentrons is
imported lazily so configuration, resolution, and validation stay usable in an
ordinary Python or LLM process.
"""

from .intent import SERSExperimentV1, intent_as_dict, validate_intent
from .machine import MachineProfile, load_machine_profile
from .resolver import ResolvedWorkflowV1, resolve_experiment
from .schema import (
    ExperimentConfig,
    SERSConfigError,
    load_experiment_config,
    validate_experiment_config,
)
from .simulation import SimulationReport, simulate_resolved
from .state import REGISTRY, ExperimentSession, ExperimentStatus
from .summary import render_compact, render_review_plan
from .validator import ValidationReport, validate_experiment

__all__ = [
    "ExperimentConfig",
    "ExperimentSession",
    "ExperimentStatus",
    "MachineProfile",
    "REGISTRY",
    "ResolvedWorkflowV1",
    "SERSConfigError",
    "SERSExperimentV1",
    "SimulationReport",
    "ValidationReport",
    "intent_as_dict",
    "load_experiment_config",
    "load_machine_profile",
    "render_compact",
    "render_review_plan",
    "resolve_experiment",
    "simulate_resolved",
    "validate_experiment",
    "validate_experiment_config",
    "validate_intent",
]
