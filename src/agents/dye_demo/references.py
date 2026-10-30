"""Logical liquid references: WHAT a print step prints, independent of WHERE that liquid physically is.

    "dilution:2"   the 2nd output of the dilution step, in series order. Its plate well is not stored: it is the well
                   the series puts that dilution in when the plan is compiled, so moving the dilutions ("put them in
                   column 10", "rows C-E") moves every print of it without rewriting anything downstream.
    "A11"          the liquid already in plate well A11 - a sample the scientist has, not made by this run.

The editable PlanSpec (the session's config) stores references in print.source and in the sources of a dictated
print.source_map. plan.build_plan and the compiler (compiler.py) resolve them to plate wells; nothing else needs to
know where a dilution happens to be. Nothing here reads the scientist's words: the router writes the reference.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.agents.dye_demo.model import ROWS, FieldError, plate_well

DILUTION = "dilution"
WELL = "well"
_DILUTION_REF = re.compile(r"(?:dilution|dil|output)s?\s*(?:[:#_\-]\s*|\s+|)(?:no\.?\s*)?(\d{1,2})", re.I)


@dataclass(frozen=True)
class LiquidRef:
    kind: str               # DILUTION (an output of the dilution step) or WELL (liquid already in a plate well)
    index: int = 0          # DILUTION: the output, 1-based in series order
    well: str = ""          # WELL: the plate well

    def __str__(self) -> str:
        return f"{DILUTION}:{self.index}" if self.kind == DILUTION else self.well


@dataclass(frozen=True)
class ResolvedSource:
    """A reference resolved against one plan: the plate well it prints from now."""

    ref: LiquidRef
    well: str
    factor: float           # the dilution factor when known (0.0: a sample the scientist has)
    made_here: bool         # this run makes the liquid in that well


class UnresolvedRef(ValueError):
    """A reference the plan cannot resolve (dilution 4 of a 3-dilution series): a run requirement, not a bad value."""


def parse_ref(value: Any) -> LiquidRef:
    """"dilution:2", "dilution 2", {"dilution": 2} and a bare 2 name the 2nd dilution; "A11" (any well spelling) names
    the liquid in that plate well. Anything else is refused, never guessed."""
    if isinstance(value, LiquidRef):
        return value
    if isinstance(value, dict):
        index = next((value[key] for key in ("dilution", "output", "index") if value.get(key) not in (None, "")), None)
        value = f"{DILUTION}:{index}" if index is not None else value.get("well") or value.get("source")
    if isinstance(value, bool) or value in (None, ""):
        raise FieldError(f"a print source is a dilution (dilution:1) or a plate well (A11), got {value!r}")
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        value = f"{DILUTION}:{int(value)}"
    text = str(value).strip()
    match = _DILUTION_REF.fullmatch(text)
    if match:
        index = int(match.group(1))
        if not 1 <= index <= len(ROWS):
            raise FieldError(f"a dilution series has outputs 1-{len(ROWS)}, got {value!r}")
        return LiquidRef(DILUTION, index=index)
    try:
        return LiquidRef(WELL, well=plate_well(text))
    except FieldError:
        raise FieldError(f"a print source is a dilution (dilution:1) or a plate well (A11), got {value!r}") from None


def is_ref_text(value: Any) -> bool:
    """A dilution reference (not a plate well)."""
    try:
        return parse_ref(value).kind == DILUTION
    except FieldError:
        return False


# ── print intents: what one source should print, as far as the scientist has said ─────────────────────────────────
#
# An item of print.source is a reference ("dilution:1", "A11") or a PrintIntent: that reference with any of
#     count      how many times it prints (per drop volume); placed by the compiler on free positions
#     rows       the paper rows it prints on (one print per row and spot column)
#     columns    the paper columns its prints use
#     positions  exact paper positions the scientist chose
#     drops      drops on each of its positions
# Every field is optional: an intent holds what was said and nothing more, and the plan-wide settings
# (print.replicates, print.paper_rows, print.columns, print.droplets_per_spot) apply where it is silent. A later edit
# of one source supersedes the older placement of THAT source only (patches.merge_intent); the compiler turns the
# intents into exact positions (plan.semantic_prints).
INTENT_FIELDS = ("count", "rows", "columns", "positions", "drops")
_INTENT_ALIASES = {"times": "count", "prints": "count", "replicates": "count", "copies": "count", "spots": "count",
                   "paper_rows": "rows", "paper_columns": "columns", "exact_positions": "positions",
                   "destinations": "positions", "drops_per_spot": "drops", "droplets": "drops",
                   "droplets_per_spot": "drops", "drops_per_position": "drops", "ref": "source", "well": "source"}


def intent_parts(item: Any) -> tuple[str, dict[str, Any]]:
    """(the item's reference, its intent fields) for a normalized print.source item."""
    if isinstance(item, dict):
        return str(item["source"]), {key: value for key, value in item.items() if key != "source"}
    return str(item), {}


def normalize_intent(item: Any) -> str | dict[str, Any]:
    """One print.source item, canonical: the reference alone, or {"source": ref, ...the fields given}."""
    from src.agents.dye_demo.limits import MAX_REPLICATES
    from src.agents.dye_demo.model import (normalize_drops, normalize_paper_column_list, normalize_paper_rows,
                                           positions_with_drops)

    if not isinstance(item, dict):
        return str(parse_ref(item))
    data = {_INTENT_ALIASES.get(str(key).strip().lower(), str(key).strip().lower()): value
            for key, value in item.items()}
    if data.get("source") in (None, "") and data.get("dilution") not in (None, ""):
        data["source"] = {"dilution": data.pop("dilution")}
    data.pop("dilution", None)
    if data.get("source") in (None, ""):
        raise FieldError("a print intent names its source (a dilution such as dilution:1, or a plate well)")
    intent: dict[str, Any] = {"source": str(parse_ref(data.pop("source")))}
    unknown = sorted(key for key, value in data.items() if key not in INTENT_FIELDS and value not in (None, ""))
    if unknown:
        raise FieldError(f"a print intent has a source and any of {', '.join(INTENT_FIELDS)}; "
                         f"{', '.join(unknown)} is not one of them")
    if data.get("count") not in (None, ""):
        count = data["count"]
        if isinstance(count, bool) or not str(count).strip().isdigit() or not 1 <= int(count) <= MAX_REPLICATES:
            raise FieldError(f"a print count is a whole number 1-{MAX_REPLICATES}, got {count!r}")
        intent["count"] = int(count)
    rows = normalize_paper_rows(data.get("rows")) if data.get("rows") not in (None, "", []) else None
    if rows:
        intent["rows"] = rows
    columns = normalize_paper_column_list(data.get("columns")) if data.get("columns") not in (None, "", []) else None
    if columns:
        intent["columns"] = columns
    if data.get("positions") not in (None, "", [], {}):
        positions, counts = positions_with_drops(data["positions"])
        if counts:
            raise FieldError("drops on particular positions are print.drops_at; positions lists only where it prints")
        if len(positions) != len(set(positions)):
            raise FieldError("a paper position is listed twice for one source")
        intent["positions"] = positions
    if data.get("drops") not in (None, ""):
        intent["drops"] = normalize_drops(data["drops"])
    return intent["source"] if len(intent) == 1 else intent


def normalize_sources(value: Any) -> list[str | dict[str, Any]] | None:
    """print.source: what prints, in print order - references, or print intents carrying a source's own count, rows,
    columns, exact positions or drops. None (or "all"/"series") is the default: every dilution of the series prints.
    One liquid may appear in several print groups (each with its own intent). Hard invariant only: no paper position
    given to two print groups exactly."""
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "none", "null", "all", "series",
                                                                              "default", "all dilutions"}):
        return None
    items = list(value) if isinstance(value, (list, tuple)) else [value]
    intents = [normalize_intent(item) for item in items]
    if not intents:
        return None
    taken: dict[str, str] = {}
    for intent in intents:
        ref, fields = intent_parts(intent)
        for position in fields.get("positions", []):
            if position in taken:
                raise FieldError(f"paper position {position} is given to both {taken[position]} and {ref}; each paper "
                                 "position takes one sample")
            taken[position] = ref
    return intents


