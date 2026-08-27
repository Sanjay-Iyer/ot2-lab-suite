"""Modular 8 x 12 paper-grid printing engine."""

from __future__ import annotations

from typing import Any

from .schema import PrintLayer


def _well(labware_by_role: dict[str, Any], role: str, well_name: str) -> Any:
    try:
        labware = labware_by_role[role]
    except KeyError as exc:
        raise ValueError(f"labware role {role!r} was not loaded") from exc
    wells = labware.wells_by_name()
    try:
        return wells[well_name]
    except KeyError as exc:
        raise ValueError(f"well {well_name!r} does not exist on labware role {role!r}") from exc


def _release_one_drop(
    protocol: Any,
    pipette: Any,
    layer: PrintLayer,
    source_well: Any,
    destination: Any,
) -> None:
    """Aspirate one droplet's worth and release it onto one paper location.

    NOTE on blow_out in a loop. blow_out leaves the plunger unprepared, so the
    next aspirate re-prepares it and can pull a small extra slug on every
    iteration after the first. The usual fix, prepare_to_aspirate() in air, is
    only available from API 2.16 and this engine is pinned to 2.15, so the
    behaviour here is deliberately identical to the four-clover and
    print-from-vial protocols already in physical use. If the first prints show
    growing droplet volume down the run, set blow_out: false on the layer and
    rely on push_out_ul alone.
    """
    pipette.aspirate(
        layer.drop_volume_ul,
        source_well.bottom(layer.source_location.bottom_offset_mm),
    )
    if layer.air_gap_ul:
        pipette.air_gap(layer.air_gap_ul, height=layer.air_gap_height_mm)
    piston_volume = layer.drop_volume_ul + layer.air_gap_ul
    if layer.push_out_ul:
        try:
            pipette.dispense(piston_volume, destination, push_out=layer.push_out_ul)
        except TypeError:
            # Protocol API 2.15 runtimes predating the push_out keyword still
            # execute the same liquid + air piston.
            pipette.dispense(piston_volume, destination)
    else:
        pipette.dispense(piston_volume, destination)
    if layer.blow_out:
        pipette.blow_out(destination)


def execute_print_layer(protocol: Any, pipette: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Print one chemical layer across one or more 96-grid paper fixtures."""
    layer = config["layer"]
    if not isinstance(layer, PrintLayer):
        layer = PrintLayer.model_validate(layer)
    labware_by_role = config["labware_by_role"]
    tips = config["tip_tracker"]

    source_well = _well(
        labware_by_role,
        layer.source_location.labware,
        layer.source_location.well,
    )
    protocol.comment(
        f"--- print layer: {layer.layer_name} "
        f"({layer.target_count} locations x {layer.drops_per_target} drop(s) "
        f"= {layer.total_deposits} deposits, tips {layer.tip_strategy}) ---"
    )

    printed = 0
    try:
        if layer.tip_strategy == "per_layer":
            tips.acquire(pipette, f"print layer {layer.layer_name}")
        for target_group in layer.paper_targets:
            if layer.tip_strategy == "per_paper":
                tips.acquire(
                    pipette,
                    f"print layer {layer.layer_name}: {target_group.labware}",
                )
            for well_name in target_group.wells:
                if layer.tip_strategy == "per_target":
                    tips.acquire(
                        pipette,
                        f"print layer {layer.layer_name}: "
                        f"{target_group.labware}:{well_name}",
                    )
                destination_well = _well(
                    labware_by_role, target_group.labware, well_name
                )
                destination = destination_well.bottom(layer.dispense_height_mm)
                for drop in range(1, layer.drops_per_target + 1):
                    _release_one_drop(protocol, pipette, layer, source_well, destination)
                    printed += 1
                    protocol.comment(
                        f"{layer.layer_name}: {target_group.labware}:{well_name} "
                        f"drop {drop}/{layer.drops_per_target} <- "
                        f"{layer.source_location.labware}:{layer.source_location.well} "
                        f"({layer.drop_volume_ul:g} uL)"
                    )
                    if layer.post_dispense_delay_s:
                        protocol.delay(seconds=layer.post_dispense_delay_s)
    finally:
        tips.release(pipette)

    if layer.drying_time_s:
        # Park without a liquid-bearing tip.  Height is explicitly relative to
        # the source well top, matching the repository's established convention.
        pipette.move_to(source_well.top(layer.park_height_mm))
        protocol.comment(
            f"drying {layer.layer_name} for {layer.drying_time_s:g} s; "
            f"pipette parked {layer.park_height_mm:g} mm above source"
        )
        protocol.delay(seconds=layer.drying_time_s)

    return {
        "kind": "print_layer",
        "ref": layer.layer_name,
        "deposits": printed,
        "locations": layer.target_count,
        "drops_per_target": layer.drops_per_target,
        "printed_volume_ul": printed * layer.drop_volume_ul,
        "drying_time_s": layer.drying_time_s,
        "tip_strategy": layer.tip_strategy,
    }
