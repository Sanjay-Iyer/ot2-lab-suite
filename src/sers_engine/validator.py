"""One validation surface over intent, resolution, and physical safety.

Validation is deliberately offline and fast: it needs no Opentrons import and no
robot, so the conversational agent can check every revision without waiting.
Simulation remains the authoritative pre-flight step.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .depth import areas_from_definitions, check_aspiration_depths
from .intent import SERSExperimentV1, validate_intent
from .machine import load_machine_profile
from .resolver import ResolvedWorkflowV1, resolve_experiment
from .schema import SERSConfigError
from .targets import ROWS

TIPS_PER_RACK = 96


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["valid", "invalid"]
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks_run: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "valid"


def _tip_capacity(start_tip: str, rack_count: int) -> int:
    """Tips reachable from ``start_tip``, counting column-major like the rack."""
    row = ROWS.index(start_tip[0])
    column = int(start_tip[1:]) - 1
    consumed_before = column * len(ROWS) + row
    return rack_count * TIPS_PER_RACK - consumed_before


def validate_experiment(
    experiment: SERSExperimentV1 | dict[str, Any],
) -> tuple[ValidationReport, ResolvedWorkflowV1 | None]:
    """Validate one experiment and return its report plus the resolved plan."""
    checks = ["intent schema"]
    try:
        intent = (
            experiment
            if isinstance(experiment, SERSExperimentV1)
            else validate_intent(experiment)
        )
    except SERSConfigError as exc:
        return ValidationReport(status="invalid", errors=[str(exc)], checks_run=checks), None

    checks.append("machine profile")
    try:
        profile = load_machine_profile(intent.machine_profile)
    except SERSConfigError as exc:
        return ValidationReport(status="invalid", errors=[str(exc)], checks_run=checks), None

    checks.extend(
        [
            "dilution arithmetic",
            "paper target expansion",
            "calibrated geometry",
            "P20 volume limits",
            "air-gap capacity",
            "destination capacity",
            "source volume ledger",
            "deck slots and labware roles",
            "workflow ordering",
        ]
    )
    try:
        plan = resolve_experiment(intent, profile)
    except SERSConfigError as exc:
        return ValidationReport(status="invalid", errors=[str(exc)], checks_run=checks), None
    except Exception as exc:  # resolution failures must surface as data
        return ValidationReport(
            status="invalid", errors=[f"{type(exc).__name__}: {exc}"], checks_run=checks
        ), None

    errors: list[str] = []
    warnings: list[str] = list(plan.warnings)
    config = plan.as_experiment_config()

    checks.append("aspiration liquid depth")
    areas = areas_from_definitions(config)
    depth_errors, depth_warnings = check_aspiration_depths(config, areas)
    errors.extend(depth_errors)
    warnings.extend(depth_warnings)

    checks.append("tip availability")
    capacity = _tip_capacity(config.tips.start_tip, len(config.deck_layout.tip_racks))
    if config.tips_required > capacity:
        errors.append(
            f"workflow needs {config.tips_required} tips but only {capacity} are "
            f"reachable from {config.tips.start_tip} across "
            f"{len(config.deck_layout.tip_racks)} rack(s); add a rack or lower the "
            "per-target tip strategy"
        )

    checks.append("paper target duplication")
    # Printing a different liquid onto the same spot is layering, which is the
    # entire point of a SERS overprint, so only a repeated *same* source is
    # flagged -- that is almost always a drop-count mistake.
    seen: dict[tuple[str, str, str], str] = {}
    for step in plan.steps:
        if step.kind != "print":
            continue
        for well in step.targets:
            key = (step.paper, well, step.source_location)
            previous = seen.get(key)
            if previous is not None:
                warnings.append(
                    f"{step.paper}:{well} is printed from {step.source_location} in both "
                    f"{previous!r} and {step.step_id!r}; use drops_per_target if you want "
                    "more than one drop of the same liquid on that spot"
                )
            seen[key] = step.step_id

    checks.append("source reserve after run")
    for requirement in plan.totals.liquid_requirements:
        if requirement.remaining_ul + 1e-9 < requirement.reserve_ul:
            errors.append(
                f"{requirement.liquid} at {requirement.location} would finish at "
                f"{requirement.remaining_ul:g} uL, below its {requirement.reserve_ul:g} uL reserve"
            )

    return (
        ValidationReport(
            status="invalid" if errors else "valid",
            errors=errors,
            warnings=warnings,
            checks_run=checks,
        ),
        plan,
    )
