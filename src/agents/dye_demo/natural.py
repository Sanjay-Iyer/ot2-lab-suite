"""Selections the conversational router uses instead of working out layouts itself.

Scientists talk about what they see: "rows a b c", "columns 1 2 and 3". The router passes that on as a selection,
{"path": "rows", "value": ["A", "B", "C"]} or {"path": "paper_columns", "value": [1, 2, 3]}, and this module turns it
into the real fields with exact arithmetic from the plan: the first plate row and the factors already in those wells,
or the first paper column and the replicate count that print exactly those columns. Nothing here reads the
scientist's words or decides what they meant; ExperimentState.propose() checks the selection against their words
and validates the result like any other change.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from src.agents.dye_demo.columns import GAP_QUESTION, columns_phrase, gap_message, paper_columns_printed
from src.agents.dye_demo.model import ROWS, FieldError, fmt_factor, normalize_paper_column
from src.agents.dye_demo.plan import build_plan, droplet_volumes, steps_enabled


class SelectionError(ValueError):
    """A selection this plan cannot print as named; `question` asks for what would make it possible."""

    def __init__(self, message: str, question: str = ""):
        super().__init__(message)
        self.question = question


def _items(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part for part in re.split(r"\s*(?:,|;|&|\band\b|\s)\s*", str(value).strip()) if part]


_ROW_NUMBERS = {
    "1": "A", "2": "B", "3": "C", "4": "D", "5": "E", "6": "F", "7": "G", "8": "H",
    "1ST": "A", "2ND": "B", "3RD": "C", "4TH": "D", "5TH": "E", "6TH": "F", "7TH": "G", "8TH": "H",
    "FIRST": "A", "SECOND": "B", "THIRD": "C", "FOURTH": "D", "FIFTH": "E", "SIXTH": "F", "SEVENTH": "G", "EIGHTH": "H",
}


def selected_rows(value: Any) -> list[str]:
    """["a", "c", "e"], "A, C, E", "1 3 5", "first 3", "A5 C5 E5" or "row 3" -> ["A", "C", "E"] or ["C"]."""
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            item_str = str(item).strip().upper()
            if item_str in ROWS:
                result.append(item_str)
            elif len(item_str) >= 2 and item_str[0] in ROWS and item_str[1:].isdigit():
                result.append(item_str[0])
            elif item_str in _ROW_NUMBERS:
                result.append(_ROW_NUMBERS[item_str])
            elif item_str.isdigit() and 1 <= int(item_str) <= 8:
                result.append(ROWS[int(item_str) - 1])
        if result:
            return sorted(set(result), key=ROWS.index)

    text = " ".join(_items(value)).upper()

    # Check for well tokens like A1, C3, E5
    wells = re.findall(r"\b([A-H])\d{1,2}\b", text, re.I)
    if wells:
        return sorted({w.upper() for w in wells}, key=ROWS.index)

    count_match = re.search(
        r"\b(?:FIRST|1ST|TOP)\s+(?:ROW|WELL|ROWS|WELLS)?\s*(\d{1,2}|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT)\b|\b(?:FIRST|1ST|TOP)\s+(\d{1,2}|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT)\s+(?:ROW|WELL|ROWS|WELLS)?\b",
        text, re.I)
    if count_match:
        val = count_match.group(1) or count_match.group(2)
        num_map = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8}
        count = num_map.get(val.upper(), int(val) if val.isdigit() else 0)
        if 1 <= count <= 8:
            return list(ROWS[:count])

    span_letter = re.search(r"\b(?:ROWS?|WELLS?)?\s*([A-H])\s*(?:-|–|TO|THROUGH|THRU)\s*([A-H])\b", text, re.I)
    if span_letter:
        first, last = ROWS.index(span_letter.group(1).upper()), ROWS.index(span_letter.group(2).upper())
        if first > last:
            raise SelectionError(f"rows {span_letter.group(1)}-{span_letter.group(2)} run backwards")
        return list(ROWS[first:last + 1])

    span_num = re.search(r"\b(?:ROWS?|WELLS?)?\s*([1-8])\s*(?:-|–|TO|THROUGH|THRU)\s*([1-8])\b", text, re.I)
    if span_num:
        first_idx, last_idx = int(span_num.group(1)) - 1, int(span_num.group(2)) - 1
        if first_idx > last_idx:
            raise SelectionError(f"rows {span_num.group(1)}-{span_num.group(2)} run backwards")
        return list(ROWS[first_idx:last_idx + 1])

    digits = [int(token) for token in re.findall(r"\b[1-8]\b", text)]
    if len(digits) > 1:
        return sorted({ROWS[d - 1] for d in digits}, key=ROWS.index)

    single_num = re.search(r"\b(?:ROWS?|WELLS?)?\s*([1-8]|1ST|2ND|3RD|4TH|5TH|6TH|7TH|8TH|FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH)\s*(?:ROWS?|WELLS?)?\b", text, re.I)
    if single_num:
        key = single_num.group(1).upper()
        if key.isdigit() and 1 <= int(key) <= 8:
            return [ROWS[int(key) - 1]]
        if key in _ROW_NUMBERS:
            return [_ROW_NUMBERS[key]]

    letters = [token for token in re.findall(r"\b[A-Z]\b", re.sub(r"\b(?:ROWS?|WELLS?)\b", " ", text))]
    if letters and all(letter in ROWS for letter in letters):
        return sorted(set(letters), key=ROWS.index)

    if digits:
        return sorted({ROWS[d - 1] for d in digits}, key=ROWS.index)

    raise SelectionError(f"plate rows are A-H (or 1-8), got {value!r}")


def selected_columns(value: Any) -> list[int]:
    """[1, 2, 3], "1-3" or "columns 1 2 and 3" -> [1, 2, 3]."""
    text = " ".join(_items(value)).lower()
    span = re.fullmatch(r"(?:columns?\s+)?(\d{1,2})\s*(?:-|–|to|through|thru)\s*(\d{1,2})", text)
    try:
        if span:
            first, last = normalize_paper_column(span.group(1)), normalize_paper_column(span.group(2))
            if first > last:
                raise SelectionError(f"paper columns {first}-{last} run backwards")
            return list(range(first, last + 1))
        numbers = re.findall(r"\d{1,2}", text)
        if not numbers:
            raise SelectionError(f"paper columns are numbers 1-12, got {value!r}")
        return sorted({normalize_paper_column(number) for number in numbers})
    except FieldError as exc:
        raise SelectionError(str(exc)) from exc


def _span(items: list[Any]) -> str:
    return f"{items[0]}-{items[-1]}" if len(items) > 1 else str(items[0])


def expand_rows(rows: list[str], config: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """The selected plate/paper rows and factors."""
    plan = build_plan(config)
    wells = {well.row: well for well in plan.wells}
    missing = [row for row in rows if row not in wells]
    if missing:
        existing_factors = [well.factor for well in plan.wells]
        if not existing_factors:
            existing_factors = [float(f) for f in (config.get("dilution") or {}).get("factors", [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0])]
        needed_count = len(rows)
        factors = list(existing_factors)
        while len(factors) < needed_count:
            last = factors[-1] if factors else 1.0
            factors.append(last * 2.0)
        chosen_factors = factors[:needed_count]
    else:
        chosen = [wells[row] for row in rows if row in wells]
        chosen_factors = [well.factor for well in chosen]
    why = "use the current plate wells corresponding to the selected paper rows"
    changes = [
        {"path": "dilution.rows", "value": rows, "kind": "dependent", "why": why},
        {"path": "dilution.start_row", "value": rows[0], "kind": "dependent", "why": why},
        {"path": "dilution.factors", "value": chosen_factors, "kind": "dependent", "why": why},
    ]
    row_phrase = ", ".join(rows) if len(rows) > 1 else f"row {rows[0]}"
    if len(rows) > 1:
        note = f"I interpreted rows {row_phrase} as paper destinations."
    else:
        note = f"I interpreted row {ROWS.index(rows[0]) + 1} as row {rows[0]} on the diagram."
    return changes, note


def expand_paper_columns(columns: list[int], config: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """The first paper column and the replicate count for these paper columns."""
    volumes = max(1, len(droplet_volumes(config)))
    changes: list[dict[str, Any]] = []
    if not steps_enabled(config)[1]:
        changes.append({"path": "print.enabled", "value": True})
    replicates = max(1, len(columns) // volumes)
    changes += [{"path": "print.paper_start_column", "value": columns[0]},
                {"path": "print.replicates", "value": replicates}]
    col_phrase = f"columns {', '.join(map(str, columns))}" if len(columns) > 1 else f"column {columns[0]}"
    return changes, f"I interpreted the named paper columns as destinations for this procedure: {col_phrase}."
