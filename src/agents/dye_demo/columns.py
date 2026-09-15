"""Paper columns named in a request, checked against the paper columns the plan will physically print.

A scientist names paper columns in words: "print in paper columns 3 and 4", "starting at paper column 2",
"columns 2-5", "only paper column 6", "not paper columns 1 and 2". The model proposes settings (first paper column,
replicate columns, drop volumes) and the plan derives the columns that are really printed: one paper column per drop
volume x replicate, side by side from the first paper column. The model is never trusted to have got that right:
column_conflict() compares the derived columns with the named ones, and any difference blocks the proposal with a
question. Where the named columns determine the settings exactly, the question offers them (still a proposal the
scientist must approve).
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from src.agents.dye_demo.plan import droplet_volumes, paper_layout, steps_enabled

_NOT_A_COLUMN = r"(?!\s*(?:drops?|droplets?|µl|ul|ml|replicates?|dilutions?|tips?|times|x\b|×|%|\.\d))"
_NUMBER = rf"\d{{1,2}}\b{_NOT_A_COLUMN}"
_LIST_REST = rf"(?:\s*(?:,|and|&)\s*(?:and\s+)?{_NUMBER})+"
_RANGE_REST = rf"\s*(?:-|–|to|through|thru|until)\s*(?:(?:paper\s+)?columns?\s+)?{_NUMBER}"
_MENTION = re.compile(
    rf"\b(?:(?P<qualifier>paper|plate|tip|tips|rack|well|wells|vial|row)\s+)?columns?\s*(?:#\s*|number\s+|no\.?\s*)?"
    rf"(?P<first>{_NUMBER})(?P<rest>{_LIST_REST}|{_RANGE_REST})?", re.I)
# "the tips in column 2", "the plate at column 3": a column of something other than the paper
_OTHER_LABWARE_BEFORE = re.compile(r"\b(?:tips?|racks?|plates?|wells?|vials?|rows?)\b(?:\s+[\w'-]+){0,2}\s*$", re.I)
# "the replicate paper columns 2", "number of paper columns 3": a count of columns, not a column number
_COUNT_BEFORE = re.compile(r"\b(?:replicates?|repeats?|repeated|number\s+of|how\s+many|count\s+of|side[-\s]by[-\s]side|"
                           r"last|final)\s+$", re.I)
_AVOID_BEFORE = re.compile(
    r"\b(?:not|never|instead\s+of|rather\s+than|except(?:\s+for)?|other\s+than|besides|apart\s+from|avoid(?:ing)?|"
    r"skip(?:ping)?|leave\s+out|leaving\s+out|without|away\s+from)\s+(?:(?:in|on|onto|into|at|from|using)\s+)?"
    r"(?:the\s+)?(?:paper\s+)?$", re.I)
_ONLY_BEFORE = re.compile(r"\b(?:only|just|a\s+single)\s+(?:(?:in|on|onto|into|at)\s+)?(?:the\s+)?(?:paper\s+)?$", re.I)
_ONLY_AFTER = re.compile(r"^\s*only\b", re.I)
# "paper columns 1 and 2 are already used": a statement about those columns, not where to print
_STATEMENT_AFTER = re.compile(r"^\s*(?:(?:on|of)\s+the\s+paper\s+)?(?:are|were|is|was|have|has|had|already|got)\b", re.I)
_PAPER_SIDE = re.compile(r"\b(?:print\w*|paper|drops?|droplets?|deposit\w*|spots?)\b", re.I)
_PLATE_SIDE = re.compile(r"\b(?:dilut\w*|plate|wells?|series)\b", re.I)

GAP_QUESTION = "Which side-by-side paper columns should this run print?"
COLUMN_KINDS = {"paper_layout", "paper_columns"}


@dataclass(frozen=True)
class ColumnMention:
    columns: tuple[int, ...]
    start: int
    end: int
    role: str                 # group | only | start | avoid
    text: str


@dataclass(frozen=True)
class ColumnRequest:
    exact: tuple[tuple[int, ...], ...] = ()     # blocks of columns that must be printed exactly
    starts: tuple[int, ...] = ()                # columns printing must start at
    avoided: frozenset[int] = frozenset()       # columns that must not be printed
    mentions: tuple[ColumnMention, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.exact or self.starts or self.avoided)


@dataclass
class ColumnConflict:
    kind: str                 # paper_layout (cannot be printed by one run) | paper_columns (the plan prints others)
    message: str
    question: str = GAP_QUESTION
    fix: list[dict[str, Any]] | None = None
    wanted: tuple[int, ...] = ()
    printed: list[int] = field(default_factory=list)


def _numbers(first: str, rest: str) -> tuple[int, ...] | None:
    values = [int(first)] + [int(number) for number in re.findall(r"\d{1,2}", rest or "")]
    if rest and re.match(r"\s*(?:-|–|to|through|thru|until)", rest, re.I):
        low, high = values[0], values[-1]
        return tuple(range(low, high + 1)) if low <= high else None
    return tuple(sorted(set(values)))


def paper_column_mentions(text: str) -> list[ColumnMention]:
    """Every place the text names paper columns, with what it asks for."""
    mentions = []
    lowered = re.sub(r"(?i)\bpaper\s+(?:print\s+)?plates?\b", "paper", text)
    paper_side, plate_side = bool(_PAPER_SIDE.search(lowered)), bool(_PLATE_SIDE.search(lowered))
    for match in _MENTION.finditer(text):
        qualifier = (match.group("qualifier") or "").lower()
        if qualifier and qualifier != "paper":
            continue
        columns = _numbers(match.group("first"), match.group("rest"))
        if not columns:
            continue
        before, after = text[:match.start()], text[match.end():]
        if _STATEMENT_AFTER.match(after) or _COUNT_BEFORE.search(before):
            continue
        if not qualifier:
            if _OTHER_LABWARE_BEFORE.search(before):
                continue
            if len(columns) == 1 and not (paper_side and not plate_side):
                continue          # a bare "column 3" in a sentence that also talks about the plate is not a paper column
        if _AVOID_BEFORE.search(before):
            role = "avoid"
        elif len(columns) > 1:
            role = "group"
        elif _ONLY_BEFORE.search(before) or _ONLY_AFTER.match(after):
            role = "only"
        else:
            role = "start"
        mentions.append(ColumnMention(columns, match.start(), match.end(), role, match.group(0)))
    return mentions


def paper_column_request(text: str) -> ColumnRequest:
    mentions = paper_column_mentions(text)
    exact = tuple(dict.fromkeys(mention.columns for mention in mentions if mention.role in {"group", "only"}))
    starts = tuple(dict.fromkeys(mention.columns[0] for mention in mentions if mention.role == "start"))
    avoided = frozenset(column for mention in mentions if mention.role == "avoid" for column in mention.columns)
    return ColumnRequest(exact, starts, avoided, tuple(mentions))


def listed_paper_columns(text: str) -> list[list[int]]:
    """Each group of paper columns written out: "paper columns 3 and 4" -> [[3, 4]], "columns 2-5" -> [[2, 3, 4, 5]]."""
    return [list(mention.columns) for mention in paper_column_mentions(text) if mention.role == "group"]


def paper_columns_printed(config: dict[str, Any]) -> list[int]:
    """The paper columns this configuration's run prints on (none when printing is off), past the paper edge included."""
    try:
        _, do_print = steps_enabled(config)
        if not do_print:
            return []
        return sorted({int(spot["column"]) for spot in paper_layout(config, include_overflow=True)})
    except (TypeError, ValueError):
        return []


