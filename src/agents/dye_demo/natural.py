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
    """["a", "b", "c"], "A-C", "1-3", "first 3", or "row 3" -> ["A", "B", "C"] or ["C"]."""
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            item_str = str(item).strip().upper()
            if item_str in ROWS:
                result.append(item_str)
            elif item_str in _ROW_NUMBERS:
                result.append(_ROW_NUMBERS[item_str])
            elif item_str.isdigit() and 1 <= int(item_str) <= 8:
                result.append(ROWS[int(item_str) - 1])
        if result:
            return sorted(set(result), key=ROWS.index)

    text = " ".join(_items(value)).upper()

    count_match = re.search(r"\b(?:FIRST|1ST|TOP)\s+(?:ROW\s+)?(\d{1,2}|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT)\b", text)
    if count_match:
        val = count_match.group(1)
        num_map = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8}
        count = num_map.get(val, int(val) if val.isdigit() else 0)
        if 1 <= count <= 8:
            return list(ROWS[:count])

    span_letter = re.fullmatch(r"(?:ROWS?\s+)?([A-H])\s*(?:-|–|TO|THROUGH|THRU)\s*([A-H])", text)
    if span_letter:
        first, last = ROWS.index(span_letter.group(1)), ROWS.index(span_letter.group(2))
        if first > last:
            raise SelectionError(f"rows {span_letter.group(1)}-{span_letter.group(2)} run backwards")
        return list(ROWS[first:last + 1])

    span_num = re.fullmatch(r"(?:ROWS?\s+)?([1-8])\s*(?:-|–|TO|THROUGH|THRU)\s*([1-8])", text)
    if span_num:
        first_idx, last_idx = int(span_num.group(1)) - 1, int(span_num.group(2)) - 1
        if first_idx > last_idx:
            raise SelectionError(f"rows {span_num.group(1)}-{span_num.group(2)} run backwards")
        return list(ROWS[first_idx:last_idx + 1])

    single_num = re.fullmatch(r"(?:ROWS?\s+)?([1-8]|1ST|2ND|3RD|4TH|5TH|6TH|7TH|8TH|FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH)(?:\s+ROWS?)?", text)
    if single_num:
        key = single_num.group(1)
        if key.isdigit():
            return [ROWS[int(key) - 1]]
        if key in _ROW_NUMBERS:
            return [_ROW_NUMBERS[key]]

    letters = [token for token in re.findall(r"\b[A-Z]\b", re.sub(r"\bROWS?\b", " ", text))]
    if letters and all(letter in ROWS for letter in letters):
        return sorted(set(letters), key=ROWS.index)

    digits = [int(token) for token in re.findall(r"\b[1-8]\b", text)]
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
    """The first row and the factors already in those plate wells."""
    if rows != list(ROWS[ROWS.index(rows[0]):ROWS.index(rows[0]) + len(rows)]):
        raise SelectionError(f"Rows {', '.join(rows)} are not next to each other. One run uses one block of consecutive "
                             "plate rows (each prints on the paper row with the same letter).",
                             question="Which consecutive rows should this run use?")
    plan = build_plan(config)
    wells = {well.row: well for well in plan.wells}
    missing = [row for row in rows if row not in wells]
    if missing:
        current = f"rows {_span([well.row for well in plan.wells])}" if plan.wells else "no rows"
        raise SelectionError(f"The current plan has no dilution in row{'s' if len(missing) > 1 else ''} "
                             f"{', '.join(missing)} (it uses {current}), so there is no factor to keep there.",
                             question=f"Which dilution factors should rows {_span(rows)} use?")
    chosen = [wells[row] for row in rows]
    why = "use the current plate wells corresponding to the selected paper rows"
    changes = [{"path": "dilution.start_row", "value": rows[0], "kind": "dependent", "why": why},
               {"path": "dilution.factors", "value": [well.factor for well in chosen], "kind": "dependent", "why": why}]
    row_range = f"{rows[0]}–{rows[-1]}" if len(rows) > 1 else f"row {rows[0]}"
    if len(rows) > 1:
        note = f"I interpreted rows {', '.join(rows)} (the first {len(rows)} rows as {row_range}) as paper destinations."
    else:
        note = f"I interpreted row {ROWS.index(rows[0]) + 1} as row {rows[0]} on the diagram."
    return changes, note


def expand_paper_columns(columns: list[int], config: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """The first paper column and the replicate count that print exactly these side-by-side columns."""
    if columns != list(range(columns[0], columns[0] + len(columns))):
        raise SelectionError(gap_message(columns), question=GAP_QUESTION)
    volumes = max(1, len(droplet_volumes(config)))
    if len(columns) % volumes:
        raise SelectionError(f"With {volumes} drop volumes, each replicate prints {volumes} side-by-side paper columns, "
                             f"so {columns_phrase(columns)} cannot be printed exactly in one run.", question=GAP_QUESTION)
    changes: list[dict[str, Any]] = []
    if not steps_enabled(config)[1]:
        changes.append({"path": "print.enabled", "value": True})
    changes += [{"path": "print.paper_start_column", "value": columns[0]},
                {"path": "print.replicates", "value": len(columns) // volumes}]
    preview = deepcopy(config)
    for change in changes:
        section, key = change["path"].split(".")
        preview.setdefault(section, {})[key] = change["value"]
    printed = paper_columns_printed(preview)
    if printed != columns:
        raise SelectionError(f"This plan would print {columns_phrase(printed)}, not {columns_phrase(columns)}.",
                             question=GAP_QUESTION)
    replicates = len(columns) // volumes
    return changes, (f"I interpreted the named paper columns as the destinations for this procedure: first paper column "
                     f"{columns[0]}, {replicates} side-by-side replicate column{'s' if replicates != 1 else ''}"
                     + (f" for each of the {volumes} drop volumes." if volumes > 1 else "."))
