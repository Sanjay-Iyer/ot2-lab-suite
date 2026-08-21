"""Experiment-independent four-clover printing capabilities.

The deterministic half of the four-clover workflow: a scientist-facing schema, a
loader, the resolver that drives the frozen geometry engine, a human review
renderer, and the local build/simulate step. Nothing here knows about any
particular experiment; every scientific value arrives through
:class:`~src.printing.clover.schemas.FourCloverExperimentJobV1` or through a
hand-written flat executor configuration.

The coordinate arithmetic itself lives in
``src/protocols/printing/02_printing_four_clover.py`` and is never duplicated.
"""

from .builder import (
    BuiltCloverProtocol,
    CloverProtocolBuildError,
    build_clover_protocol,
    render_protocol_source,
    simulate_clover_protocol,
)
from .loader import (
    CloverJobLoadError,
    load_experiment_job,
    load_experiment_job_mapping,
    load_machine_profile,
    load_manual_executor_config,
)
from .resolver import (
    CloverResolutionError,
    build_executor_config,
    geometry_engine,
    resolve_executor_config,
    resolve_experiment_job,
    resolve_manual_config,
)
from .review import render_clover_coordinates, render_clover_review
from .schemas import (
    CloverExperimentSpecV1,
    CloverGeometryV1,
    CloverMachineV1,
    CloverPlacementV1,
    CloverPrintingV1,
    CloverSourceV1,
    FourCloverExperimentJobV1,
    ResolvedCloverPlanV1,
)

__all__ = [
    "BuiltCloverProtocol",
    "CloverExperimentSpecV1",
    "CloverGeometryV1",
    "CloverJobLoadError",
    "CloverMachineV1",
    "CloverPlacementV1",
    "CloverPrintingV1",
    "CloverProtocolBuildError",
    "CloverResolutionError",
    "CloverSourceV1",
    "FourCloverExperimentJobV1",
    "ResolvedCloverPlanV1",
    "build_clover_protocol",
    "build_executor_config",
    "geometry_engine",
    "load_experiment_job",
    "load_experiment_job_mapping",
    "load_machine_profile",
    "load_manual_executor_config",
    "render_clover_coordinates",
    "render_clover_review",
    "render_protocol_source",
    "resolve_executor_config",
    "resolve_experiment_job",
    "resolve_manual_config",
    "simulate_clover_protocol",
]