def format_columns(columns: list[int] | tuple[int, ...]) -> str:
    values = sorted(columns)
    if not values:
        return "none"
    if len(values) > 1 and values == list(range(values[0], values[-1] + 1)):
        return f"{values[0]}-{values[-1]}"
    return ", ".join(str(value) for value in values)


def columns_phrase(columns: list[int] | tuple[int, ...]) -> str:
    """'paper column 3', 'paper columns 3-4', 'no paper columns'."""
    if not columns:
        return "no paper columns"
    return f"paper column {columns[0]}" if len(columns) == 1 else f"paper columns {format_columns(columns)}"


def columns_words(columns: list[int] | tuple[int, ...]) -> str:
    """'paper columns 3 and 4' / 'paper columns 3, 4 and 5': the words a request would use."""
    values = [str(value) for value in columns]
    if len(values) == 1:
        return f"paper column {values[0]}"
    return f"paper columns {', '.join(values[:-1])} and {values[-1]}"


def gap_message(group: tuple[int, ...] | list[int]) -> str:
    return (f"Paper columns {', '.join(str(column) for column in group)} are not side by side. One run prints "
            "side-by-side paper columns starting at the first paper column, so leaving a gap takes two runs: print the "
            "first columns, then print again starting at the next column.")


def gap_conflict(text: str) -> ColumnConflict | None:
    """Columns that no single run can print: a gap inside one listed block, or two different blocks."""
    request = paper_column_request(text)
    for group in request.exact:
        if list(group) != list(range(group[0], group[0] + len(group))):
            return ColumnConflict("paper_layout", gap_message(group), wanted=group)
    if len(request.exact) > 1:
        named = " and ".join(columns_phrase(group) for group in request.exact)
        return ColumnConflict("paper_layout", f"You named {named}. One run prints one block of side-by-side paper "
                              "columns, so printing each of them takes a separate run.")
    return None


