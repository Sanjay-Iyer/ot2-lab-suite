"""Render a resolved workflow as the plan a scientist reviews.

This is the review interface, not generated protocol code.  Everything a person
needs to decide "yes, run that" should be visible here in physical terms.
"""

from __future__ import annotations

from .resolver import ResolvedWorkflowV1


def _duration(seconds: float) -> str:
    seconds = float(seconds)
    if seconds < 90:
        return f"{seconds:.0f} s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f} min"
    return f"{minutes / 60:.1f} h"


def _wells(items: list[str], limit: int = 12) -> str:
    if len(items) <= limit:
        return ", ".join(items)
    return f"{', '.join(items[:limit])} ... (+{len(items) - limit} more)"


def render_review_plan(plan: ResolvedWorkflowV1) -> str:
    """A complete, physical description of what the robot will do."""
    lines: list[str] = []
    add = lines.append

    add(f"EXPERIMENT  {plan.experiment_name}   [{plan.experiment_id}]")
    if plan.description:
        add(f"            {plan.description}")
    add(f"            machine profile: {plan.machine_profile_id}")
    add("")

    add("DECK")
    for slot, contents in plan.deck.items():
        add(f"  {slot:<8}{contents}")
    add("")

    add("LIQUIDS TO LOAD BEFORE THE RUN")
    if plan.totals.liquid_requirements:
        for item in plan.totals.liquid_requirements:
            add(
                f"  {item.liquid:<18}{item.location:<18}"
                f"load {item.loaded_volume_ul:>9,.0f} uL   "
                f"uses {item.consumed_ul:>8,.1f} uL   "
                f"leaves {item.remaining_ul:>9,.1f} uL"
            )
    else:
        add("  (none declared)")
    add("")

    for index, step in enumerate(plan.steps, start=1):
        if step.kind == "dilution":
            head = f"STEP {index} - {step.label}"
            add(head)
            add(f"  Destination     {step.destination}")
            if step.dilution_factor_requested:
                add(
                    f"  Dilution        {step.dilution_factor_requested:g}x requested, "
                    f"{step.dilution_factor_achieved:g}x achieved"
                )
            add(f"  Final volume    {step.final_volume_ul:g} uL")
            add(
                f"  {step.source_liquid:<15} {step.stock_volume_ul:g} uL "
                f"from {step.source_location}"
                + (
                    f"  (chunks {step.stock_chunks_ul})"
                    if len(step.stock_chunks_ul) > 1
                    else ""
                )
            )
            if step.diluent_volume_ul:
                add(
                    f"  {step.diluent_liquid:<15} {step.diluent_volume_ul:g} uL "
                    f"from {step.diluent_location}"
                    + (
                        f"  (chunks {step.diluent_chunks_ul})"
                        if len(step.diluent_chunks_ul) > 1
                        else ""
                    )
                )
            else:
                add("  (undiluted - straight stock transfer)")
            if step.mix_cycles:
                add(f"  Mix             {step.mix_cycles} x {step.mix_volume_ul:g} uL")
            add(f"  Tips            {step.tips}")
        elif step.kind == "print":
            add(f"STEP {index} - {step.label}")
            add(f"  Source          {step.source_liquid} ({step.source_location})")
            add(f"  Paper           {step.paper} (slot {step.paper_slot})")
            add(f"  Targets         {_wells(step.targets)}")
            add(
                f"  Volume          {step.drop_volume_ul:g} uL x "
                f"{step.drops_per_target} drop(s) per location "
                f"= {step.total_deposits} deposits, {step.printed_volume_ul:g} uL"
            )
            add(f"  Release height  {step.dispense_height_mm:g} mm above paper floor")
            add(f"  Tips            {step.tips} ({step.tip_strategy})")
        else:
            add(f"STEP {index} - WAIT")
            add(f"  Duration        {_duration(step.duration_s)}")
            if step.reason:
                add(f"  Reason          {step.reason}")
        add("")

    totals = plan.totals
    add("TOTALS")
    add(
        f"  Operations      {totals.dilution_count} dilution(s), "
        f"{totals.print_count} print(s), {totals.wait_count} wait(s)"
    )
    add(f"  Deposits        {totals.deposits} ({totals.printed_volume_ul:g} uL printed)")
    add(f"  Tips            {totals.tips_required}")
    add(
        f"  Estimated time  {_duration(totals.estimated_duration_s)}"
        + (f", including {_duration(totals.hold_time_s)} of holds" if totals.hold_time_s else "")
    )
    add("")
    add("FINAL WELL VOLUMES")
    for location, volume in plan.totals.final_well_volumes.items():
        add(f"  {location:<22}{volume:>10,.1f} uL")

    if plan.warnings:
        add("")
        add("RESOLVER WARNINGS")
        for warning in plan.warnings:
            add(f"  - {warning}")

    add("")
    add(f"config hash   {plan.config_hash}")
    add(f"resolved hash {plan.resolved_hash}")
    return "\n".join(lines)


def render_compact(plan: ResolvedWorkflowV1) -> str:
    """One line per step, for quick confirmation after a small revision."""
    lines = [f"{plan.experiment_name} ({plan.totals.tips_required} tips, "
             f"{plan.totals.deposits} deposits)"]
    for index, step in enumerate(plan.steps, start=1):
        if step.kind == "dilution":
            detail = (
                f"{step.dilution_factor_achieved:g}x {step.source_liquid} -> "
                f"{step.destination} ({step.stock_volume_ul:g} + "
                f"{step.diluent_volume_ul:g} uL)"
            )
        elif step.kind == "print":
            detail = (
                f"{step.source_liquid} -> {step.paper} {_wells(step.targets, 6)} "
                f"x{step.drops_per_target} @ {step.drop_volume_ul:g} uL"
            )
        else:
            detail = f"hold {_duration(step.duration_s)}"
        lines.append(f"  {index}. [{step.kind}] {step.step_id}: {detail}")
    return "\n".join(lines)