def dilution_outputs(config: dict[str, Any]) -> list[ResolvedSource]:
    """The outputs of the dilution step, in series order: output i is the i-th plate row of the series, in the plan's
    plate column, with the i-th factor (0.0 past the factors: a row that only holds liquid already)."""
    from src.agents.dye_demo.plan import dilution_rows, factors_of, steps_enabled

    do_dilution = steps_enabled(config)[0]
    rows = dilution_rows(config)
    factors = factors_of(config)
    column = str((config.get("dilution") or {}).get("plate_column", ""))
    return [ResolvedSource(LiquidRef(DILUTION, index=index), f"{row}{column}",
                           factors[index - 1] if index <= len(factors) else 0.0,
                           do_dilution and index <= len(factors))
            for index, row in enumerate(rows, start=1)]


def resolve(ref: Any, config: dict[str, Any]) -> ResolvedSource:
    """The plate well a reference prints from in this plan. A plate well that this run makes a dilution in is that
    dilution (its factor is known); any other well holds a sample the scientist has."""
    ref = parse_ref(ref)
    outputs = dilution_outputs(config)
    if ref.kind == DILUTION:
        if ref.index > len(outputs):
            count = len(outputs)
            raise UnresolvedRef(f"the print source dilution {ref.index} does not exist: the plan has {count} "
                                f"dilution{'s' if count != 1 else ''}")
        return outputs[ref.index - 1]
    made = next((output for output in outputs if output.well == ref.well and output.made_here), None)
    if made is not None:
        return ResolvedSource(ref, ref.well, made.factor, True)
    return ResolvedSource(ref, ref.well, 0.0, False)


def describe_intent(item: Any, config: dict[str, Any]) -> str:
    """'dilution 1 (2×, now in A10): 3 prints, 2 drops each' - a print intent as said, nothing placed yet."""
    ref, fields = intent_parts(item)
    said = []
    if "positions" in fields:
        said.append("exactly at " + ", ".join(fields["positions"]))
    if "count" in fields:
        said.append(f"{fields['count']} print{'s' if fields['count'] != 1 else ''}")
    if "rows" in fields:
        said.append(f"paper row{'s' if len(fields['rows']) > 1 else ''} {', '.join(fields['rows'])}")
    if "columns" in fields:
        said.append(f"paper column{'s' if len(fields['columns']) > 1 else ''} "
                    f"{', '.join(str(column) for column in fields['columns'])}")
    if "drops" in fields:
        said.append(f"{fields['drops']} drop{'s' if fields['drops'] != 1 else ''} each")
    return describe(ref, config) + (": " + ", ".join(said) if said else "")


def describe(ref: Any, config: dict[str, Any]) -> str:
    """'dilution 2 (4×, now B11)' or 'plate well A11 (sample already in the plate)' for the router and the plan view."""
    ref = parse_ref(ref)
    try:
        source = resolve(ref, config)
    except UnresolvedRef as exc:
        return f"{ref} ({exc})"
    if ref.kind == DILUTION:
        factor = f"{source.factor:g}×, " if source.factor > 0 else ""
        return f"dilution {ref.index} ({factor}now in {source.well})"
    return f"plate well {ref.well}" + (" (made by this run)" if source.made_here else " (sample already in the plate)")