def column_conflict(text: str, after: dict[str, Any]) -> ColumnConflict | None:
    """The difference between the paper columns the text names and the columns `after` prints, or None."""
    request = paper_column_request(text)
    if not request:
        return None
    gap = gap_conflict(text)
    if gap is not None:
        return gap
    target = request.exact[0] if request.exact else None
    starts = request.starts
    if len(set(starts)) > 1 or (target is not None and any(start != target[0] for start in starts)):
        named = ", ".join(str(start) for start in dict.fromkeys(((target[0],) if target else ()) + starts))
        return ColumnConflict("paper_columns", f"You named more than one first paper column ({named}).")
    if target is not None and set(target) & request.avoided:
        return ColumnConflict("paper_columns", f"You asked for {columns_phrase(target)} and also to avoid "
                              f"{columns_phrase(sorted(set(target) & request.avoided))}.")

    printed = paper_columns_printed(after)
    _, prints = steps_enabled(after)
    wanted_start = target[0] if target is not None else (starts[0] if starts else None)
    asked = (f"You asked for {columns_phrase(target)}" if target is not None
             else f"You asked to start printing at paper column {wanted_start}" if wanted_start is not None
             else f"You asked me to avoid {columns_phrase(sorted(request.avoided))}")
    if wanted_start is not None and not prints:
        problem = "this plan does not print in this run"
    elif target is not None and printed != list(target):
        problem = f"this change would print {columns_phrase(printed)}"
    elif target is None and wanted_start is not None and printed and printed[0] != wanted_start:
        problem = f"this change would print {columns_phrase(printed)}"
    elif request.avoided & set(printed):
        return ColumnConflict("paper_columns", f"{asked}, but this change would print {columns_phrase(printed)}.",
                              printed=printed)
    else:
        return None
    message = f"{asked}, but {problem}."
    fix = _layout_fix(after, target, wanted_start, text, request)
    if fix is None:
        volumes = max(1, len(droplet_volumes(after)))
        if target is not None and len(target) % volumes:
            message += (f" With {volumes} drop volumes, each replicate prints {volumes} side-by-side paper columns, so "
                        f"{columns_phrase(target)} cannot be printed exactly in one run.")
        return ColumnConflict("paper_columns", message, wanted=target or (), printed=printed)
    preview = _preview(after, fix)
    shown = columns_phrase(paper_columns_printed(preview))
    settings = [f"first paper column {wanted_start}"]
    if target is not None:
        replicates = next(item["value"] for item in fix if item["path"] == "print.replicates")
        volumes = max(1, len(droplet_volumes(after)))
        settings.append(f"{replicates} side-by-side replicate column{'s' if replicates != 1 else ''}"
                        + (" for each drop volume" if volumes > 1 else ""))
    if not prints:
        settings.append("printing turned on")
    question = f"Should this run print {shown} ({', '.join(settings)})?"
    return ColumnConflict("paper_columns", message, question, fix, wanted=target or (wanted_start,), printed=printed)


