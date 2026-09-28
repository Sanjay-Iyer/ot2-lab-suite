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
from typing import Any, Iterable

from src.agents.dye_demo.columns import GAP_QUESTION
from src.agents.dye_demo.grounding import value_stated
from src.agents.dye_demo.model import (
    ROWS,
    FieldError,
    fmt_factor,
    normalize_paper_column,
    normalize_paper_rows,
    normalize_source_map,
    paper_position,
    plate_well,
)
from src.agents.dye_demo.placement import Need, PlacementError, allocate, placement_of
from src.agents.dye_demo.plan import build_plan, droplet_volumes, explicit_paper_rows, steps_enabled


class SelectionError(ValueError):
    """A selection this plan cannot print as named; `question` asks for what would make it possible.

    `fix` is a selection value a plain "yes" to the question would use instead (for example an even split of the
    prints between the named source wells)."""

    def __init__(self, message: str, question: str = "", fix: Any = None):
        super().__init__(message)
        self.question = question
        self.fix = fix


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


def selected_paper_rows(value: Any) -> list[str]:
    """Paper rows named in a selection: a list is taken item by item and a row that does not exist on the paper is
    refused (never dropped); a phrase ("rows 1 through 3", "first three rows") is read like the plate-row phrases."""
    try:
        if isinstance(value, (list, tuple)):
            rows = normalize_paper_rows(list(value))
        else:
            try:
                rows = selected_rows(value)
            except SelectionError:
                rows = normalize_paper_rows(value)
    except FieldError as exc:
        raise SelectionError(str(exc), "Which paper rows (A-H) should the samples print on?") from exc
    if not rows:
        raise SelectionError("no paper rows were named", "Which paper rows (A-H) should the samples print on?")
    return rows


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
    """The selected PLATE rows of the dilution series and their factors. Where they print is a separate setting
    (print.paper_rows / the print map, see expand_paper_rows): selecting plate rows never sets paper rows."""
    plan = build_plan(config)
    wells = {well.row: well for well in plan.wells}
    missing = [row for row in rows if row not in wells]
    if missing:
        existing_factors = [well.factor for well in plan.wells]
        if not existing_factors:
            existing_factors = [float(f) for f in (config.get("dilution") or {}).get("factors", [])]
        if len(rows) > len(existing_factors):
            # Rows pick or move the dilutions the plan has; more rows than dilutions needs factors nobody gave (the
            # series was extended with last x 2, a factor the scientist never named, marked as verified).
            extra = [row for row in rows if row not in wells][-(len(rows) - len(existing_factors)):]
            have = ", ".join(fmt_factor(factor) for factor in existing_factors) or "none"
            raise SelectionError(
                f"The plan has {len(existing_factors)} dilution{'s' if len(existing_factors) != 1 else ''} ({have}), and "
                f"{len(rows)} plate rows were named ({', '.join(rows)}).",
                f"Which dilution factor{'s' if len(extra) > 1 else ''} should plate row{'s' if len(extra) > 1 else ''} "
                f"{', '.join(extra)} hold?")
        chosen_factors = existing_factors[:len(rows)]
    else:
        chosen = [wells[row] for row in rows if row in wells]
        chosen_factors = [well.factor for well in chosen]
    why = "the dilutions in the selected plate rows"
    changes = [
        {"path": "dilution.rows", "value": rows, "kind": "dependent", "why": why},
        {"path": "dilution.start_row", "value": rows[0], "kind": "dependent", "why": why},
        {"path": "dilution.factors", "value": chosen_factors, "kind": "dependent", "why": why},
    ]
    row_phrase = ", ".join(rows) if len(rows) > 1 else f"row {rows[0]}"
    note = (f"I read rows {row_phrase} as the plate rows that hold the dilutions" if len(rows) > 1
            else f"I read row {ROWS.index(rows[0]) + 1} as plate row {rows[0]} (it holds the dilution)")
    return changes, note + "; where they print is a separate setting."


def _rows_words(rows: list[str]) -> str:
    return f"paper row {rows[0]}" if len(rows) == 1 else "paper rows " + ", ".join(rows[:-1]) + f" and {rows[-1]}"


