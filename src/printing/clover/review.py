"""Plain-language rendering of a resolved four-clover plan.

The point of this module is that a scientist can approve or reject a run without
reading YAML, and without trusting a summary written by a language model: every
number below comes from the resolved plan, which came from the same geometry code
the robot executes.
"""
from __future__ import annotations

from .schemas import ResolvedCloverPlanV1


def render_clover_review(plan: ResolvedCloverPlanV1) -> str:
    """Render the whole run: source, geometry, every coordinate, and totals."""
    lines: list[str] = []
    add = lines.append

    add(f"FOUR-CLOVER EXPERIMENT: {plan.experiment_id}")
    add(f"  job_id  : {plan.job_id}")
    add(f"  plan_id : {plan.plan_id}")
    add("")

    config = plan.executor_config
    deck = config["deck"]
    source = plan.source
    add("SOURCE")
    add(
        f"  {source.liquid_id} in vial {source.well} of "
        f"{deck['source']['load_name']} (slot {deck['source']['slot']})"
    )
    add(
        f"  load {source.loaded_volume_ul:g} uL; the run uses "
        f"{source.required_volume_ul:g} uL and leaves "
        f"{source.remaining_volume_ul:g} uL"
    )
    add(
        f"  reserve {source.minimum_remaining_ul:g} uL; "
        f"{source.submerged_margin_ul:g} uL above the ~{source.submersion_volume_ul:g} uL "
        "needed to keep the tip submerged"
    )
    add("")

    add("SUBSTRATE")
    add(
        f"  {deck['paper']['load_name']} in slot {deck['paper']['slot']}, "
        f"surface {plan.paper_surface_mm:g} mm above the slot floor"
    )
    add(
        f"  droplets released {plan.dispense_standoff_mm:g} mm above the surface "
        f"-> absolute z {plan.absolute_dispense_mm:g} mm"
    )
    box = plan.usable_box
    add(
        f"  usable area x [{box['min_x']:.2f}, {box['max_x']:.2f}] "
        f"y [{box['min_y']:.2f}, {box['max_y']:.2f}] mm (paper-local)"
    )
    add("")

    printing = config["printing"]
    add("PRINTING")
    add(f"  droplet volume     : {plan.droplet_volume_ul:g} uL of liquid per droplet")
    add(
        f"  piston load        : {plan.piston_load_ul:g} uL "
        f"(chase {float(printing.get('pre_air_chase_ul', 0) or 0):g} + liquid "
        f"{plan.droplet_volume_ul:g} + air gap "
        f"{float(printing.get('air_gap_ul', 0) or 0):g})"
    )
    add(f"  order              : {plan.order}")
    add(f"  inter-drop delay   : {float(printing.get('inter_drop_delay_s', 0) or 0):g} s")
    add(f"  inter-layer delay  : {float(printing.get('inter_layer_delay_s', 0) or 0):g} s")
    add(f"  inter-clover delay : {float(printing.get('inter_clover_delay_s', 0) or 0):g} s")
    add("")

    for index, clover in enumerate(plan.clovers, start=1):
        add(f"CLOVER {index} - {clover.name}")
        add(
            f"  reference : {clover.reference_well}"
            f"  offset ({clover.center_offset_x_mm:+.2f}, "
            f"{clover.center_offset_y_mm:+.2f}) mm"
        )
        add(f"  centre    : x {clover.center_x_mm:.2f}, y {clover.center_y_mm:.2f} mm")
        half_width = abs(clover.droplets[0].offset_x_mm)
        half_height = abs(clover.droplets[0].offset_y_mm)
        add(
            f"  geometry  : half {half_width:g} x {half_height:g} mm "
            f"({2 * half_width:g} x {2 * half_height:g} mm between opposing droplets, "
            f"{clover.geometry_source})"
        )
        add(
            f"  layers    : {clover.layers}"
            f"   droplet volume: {plan.droplet_volume_ul:g} uL"
        )
        for droplet in clover.droplets:
            add(
                f"    {droplet.key.upper()}: x {droplet.x_mm:.2f}, "
                f"y {droplet.y_mm:.2f}, z {droplet.z_mm:g}"
                f"   (offset {droplet.offset_x_mm:+.2f}, {droplet.offset_y_mm:+.2f})"
            )
        add("")

    if plan.minimum_intra_clover_distance_mm is not None:
        add(
            "  minimum distance within a clover : "
            f"{plan.minimum_intra_clover_distance_mm:.2f} mm"
        )
    if plan.minimum_inter_clover_distance_mm is not None:
        add(
            "  minimum distance between clovers : "
            f"{plan.minimum_inter_clover_distance_mm:.2f} mm"
        )
    if plan.minimum_intra_clover_distance_mm is not None:
        add("")

    totals = plan.totals
    add("TOTALS")
    add(f"  clover patterns    : {totals.clover_count}")
    add(f"  layers in total    : {totals.layer_total}")
    add(f"  droplet deposits   : {totals.deposit_count}")
    add(f"  printed liquid     : {totals.printed_liquid_ul:g} uL")
    add(f"  execution steps    : {totals.execution_steps}")
    add(
        f"  tips               : {totals.tip_count} "
        f"({config['tips']['p20']['print_tip']}, held for the whole run, "
        f"return_tips={bool(config['tips'].get('return_tips', True))})"
    )
    add(f"  configured delays  : {totals.configured_delay_s:g} s in total")

    if plan.warnings:
        add("")
        add("WARNINGS")
        for warning in plan.warnings:
            add(f"  - {warning}")

    return "\n".join(lines)


def render_clover_coordinates(plan: ResolvedCloverPlanV1) -> str:
    """Just the resolved coordinates, for a quick pre-run inspection."""
    lines = [
        f"{plan.experiment_id}: {plan.totals.clover_count} clovers, "
        f"{plan.totals.deposit_count} deposits, z {plan.absolute_dispense_mm:g} mm"
    ]
    for clover in plan.clovers:
        lines.append(
            f"  {clover.name} @ {clover.reference_well} "
            f"centre ({clover.center_x_mm:.2f}, {clover.center_y_mm:.2f}) "
            f"x{clover.layers} layer(s)"
        )
        for droplet in clover.droplets:
            lines.append(
                f"    {droplet.key.upper()}  x {droplet.x_mm:.2f}  "
                f"y {droplet.y_mm:.2f}  z {droplet.z_mm:g}"
            )
    return "\n".join(lines)