def _layout_fix(after: dict[str, Any], target: tuple[int, ...] | None, start: int | None, text: str,
                request: ColumnRequest) -> list[dict[str, Any]] | None:
    """The settings that print exactly the named columns, when the words determine them."""
    if start is None:
        return None
    evidence = next((mention.text for mention in request.mentions if mention.role != "avoid"), "")
    fix: list[dict[str, Any]] = []
    _, prints = steps_enabled(after)
    if not prints:
        fix.append({"path": "print.enabled", "value": True, "kind": "requested", "evidence": evidence,
                    "why": "you asked for paper columns, and printing is off in this plan"})
    fix.append({"path": "print.paper_start_column", "value": start, "kind": "requested", "evidence": evidence,
                "why": "the first paper column you named"})
    if target is not None:
        volumes = max(1, len(droplet_volumes(after)))
        if len(target) % volumes:
            return None
        fix.append({"path": "print.replicates", "value": len(target) // volumes, "kind": "requested",
                    "evidence": evidence, "why": "the number of side-by-side paper columns you named"})
    preview = _preview(after, fix)
    columns = paper_columns_printed(preview)
    if (target is not None and columns != list(target)) or not columns or columns[0] != start \
            or request.avoided & set(columns):
        return None
    return fix


def _preview(config: dict[str, Any], fix: list[dict[str, Any]]) -> dict[str, Any]:
    preview = deepcopy(config)
    for item in fix:
        section, key = item["path"].split(".")
        preview.setdefault(section, {})[key] = item["value"]
    return preview


# ── answers to "Which side-by-side paper columns should this run print?" ───────

_ANSWER = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|so|then|yes|yeah|sure|please|just|only|use|print(?:ing)?|in|on|onto|into|at|the|paper|columns?|"
    r"start(?:ing)?|from|how\s+about|let'?s\s+do|do)[,\s]+)*"
    rf"(?P<first>{_NUMBER})(?P<rest>{_LIST_REST}|{_RANGE_REST})?"
    r"\s*(?:(?:instead|please|then|only|thanks|thank\s+you)\b[\s.,!]*)*[.!]*\s*$", re.I)


def column_answer(text: str) -> list[int] | None:
    """The paper columns a short answer names ("columns 3 and 4", "3 and 4", "3-4"), or None."""
    match = _ANSWER.match(text)
    if match:
        columns = _numbers(match.group("first"), match.group("rest"))
        return list(columns) if columns else None
    request = paper_column_request(text)
    if len(request.exact) == 1 and not request.starts:
        return list(request.exact[0])
    if not request.exact and len(request.starts) == 1:
        return [request.starts[0]]
    return None


def rewrite_paper_columns(text: str, columns: list[int]) -> str:
    """The request with the paper columns it named replaced by the answer (an avoided column list is kept)."""
    words = columns_words(columns)
    mentions = [mention for mention in paper_column_mentions(text) if mention.role != "avoid"]
    if not mentions:
        return f"{text.rstrip().rstrip('.!?')} in {words}".strip()
    pieces, cursor = [], 0
    for mention in mentions:
        pieces.append(text[cursor:mention.start])
        pieces.append(words)
        cursor = mention.end
    pieces.append(text[cursor:])
    return "".join(pieces)
