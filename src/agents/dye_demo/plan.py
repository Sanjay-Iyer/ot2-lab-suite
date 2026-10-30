"""Deterministic plan for the dye demo: every liquid movement, FROM and TO, with its tip.

This mirrors src/protocols/printing/13_ai_agent_dilution_print_demo.py exactly:
the same operation order, the same volume splitting and the same tip allocation.
tests/test_ai_dye_demo_protocol.py runs that protocol against a recording fake
and asserts that both describe the same motion, so a summary shown to the
scientist cannot drift from what the robot will do.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from src.agents.dye_demo.model import (
    EPSILON_UL,
    ROWS,
    TIP_ORDER,
    FieldError,
    circle_area_mm2,
    is_off_deck,
    liquid_handling,
    normalize_drops_at,
    normalize_paper_column_list,
    normalize_source_map,
    plate_well,
    slot_of,
    well_geometry,
)
from src.agents.dye_demo.references import (
    DILUTION,
    ResolvedSource,
    UnresolvedRef,
    dilution_outputs,
    intent_parts,
    normalize_sources,
    resolve,
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

    transfer: aspirate `volume_ul` from vial `source`, take the air gap, dispense liquid + gap `height_mm` above the
              bottom of plate well `destination`, then blow-out and the well shake (liquid_handling).
    mix:      mix plate well `destination` `reps` times with `volume_ul`, right after its dye, with that transfer's
              tip; then blow-out and the well shake.
    print:    `droplets` drops of `volume_ul` each from plate well `source` onto the ONE paper position
              `destination` (no mixing before printing).
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
    height_mm: float = 0.0
    droplets: int = 0
    column: int = 0
    reps: int = 0


@dataclass(frozen=True)
class TipAssignment:
    tip: str
    group: str
    first_operation: int
    operation_count: int


@dataclass(frozen=True)
class PrintSource:
    """A plate well the print step draws from, and the paper positions it prints, in print order."""

    well: str
    positions: tuple[str, ...]
    factor: float            # the dilution factor when it is known; 0.0 when it is not (a sample the scientist has)
    made_here: bool          # this run makes the dilution in this well
    start_ul: float          # liquid in the well when printing starts (assumed when the scientist did not say)
    draw_ul: float           # liquid the print step takes from it


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
    mapped: bool = False                                   # printing follows a map (dictated or semantic, not the series)
    print_sources: list[PrintSource] = field(default_factory=list)
    # the paper row each series row prints on when there is no print map (print.paper_rows, or the same letters)
    paper_rows: list[str] = field(default_factory=list)
    # why the semantic print fields cannot be laid out as asked (a reference that does not resolve, more samples than
    # paper rows, no room left on the paper): validate() reports each one; they block a run, never an edit
    print_issues: list[str] = field(default_factory=list)

    @property
    def print_positions(self) -> list[str]:
        return [op.destination for op in self.operations if op.kind == "print"]

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
    """Plate rows the series occupies, one per factor."""
    dilution = config.get("dilution") or {}
    explicit = dilution.get("rows")
    if isinstance(explicit, (list, tuple)) and explicit:
        valid = [str(r).strip().upper() for r in explicit if str(r).strip().upper() in ROWS]
        if valid:
            return sorted(set(valid), key=ROWS.index)
    start = str(dilution.get("start_row", "A")).upper()
    if start not in ROWS:
        return []
    first = ROWS.index(start)
    return list(ROWS[first:first + len(factors_of(config))])


def explicit_paper_rows(config: dict[str, Any]) -> list[str] | None:
    """print.paper_rows as given (upper-cased, in the given order), or None when the plan uses the default: each
    dilution prints on the paper row with its own plate-row letter. validate() refuses a list that does not fit."""
    raw = (config.get("print") or {}).get("paper_rows")
    if raw in (None, "", []):
        return None
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    return [str(item).strip().upper() for item in items]


def paper_rows_of(config: dict[str, Any], rows: list[str]) -> list[str]:
    """The PAPER row each series row prints on, in series order.

    Where the dilutions are made (dilution.rows, plate rows) and where they print (print.paper_rows, paper rows) are
    independent settings: with print.paper_rows the i-th dilution of the series prints on its i-th paper row; without
    it, each dilution prints on the paper row with the same letter as its plate row (the original demo layout).
    Mirrors the protocol's _paper_rows."""
    explicit = explicit_paper_rows(config)
    return list(rows) if explicit is None else explicit


