"""Experiment-independent standard printing capabilities.

This package holds the deterministic half of the standard printing workflow:
exact laboratory arithmetic, the job-to-plan resolver, the human review renderer,
and the canonical equivalence machinery. Nothing here knows about any particular
experiment; every scientific value arrives through
:class:`~src.printing.schemas.experiments.PrintExperimentJobV1`.
"""

from .builder import (
    BuiltStandardProtocol,
    StandardProtocolBuildError,
    build_standard_protocol,
    render_protocol_source,
    simulate_standard_protocol,
    write_plan_artifact,
)
from .capabilities import (
    CapabilityError,
    direct_dilution_volumes,
    serial_cascade,
    split_transfer,
)
from .equivalence import (
    compare_plans,
    physical_sha256,
    physical_trace,
    semantic_diff,
    structural_sha256,
    structural_trace,
    trace_fingerprints,
)
from .loader import (
    ExperimentJobLoadError,
    load_experiment_job,
    load_experiment_job_mapping,
)
from .resolver import ExperimentResolutionError, resolve_experiment_job
from .review import render_plan_review, render_substrate_map

__all__ = [
    "BuiltStandardProtocol",
    "CapabilityError",
    "ExperimentJobLoadError",
    "ExperimentResolutionError",
    "StandardProtocolBuildError",
    "build_standard_protocol",
    "compare_plans",
    "direct_dilution_volumes",
    "load_experiment_job",
    "load_experiment_job_mapping",
    "physical_sha256",
    "physical_trace",
    "render_plan_review",
    "render_protocol_source",
    "render_substrate_map",
    "resolve_experiment_job",
    "semantic_diff",
    "serial_cascade",
    "simulate_standard_protocol",
    "split_transfer",
    "structural_sha256",
    "structural_trace",
    "trace_fingerprints",
    "write_plan_artifact",
]
