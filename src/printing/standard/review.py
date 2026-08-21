"""Human-readable rendering of a resolved experiment, for review before approval.

A scientist must be able to check what the robot will do without reading YAML or
a 200-line action trace. Everything rendered here is derived from the resolved
plan, so the review cannot drift from what will actually be executed.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..schemas.experiments import PrintExperimentJobV1, ResolvedExperimentPlanV1


def _substrate_roles(actions: Sequence[Mapping[str, Any]]) -> list[str]:
    seen: list[str] = []
    for action in actions:
        if action["action"] == "PRINT":
            role = action["destination"]["labware"]
            if role not in seen:
                seen.append(role)
    return seen


def _well_sort_key(well: str) -> tuple[int, str]:
    return (int(well[1:]), well[0])


def substrate_deposits(
    plan: ResolvedExperimentPlanV1,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Per substrate well, the ordered deposits and the rests between them."""
    actions = plan.action_dicts()
    delays = [
        (action["sequence_index"], float(action["duration_s"]))
        for action in actions
        if action["action"] == "DELAY"
    ]

    def rest_between(start: int, end: int) -> float:
        return sum(duration for index, duration in delays if start < index < end)

    events: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for action in actions:
        if action["action"] != "PRINT":
            continue
        role = action["destination"]["labware"]
        well = action["destination"]["well"]
        events.setdefault(role, {}).setdefault(well, []).append(
            {
                "sequence_index": action["sequence_index"],
                "liquid_id": action["liquid_id"],
                "volume_ul": float(action["volume_ul"]),
                "source": action["source"],
            }
        )

    summary: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for role, wells in events.items():
        summary[role] = {}
        for well, prints in wells.items():
            groups: list[dict[str, Any]] = []
            for entry in prints:
                if (
                    groups
                    and groups[-1]["liquid_id"] == entry["liquid_id"]
                    and groups[-1]["volume_ul"] == entry["volume_ul"]
                ):
                    previous = groups[-1]["_last_index"]
                    gap = rest_between(previous, entry["sequence_index"])
                    previous_gaps = groups[-1]["_gaps"]
                    previous_gaps.append(gap)
                    groups[-1]["drops"] += 1
                    groups[-1]["_last_index"] = entry["sequence_index"]
                    continue
                groups.append(
                    {
                        "liquid_id": entry["liquid_id"],
                        "volume_ul": entry["volume_ul"],
                        "drops": 1,
                        "source": entry["source"],
                        "_first_index": entry["sequence_index"],
                        "_last_index": entry["sequence_index"],
                        "_gaps": [],
                    }
                )
            rendered: list[dict[str, Any]] = []
            for index, group in enumerate(groups):
                gaps = group.pop("_gaps")
                first = group.pop("_first_index")
                last = group.pop("_last_index")
                spacing = None
                if gaps and len(set(gaps)) == 1:
                    spacing = gaps[0]
                elif gaps:
                    spacing = gaps
                rest_before = (
                    rest_between(groups[index - 1]["_last_index_kept"], first)
                    if index > 0
                    else 0.0
                )
                group["_last_index_kept"] = last
                rendered.append(
                    {
                        **{k: v for k, v in group.items() if not k.startswith("_")},
                        "spacing_s": spacing,
                        "rest_before_s": rest_before,
                    }
                )
            summary[role][well] = rendered
    return summary


def render_substrate_map(plan: ResolvedExperimentPlanV1) -> str:
    """Row-by-row description of every printed substrate position."""
    summary = substrate_deposits(plan)
    lines: list[str] = []
    for role in _substrate_roles(plan.action_dicts()):
        wells = summary.get(role, {})
        lines.append(f"Substrate '{role}' - {len(wells)} printed positions")
        rows = sorted({well[0] for well in wells})
        for row in rows:
            lines.append(f"  Row {row}")
            for well in sorted(
                (w for w in wells if w[0] == row), key=_well_sort_key
            ):
                parts = []
                for deposit in wells[well]:
                    drops = deposit["drops"]
                    piece = (
                        f"{deposit['liquid_id']} "
                        f"{drops} x {deposit['volume_ul']:g} uL"
                    )
                    spacing = deposit["spacing_s"]
                    if drops > 1 and isinstance(spacing, (int, float)) and spacing:
                        piece += f" at {spacing:g} s spacing"
                    elif drops > 1 and isinstance(spacing, list):
                        piece += " at " + "/".join(f"{value:g}" for value in spacing) + " s"
                    if deposit["rest_before_s"]:
                        piece = f"[after {deposit['rest_before_s']:g} s rest] " + piece
                    parts.append(piece)
                lines.append(f"    {well}: " + "; then ".join(parts))
    return "\n".join(lines)


