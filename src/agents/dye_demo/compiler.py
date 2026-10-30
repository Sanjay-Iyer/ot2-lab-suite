"""PlanSpec -> CompiledPlan: the one place the editable plan becomes exactly what the robot runs.

    PlanSpec (the session's config, ExperimentState.config)
        what the scientist means: dilution factors, plate column and rows, which liquids print (print.source - logical
        references such as "dilution:1", references.py), paper rows and columns, replicates, drops. It may be
        incomplete or not yet runnable while it is being built; only HARD errors keep an edit out (validation.py).
    CompiledPlan (compile_plan)
        what a run does: the exact print map (print.source_map with plate wells only - every reference resolved to
        the well its dilution is in now, every paper position decided, extra prints placed on free positions), the
        operations, volumes and tips (plan.build_plan), and every check (validate). The protocol is given its
        `config`, never the PlanSpec.

The print map is generated here, when a run is prepared or the plan is checked for running - never while the plan is
edited in conversation. Compiling is deterministic and lossless: the compiled config must give exactly the prints and
tips of the PlanSpec's own plan, or the compile reports it (compile.parity) and the run is blocked.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from src.agents.dye_demo.plan import Plan, build_plan, droplet_volumes
from src.agents.dye_demo.validation import Report, hard_errors, run_requirements, validate

# The PlanSpec's semantic print fields: the compiled config says the same thing with an exact print map.
SEMANTIC_PRINT_FIELDS = ("source", "columns", "drops_at", "paper_rows")


@dataclass
class CompiledPlan:
    spec: dict[str, Any]            # the PlanSpec this was compiled from
    config: dict[str, Any]          # the execution config: exact print map, no semantic print fields
    plan: Plan | None               # operations, volumes and tips of the execution config (None: not compiled)
    report: Report                  # every check of the PlanSpec and of the execution config

    @property
    def source_map(self) -> list[dict[str, Any]] | None:
        """The exact print map: [{"source": plate well, "positions": [...], "volume_ul"?, "drops"?}], or None when this
        run does not print."""
        return deepcopy((self.config.get("print") or {}).get("source_map"))

    @property
    def errors(self) -> list[str]:
        return self.report.error_messages()

    @property
    def requirements(self) -> list[str]:
        """What the plan still needs before it can run (validation.run_requirements)."""
        return [issue.message for issue in run_requirements(self.report)]

    @property
    def ok(self) -> bool:
        return self.report.ok


def lower_print(plan: Plan, config: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The plan's prints as an exact print map, in print order. Consecutive prints from one well at one drop volume
    are one entry, so the compiled map prints in exactly the plan's order with exactly its tips. A drop count is
    written only where it differs from print.droplets_per_spot, a volume only where the plan has several or another."""
    if not plan.do_print:
        return None
    printing = config.get("print") or {}
    default_drops = int(printing.get("droplets_per_spot", 1) or 1)
    volumes = droplet_volumes(config)
    default_volume = volumes[0] if volumes else None
    entries: list[dict[str, Any]] = []
    for op in plan.operations:
        if op.kind != "print":
            continue
        last = entries[-1] if entries else None
        if last is None or last["source"] != op.source or last["_volume"] != op.volume_ul:
            last = {"source": op.source, "positions": [], "_volume": op.volume_ul}
            entries.append(last)
        last["positions"].append(op.destination)
        if op.droplets != default_drops:
            last.setdefault("drops", {})[op.destination] = op.droplets
    for entry in entries:
        volume = entry.pop("_volume")
        if len(volumes) > 1 or default_volume is None or abs(volume - default_volume) > 1e-9:
            entry["volume_ul"] = volume
    return entries or None


def _motion(plan: Plan) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """What the robot does: every operation's liquid movement, and every tip with the operations it serves."""
    operations = [(op.kind, op.source, op.destination, round(op.volume_ul, 6), op.droplets, op.reps)
                  for op in plan.operations]
    tips = [(tip.tip, tip.first_operation, tip.operation_count) for tip in plan.tips]
    return operations, tips


def compile_plan(spec: dict[str, Any], *, occupied: Iterable[str] = ()) -> CompiledPlan:
    """Compile a PlanSpec. `occupied` are paper positions earlier live runs printed on (prints placed past the paper
    edge avoid them, and reusing one is a warning). Never raises for an unfinished plan: what is missing is in
    `report` as errors (run requirements), so the run is blocked and the scientist is told what is needed."""
    occupied = {str(position) for position in occupied}
    report = validate(spec, printed_positions=occupied)
    execution = deepcopy(spec)
    if hard_errors(report):
        # not a well-formed plan (an edit never leaves one): nothing to lower; the errors say why
        return CompiledPlan(spec=deepcopy(spec), config=execution, plan=None, report=report)
    try:
        spec_plan = build_plan(spec, occupied=occupied)
    except (TypeError, ValueError, ZeroDivisionError, KeyError) as exc:
        report.error("compile.failed", f"the plan could not be compiled: {exc}")
        return CompiledPlan(spec=deepcopy(spec), config=execution, plan=None, report=report)
    if spec_plan.do_print and not spec_plan.print_positions:
        # printing is on but nothing can print yet (a source that does not resolve, ...): nothing to lower, and the
        # errors already say what is missing
        return CompiledPlan(spec=deepcopy(spec), config=execution, plan=spec_plan, report=report)
    printing = execution.setdefault("print", {})
    for key in SEMANTIC_PRINT_FIELDS:
        printing[key] = None
    printing["source_map"] = lower_print(spec_plan, spec)
    compiled_report = validate(execution, printed_positions=occupied)
    known = set(report.error_messages())
    for issue in compiled_report.errors:
        if issue.message not in known:
            report.error(issue.code, issue.message)
    compiled_plan = build_plan(execution, occupied=occupied)
    if _motion(spec_plan) != _motion(compiled_plan):
        report.error("compile.parity", "the compiled print map does not reproduce the plan's motion; the run is "
                                       "blocked (please report this)")
    return CompiledPlan(spec=deepcopy(spec), config=execution, plan=compiled_plan, report=report)
