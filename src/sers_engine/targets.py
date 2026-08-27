"""Concise A1:H12 target grammar, expanded to explicit wells before execution.

The agent may speak the way a scientist does -- ``column 1``, ``A1:C1``,
``rows A-C``, ``A1, B4, F7`` -- but nothing reaches the robot until it is a
plain, ordered list of wells on the standard 8 x 12 grid.
"""

from __future__ import annotations

import re
from typing import Iterable

ROWS = "ABCDEFGH"
COLUMNS = list(range(1, 13))

_WELL = re.compile(r"^([A-H])(\d{1,2})$", re.IGNORECASE)
_RANGE = re.compile(r"^([A-H])(\d{1,2})\s*[:\-]\s*([A-H])(\d{1,2})$", re.IGNORECASE)
_COLUMNS = re.compile(r"^col(?:umn)?s?\s+(.+)$", re.IGNORECASE)
_ROWS = re.compile(r"^rows?\s+(.+)$", re.IGNORECASE)
_INT_LIST = re.compile(r"^\s*\d{1,2}(\s*(?:,|and|&|-|to)\s*\d{1,2})*\s*$", re.IGNORECASE)


class TargetSpecError(ValueError):
    """A target specification cannot be turned into real paper locations."""


def _well(row: str, column: int) -> str:
    return f"{row.upper()}{column}"


def _check(row: str, column: int, spec: str) -> None:
    if row.upper() not in ROWS:
        raise TargetSpecError(f"{spec!r}: row {row!r} is outside A-H")
    if column not in COLUMNS:
        raise TargetSpecError(f"{spec!r}: column {column} is outside 1-12")


def _expand_numbers(text: str, spec: str) -> list[int]:
    """Parse ``1``, ``1 and 2``, ``1,2,3``, ``1-4``, ``1 to 4``."""
    numbers: list[int] = []
    for chunk in re.split(r"\s*(?:,|and|&)\s*", text.strip()):
        if not chunk:
            continue
        span = re.match(r"^(\d{1,2})\s*(?:-|to|through|\u2013)\s*(\d{1,2})$", chunk, re.IGNORECASE)
        if span:
            start, end = int(span.group(1)), int(span.group(2))
            step = 1 if end >= start else -1
            numbers.extend(range(start, end + step, step))
            continue
        if not chunk.strip().isdigit():
            raise TargetSpecError(f"{spec!r}: cannot read column list {chunk!r}")
        numbers.append(int(chunk))
    if not numbers:
        raise TargetSpecError(f"{spec!r}: no columns given")
    return numbers


def _expand_letters(text: str, spec: str) -> list[str]:
    """Parse ``A``, ``A and C``, ``A,B``, ``A-H``, ``A to H``."""
    letters: list[str] = []
    for chunk in re.split(r"\s*(?:,|and|&)\s*", text.strip()):
        chunk = chunk.strip()
        if not chunk:
            continue
        span = re.match(r"^([A-H])\s*(?:-|to|through|\u2013)\s*([A-H])$", chunk, re.IGNORECASE)
        if span:
            start, end = ROWS.index(span.group(1).upper()), ROWS.index(span.group(2).upper())
            step = 1 if end >= start else -1
            letters.extend(ROWS[index] for index in range(start, end + step, step))
            continue
        if len(chunk) != 1 or chunk.upper() not in ROWS:
            raise TargetSpecError(f"{spec!r}: cannot read row list {chunk!r}")
        letters.append(chunk.upper())
    if not letters:
        raise TargetSpecError(f"{spec!r}: no rows given")
    return letters


def expand_target_spec(spec: str) -> list[str]:
    """Turn one concise specification into explicit wells, in column-major order.

    Column-major matches how an Opentrons 96-well definition is ordered, so a
    rectangular block prints down each column before moving right.
    """
    text = str(spec).strip()
    if not text:
        raise TargetSpecError("empty target specification")

    single = _WELL.match(text)
    if single:
        row, column = single.group(1), int(single.group(2))
        _check(row, column, text)
        return [_well(row, column)]

    block = _RANGE.match(text)
    if block:
        row_a, col_a, row_b, col_b = (
            block.group(1).upper(), int(block.group(2)),
            block.group(3).upper(), int(block.group(4)),
        )
        _check(row_a, col_a, text)
        _check(row_b, col_b, text)
        row_lo, row_hi = sorted((ROWS.index(row_a), ROWS.index(row_b)))
        col_lo, col_hi = sorted((col_a, col_b))
        return [
            _well(ROWS[r], c)
            for c in range(col_lo, col_hi + 1)
            for r in range(row_lo, row_hi + 1)
        ]

    columns = _COLUMNS.match(text)
    if columns:
        return [
            _well(row, column)
            for column in _expand_numbers(columns.group(1), text)
            for row in ROWS
        ]

    rows = _ROWS.match(text)
    if rows:
        return [
            _well(row, column)
            for column in COLUMNS
            for row in _expand_letters(rows.group(1), text)
        ]

    # A bare number or number list means whole columns: "1", "1 and 2".
    if _INT_LIST.match(text):
        return [
            _well(row, column)
            for column in _expand_numbers(text, text)
            for row in ROWS
        ]

    # Comma-separated wells: "A1, B4, F7".
    if "," in text:
        wells: list[str] = []
        for chunk in text.split(","):
            wells.extend(expand_target_spec(chunk))
        return wells

    raise TargetSpecError(
        f"cannot read paper target {text!r}. Use a well (A1), a block (A1:C3), "
        "a column list (column 1, columns 1 and 2), a row list (rows A-C), or "
        "comma-separated wells (A1, B4, F7)."
    )


def resolve_targets(spec: str | Iterable[str]) -> list[str]:
    """Expand one spec or a list of specs, preserving order and rejecting repeats.

    A repeated location is refused rather than silently collapsed: printing the
    same liquid twice onto one spot is a drop-count decision, not a target list
    decision, so the ambiguity is surfaced instead of guessed.
    """
    specs = [spec] if isinstance(spec, str) else list(spec)
    wells: list[str] = []
    for item in specs:
        wells.extend(expand_target_spec(item))
    duplicates = sorted({well for well in wells if wells.count(well) > 1})
    if duplicates:
        raise TargetSpecError(
            f"paper targets repeat {duplicates}; use drops_per_target to put more "
            "than one drop on a location"
        )
    return wells
