"""Deterministic plan for the dye demo: every liquid movement, FROM and TO, with its tip.

This mirrors src/protocols/printing/13_ai_agent_dilution_print_demo.py exactly:
the same operation order, the same volume splitting and the same tip allocation.
tests/test_ai_dye_demo_protocol.py runs that protocol against a recording fake
and asserts that both describe the same motion, so a summary shown to the
scientist cannot drift from what the robot will do.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.agents.dye_demo.model import (
    EPSILON_UL,
    ROWS,
    TIP_ORDER,
    circle_area_mm2,
    is_off_deck,
    slot_of,
    well_geometry,
)


@dataclass(frozen=True)
class DilutionWell:
    row: str
    well: str
    factor: float
    sample_ul: float
    solvent_ul: float


@dataclass(frozen=True)
class Operation:
    """One tip-bearing unit of work, in execution order.

    transfer: aspirate `volume_ul` from vial `source`, dispense it `from_top_mm`
              below the top of plate well `destination`, then blow out.
    print:    mix plate well `source`, then `droplets` drops of `volume_ul` each
              onto paper position `destination`.
    Consecutive operations with the same `group` share one tip.
    """

    kind: str
    group: str
    source: str
    destination: str
    volume_ul: float
    factor: float
    role: str = ""
    chunk: int = 0
    chunks: int = 0
    from_top_mm: float = 0.0
    droplets: int = 0
    column: int = 0


@dataclass(frozen=True)
class TipAssignment:
    tip: str
    group: str
    first_operation: int
    operation_count: int


@dataclass
class Plan:
    do_dilution: bool
    do_print: bool
    rows: list[str]
    factors: list[float]
    plate_column: str
    total_volume_ul: float
    wells: list[DilutionWell]
    spots: list[dict[str, Any]]
    operations: list[Operation]
    tip_groups: list[str]
    tips: list[TipAssignment]
    policy: str
    start_tip: str | None
    tips_available: int
    next_tip: str | None
    source_volume_ul: float
    print_draw_per_well_ul: float
    total_drops: int
    printed_fluid_ul: float
    vial_use_ul: dict[str, float]

    @property
    def tips_needed(self) -> int:
        return len(self.tip_groups)

    @property
    def tips_short(self) -> int:
        return max(0, self.tips_needed - self.tips_available)

    def well_for_row(self, row: str) -> DilutionWell | None:
        return next((well for well in self.wells if well.row == row), None)


# ── building blocks (each mirrors a protocol helper of the same name) ───────────

def factors_of(config: dict[str, Any]) -> list[float]:
    try:
        return [float(value) for value in (config.get("dilution") or {}).get("factors", [])]
    except (TypeError, ValueError):
        return []


def dilution_rows(config: dict[str, Any]) -> list[str]:
    """Plate rows the series occupies, one per factor, from start_row down."""
    start = str((config.get("dilution") or {}).get("start_row", "A")).upper()
    if start not in ROWS:
        return []
    first = ROWS.index(start)
    return list(ROWS[first:first + len(factors_of(config))])


def droplet_volumes(config: dict[str, Any]) -> list[float]:
    raw = (config.get("print") or {}).get("droplet_volume_ul", [])
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    try:
        return [float(value) for value in values]
    except (TypeError, ValueError):
        return []


def paper_layout(config: dict[str, Any], *, include_overflow: bool = False) -> list[dict[str, Any]]:
    """One paper column per droplet volume x replicate, left to right.

    Columns past print.paper_columns are dropped unless include_overflow is set,
    matching the protocol's placed/skipped split.
    """
    printing = config.get("print") or {}
    start = int(printing.get("paper_start_column", 1))
    replicates = int(printing.get("replicates", 1))
    droplets = int(printing.get("droplets_per_spot", 1))
    width = int(printing.get("paper_columns", 12))
    spots: list[dict[str, Any]] = []
    column = start
    for volume in droplet_volumes(config):
        for replicate in range(1, replicates + 1):
            if include_overflow or 1 <= column <= width:
                spots.append({"column": column, "volume_ul": volume,
                              "droplets": droplets, "replicate": replicate})
            column += 1
    return spots


def split_volume(total_ul: float, max_transfer_ul: float, minimum_ul: float) -> list[float]:
    """Split a volume into P20-sized transfers, never leaving a sub-minimum remainder.

    A greedy split of 140.63 uL into 20 uL pieces would end with a 0.63 uL
    transfer, below the P20's 1 uL floor. The last two pieces are rebalanced into
    two equal transfers instead (20 + 0.63 -> 10.31 + 10.31): same transfer count,
    every transfer inside the pipette's working range.
    """
    remaining = float(total_ul)
    chunks: list[float] = []
    while remaining > EPSILON_UL:
        chunk = min(float(max_transfer_ul), remaining)
        chunks.append(round(chunk, 2))
        remaining = round(remaining - chunk, 6)
    if len(chunks) >= 2 and chunks[-1] < float(minimum_ul):
        pair = chunks[-2] + chunks[-1]
        first = round(pair / 2.0, 2)
        chunks[-2:] = [first, round(pair - first, 2)]
    return chunks


def steps_enabled(config: dict[str, Any]) -> tuple[bool, bool]:
    run_modes = config.get("run_modes") or {}
    do_dilution = bool(run_modes.get("do_dilution", True)
                       and (config.get("dilution") or {}).get("enabled", True))
    do_print = bool(run_modes.get("do_print", True)
                    and (config.get("print") or {}).get("enabled", True))
    return do_dilution, do_print


def build_operations(config: dict[str, Any], rows: list[str], factors: list[float],
                     spots: list[dict[str, Any]], do_dilution: bool,
                     do_print: bool) -> list[Operation]:
    dilution = config.get("dilution") or {}
    policy = str((config.get("tips") or {}).get("policy", "per_liquid"))
    minimum = float((config.get("safety") or {}).get("p20_min_volume_ul", 1.0))
    max_transfer = float(dilution.get("max_transfer_ul", 20.0))
    column = str(dilution.get("plate_column", ""))
    total = float(dilution.get("total_volume_ul", 0.0) or 0.0)
    vials = {
        role: str(spec.get("vial", ""))
        for spec in (config.get("materials") or {}).values()
        if isinstance(spec, dict) and (role := spec.get("role"))
    }
    operations: list[Operation] = []
    if do_dilution:
        for role, height_key in (("solvent", "solvent_dispense_from_top_mm"),
                                 ("sample", "sample_dispense_from_top_mm")):
            for row, factor in zip(rows, factors):
                sample_ul = total / factor
                volume = total - sample_ul if role == "solvent" else sample_ul
                if volume <= EPSILON_UL:
                    continue
                well = f"{row}{column}"
                chunks = split_volume(volume, max_transfer, minimum)
                for index, chunk in enumerate(chunks, start=1):
                    group = role if policy == "per_liquid" else f"{role}:{well}:{index}"
                    operations.append(Operation(
                        kind="transfer", group=group, source=vials.get(role, ""),
                        destination=well, volume_ul=chunk, factor=factor, role=role,
                        chunk=index, chunks=len(chunks),
                        from_top_mm=float(dilution.get(height_key, -2.0)),
                    ))
    if do_print:
        for row, factor in zip(rows, factors):
            source = f"{row}{column}"
            for spot in spots:
                position = f"{row}{spot['column']}"
                group = f"print:{row}" if policy == "per_liquid" else f"print:{position}"
                operations.append(Operation(
                    kind="print", group=group, source=source, destination=position,
                    volume_ul=float(spot["volume_ul"]), factor=factor,
                    droplets=int(spot["droplets"]), column=int(spot["column"]),
                ))
    return operations


def tip_groups(operations: list[Operation]) -> list[str]:
    """One entry per tip pick-up: a new tip whenever the group changes."""
    groups: list[str] = []
    for operation in operations:
        if not groups or groups[-1] != operation.group:
            groups.append(operation.group)
    return groups


def build_plan(config: dict[str, Any]) -> Plan:
    do_dilution, do_print = steps_enabled(config)
    dilution = config.get("dilution") or {}
    factors = factors_of(config)
    rows = dilution_rows(config)
    column = str(dilution.get("plate_column", ""))
    total = float(dilution.get("total_volume_ul", 0.0) or 0.0)
    wells = [
        DilutionWell(row, f"{row}{column}", factor, total / factor, total - total / factor)
        for row, factor in zip(rows, factors) if factor > 0
    ]
    spots = paper_layout(config)
    operations = build_operations(config, rows, factors, spots, do_dilution, do_print)
    groups = tip_groups(operations)

    start = str((config.get("tips") or {}).get("start_tip", "A1")).upper()
    start_index = TIP_ORDER.index(start) if start in TIP_ORDER else None
    available = 0 if start_index is None else len(TIP_ORDER) - start_index
    names = [] if start_index is None else list(TIP_ORDER[start_index:start_index + len(groups)])
    assignments: list[TipAssignment] = []
    cursor = 0
    for tip, group in zip(names, groups):
        count = 0
        while cursor + count < len(operations) and operations[cursor + count].group == group:
            count += 1
        assignments.append(TipAssignment(tip, group, cursor, count))
        cursor += count
    next_index = None if start_index is None else start_index + len(groups)
    next_tip = TIP_ORDER[next_index] if next_index is not None and next_index < len(TIP_ORDER) else None

    prepared = dilution.get("prepared_volume_ul")
    source_volume = total if do_dilution or prepared in (None, "") else float(prepared)
    draw = sum(spot["volume_ul"] * spot["droplets"] for spot in spots) if do_print else 0.0
    vial_use = {"solvent": 0.0, "sample": 0.0}
    for operation in operations:
        if operation.kind == "transfer":
            vial_use[operation.role] += operation.volume_ul
    return Plan(
        do_dilution=do_dilution, do_print=do_print, rows=rows, factors=factors,
        plate_column=column, total_volume_ul=total, wells=wells, spots=spots,
        operations=operations, tip_groups=groups, tips=assignments,
        policy=str((config.get("tips") or {}).get("policy", "per_liquid")),
        start_tip=start if start_index is not None else None,
        tips_available=available, next_tip=next_tip,
        source_volume_ul=source_volume, print_draw_per_well_ul=draw,
        total_drops=len(rows) * sum(spot["droplets"] for spot in spots) if do_print else 0,
        printed_fluid_ul=len(rows) * draw if do_print else 0.0,
        vial_use_ul={role: round(volume, 2) for role, volume in vial_use.items()},
    )


# ── geometry used by the liquid checks ──────────────────────────────────────────

def well_area_mm2(config: dict[str, Any], role: str) -> float | None:
    """Cross-section of the labware's wells, when its definition is on disk."""
    spec = (config.get("deck") or {}).get(role) or {}
    geometry = well_geometry(str(spec.get("load_name", "")))
    return circle_area_mm2(geometry[0]) if geometry else None


def labware_on_deck(config: dict[str, Any], role: str) -> bool:
    return not is_off_deck(slot_of(config, role))
