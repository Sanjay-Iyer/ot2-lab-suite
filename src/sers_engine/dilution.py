"""Modular P20 dilution execution with deterministic liquid chunking."""

from __future__ import annotations

import math
from typing import Any

from .schema import DilutionOperation, PipetteConfig


def plan_transfer_chunks(
    total_volume_ul: float,
    maximum_chunk_ul: float = 18.0,
    minimum_chunk_ul: float = 1.0,
) -> list[float]:
    """Split a transfer without creating a sub-minimum final aspiration.

    The splitter is greedy so 20 uL becomes ``[18, 2]`` and 80 uL becomes
    ``[18, 18, 18, 18, 8]``.  If a greedy remainder would be below the
    pipette minimum, liquid is borrowed from the preceding chunk.
    """
    total = float(total_volume_ul)
    maximum = float(maximum_chunk_ul)
    minimum = float(minimum_chunk_ul)
    if not all(math.isfinite(value) for value in (total, maximum, minimum)):
        raise ValueError("transfer volumes must be finite")
    if total < minimum:
        raise ValueError(
            f"transfer volume {total:g} uL is below the pipette minimum {minimum:g} uL"
        )
    if maximum < minimum:
        raise ValueError("maximum chunk must be at least the minimum chunk")
    if total <= maximum + 1e-9:
        return [round(total, 10)]

    full_chunks = int(math.floor((total + 1e-9) / maximum))
    chunks = [maximum] * full_chunks
    remainder = total - sum(chunks)
    if abs(remainder) <= 1e-9:
        return [round(value, 10) for value in chunks]
    if remainder >= minimum - 1e-9:
        chunks.append(remainder)
    else:
        amount_to_borrow = minimum - remainder
        if not chunks or chunks[-1] - amount_to_borrow < minimum - 1e-9:
            count = int(math.ceil(total / maximum))
            even = total / count
            if even < minimum - 1e-9:
                raise ValueError("transfer cannot be split within the pipette limits")
            chunks = [even] * count
        else:
            chunks[-1] -= amount_to_borrow
            chunks.append(minimum)

    rounded = [round(value, 10) for value in chunks]
    if any(value < minimum - 1e-9 or value > maximum + 1e-9 for value in rounded):
        raise ValueError("planned transfer chunk is outside the pipette limits")
    if abs(sum(rounded) - total) > 1e-7:
        raise ValueError("planned transfer chunks do not sum to the requested volume")
    return rounded


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


def _destination_position(well: Any, destination: Any) -> Any:
    """Resolve the dispense point against the well bottom or its rim."""
    if destination.dispense_reference == "top":
        return well.top(destination.dispense_offset_mm)
    return well.bottom(destination.dispense_offset_mm)


def execute_dilution_step(protocol: Any, pipette: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Execute one validated dilution operation.

    ``config`` is the small runtime bundle prepared by the orchestrator and
    contains ``operation``, ``pipette_config``, ``labware_by_role``, and the
    shared ``tip_tracker``.  The same tracker is passed to every module so tip
    consumption never resets between printing and dilution phases.
    """
    operation = config["operation"]
    if not isinstance(operation, DilutionOperation):
        operation = DilutionOperation.model_validate(operation)
    pipette_config = config["pipette_config"]
    if not isinstance(pipette_config, PipetteConfig):
        pipette_config = PipetteConfig.model_validate(pipette_config)
    labware_by_role = config["labware_by_role"]
    tips = config["tip_tracker"]

    stock_well = _well(
        labware_by_role, operation.source.labware, operation.source.well
    )
    diluent_well = _well(
        labware_by_role, operation.diluent.labware, operation.diluent.well
    )
    destination_well = _well(
        labware_by_role, operation.destination.labware, operation.destination.well
    )
    destination_point = _destination_position(destination_well, operation.destination)

    stock_chunks = plan_transfer_chunks(
        operation.stock_volume_ul,
        pipette_config.max_transfer_volume_ul,
        pipette_config.minimum_volume_ul,
    )
    diluent_chunks = (
        plan_transfer_chunks(
            operation.diluent_volume_ul,
            pipette_config.max_transfer_volume_ul,
            pipette_config.minimum_volume_ul,
        )
        if operation.diluent_volume_ul > 0
        else []
    )
    protocol.comment(f"--- dilution: {operation.operation_id} ---")

    def transfer(
        source_well: Any,
        source_offset_mm: float,
        chunks: list[float],
        purpose: str,
        pre_mix_cycles: int = 0,
        pre_mix_volume_ul: float = 0.0,
    ) -> None:
        """Move one liquid across on a single fresh tip, chunked for the P20."""
        tips.acquire(pipette, purpose)
        try:
            if pre_mix_cycles:
                pipette.mix(
                    pre_mix_cycles,
                    pre_mix_volume_ul,
                    source_well.bottom(source_offset_mm),
                )
                protocol.comment(
                    f"{purpose}: pre-mixed {pre_mix_cycles} x {pre_mix_volume_ul:g} uL"
                )
            final_chunk = len(chunks)
            for index, volume in enumerate(chunks, start=1):
                pipette.aspirate(volume, source_well.bottom(source_offset_mm))
                if pipette_config.transfer_air_gap_ul:
                    pipette.air_gap(
                        pipette_config.transfer_air_gap_ul,
                        height=pipette_config.air_gap_height_mm,
                    )
                pipette.dispense(
                    volume + pipette_config.transfer_air_gap_ul,
                    destination_point,
                )
                # blow_out leaves the plunger unprepared, and API 2.15 has no
                # prepare_to_aspirate() to re-arm it in air.  Purging between
                # chunks would therefore add a small extra slug to every later
                # aspiration of this transfer and shift the finished dilution
                # ratio, so the tip is purged once, after the last chunk.
                if operation.blow_out and index == final_chunk:
                    pipette.blow_out(destination_well.top(-2.0))
                protocol.comment(
                    f"{purpose}: chunk {index}/{final_chunk} = {volume:g} uL"
                )
        finally:
            tips.release(pipette)

    transfer(
        stock_well,
        operation.source.bottom_offset_mm,
        stock_chunks,
        f"{operation.operation_id}: {operation.source.material or 'stock'}",
        operation.pre_mix_cycles,
        operation.pre_mix_volume_ul,
    )
    if diluent_chunks:
        transfer(
            diluent_well,
            operation.diluent.bottom_offset_mm,
            diluent_chunks,
            f"{operation.operation_id}: {operation.diluent.material or 'diluent'}",
        )
    else:
        protocol.comment(
            f"{operation.operation_id}: undiluted (1x) - no diluent leg"
        )

    if operation.mix_cycles:
        tips.acquire(pipette, f"{operation.operation_id}: destination mix")
        try:
            pipette.mix(
                operation.mix_cycles,
                operation.mix_volume_ul,
                destination_well.bottom(operation.destination.mix_bottom_offset_mm),
            )
            protocol.comment(
                f"mixed {operation.destination.well}: {operation.mix_cycles} x "
                f"{operation.mix_volume_ul:g} uL"
            )
        finally:
            tips.release(pipette)

    return {
        "kind": "dilution",
        "ref": operation.operation_id,
        "stock_chunks_ul": stock_chunks,
        "diluent_chunks_ul": diluent_chunks,
        "destination_volume_added_ul": (
            operation.stock_volume_ul + operation.diluent_volume_ul
        ),
    }