def _moved_map(plan, new_row: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    """The print map the plan's print operations give when each paper position moves to `new_row(source, position)`
    (a paper row, or a list of paper rows for one source printed on several rows). Each source keeps its paper columns
    and its drop volume; entries follow the plan's print order."""
    volumes = droplet_volumes(config)
    default = volumes[0] if volumes else 0.0
    entries: dict[tuple[str, float], list[str]] = {}
    for operation in plan.operations:
        if operation.kind != "print":
            continue
        rows = new_row(operation.source, operation.destination)
        positions = entries.setdefault((operation.source, float(operation.volume_ul)), [])
        for row in rows if isinstance(rows, list) else [rows]:
            if f"{row}{operation.destination[1:]}" not in positions:
                positions.append(f"{row}{operation.destination[1:]}")
    return [{"source": source, "positions": positions,
             **({"volume_ul": volume} if len(volumes) > 1 or abs(volume - default) > 1e-9 else {})}
            for (source, volume), positions in entries.items()]


def expand_paper_rows(rows: list[str], config: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """The PAPER rows the printed samples land on, as settings. Only where they print changes: the plate wells (where the
    dilutions are made) stay exactly as they are, and so do the paper columns.

      a plan printing its dilution series (no print map): print.paper_rows - the i-th dilution prints on the i-th paper
        row named, top to bottom in series order ("dilutions in A11, C11, E11, print on paper rows A, B, C" is
        A11 -> A, C11 -> B, E11 -> C). One paper row per dilution.
      a plan printing from a print map: each source well keeps its paper columns and moves to its new paper row.
      one sample and several paper rows: it prints on every one of them, in its paper columns.
    """
    changes: list[dict[str, Any]] = []
    preview = deepcopy(config)
    if not steps_enabled(config)[1]:
        changes.append({"path": "print.enabled", "value": True, "kind": "dependent",
                        "why": "you asked where to print, and printing is off in this plan"})
        preview.setdefault("print", {})["enabled"] = True
    plan = build_plan(preview)
    sources = [source.well for source in plan.print_sources]
    if not sources:
        raise SelectionError("this plan has no sample to print on those paper rows",
                             "Which plate wells should print on these paper rows?")
    names = ", ".join(sources[:-1]) + f" and {sources[-1]}" if len(sources) > 1 else sources[0]
    if len(sources) > 1 and len(rows) != len(sources) and not plan.mapped \
            and explicit_paper_rows(preview) is None and set(rows) < set(plan.rows):
        # Each dilution prints on the row with its own letter, so naming SOME of those rows ("print rows A, C and F" of
        # an eight-row plan) can only pick which dilutions print: exactly the plate-row selection (one paper row per
        # sample could not be laid out otherwise).
        picked, note = expand_rows(rows, config)
        return changes + picked, note
    if len(sources) > 1 and len(rows) != len(sources):
        raise SelectionError(
            f"This plan prints {len(sources)} samples ({names}), one paper row each, and you named "
            f"{len(rows)} paper row{'s' if len(rows) != 1 else ''} ({', '.join(rows)}).",
            f"Which {len(sources)} paper rows should {names} print on (one row each)?")
    if len(sources) > 1:
        for source in plan.print_sources:
            spread = sorted({position[0] for position in source.positions}, key=ROWS.index)
            if len(spread) > 1:
                raise SelectionError(f"{source.well} prints on paper rows {', '.join(spread)}, so it is not clear which "
                                     "of its rows should move.", f"Which paper positions should {source.well} print on?")
    pairs = ", ".join(f"{source} → row {row}" for source, row in zip(sources, rows)) if len(sources) > 1 \
        else f"{sources[0]} → {_rows_words(rows)}"
    note = f"I read {_rows_words(rows)} as where the samples print ({pairs}); the plate wells stay as they are."
    if not plan.mapped and (len(sources) > 1 or len(rows) == 1):
        changes.append({"path": "print.paper_rows", "value": list(rows)})
        return changes, note
    target = dict(zip(sources, rows)) if len(sources) > 1 else {sources[0]: list(rows)}
    try:
        moved = normalize_source_map(_moved_map(plan, lambda source, _position: target[source], preview))
    except FieldError as exc:
        raise SelectionError(str(exc), "Which paper positions should each sample print on?") from exc
    changes.append({"path": "print.source_map", "value": moved})
    return changes, note


def expand_paper_columns(columns: list[int], config: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """The paper columns as settings: side-by-side columns as the first paper column and a replicate count; columns
    with gaps, or a plan that already prints from a print map, as an explicit print map (each source keeps its paper
    rows and prints in exactly these columns)."""
    volumes = max(1, len(droplet_volumes(config)))
    changes: list[dict[str, Any]] = []
    if not steps_enabled(config)[1]:
        changes.append({"path": "print.enabled", "value": True})
    col_phrase = f"columns {', '.join(map(str, columns))}" if len(columns) > 1 else f"column {columns[0]}"
    note = f"I interpreted the named paper columns as destinations for this procedure: {col_phrase}."
    side_by_side = columns == list(range(columns[0], columns[0] + len(columns)))
    plan = build_plan(config)
    if plan.mapped or (not side_by_side and volumes == 1):
        if plan.mapped:
            rows_of = {source.well: sorted({position[0] for position in source.positions}, key=ROWS.index)
                       for source in plan.print_sources}
        else:
            # each dilution keeps the paper row it prints on (print.paper_rows, or its own letter)
            destination = dict(zip(plan.rows, plan.paper_rows))
            rows_of = {well.well: [destination.get(well.row, well.row)] for well in plan.wells}
        if not rows_of:
            raise SelectionError("there is no source well to print from", "Which plate well should print on these "
                                                                           "paper columns?")
        try:
            mapped = normalize_source_map([
                {"source": well, "positions": [f"{row}{column}" for row in rows for column in columns]}
                for well, rows in rows_of.items()])
        except FieldError as exc:
            raise SelectionError(str(exc)) from exc
        changes.append({"path": "print.source_map", "value": mapped})
        return changes, note
    if not side_by_side:
        raise SelectionError(f"With {volumes} drop volumes each replicate prints {volumes} side-by-side paper columns, "
                             f"so {col_phrase} cannot be printed exactly.", GAP_QUESTION)
    replicates = max(1, len(columns) // volumes)
    changes += [{"path": "print.paper_start_column", "value": columns[0]},
                {"path": "print.replicates", "value": replicates}]
    return changes, note


# ── print maps: which plate well prints on which paper positions ────────────────

_ALL_WORDS = {"all", "every", "everywhere", "each", "all current", "current", "same", "all prints", "all positions"}


def _as_items(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [part for part in re.split(r"\s*(?:,|;|&|\band\b)\s*|\s+", str(value).strip()) if part]


def _map_entries(value: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(entries, group) from the router's print_map value: a list of {source, ...} entries, one entry, or a group
    {"sources": [...], "total": N} that names sources and a total without saying how to divide it."""
    if isinstance(value, dict) and ("sources" in value or "total" in value):
        return [], value
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], dict) \
            and ("sources" in value[0] or "total" in value[0]):
        return [], value[0]            # the same group written as a one-item list (2026-09-27 J01)
    if isinstance(value, dict):
        return [value], {}
    if isinstance(value, (list, tuple)):
        return [item if isinstance(item, dict) else {"source": item} for item in value], {}
    return [{"source": value}], {}


def _source_of(entry: dict[str, Any]) -> str:
    raw = next((entry[key] for key in ("source", "well", "from", "source_well") if entry.get(key) not in (None, "")),
               None)
    if raw is None:
        raise SelectionError("a print-map entry does not name its plate well", "Which plate well holds the sample to "
                                                                               "print?")
    try:
        return plate_well(raw)
    except FieldError as exc:
        raise SelectionError(str(exc)) from exc


def _even_split(sources: list[str], total: int) -> list[dict[str, Any]] | None:
    if not sources or total < len(sources) or total % len(sources):
        return None
    return [{"source": source, "count": total // len(sources)} for source in sources]


def _split_question(sources: list[str], total: int) -> SelectionError:
    names = ", ".join(sources[:-1]) + f" and {sources[-1]}" if len(sources) > 1 else sources[0]
    fix = _even_split(sources, total)
    message = f"You named {len(sources)} source wells ({names}) and {total} prints, but not how to divide them."
    if fix is not None:
        question = (f"You have {len(sources)} source wells and asked for {total} prints. Should I print "
                    f"{total // len(sources)} from each of {names} (each on its own paper row)?")
    else:
        question = f"How should the {total} prints be divided between {names}?"
    return SelectionError(message, question, fix)


def unstated_split(value: Any, words: str) -> SelectionError | None:
    """Prints divided between several wells in counts the scientist's words never give ("I have samples in A11 and B11.
    Make 10 spots." read as 5 each): the same question as a total with no split, which the router is told to leave to
    Python. None when every count is stated, or the map is not a split by counts."""
    entries = value if isinstance(value, list) else []
    counted = [entry for entry in entries if isinstance(entry, dict) and entry.get("count") not in (None, "")
               and entry.get("positions") in (None, "", []) and entry.get("columns") in (None, "", [])]
    if len(entries) < 2 or len(counted) != len(entries):
        return None
    try:
        counts = [int(float(entry["count"])) for entry in counted]
        sources = [_source_of(entry) for entry in counted]
    except (TypeError, ValueError, FieldError, SelectionError):
        return None
    if all(value_stated(count, words) for count in counts):
        return None
    return _split_question(sources, sum(counts))


def _home_rows(plan) -> dict[str, str]:
    """{plate well: the paper row it prints on} for a plan printing its dilution series without a print map
    (print.paper_rows, or each dilution's own letter)."""
    if plan.mapped:
        return {}
    return {f"{row}{plan.plate_column}": paper_row for row, paper_row in zip(plan.rows, plan.paper_rows)}


def _allocate(source: str, count: int, taken: set[str], reserved_rows: set[str], anchor: int, width: int,
              own: str, entry: dict[str, Any]) -> list[str]:
    """`count` free paper positions for one source (placement.allocate): its own paper row nearest the column it prints
    on now (else the first paper column), then the nearest free rows; within the entry's paper rows / columns and its
    placement wish ("adjacent", "same_row", "same_column") when it gives them. Deterministic, so the same words give
    the same map."""
    try:
        rows = tuple(normalize_paper_rows(entry["rows"])) if entry.get("rows") not in (None, "", []) else ()
        columns = tuple(normalize_paper_column(item) for item in _as_items(entry.get("columns")))
        need = Need(source, count, home_row=own, rows=rows, columns=columns,
                    placement=placement_of(entry.get("placement")), anchor_column=columns[0] if columns else anchor)
        return allocate([need], taken, width=width, reserved_rows=reserved_rows)[0]
    except FieldError as exc:
        raise SelectionError(str(exc)) from exc
    except PlacementError as exc:
        raise SelectionError(str(exc), "Which paper positions should it print on?") from exc


def expand_print_map(value: Any, config: dict[str, Any], *,
                     occupied: Iterable[str] = ()) -> tuple[list[dict[str, Any]], str]:
    """A print-map selection as field changes.

    Entry forms the router uses (all resolved here, never by the model):
      {"source": "A11", "positions": ["A1", "B1"]}       exactly these paper positions, in this order
      {"source": "A11", "positions": "all"}              every position the plan prints now ("use this for all prints")
      {"source": "A11", "count": 10}                     10 prints on free positions: along the source's own paper row
                                                         nearest the column it prints on now (else the first paper
                                                         column), then the nearest free rows (placement.py)
      {"source": "A11", "count": 3, "columns": [5]}      3 prints within those paper columns (Python picks the rows)
      {"source": "A11", "count": 3, "rows": ["B"]}       3 prints within those paper rows (Python picks the columns)
      {"source": "A11", "count": 3, "placement": "adjacent"}  3 side-by-side prints (also "same_row", "same_column")
      {"source": "A11", "columns": [1, 2, 3]}            its own paper row in those columns (with "rows": those rows)
      {"source": "A11", "rows": ["A", "B"], "columns": [3]}  those paper rows in those columns (A3, B3)
      {"sources": ["A11", "B11"], "total": 10}           a total without a split: a question, never a guess
    A source's "own paper row" is the paper row it prints on in the current plan (print.paper_rows), else its letter.
    `occupied` are paper positions no counted print may use (printed on by an earlier live run of the session).
    """
    entries, group = _map_entries(value)
    printing = config.get("print") or {}
    start = int(printing.get("paper_start_column", 1) or 1)
    width = int(printing.get("paper_columns", 12) or 12)
    plan = build_plan(config)
    current = plan.print_positions if plan.do_print else []
    home = _home_rows(plan)
    if group:
        sources = [_source_of({"source": item}) for item in _as_items(group.get("sources"))]
        try:
            total = int(float(group.get("total")))
        except (TypeError, ValueError):
            total = 0
        if not sources:
            raise SelectionError("the print map names no source well", "Which plate well holds the sample to print?")
        if len(sources) == 1 and total > 0:
            entries = [{"source": sources[0], "count": total}]
        else:
            raise _split_question(sources, total or len(current))
    kinds = []
    for entry in entries:
        spec = "positions" if entry.get("positions") not in (None, "", []) else \
            "count" if entry.get("count") not in (None, "") else \
            "grid" if entry.get("columns") not in (None, "", []) or entry.get("rows") not in (None, "", []) else "none"
        if spec == "positions" and str(entry["positions"]).strip().lower() in _ALL_WORDS:
            spec = "all"
        kinds.append(spec)
    sources = [_source_of(entry) for entry in entries]
    if not sources:
        raise SelectionError("the print map names no source well", "Which plate well holds the sample to print?")
    if len(entries) == 1 and kinds[0] == "none":
        kinds = ["all"]                         # "use A11 for printing": everything the plan prints now
    if kinds.count("all") > 1 or ("all" in kinds and len(entries) > 1) or \
            (len(entries) > 1 and "none" in kinds):
        raise _split_question(sources, len(current))
    taken: set[str] = set(occupied)             # counted prints never go where an earlier live run printed
    resolved: dict[int, list[str]] = {}
    for index, (entry, kind, source) in enumerate(zip(entries, kinds, sources)):
        try:
            if kind == "all":
                if not current:
                    raise SelectionError("this plan prints nothing yet, so 'all prints' names no paper positions",
                                         f"How many prints should {source} make?")
                resolved[index] = list(current)
            elif kind == "positions":
                resolved[index] = [paper_position(item) for item in _as_items(entry["positions"])]
            elif kind == "grid":
                rows = (normalize_paper_rows(entry["rows"]) if entry.get("rows") not in (None, "", [])
                        else [home.get(source, source[0])])
                columns = [normalize_paper_column(item) for item in _as_items(entry.get("columns"))] or [start]
                resolved[index] = [paper_position(f"{row}{column}") for row in rows for column in columns]
        except FieldError as exc:
            raise SelectionError(str(exc)) from exc
        taken.update(resolved.get(index, []))
    # where each source prints now: its new prints start on that row, next to that column
    now = {item.well: item.positions[0] for item in plan.print_sources if item.positions} if plan.do_print else {}
    reserved = {now[source][0] if source in now else home.get(source, source[0]) for source in sources}
    for index, (entry, kind, source) in enumerate(zip(entries, kinds, sources)):
        if kind != "count":
            continue
        try:
            count = int(float(entry["count"]))
        except (TypeError, ValueError) as exc:
            raise SelectionError(f"{entry['count']!r} is not a number of prints") from exc
        if count < 1:
            raise SelectionError(f"{source} would print {count} times; the number of prints must be at least 1")
        own = now[source][0] if source in now else home.get(source, source[0])
        anchor = int(now[source][1:]) if source in now else start
        resolved[index] = _allocate(source, count, taken, reserved - {own}, anchor, width, own, entry)
        taken.update(resolved[index])
    try:
        mapped = normalize_source_map([
            {"source": source, "positions": resolved[index],
             **({"volume_ul": entry["volume_ul"]} if entry.get("volume_ul") not in (None, "") else {})}
            for index, (entry, source) in enumerate(zip(entries, sources))])
    except FieldError as exc:
        raise SelectionError(str(exc)) from exc
    changes: list[dict[str, Any]] = []
    if not steps_enabled(config)[1]:
        changes.append({"path": "print.enabled", "value": True, "kind": "dependent",
                        "why": "you asked to print from these wells"})
    changes.append({"path": "print.source_map", "value": mapped})
    summary = "; ".join(f"{entry['source']} on {len(entry['positions'])} paper position"
                        f"{'s' if len(entry['positions']) != 1 else ''}" for entry in mapped)
    return changes, f"I mapped the prints as: {summary}."
