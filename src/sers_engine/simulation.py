"""Deterministic OT-2 simulation of a resolved workflow.

Simulation and live execution consume the same :class:`ResolvedWorkflowV1`.
Neither re-interprets natural language, and neither recomputes a volume.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .orchestrator import run_unified_protocol
from .provenance.models import now_iso
from .resolver import ResolvedWorkflowV1


class SimulationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "failed"]
    experiment_id: str
    experiment_name: str
    config_hash: str
    resolved_hash: str
    machine_profile_id: str
    command_count: int = 0
    dilution_count: int = 0
    print_count: int = 0
    wait_count: int = 0
    deposits: int = 0
    printed_volume_ul: float = 0.0
    tips_used: int = 0
    tips_required: int = 0
    hold_time_s: float = 0.0
    estimated_duration_s: float = 0.0
    liquid_requirements: list[dict[str, Any]] = Field(default_factory=list)
    depth_warnings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    tip_log: list[dict[str, str]] = Field(default_factory=list)
    simulated_at: str = ""
    wall_clock_s: float = 0.0

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def _numpy_compatibility_shim() -> None:
    # Opentrons 9.0 imports numpy.trapz, removed in newer NumPy.  This is the
    # same compatibility shim used by the repository's established simulators.
    import numpy as np

    if not hasattr(np, "trapz") and hasattr(np, "trapezoid"):
        np.trapz = np.trapezoid  # type: ignore[attr-defined]


def simulate_resolved(plan: ResolvedWorkflowV1) -> SimulationReport:
    """Run the resolved workflow on virtual hardware and report what happened."""
    started = time.time()
    base = {
        "experiment_id": plan.experiment_id,
        "experiment_name": plan.experiment_name,
        "config_hash": plan.config_hash,
        "resolved_hash": plan.resolved_hash,
        "machine_profile_id": plan.machine_profile_id,
        "dilution_count": plan.totals.dilution_count,
        "print_count": plan.totals.print_count,
        "wait_count": plan.totals.wait_count,
        "tips_required": plan.totals.tips_required,
        "hold_time_s": plan.totals.hold_time_s,
        "estimated_duration_s": plan.totals.estimated_duration_s,
        "liquid_requirements": [
            item.model_dump(mode="json") for item in plan.totals.liquid_requirements
        ],
        "warnings": list(plan.warnings),
        "simulated_at": now_iso(),
    }

    try:
        config = plan.as_experiment_config()
        _numpy_compatibility_shim()
        from opentrons.simulate import get_protocol_api

        protocol = get_protocol_api(
            config.api_level, robot_type=config.robot_type, use_virtual_hardware=True
        )
        summary = run_unified_protocol(protocol, config)
        return SimulationReport(
            status="passed",
            command_count=len(protocol.commands()),
            deposits=summary["deposits"],
            printed_volume_ul=summary["printed_volume_ul"],
            tips_used=summary["tips_used"],
            depth_warnings=summary.get("depth_warnings", []),
            tip_log=summary["tip_log"],
            wall_clock_s=round(time.time() - started, 2),
            **base,
        )
    except Exception as exc:
        return SimulationReport(
            status="failed",
            errors=[f"{type(exc).__name__}: {exc}"],
            wall_clock_s=round(time.time() - started, 2),
            **base,
        )
