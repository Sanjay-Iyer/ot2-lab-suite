"""Where prints go on the paper: a deterministic allocator of free paper positions.

The scientist - through the model - says HOW MANY prints each sample needs and, when they care, WHERE: exact paper
positions, paper rows or columns, the same row or column, or side by side. This module decides the exact positions;
nothing here reads words. Validation then checks the result like any other print map (print.source_map).

The order a new print is placed in (deterministic: the same plan and request always give the same positions):
  1. positions a sample already prints on are kept, never moved (a lower count keeps its first prints);
  2. its own paper row - the row it prints on now, else its dilution's paper row - nearest free column first, to the
     right of its last print before the left at the same distance, so a full right edge continues leftwards;
  3. then the nearest other rows the same way, rows that belong to other samples last.
A dilution series keeps its replicates side by side: the k-th new print of every dilution goes in one shared paper
column when one is free in all their rows (allocate_side_by_side), like the default layout.
A paper position is never used twice: not by another sample of the plan, and not when an earlier live run of the
session already printed on it. A request is refused only when no free position is left - a real limit of the paper.

Placement wishes narrow the search: `rows` and/or `columns` (only those), "same_row" (one row), "same_column" (one
column, the sample's own row first) and "adjacent" (one unbroken side-by-side run in a row, next to the sample's
existing prints when there is room).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.agents.dye_demo.model import PAPER_COLUMNS, ROWS, positions_text
from src.agents.dye_demo.plan import build_plan, droplet_volumes, paper_layout, paper_rows_of, print_map, row_factors

PLACEMENTS = ("auto", "adjacent", "same_row", "same_column")
_PLACEMENT_WORDS = {
    "auto": "auto", "any": "auto", "anywhere": "auto", "": "auto",
    "adjacent": "adjacent", "side_by_side": "adjacent", "next_to_each_other": "adjacent", "together": "adjacent",
    "contiguous": "adjacent", "same_row": "same_row", "row": "same_row", "same_column": "same_column",
    "column": "same_column",
}


class PlacementError(ValueError):
    """Not enough free paper positions (a real limit of the paper), or a placement wish that cannot be met."""


def placement_of(value: object) -> str:
    """A placement wish as one of PLACEMENTS ("next to each other" -> adjacent); anything unknown is auto."""
    key = "_".join(str(value or "").strip().lower().replace("-", " ").split())
    return _PLACEMENT_WORDS.get(key, "auto")


@dataclass(frozen=True)
class Need:
    """Prints one sample (one plate well, at one drop volume) needs on the paper."""

    source: str                          # the plate well printed from
    count: int                           # prints in total, the ones it already has included
    existing: tuple[str, ...] = ()       # paper positions it already prints on: kept, in this order
    home_row: str = ""                   # its paper row ("" = the row letter of the source well)
    rows: tuple[str, ...] = ()           # only these paper rows (empty = any)
    columns: tuple[int, ...] = ()        # only these paper columns (empty = any)
    placement: str = "auto"
    anchor_column: int = 1               # where its search starts when it has no print yet

    @property
    def row(self) -> str:
        """The row its new prints start in: the row it prints on now, else its home row."""
        if self.existing:
            return self.existing[-1][0]
        return (self.home_row or self.source[0]).upper()

    @property
    def anchor(self) -> int:
        return int(self.existing[-1][1:]) if self.existing else int(self.anchor_column)


def _column_order(anchor: int, width: int) -> list[int]:
    """Columns nearest the anchor first; at the same distance the right one first (the side-by-side direction)."""
    return sorted(range(1, width + 1), key=lambda column: (abs(column - anchor), column < anchor))


def _row_order(home: str, reserved: set[str]) -> list[str]:
    """Rows nearest `home` first (below before above at the same distance); rows reserved for other samples last."""
    first = ROWS.index(home)
    return sorted(ROWS, key=lambda row: (row != home and row in reserved, abs(ROWS.index(row) - first),
                                         ROWS.index(row) < first))


def _candidates(need: Need, reserved: set[str], width: int) -> list[str]:
    """Every position `need` may use, best first (the module docstring's order, narrowed by its wishes)."""
    rows = [row for row in _row_order(need.row, reserved) if not need.rows or row in need.rows]
    columns = [column for column in _column_order(need.anchor, width) if not need.columns or column in need.columns]
    if not rows or not columns:
        return []
    if need.placement == "same_row":
        return [f"{rows[0]}{column}" for column in columns]
    if need.placement == "same_column":
        return [f"{row}{columns[0]}" for row in rows]
    return [f"{row}{column}" for row in rows for column in columns]            # row by row, nearest column first


def _adjacent(need: Need, extra: int, taken: set[str], reserved: set[str], width: int) -> list[str] | None:
    """`extra` free side-by-side positions in one row: touching the sample's existing prints when it has some in that
    row, else as close to its anchor as possible; its own row first."""
    rows = [row for row in _row_order(need.row, reserved) if not need.rows or row in need.rows]
    allowed = set(need.columns) if need.columns else set(range(1, width + 1))
    for row in rows:
        mine = sorted(int(position[1:]) for position in need.existing if position[0] == row)
        runs = []
        for start in range(1, width - extra + 2):
            span = range(start, start + extra)
            if all(column in allowed and f"{row}{column}" not in taken for column in span):
                runs.append(start)
        if mine:
            touching = [start for start in runs if start == mine[-1] + 1 or start + extra == mine[0]]
            if touching:
                start = min(touching, key=lambda s: (s < mine[0], s))   # extend to the right before the left
                return [f"{row}{column}" for column in range(start, start + extra)]
        if runs:
            anchor = need.anchor
            start = min(runs, key=lambda s: (min(abs(column - anchor) for column in range(s, s + extra)), s < anchor))
            return [f"{row}{column}" for column in range(start, start + extra)]
    return None


def _free_count(taken: set[str], width: int) -> int:
    return sum(f"{row}{column}" not in taken for row in ROWS for column in range(1, width + 1))


def _refusal(need: Need, extra: int, taken: set[str], width: int) -> PlacementError:
    free = _free_count(taken, width)
    if free < extra:
        return PlacementError(f"there are not enough free paper positions for {extra} more print"
                              f"{'s' if extra != 1 else ''} of {need.source}: {free} of the paper's "
                              f"{len(ROWS) * width} positions {'is' if free == 1 else 'are'} free")
    wishes = []
    if need.rows:
        wishes.append(f"paper row{'s' if len(need.rows) > 1 else ''} {', '.join(need.rows)}")
    if need.columns:
        wishes.append(f"paper column{'s' if len(need.columns) > 1 else ''} {', '.join(map(str, need.columns))}")
    if need.placement != "auto":
        wishes.append({"adjacent": "side by side", "same_row": f"paper row {need.row}",
                       "same_column": "one paper column"}[need.placement])
    where = " and ".join(wishes) or "the paper"
    return PlacementError(f"there is no room for {extra} more print{'s' if extra != 1 else ''} of {need.source} "
                          f"in {where}")


def allocate(needs: Iterable[Need], occupied: Iterable[str] = (), *, width: int = PAPER_COLUMNS,
             reserved_rows: Iterable[str] = ()) -> list[list[str]]:
    """The paper positions of each need, in order: the prints it already has (never moved), then its new ones.

    `occupied` are positions no new print may use (printed earlier in the session, or kept by prints outside these
    needs). `reserved_rows` are paper rows other samples print on (used last). Raises PlacementError when a need cannot
    be placed."""
    needs = list(needs)
    kept = [list(need.existing)[:max(0, need.count)] for need in needs]
    taken = set(occupied) | {position for positions in kept for position in positions}
    reserved = {row.upper() for row in reserved_rows}
    placed: list[list[str]] = []
    for need, existing in zip(needs, kept):
        extra = need.count - len(existing)
        if extra <= 0:
            placed.append(existing)
            continue
        others = reserved - {need.row}
        if need.placement == "adjacent":
            new = _adjacent(need, extra, taken, others, width) or []
        else:
            new = [position for position in _candidates(need, others, width) if position not in taken][:extra]
        if len(new) < extra:
            raise _refusal(need, extra, taken, width)
        taken.update(new)
        placed.append(existing + new)
    return placed


def allocate_side_by_side(needs: Iterable[Need], occupied: Iterable[str] = (), *,
                          width: int = PAPER_COLUMNS) -> list[list[str]] | None:
    """Series-like needs - each sample on its own paper row, the same number of new prints, no wishes: the k-th new
    print of every sample goes in one shared paper column, the free column nearest the current ones (right first),
    so replicates stay side by side as columns like the default layout. None when no such set of columns exists (the
    caller then places each sample on its own with allocate())."""
    needs = list(needs)
    if not needs or any(need.placement != "auto" or need.rows or need.columns for need in needs):
        return None
    extras = {need.count - len(need.existing) for need in needs}
    rows = [need.row for need in needs]
    if len(extras) != 1 or len(set(rows)) != len(rows) or any(len({p[0] for p in need.existing}) > 1 for need in needs):
        return None
    extra = extras.pop()
    if extra <= 0:
        return None
    taken = set(occupied) | {position for need in needs for position in need.existing}
    anchor = max((need.anchor for need in needs), default=1)
    columns = [column for column in _column_order(anchor, width)
               if all(f"{row}{column}" not in taken for row in rows)][:extra]
    if len(columns) < extra:
        return None
    return [list(need.existing) + [f"{need.row}{column}" for column in columns] for need in needs]


# ── plans: the prints a replicate count asks for, as an explicit print map ─────────────────────────────────────────

class CountUnclear(PlacementError):
    """A replicate count that does not say how many prints each sample of a print map should have (they print a
    different number of times now): a question, never a guess."""

    def __init__(self, message: str, question: str):
        super().__init__(message)
        self.question = question


def _entry(source: str, positions: list[str], volume: float, several_volumes: bool) -> dict:
    entry = {"source": source, "positions": list(positions)}
    if several_volumes:
        entry["volume_ul"] = volume
    return entry


def _new_positions(entries: list[dict], before: Iterable[str]) -> list[str]:
    had = set(before)
    return [position for entry in entries for position in entry["positions"] if position not in had]


def series_as_map(before: dict, after: dict, occupied: Iterable[str] = (), *,
                  width: int = PAPER_COLUMNS) -> tuple[list[dict], list[str]] | None:
    """`after`'s dilution series as an explicit print map when its side-by-side layout needs a paper column past the
    paper's edge. The prints the side-by-side layout does place on the paper stay exactly there; only the ones past the
    edge go on free positions, side by side in shared columns when possible. Returns (print map, the positions `before`
    did not print on), or None when the side-by-side layout fits the paper."""
    spots = paper_layout(after, include_overflow=True)
    if not spots or max(int(spot["column"]) for spot in spots) <= width:
        return None
    printing = after.get("print") or {}
    replicates = int(printing.get("replicates", 1))
    start = min(max(int(printing.get("paper_start_column", 1)), 1), width)
    volumes = droplet_volumes(after)
    plan = build_plan(after)
    series = [(f"{row}{plan.plate_column}", paper_row)
              for (row, _), paper_row in zip(row_factors(plan.rows, plan.factors), paper_rows_of(after, plan.rows))]
    fits = {volume: [int(spot["column"]) for spot in spots
                     if float(spot["volume_ul"]) == float(volume) and int(spot["column"]) <= width]
            for volume in volumes}
    needs = {volume: [Need(source, replicates, tuple(f"{paper_row}{column}" for column in fits[volume]), paper_row,
                           anchor_column=start) for source, paper_row in series]
             for volume in volumes}
    rows = {paper_row for _, paper_row in series}
    taken = set(occupied) | {position for group in needs.values() for need in group for position in need.existing}
    placed: dict[float, list[list[str]]] = {}
    for volume in volumes:
        placed[volume] = (allocate_side_by_side(needs[volume], taken, width=width)
                          or allocate(needs[volume], taken, width=width, reserved_rows=rows))
        taken.update(position for positions in placed[volume] for position in positions)
    entries = [_entry(source, placed[volume][index], volume, len(volumes) > 1)
               for index, (source, _) in enumerate(series) for volume in volumes]
    return entries, _new_positions(entries, build_plan(before).print_positions)


def map_with_count(before: dict, after: dict, occupied: Iterable[str] = (), *,
                   width: int = PAPER_COLUMNS) -> tuple[list[dict], list[str]]:
    """`after`'s print map brought to its replicate count: each entry (a sample at one drop volume) keeps its paper
    positions and gets its new prints on free positions, or keeps its first prints when the count goes down. Raises
    CountUnclear when the entries do not all print `before`'s count now ("2 replicates" of a sample printed 6 times
    and one printed 4 times has no single meaning). Returns (print map, the new positions)."""
    entries = print_map(after) or []
    old = int((before.get("print") or {}).get("replicates", 1))
    new = int((after.get("print") or {}).get("replicates", 1))
    counts = [len(entry["positions"]) for entry in entries]
    if set(counts) != {old}:
        shown = ", ".join(f"{entry['source']} {count} time{'s' if count != 1 else ''}"
                          for entry, count in zip(entries[:4], counts[:4])) + (", ..." if len(entries) > 4 else "")
        raise CountUnclear(f"the print map prints {shown} now, so {new} replicate{'s' if new != 1 else ''} does not "
                           "say how many prints each sample should have",
                           "How many prints of each sample should there be, and on which paper positions if it "
                           "matters?")
    needs = [Need(entry["source"], new, tuple(entry["positions"]), entry["positions"][0][0],
                  anchor_column=int(entry["positions"][-1][1:])) for entry in entries]
    rows = {entry["positions"][0][0] for entry in entries}
    placed = allocate_side_by_side(needs, occupied, width=width) or allocate(needs, occupied, width=width,
                                                                              reserved_rows=rows)
    result = [{**entry, "positions": positions} for entry, positions in zip(entries, placed)]
    return result, _new_positions(result, (position for entry in entries for position in entry["positions"]))


def describe_new(positions: list[str]) -> str:
    """'A11', 'A11-H11' or 'A10, A11, B12' for a note."""
    return positions_text(positions, limit=10) if positions else "none"
