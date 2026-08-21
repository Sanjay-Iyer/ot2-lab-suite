"""
Deterministic labware position geometry.

Pure coordinate math — no JSON, no file I/O, no Opentrons imports. Given a
grid description this module returns named positions; everything downstream
(:mod:`src.labware.builder`) turns those into a labware definition.

Coordinate convention (OT-2, all values in millimetres)
------------------------------------------------------
* Origin is the FRONT-LEFT-BOTTOM corner of the labware.
* x increases to the RIGHT, y increases toward the BACK, z increases UP.
* Row "A" is the BACK row (highest y); y DECREASES from A toward H.
* Column 1 is the LEFT column; x INCREASES from 1 toward 12.

That direction convention is not a guess — it is read off the known-working
``labware/paper_print_96_flat.json`` and reproduced exactly:

    A1  = (14.38, 74.24)      A12 = (113.38, 74.24)
    H1  = (14.38, 11.24)      H12 = (113.38, 11.24)

so ``x = 14.38 + 9.0 * column_index`` and ``y = 74.24 - 9.0 * row_index``.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from typing import List, Sequence

# Opentrons definitions in this repo carry 2-decimal coordinates. Rounding at
# generation time (rather than letting float error accumulate) is what makes
# regeneration byte-stable — 14.38 + 9.0 * 5 is 59.379999999999995 in binary
# floating point, and the reference file says 59.38.
COORD_DECIMALS = 2


@dataclass(frozen=True)
class GridPosition:
    """One addressable position (a "well") in a rectangular grid."""

    name: str            # "D6"
    row_index: int       # 0-based, 0 = row A = back
    column_index: int    # 0-based, 0 = column 1 = left
    x: float             # mm from the left edge
    y: float             # mm from the front edge

    @property
    def row_letter(self) -> str:
        return self.name.rstrip(string.digits)

    @property
    def column_label(self) -> int:
        return self.column_index + 1


def row_letters(count: int) -> List[str]:
    """``['A', 'B', ...]`` for *count* rows, continuing ``AA, AB, ...`` past 26."""
    if count < 1:
        raise ValueError(f"row count must be >= 1, got {count}")
    letters: List[str] = []
    for i in range(count):
        if i < 26:
            letters.append(string.ascii_uppercase[i])
        else:
            letters.append(
                string.ascii_uppercase[i // 26 - 1] + string.ascii_uppercase[i % 26]
            )
    return letters


def generate_rectangular_grid(
    rows: int,
    columns: int,
    origin_x_mm: float,
    origin_y_mm: float,
    spacing_x_mm: float,
    spacing_y_mm: float,
    decimals: int = COORD_DECIMALS,
) -> List[GridPosition]:
    """Return every position of a ``rows`` x ``columns`` grid, in column-major order.

    ``origin_x_mm`` / ``origin_y_mm`` are the centre of **A1** — the back-left
    position. Positions come back ordered A1, B1, ... H1, A2, ... H12, which is
    the order Opentrons' ``ordering`` array and the ``wells`` object both use.
    """
    if rows < 1:
        raise ValueError(f"rows must be >= 1, got {rows}")
    if columns < 1:
        raise ValueError(f"columns must be >= 1, got {columns}")

    letters = row_letters(rows)
    positions: List[GridPosition] = []

    for column_index in range(columns):
        x = round(origin_x_mm + column_index * spacing_x_mm, decimals)
        for row_index in range(rows):
            # Row A is the back row, so y decreases as the row index grows.
            y = round(origin_y_mm - row_index * spacing_y_mm, decimals)
            positions.append(
                GridPosition(
                    name=f"{letters[row_index]}{column_index + 1}",
                    row_index=row_index,
                    column_index=column_index,
                    x=x,
                    y=y,
                )
            )
    return positions


def column_major_ordering(positions: Sequence[GridPosition]) -> List[List[str]]:
    """Group positions into the schema's ``ordering`` array: one list per column."""
    if not positions:
        return []
    column_count = max(p.column_index for p in positions) + 1
    columns: List[List[str]] = [[] for _ in range(column_count)]
    for position in positions:
        columns[position.column_index].append(position.name)
    return columns


def flatten_ordering(ordering: Sequence[Sequence[str]]) -> List[str]:
    """Flatten ``ordering`` to the single well-name list used by ``groups[].wells``."""
    return [name for column in ordering for name in column]