def print_map(config: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The explicit print map (print.source_map) with every source a plate well, or None when printing follows the
    dilution rows. A dilution reference in the map ("dilution:2") is resolved to the well that dilution is in now.

    A malformed map gives [] (nothing printed from it), and an entry whose reference does not resolve prints nothing:
    validate() names the problem (map_problems).
    """
    return _resolved_map(config)[0]


def map_problems(config: dict[str, Any]) -> list[str]:
    """Why entries of a dictated print map cannot print: references that do not resolve in this plan."""
    return _resolved_map(config)[1]


def _resolved_map(config: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, list[str]]:
    raw = (config.get("print") or {}).get("source_map")
    if raw in (None, "", []):
        return None, []
    try:
        entries = normalize_source_map(raw)
    except FieldError:
        return [], []
    resolved, problems = [], []
    for entry in entries:
        try:
            plate_well(entry["source"])
            resolved.append(entry)
            continue
        except FieldError:
            pass
        try:
            resolved.append({**entry, "source": resolve(entry["source"], config).well})
        except (UnresolvedRef, FieldError) as exc:
            problems.append(str(exc))
    return resolved, problems


def print_drops_at(config: dict[str, Any]) -> dict[str, int]:
    """print.drops_at: drops on particular paper positions (every other position takes print.droplets_per_spot)."""
    try:
        return normalize_drops_at((config.get("print") or {}).get("drops_at")) or {}
    except FieldError:
        return {}


def print_intents(config: dict[str, Any]) -> tuple[list[tuple[ResolvedSource, dict[str, Any]]], list[str]]:
    """What prints, resolved to plate wells now, each with its own print intent (references.INTENT_FIELDS; {} when the
    plan-wide settings apply), in print order - and why any source could not be resolved. print.source when it is set;
    else every output of the dilution series (each dilution prints, the original demo layout)."""
    raw = (config.get("print") or {}).get("source")
    if raw in (None, "", []):
        return [(output, {}) for output in dilution_outputs(config)], []
    try:
        intents = normalize_sources(raw) or []
    except FieldError as exc:
        return [], [str(exc)]
    resolved, problems = [], []
    for item in intents:
        ref, fields = intent_parts(item)
        try:
            resolved.append((resolve(ref, config), fields))
        except UnresolvedRef as exc:
            problems.append(str(exc))
    return resolved, problems


def selected_sources(config: dict[str, Any]) -> tuple[list[ResolvedSource], list[str]]:
    """The liquids the print step prints, resolved to plate wells, in print order (print_intents without the
    intents)."""
    intents, problems = print_intents(config)
    return [source for source, _ in intents], problems


def is_semantic_print(config: dict[str, Any]) -> bool:
    """Printing the protocol's own series layout cannot express - only the compiled print map can (compiler.py): a
    print source selection (print.source), exact paper columns (print.columns), one sample on several paper rows, or a
    side-by-side layout that runs past the paper's edge (the extra prints go on free positions, placement.py). A
    dictated print map is its own path."""
    printing = config.get("print") or {}
    if print_map(config) is not None:
        return False
    if any(printing.get(key) not in (None, "", [], {}) for key in ("source", "columns")):
        return True
    explicit = explicit_paper_rows(config)
    if explicit is not None and len(explicit) != len(dilution_rows(config)):
        return True
    width = int(printing.get("paper_columns", 12) or 12)
    try:
        return any(int(spot["column"]) > width for spot in paper_layout(config, include_overflow=True))
    except (TypeError, ValueError):
        return False


def _semantic_spots(config: dict[str, Any], issues: list[str], columns: Any = None) -> list[dict[str, Any]]:
    """The spots one printed source takes on one paper row: side by side from print.paper_start_column, or exactly the
    paper columns given (a source's own columns, else print.columns), each drop volume taking its share in order."""
    printing = config.get("print") or {}
    try:
        columns = normalize_paper_column_list(columns if columns is not None else printing.get("columns"))
    except FieldError as exc:
        issues.append(str(exc))
        return []
    if columns is None:
        return paper_layout(config, include_overflow=True)
    volumes = droplet_volumes(config) or [0.0]
    if len(columns) % len(volumes):
        issues.append(f"{len(columns)} paper columns cannot be shared by {len(volumes)} drop volumes; name "
                      f"{len(volumes)} columns per replicate")
    per_volume = len(columns) // len(volumes)
    droplets = int(printing.get("droplets_per_spot", 1))
    return [{"column": columns[index * per_volume + replicate], "volume_ul": volume, "droplets": droplets,
             "replicate": replicate + 1}
            for index, volume in enumerate(volumes) for replicate in range(per_volume)]


def semantic_prints(config: dict[str, Any], *, occupied: Iterable[str] = ()
                    ) -> tuple[list[tuple[ResolvedSource, str, float, int]], list[str]]:
    """Every print of a semantic PlanSpec as (source, paper position, drop volume, drops), in print order, and the
    problems that keep it from being laid out as asked. This is where print intents become exact positions.

    Each source follows its own print intent where it has one, and the plan-wide settings where it is silent:
      positions  exactly those paper positions (the scientist chose them);
      rows       one print per listed paper row and spot column (its own columns, else the plan's spots);
      count      that many prints per drop volume, placed on free positions: its home paper row first, side by side
                 from the first paper column, then the nearest free positions (placement.py) - within its own rows
                 and columns when it also gives them;
      neither    the plan-wide layout: its home paper row (print.paper_rows paired in order with the sources that use
                 them, every listed row for a single source, else the row with its well's letter) with the plan's spots.
    Exact and row-based positions are fixed first; prints past the paper's edge and counted prints then go on free
    positions, never on one in `occupied` (printed by an earlier live run) or one another source already has.
    Drops: the source's own `drops`, else print.droplets_per_spot (print.drops_at still sets single positions)."""
    from src.agents.dye_demo.placement import Need, PlacementError, allocate, allocate_side_by_side

    printing = config.get("print") or {}
    intents, issues = print_intents(config)
    spots = _semantic_spots(config, issues)
    volumes = droplet_volumes(config) or [0.0]
    width = int(printing.get("paper_columns", 12) or 12)
    start = min(max(int(printing.get("paper_start_column", 1) or 1), 1), width)
    default_drops = int(printing.get("droplets_per_spot", 1) or 1)
    # the plan-wide paper rows pair, in order, with the sources that do not say where they print themselves
    explicit = explicit_paper_rows(config)
    users = [index for index, (_, fields) in enumerate(intents) if not {"rows", "positions"} & set(fields)]
    home: dict[int, list[str]] = {}
    for index, (source, _) in enumerate(intents):
        home[index] = [source.well[0]]
    if explicit is not None and users:
        if len(intents) == 1:
            home[users[0]] = list(explicit)
        else:
            if len(explicit) != len(users) and not issues:
                noun = "dilution" if all(intents[index][0].ref.kind == DILUTION for index in users) else "sample"
                issues.append(f"{len(users)} {noun}{'s' if len(users) != 1 else ''} would print on {len(explicit)} "
                              f"paper row{'s' if len(explicit) != 1 else ''} ({', '.join(explicit)}): each sample needs "
                              "one paper row of its own (or print one sample on several rows)")
            for index, row in zip(users, explicit):
                home[index] = [row]
            for index in users[len(explicit):]:
                home[index] = []
    groups = []                      # one per (source, paper row, drop volume); "count" groups are placed by count
    for index, (source, fields) in enumerate(intents):
        drops = int(fields.get("drops", default_drops))
        if "positions" in fields:
            if len(volumes) > 1:
                issues.append(f"{source.well} prints at exact paper positions, which take one drop volume; the plan "
                              f"has {len(volumes)}")
            groups.append({"source": index, "row": "", "volume": volumes[0], "count": len(fields["positions"]),
                           "positions": list(fields["positions"]), "drops": drops, "counted": False, "exact": True})
            continue
        if "count" in fields:
            for volume in volumes:
                groups.append({"source": index, "row": home[index][0] if home[index] else source.well[0],
                               "volume": volume, "count": int(fields["count"]), "positions": [], "drops": drops,
                               "counted": True, "exact": False, "rows": tuple(fields.get("rows", ())),
                               "columns": tuple(fields.get("columns", ()))})
            continue
        own_spots = spots if "columns" not in fields else _semantic_spots(config, issues, fields["columns"])
        for row in fields.get("rows") or home[index]:
            for volume in volumes:
                columns = [int(spot["column"]) for spot in own_spots if float(spot["volume_ul"]) == volume]
                groups.append({"source": index, "row": row, "volume": volume, "count": len(columns),
                               "positions": [f"{row}{column}" for column in columns if 1 <= column <= width],
                               "drops": drops, "counted": False, "exact": False})
    claimed: dict[str, str] = {}
    for group in groups:
        for position in group["positions"]:
            well = intents[group["source"]][0].well
            if position in claimed and claimed[position] != well:
                issues.append(f"paper position {position} would be printed from both {claimed[position]} and {well}")
            claimed.setdefault(position, well)
    taken = set(occupied) | set(claimed)
    reserved = {group["row"] for group in groups if group["row"]}
    for counted in (False, True):
        # first the fixed layouts that ran past the paper's edge, then the counted prints, on what is still free
        for volume in volumes:
            members = [group for group in groups if group["volume"] == volume and group["counted"] is counted
                       and not group["exact"] and len(group["positions"]) < group["count"]]
            if not members:
                continue
            needs = [Need(intents[group["source"]][0].well, group["count"], tuple(group["positions"]), group["row"],
                          rows=group.get("rows", ()), columns=group.get("columns", ()), anchor_column=start)
                     for group in members]
            try:
                placed = allocate_side_by_side(needs, taken, width=width) or allocate(needs, taken, width=width,
                                                                                      reserved_rows=reserved)
            except PlacementError as exc:
                issues.append(str(exc))
                continue
            for group, positions in zip(members, placed):
                group["positions"] = positions
            taken.update(position for positions in placed for position in positions)
    prints = [(intents[group["source"]][0], position, group["volume"], group["drops"]) for group in groups
              for position in group["positions"]]
    return prints, issues


def row_factors(rows: list[str], factors: list[float]) -> list[tuple[str, float]]:
    """Each series row with its dilution factor; 0.0 for a row the factor list does not cover (print only)."""
    return [(row, factors[index] if index < len(factors) else 0.0) for index, row in enumerate(rows)]


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


def tip_group(policy: str, liquid_group: str, own_group: str) -> str:
    """The tip group of one operation (a new tip is picked up whenever the group changes): every operation shares one
    tip under single_tip, one tip per liquid or printed sample under per_liquid, and one tip each under
    new_tip_every_transfer. Mirrors the protocol's _tip_group."""
    if policy == "single_tip":
        return "run"
    return liquid_group if policy == "per_liquid" else own_group


def transfer_limit(config: dict[str, Any]) -> float:
    """The most liquid one vial transfer carries: the transfer ceiling, less the air gap that rides in the same tip
    (liquid + air gap never exceeds the P20). Mirrors the protocol's _transfer_limit."""
    dilution = config.get("dilution") or {}
    p20_max = float((config.get("safety") or {}).get("p20_max_volume_ul", 20.0))
    air_gap = float(liquid_handling(config).get("air_gap_ul", 0.0) or 0.0)
    return min(float(dilution.get("max_transfer_ul", 20.0)), p20_max - air_gap)


def mixes_dilutions(config: dict[str, Any]) -> bool:
    mixing = config.get("mixing") or {}
    try:
        return bool(mixing.get("enabled", True)) and int(mixing.get("reps", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def build_operations(config: dict[str, Any], rows: list[str], factors: list[float],
                     spots: list[dict[str, Any]], do_dilution: bool,
                     do_print: bool, *, prints: list[tuple[ResolvedSource, str, float, int]] | None = None
                     ) -> list[Operation]:
    """`prints`: the semantic print layout (semantic_prints), when the plan prints from it instead of the series or a
    dictated map. print.drops_at sets the drops of the positions it names, whichever layout printed them."""
    operations = _operations(config, rows, factors, spots, do_dilution, do_print, prints)
    drops = print_drops_at(config)
    if drops:
        operations = [replace(op, droplets=drops[op.destination]) if op.kind == "print" and op.destination in drops
                      else op for op in operations]
    return operations


def _operations(config: dict[str, Any], rows: list[str], factors: list[float], spots: list[dict[str, Any]],
                do_dilution: bool, do_print: bool, prints: list[tuple[ResolvedSource, str, float, int]] | None
                ) -> list[Operation]:
    dilution = config.get("dilution") or {}
    mixing = config.get("mixing") or {}
    policy = str((config.get("tips") or {}).get("policy", "per_liquid"))
    minimum = float((config.get("safety") or {}).get("p20_min_volume_ul", 1.0))
    limit = transfer_limit(config)
    dispense_mm = float(liquid_handling(config)["plate_dispense_height_mm"])
    column = str(dilution.get("plate_column", ""))
    total = float(dilution.get("total_volume_ul", 0.0) or 0.0)
    vials = {
        role: str(spec.get("vial", ""))
        for spec in (config.get("materials") or {}).values()
        if isinstance(spec, dict) and (role := spec.get("role"))
    }
    operations: list[Operation] = []
    if do_dilution:
        for role in ("solvent", "sample"):
            for row, factor in zip(rows, factors):
                sample_ul = total / factor
                volume = total - sample_ul if role == "solvent" else sample_ul
                if volume <= EPSILON_UL:
                    continue
                well = f"{row}{column}"
                chunks = split_volume(volume, limit, minimum)
                for index, chunk in enumerate(chunks, start=1):
                    group = tip_group(policy, role, f"{role}:{well}:{index}")
                    operations.append(Operation(
                        kind="transfer", group=group, source=vials.get(role, ""),
                        destination=well, volume_ul=chunk, factor=factor, role=role,
                        chunk=index, chunks=len(chunks), height_mm=dispense_mm,
                    ))
                if role == "sample" and mixes_dilutions(config) and total - volume > EPSILON_UL:
                    # the dye is in and the well holds both liquids: mix it now, with the tip that just dispensed
                    operations.append(Operation(
                        kind="mix", group=operations[-1].group, source=well, destination=well,
                        volume_ul=float(mixing.get("volume_ul", 0) or 0), factor=factor, reps=int(mixing["reps"]),
                    ))
    if do_print and prints is not None:
        # The semantic layout: each print names its source well (a reference resolved by semantic_prints) and its one
        # paper position; one tip per source under per_liquid, as for a print map.
        for source, position, volume, droplets in prints:
            operations.append(Operation(
                kind="print", group=tip_group(policy, f"print:{source.well}", f"print:{position}"), source=source.well,
                destination=position, volume_ul=float(volume), factor=float(source.factor), droplets=int(droplets),
                column=int(position[1:]),
            ))
    elif do_print:
        entries = print_map(config)
        if entries is not None:
            # An explicit map: each source well prints exactly its positions, in order. A source may feed any
            # number of positions; its factor is known only when this run makes that well. A position may take its
            # own drop count (entry "drops"); the others take print.droplets_per_spot.
            volumes = droplet_volumes(config)
            default_volume = volumes[0] if volumes else 0.0
            droplets = int((config.get("print") or {}).get("droplets_per_spot", 1))
            made = {f"{row}{column}": factor for row, factor in zip(rows, factors)} if do_dilution else {}
            for entry in entries:
                source = entry["source"]
                volume = float(entry.get("volume_ul", default_volume))
                drops = entry.get("drops") or {}
                for position in entry["positions"]:
                    group = tip_group(policy, f"print:{source}", f"print:{position}")
                    operations.append(Operation(
                        kind="print", group=group, source=source, destination=position, volume_ul=volume,
                        factor=float(made.get(source, 0.0)), droplets=int(drops.get(position, droplets)),
                        column=int(position[1:]),
                    ))
        else:
            # Each series row prints on its paper row (print.paper_rows, or the row with the same letter), one paper
            # column per spot.
            for (row, factor), paper_row in zip(row_factors(rows, factors), paper_rows_of(config, rows)):
                source = f"{row}{column}"
                for spot in spots:
                    position = f"{paper_row}{spot['column']}"
                    group = tip_group(policy, f"print:{row}", f"print:{position}")
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


def build_plan(config: dict[str, Any], *, occupied: Iterable[str] = ()) -> Plan:
    """The deterministic plan of a configuration: every liquid movement in order, with its tip. A PlanSpec that prints
    semantically (is_semantic_print: a source selection, exact columns, prints past the paper edge) is laid out here
    from its references - the wells they resolve to now - so the plan, its checks and its rendering always show where
    the liquids are; the compiler (compiler.py) lowers the same layout into the exact print map the protocol runs.
    `occupied`: paper positions earlier live runs printed on, which extra prints placed past the edge avoid."""
    do_dilution, do_print = steps_enabled(config)
    dilution = config.get("dilution") or {}
    factors = factors_of(config)
    rows = dilution_rows(config)
    column = str(dilution.get("plate_column", ""))
    total = float(dilution.get("total_volume_ul", 0.0) or 0.0)
    dictated = print_map(config) is not None
    semantic = do_print and not dictated and is_semantic_print(config)
    mapped = dictated or semantic
    prints, issues = semantic_prints(config, occupied=occupied) if semantic else (None, [])
    if dictated and do_print:
        issues = map_problems(config)
    if do_dilution:
        # Making dilutions: one factor per series row (validate() requires the counts to agree).
        wells = [
            DilutionWell(row, f"{row}{column}", factor, total / factor, total - total / factor)
            for row, factor in zip(rows, factors) if factor > 0
        ]
    elif dictated and do_print:
        wells = []           # printing from the wells the map names; no dilution series is involved
    else:
        # Printing the series rows that already hold liquid: a row the factor list does not cover prints too. With a
        # print source selection, only the dilutions it prints (a well holding the scientist's own sample is no
        # dilution of this series).
        printed = None
        if semantic and (config.get("print") or {}).get("source") not in (None, "", []):
            printed = {source.well for source in selected_sources(config)[0] if source.ref.kind == DILUTION}
        wells = [
            DilutionWell(row, f"{row}{column}", factor, total / factor if factor > 0 else 0.0,
                         total - total / factor if factor > 0 else 0.0)
            for row, factor in row_factors(rows, factors) if printed is None or f"{row}{column}" in printed
        ]
    spots = [] if mapped else paper_layout(config)
    operations = build_operations(config, rows, factors, spots, do_dilution, do_print, prints=prints)
    if do_print:
        drops = print_drops_at(config)
        printed_positions = {op.destination for op in operations if op.kind == "print"}
        unused = [position for position in drops if position not in printed_positions]
        if unused:
            issues = list(issues) + [f"drops are set for paper position{'s' if len(unused) > 1 else ''} "
                                     f"{', '.join(unused)}, but nothing prints there"]
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
    made = {well.well for well in wells} if do_dilution else set()
    order: list[str] = []
    positions: dict[str, list[str]] = {}
    draws: dict[str, float] = {}
    factor_of: dict[str, float] = {}
    for operation in operations:
        if operation.kind != "print":
            continue
        if operation.source not in positions:
            order.append(operation.source)
            positions[operation.source], draws[operation.source] = [], 0.0
            factor_of[operation.source] = operation.factor
        positions[operation.source].append(operation.destination)
        draws[operation.source] += operation.volume_ul * operation.droplets
    print_sources = [
        PrintSource(well, tuple(positions[well]), factor_of[well], well in made,
                    total if well in made or prepared in (None, "") else float(prepared), round(draws[well], 6))
        for well in order
    ]
    if mapped:
        draw = max((source.draw_ul for source in print_sources), default=0.0) if do_print else 0.0
    else:
        draw = sum(spot["volume_ul"] * spot["droplets"] for spot in spots) if do_print else 0.0
    prints = [operation for operation in operations if operation.kind == "print"]
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
        total_drops=sum(operation.droplets for operation in prints),
        printed_fluid_ul=round(sum(operation.volume_ul * operation.droplets for operation in prints), 6),
        vial_use_ul={role: round(volume, 2) for role, volume in vial_use.items()},
        mapped=mapped, print_sources=print_sources,
        paper_rows=[] if mapped else paper_rows_of(config, rows),
        print_issues=list(issues) if do_print else [],
    )


# ── geometry used by the liquid checks ──────────────────────────────────────────

def well_area_mm2(config: dict[str, Any], role: str) -> float | None:
    """Cross-section of the labware's wells, when its definition is on disk."""
    spec = (config.get("deck") or {}).get(role) or {}
    geometry = well_geometry(str(spec.get("load_name", "")))
    return circle_area_mm2(geometry[0]) if geometry else None


def labware_on_deck(config: dict[str, Any], role: str) -> bool:
    return not is_off_deck(slot_of(config, role))
