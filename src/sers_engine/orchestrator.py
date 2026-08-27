"""Load the deck once and execute an ordered unified SERS workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .depth import areas_from_loaded_labware, check_aspiration_depths
from .dilution import execute_dilution_step
from .printing import execute_print_layer
from .schema import (
    ExperimentConfig,
    LabwareSpec,
    REPO_ROOT,
    WaitStep,
    validate_experiment_config,
)


class TipTracker:
    """Explicit, run-wide tip allocation shared by every execution module."""

    def __init__(
        self,
        protocol: Any,
        tip_racks_by_role: dict[str, Any],
        ordered_roles: list[str],
        start_tip: str,
        return_tips: bool,
        required_tips: int,
    ) -> None:
        self._protocol = protocol
        self._return_tips = return_tips
        self._available: list[tuple[str, str, Any]] = []
        self.usage: list[dict[str, str]] = []

        for index, role in enumerate(ordered_roles):
            rack = tip_racks_by_role[role]
            entries = list(rack.wells_by_name().items())
            if index == 0:
                names = [name for name, _ in entries]
                if start_tip not in names:
                    raise ValueError(
                        f"start tip {start_tip!r} does not exist on tip rack {role!r}"
                    )
                entries = entries[names.index(start_tip) :]
            self._available.extend((role, name, well) for name, well in entries)
        if required_tips > len(self._available):
            raise RuntimeError(
                f"workflow requires {required_tips} tips but only "
                f"{len(self._available)} are available from {start_tip}"
            )

    def acquire(self, pipette: Any, purpose: str) -> None:
        self.release(pipette)
        if not self._available:
            raise RuntimeError("ran out of tips during SERS workflow")
        role, name, tip = self._available.pop(0)
        pipette.pick_up_tip(tip)
        self.usage.append({"tip_rack": role, "well": name, "purpose": purpose})
        self._protocol.comment(f"tip {role}:{name} -> {purpose}")

    def release(self, pipette: Any) -> None:
        if not pipette.has_tip:
            return
        if self._return_tips:
            pipette.return_tip()
        else:
            pipette.drop_tip()


def _definition_file(spec: LabwareSpec) -> Path:
    if spec.definition_path is None:
        raise ValueError(f"custom labware {spec.load_name!r} needs definition_path")
    path = Path(spec.definition_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"custom labware definition not found: {resolved}")
    return resolved


def _load_labware(protocol: Any, role: str, spec: LabwareSpec) -> Any:
    if spec.definition_path:
        definition_path = _definition_file(spec)
        definition = json.loads(definition_path.read_text(encoding="utf-8"))
        actual_load_name = definition.get("parameters", {}).get("loadName")
        actual_namespace = definition.get("namespace")
        actual_version = definition.get("version")
        expected = (spec.load_name, spec.namespace, spec.version)
        actual = (actual_load_name, actual_namespace, actual_version)
        if actual != expected:
            raise ValueError(
                f"labware definition identity mismatch for {role!r}: "
                f"expected {expected}, found {actual}"
            )
        return protocol.load_labware_from_definition(definition, str(spec.slot))

    kwargs: dict[str, Any] = {}
    if spec.namespace and spec.namespace != "opentrons":
        kwargs["namespace"] = spec.namespace
    if spec.version is not None and spec.namespace != "opentrons":
        kwargs["version"] = spec.version
    return protocol.load_labware(spec.load_name, str(spec.slot), **kwargs)


def _verify_loaded_wells(config: ExperimentConfig, labware_by_role: dict[str, Any]) -> None:
    required: list[tuple[str, str, str]] = []
    for step in config.dilutions:
        required.extend(
            [
                (step.source.labware, step.source.well, f"{step.operation_id} source"),
                (step.diluent.labware, step.diluent.well, f"{step.operation_id} diluent"),
                (
                    step.destination.labware,
                    step.destination.well,
                    f"{step.operation_id} destination",
                ),
            ]
        )
    for layer in config.print_layers:
        required.append(
            (
                layer.source_location.labware,
                layer.source_location.well,
                f"{layer.layer_name} source",
            )
        )
        for target in layer.paper_targets:
            required.extend(
                (target.labware, well, f"{layer.layer_name} target")
                for well in target.wells
            )
    for role, well, label in required:
        if well not in labware_by_role[role].wells_by_name():
            raise ValueError(f"{label} well {role}:{well} does not exist")

    # The paper definition is a shallow coordinate proxy, so geometry/volume
    # checks intentionally apply only to actual liquid labware.
    for role, spec in config.deck_layout.labware.items():
        if spec.kind == "paper" or spec.safe_max_volume_ul is None:
            continue
        smallest_capacity = min(
            float(well.max_volume) for well in labware_by_role[role].wells()
        )
        if spec.safe_max_volume_ul > smallest_capacity + 1e-9:
            raise ValueError(
                f"configured safe limit {spec.safe_max_volume_ul:g} uL for {role!r} "
                f"exceeds its {smallest_capacity:g} uL labware capacity"
            )


def _verify_aspiration_depths(
    protocol: Any, config: ExperimentConfig, labware_by_role: dict[str, Any]
) -> list[str]:
    """Confirm every aspiration height sits inside the liquid it draws from.

    The arithmetic lives in :mod:`sers_engine.depth` so the fast offline
    validator and this authoritative pre-flight check cannot drift apart.
    """
    areas = areas_from_loaded_labware(config, labware_by_role)
    errors, warnings = check_aspiration_depths(config, areas)
    if errors:
        raise ValueError(errors[0])
    for note in warnings:
        protocol.comment(f"WARNING {note}")
    return warnings


def execute_wait_step(protocol: Any, pipette: Any, hold: WaitStep, labware_by_role: dict[str, Any]) -> dict[str, Any]:
    """Hold for a fixed time with the pipette parked clear of the deck."""
    if pipette.has_tip:
        # Never wait out a hold with a wet tip on the nozzle.
        pipette.drop_tip()
    if hold.park_labware and hold.park_well:
        park = labware_by_role[hold.park_labware].wells_by_name()[hold.park_well]
        pipette.move_to(park.top(hold.park_height_mm))
        where = (
            f"{hold.park_height_mm:g} mm above "
            f"{hold.park_labware}:{hold.park_well}"
        )
    else:
        where = "at its current safe position"
    protocol.comment(
        f"--- wait: {hold.wait_id} for {hold.duration_s:g} s "
        f"({hold.reason or 'hold'}); pipette parked {where} ---"
    )
    protocol.delay(seconds=hold.duration_s)
    return {
        "kind": "wait",
        "ref": hold.wait_id,
        "duration_s": hold.duration_s,
        "reason": hold.reason,
    }


def run_workflow_steps(
    protocol: Any,
    pipette: Any,
    config: ExperimentConfig,
    labware_by_role: dict[str, Any],
    tip_tracker: "TipTracker",
) -> list[dict[str, Any]]:
    """Execute the ordered workflow against an already-prepared deck.

    Split out from :func:`run_unified_protocol` so the offline recorder can drive
    exactly this code path with stand-in labware, keeping the uploaded protocol
    and the simulation on one implementation.
    """
    dilution_by_id = {step.operation_id: step for step in config.dilutions}
    layer_by_name = {layer.layer_name: layer for layer in config.print_layers}
    wait_by_id = {hold.wait_id: hold for hold in config.waits}
    executed: list[dict[str, Any]] = []

    try:
        for item in config.ordered_workflow():
            if item.kind == "dilution":
                executed.append(
                    execute_dilution_step(
                        protocol,
                        pipette,
                        {
                            "operation": dilution_by_id[item.ref],
                            "pipette_config": config.pipette,
                            "labware_by_role": labware_by_role,
                            "tip_tracker": tip_tracker,
                        },
                    )
                )
            elif item.kind == "wait":
                executed.append(
                    execute_wait_step(
                        protocol, pipette, wait_by_id[item.ref], labware_by_role
                    )
                )
            else:
                executed.append(
                    execute_print_layer(
                        protocol,
                        pipette,
                        {
                            "layer": layer_by_name[item.ref],
                            "labware_by_role": labware_by_role,
                            "tip_tracker": tip_tracker,
                        },
                    )
                )
    finally:
        tip_tracker.release(pipette)
    return executed


def summarize_execution(
    executed: list[dict[str, Any]], config: ExperimentConfig, tip_tracker: "TipTracker"
) -> dict[str, Any]:
    """Roll the per-step results up into one run summary."""
    total_deposits = sum(step.get("deposits", 0) for step in executed)
    total_printed = sum(step.get("printed_volume_ul", 0.0) for step in executed)
    total_drying = sum(
        step.get("drying_time_s", 0.0) + step.get("duration_s", 0.0) for step in executed
    )
    return {
        "experiment_name": config.experiment_name,
        "executed_steps": executed,
        "tips_used": len(tip_tracker.usage),
        "tip_log": tip_tracker.usage,
        "deposits": total_deposits,
        "printed_volume_ul": total_printed,
        "drying_time_s": total_drying,
    }


def run_unified_protocol(
    protocol_context: Any, config_dict: dict[str, Any] | ExperimentConfig
) -> dict[str, Any]:
    """Load the requested deck and execute the workflow in declared order."""
    config = (
        config_dict
        if isinstance(config_dict, ExperimentConfig)
        else validate_experiment_config(config_dict)
    )
    protocol = protocol_context
    protocol.comment(f"=== SERS experiment: {config.experiment_name} ===")

    labware_by_role = {
        role: _load_labware(protocol, role, spec)
        for role, spec in config.deck_layout.labware.items()
    }
    tip_racks_by_role = {
        role: _load_labware(protocol, role, spec)
        for role, spec in config.deck_layout.tip_racks.items()
    }
    labware_by_role.update(tip_racks_by_role)
    _verify_loaded_wells(config, labware_by_role)
    depth_notes = _verify_aspiration_depths(protocol, config, labware_by_role)

    pipette = protocol.load_instrument(
        config.pipette.name,
        config.pipette.mount,
        tip_racks=[tip_racks_by_role[role] for role in config.pipette.tip_rack_roles],
    )
    pipette.flow_rate.aspirate = config.pipette.aspirate_flow_rate_ul_s
    pipette.flow_rate.dispense = config.pipette.dispense_flow_rate_ul_s

    tip_tracker = TipTracker(
        protocol,
        tip_racks_by_role,
        config.pipette.tip_rack_roles,
        config.tips.start_tip,
        config.tips.return_tips,
        config.tips_required,
    )
    executed = run_workflow_steps(protocol, pipette, config, labware_by_role, tip_tracker)
    summary = summarize_execution(executed, config, tip_tracker)
    protocol.comment(
        f"SERS complete: {summary['deposits']} deposits, "
        f"{summary['printed_volume_ul']:g} uL printed, {summary['tips_used']} tips used"
    )
    summary["depth_warnings"] = depth_notes
    return summary