def render_plan_review(
    plan: ResolvedExperimentPlanV1,
    job: PrintExperimentJobV1 | None = None,
) -> str:
    """The complete pre-approval summary a scientist reads instead of the YAML."""
    lines: list[str] = []
    title = job.experiment.metadata.title if job else plan.experiment_id
    lines.append(f"EXPERIMENT: {title}")
    lines.append(f"  experiment_id : {plan.experiment_id}")
    lines.append(f"  job_id        : {plan.job_id}")
    lines.append(f"  plan_id       : {plan.plan_id}")
    if job and job.experiment.metadata.description:
        lines.append(f"  description   : {job.experiment.metadata.description}")
    lines.append("")

    load_actions = [a for a in plan.action_dicts() if a["action"] == "LOAD_LABWARE"]
    pipette_action = next(
        a for a in plan.action_dicts() if a["action"] == "LOAD_PIPETTE"
    )
    lines.append("DECK")
    for action in load_actions:
        lines.append(
            f"  slot {action['slot']:>2}  {action['role']:<12} "
            f"{action['namespace']}/{action['load_name']} v{action['version']}"
        )
    lines.append(
        f"  pipette   {pipette_action['pipette']} on {pipette_action['mount']} mount "
        f"({pipette_action['minimum_volume_ul']:g}-{pipette_action['maximum_volume_ul']:g} uL)"
    )
    lines.append("")

    lines.append("LIQUIDS LOADED BY THE OPERATOR")
    for liquid in plan.initial_liquids:
        location = liquid.location
        lines.append(
            f"  {location.labware}:{location.well:<4} {liquid.liquid_id:<14} "
            f"{liquid.display_name} - load {liquid.loaded_volume_ul:g} uL, "
            f"experiment consumes {liquid.scientific_allocation_ul:g} uL"
        )
    lines.append("")

    if plan.preparation_math:
        lines.append("PREPARATION")
        for entry in plan.preparation_math:
            lines.append(f"  {entry['preparation_id']} ({entry['method']})")
            products = entry.get("products", [])
            wells = entry.get("destination_wells", [])
            factors = entry.get("factors", [])
            for index, product in enumerate(products):
                factor = factors[index] if index < len(factors) else "?"
                well = wells[index] if index < len(wells) else "?"
                if entry["method"] == "back_calculated_serial_cascade":
                    detail = (
                        f"diluent {entry['diluent_ul'][index]:g} uL, "
                        f"carried in {entry['outgoing_ul'][index - 1]:g} uL"
                        if index > 0
                        else f"stock {entry['stock_allocation_ul']:g} uL"
                    )
                else:
                    detail = (
                        f"stock {entry['stock_ul'][index]:g} uL + "
                        f"diluent {entry['diluent_ul'][index]:g} uL"
                    )
                lines.append(
                    f"    {well:<4} {product:<14} 1/{factor} - {detail}"
                )
        lines.append("")

    transfer_actions = [
        action for action in plan.action_dicts() if action["action"] == "TRANSFER"
    ]
    mix_actions = [
        action for action in plan.action_dicts() if action["action"] == "MIX"
    ]
    if transfer_actions or mix_actions:
        lines.append("RESOLVED LIQUID OPERATIONS")
        transfer_groups: dict[tuple[Any, ...], dict[str, Any]] = {}
        for action in transfer_actions:
            key = (
                action["operation_id"],
                action["liquid_id"],
                action["result_liquid_id"],
                action["source"]["labware"],
                action["source"]["well"],
                action["destination"]["labware"],
                action["destination"]["well"],
            )
            group = transfer_groups.setdefault(
                key, {"volume_ul": 0.0, "chunk_count": action["chunk_count"]}
            )
            group["volume_ul"] += float(action["volume_ul"])
        for key, group in transfer_groups.items():
            operation_id, liquid_id, result_id, src_role, src_well, dst_role, dst_well = key
            lines.append(
                f"  {operation_id} [transfer] {liquid_id} "
                f"{src_role}:{src_well} -> {dst_role}:{dst_well}, "
                f"{group['volume_ul']:g} uL total in {group['chunk_count']} chunk(s), "
                f"result={result_id}"
            )
        for action in mix_actions:
            lines.append(
                f"  {action['operation_id']} [mix] {action['liquid_id']} at "
                f"{action['location']['labware']}:{action['location']['well']}, "
                f"{action['cycles']} x {action['volume_ul']:g} uL"
            )
        lines.append("")

    lines.append("SUBSTRATE LAYOUT")
    lines.append(render_substrate_map(plan))
    lines.append("")

    print_actions = [
        action for action in plan.action_dicts() if action["action"] == "PRINT"
    ]
    if print_actions:
        lines.append("PRINT SOURCES AND DESTINATIONS")
        seen_prints: set[tuple[str, str, str, str, str]] = set()
        for action in print_actions:
            key = (
                action["liquid_id"],
                action["source"]["labware"],
                action["source"]["well"],
                action["destination"]["labware"],
                action["destination"]["well"],
            )
            if key in seen_prints:
                continue
            seen_prints.add(key)
            liquid_id, src_role, src_well, dst_role, dst_well = key
            lines.append(
                f"  {liquid_id}: {src_role}:{src_well} -> {dst_role}:{dst_well}"
            )
        lines.append("")

    if job:
        print_steps = [step for step in job.experiment.procedure if step.type == "print"]
        if print_steps:
            lines.append("CONDITIONS, CONTROLS, AND REPLICATES")
            for step in print_steps:
                count = len(step.targets)
                for index, well in enumerate(step.targets, start=1):
                    designation = (
                        f"replicate {index}/{count}"
                        if step.source.kind == "liquid" and count > 1
                        else f"series point {index}/{count}"
                        if step.source.kind == "series"
                        else "single target"
                    )
                    lines.append(
                        f"  {step.substrate}:{well} condition={step.id} "
                        f"purpose={step.purpose} {designation}"
                    )
            lines.append("")

    if job:
        lines.append("PROCEDURE AS REQUESTED")
        for step in job.experiment.procedure:
            summary = f"  {step.id} [{step.type}]"
            if step.type == "print":
                summary += (
                    f" - targets={list(step.targets)}, {step.repeats} x "
                    f"{step.volume_ul:g} uL, purpose={step.purpose}"
                )
                if step.delay_after_pass_s:
                    summary += f", {step.delay_after_pass_s:g} s rest after each pass"
                if step.mix_before_aspirate:
                    summary += (
                        f", mix {step.mix_before_aspirate.cycles}x"
                        f"{step.mix_before_aspirate.volume_ul:g} uL before aspirating"
                    )
            elif step.type == "serial_dilution":
                summary += (
                    f" - {len(step.destination_wells)} points, 1/{step.fold} steps, "
                    f"{step.target_usable_volume_ul:g} uL usable each"
                )
            elif step.type == "direct_dilution":
                summary += f" - {len(step.points)} independent points"
            elif step.type == "delay":
                summary += f" - {step.duration_s:g} s"
            lines.append(summary)
            if step.description:
                lines.append(f"      {step.description}")
        lines.append("")

    totals = plan.totals
    lines.append("TOTALS")
    lines.append(f"  physical actions          : {totals.action_count}")
    lines.append(f"  transfers / mixes / prints: "
                 f"{totals.transfer_count} / {totals.mix_count} / {totals.print_count}")
    lines.append(f"  printed liquid            : {totals.printed_liquid_ul:g} uL")
    lines.append(
        f"  configured rests          : {totals.delay_count} totalling "
        f"{totals.configured_experimental_delay_s:g} s"
    )
    lines.append(f"  tips consumed             : {totals.tip_count}")
    accessibility = plan.source_accessibility
    lines.append(
        f"  source accessibility      : {accessibility.status} over "
        f"{accessibility.checked_aspirate_or_mix_actions} checked actions "
        f"(minimum margin {accessibility.minimum_nominal_submerged_margin_ul:.3f} uL)"
    )
    return "\n".join(lines)
