"""The one authoritative experiment state for a dye demo session.

Nothing writes to the configuration except ExperimentState.apply(), and apply()
only accepts a Proposal that was built against the current revision. The LLM
never supplies a whole configuration: it proposes individual field changes (or a
relative operation such as "scale by 0.5" that deterministic code computes), each
checked against the allowlist, normalised, compared with the current value,
checked against the scientist's own words, validated as a whole plan, shown to the
scientist, and applied only after an explicit yes.

Deterministic rules enforced here, whatever the LLM returns:
  * a deck slot, tip, vial, volume or count the scientist did not state is refused
    with a question, never guessed; a value (or step direction) found only in their earlier
    messages in this conversation is accepted as carried over and shown for checking;
  * a volume needs its unit, and a stated unit must agree with the proposed µL;
  * a value the scientist corrected away ("slot 8 - sorry, slot 6") cannot be used;
  * a claimed current value that is wrong ("from 2 drops to 3" when it is 1) stops the change;
  * skipping the dilution step needs a record that the dilutions physically exist;
  * a print or dilution step is switched on or off only when the words ask for exactly that
    ("the paper print plate" is labware, not a request about printing);
  * row and paper-column selections become fields by exact arithmetic from the plan (natural.py);
  * paper columns the scientist names must be exactly the paper columns the plan will print;
  * every applied revision is snapshotted, so rollback uses stored values only.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from src.agents.dye_demo.columns import (  # noqa: F401 - listed_paper_columns is imported from here by callers
    GAP_QUESTION,
    column_conflict,
    columns_phrase,
    columns_words,
    gap_conflict,
    listed_paper_columns,
    paper_column_request,
    paper_columns_printed,
)
from src.agents.dye_demo.grounding import (
    column_side_unclear,
    labware_cues,
    quoted_in,
    referent,
    slot_value_supported,
    value_stated,
)
from src.agents.dye_demo.language import (
    TEMPORAL_FUTURE,
    conflicting_well_reference,
    evidence_supported,
    numbers_in,
    numbers_mentioned,
    well_tokens,
)
from src.agents.dye_demo.model import (
    CARRIED_OVER,
    EARLIER_REVISION,
    EDITABLE_FIELDS,
    LABWARE_NAMES,
    ROWS,
    TIP_ORDER,
    FieldError,
    canonicalize_path,
    field_label,
    fmt_factor,
    fmt_num,
    get_path,
    is_off_deck,
    lab_owned_reason,
    material_label,
    normalize_slot,
    normalize_source_map,
    positions_text,
    positions_with_drops,
    resolve_path,
    set_path,
    slot_of,
    volume_in_microlitres,
)
from src.agents.dye_demo.natural import (
    SelectionError,
    expand_drops_at,
    expand_paper_columns,
    expand_paper_rows,
    expand_print_map,
    expand_rows,
    selected_columns,
    selected_paper_rows,
    selected_rows,
    unstated_split,
)
from src.agents.dye_demo.placement import PlacementError, describe_new, map_at_anchor, map_with_count, series_as_map
from src.agents.dye_demo.plan import (
    build_plan,
    explicit_paper_rows,
    factors_of,
    paper_layout,
    print_map,
    row_factors,
    steps_enabled,
)
from src.agents.dye_demo.validation import (
    DeckConflict,
    Report,
    deck_conflicts,
    free_slots,
    recorded_liquid_errors,
    validate,
)

_SLOT_PATHS = {"deck.plate.slot", "deck.paper.slot", "deck.tuberack.slot", "deck.tiprack.slot"}
_WELL_PATHS = {"tips.start_tip", "materials.sample.vial", "materials.solvent.vial"}
_NUMBERED_VIAL = re.compile(r"\bvial\s*(?:number\s+|no\.?\s*|#\s*)?\d{1,2}\b(?!\s*(?:µl|ul|ml|%))", re.I)
_VOLUME_PATHS = {"print.droplet_volume_ul", "dilution.total_volume_ul", "dilution.prepared_volume_ul",
                 "mixing.volume_ul"}
_COUNT_PATHS = {"print.droplets_per_spot", "print.replicates", "mixing.reps", "print.paper_start_column",
                "dilution.plate_column"}
_NULLABLE = {"dilution.prepared_volume_ul", "materials.sample.label", "materials.solvent.label", "dilution.rows",
             "print.source_map", "print.paper_rows"}
# Physical record of plate wells the scientist says already hold what they want to print (one confirmation: applying
# the proposal that uses them records them, and later proposals do not ask again).
SOURCES_PRESENT = "sources_present"
# Physical record of the tips live runs of this session picked up: they are no longer in the rack as fresh tips. A run
# that would pick one again is refused until the starting tip moves past them or a fresh rack is reported.
TIPS_USED = "tips_used"
# Physical record of the liquid live runs left in the plate wells they made or printed from: {well: {"volume_ul",
# "revision", "run"}}. Checked before the next run prints from those wells; a volume the scientist states later wins.
WELL_VOLUMES = "well_volumes"
# Physical record placeholder: "the dilutions of the plan this proposal produces are already in the plate".
PREPARED_FROM_PLAN = "prepared dilutions of the resulting plan"
ASSUMED_FROM_PLAN = "assumed existing samples of the resulting plan"
_QUANTITY = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?|\.\d+)\s*(µL|uL|µl|ul|microlit\w*|mL|ml|millilit\w*)?(?![\w])", re.I)
# Words that show the scientist was talking about a field at all. A number that happens to
# appear in the request ("slot 4") is not evidence for a different field ("4 drops").
_FIELD_HINTS = {
    "print.droplets_per_spot": r"\bdrops?\b|\bdroplets?\b|\bstack|\bspots?\b|\bpositions?\b|\btwice\b|\bthrice\b|\btimes\b",
    "print.replicates": r"\breplicates?\b|\brepeats?\b|\brepeated\b|\bcopies\b|\bside\s+by\s+side\b|\btimes\b|\bcolumns\b|"
                        r"\bcolumn\s+\d{1,2}\s*(?:,|and|&|-|–|to|through|thru)\s*\d",
    "print.paper_start_column": r"\bcolumns?\b|\bpaper\b",
    "print.droplet_volume_ul": r"\bdrops?\b|\bdroplets?\b|\bprint",
    "dilution.plate_column": r"\bcolumns?\b",
    "dilution.total_volume_ul": r"\btotal\b|\beach\b|\bper\b|\bdilutions?\b|\bwells?\b|\bvolume\b",
    "dilution.prepared_volume_ul": r"\bprepared\b|\balready\b|\bin\s+(?:each|the)\s+wells?\b|\bvolume\b|\bholds?\b|"
                                   r"\bleft\b|\bremain\w*|\bcontains?\b|\bnow\s+ha(?:s|ve)\b",
    "dilution.start_row": r"\brows?\b|\bstart",
    "dilution.factors": r"\bdilut|\bfactors?\b|\bfold\b|\d\s*[x×]|[x×]\s*\d|\bseries\b|\btimes\b",
    "mixing.reps": r"\bmix",
    "mixing.volume_ul": r"\bmix",
    "tips.start_tip": r"\btips?\b",
    "tips.return_tips": r"\breturn|\btrash|\bdiscard|\bthrow|\bdrop\s+(?:the\s+)?(?:used\s+)?tips|\breus|\bput\s+(?:the\s+)?"
                        r"(?:used\s+)?tips\s+back",
    "tips.policy": r"\btips?\b|\bpolicy\b|\bfresh\b|\bnew\b|\breus|\bsame\b|\bchange\b|\bcontaminat",
    "materials.sample.vial": r"\bdye\b|\bsample\b|\bstock\b|\bcv\b|\bviolet\b",
    "materials.solvent.vial": r"\bwater\b|\bsolvent\b|\bdiluent\b|\bbuffer\b",
    "materials.sample.label": r"\bdye\b|\bsample\b|\bstock\b|\bname|\bcall|\blabel|\bcv\b|\bviolet\b|\bfluorescein|\brhodamine",
    "materials.solvent.label": r"\bwater\b|\bsolvent\b|\bdiluent\b|\bbuffer\b|\bname|\bcall|\blabel|\bethanol|\bpbs\b",
    "dilution.enabled": r"\bdilut|\bskip|\bprint(?:s|ing)?\s+only\b|\b(?:only|just)\s+print(?:s|ing)?\b|\balready\b|\bstep\b|\bmake\b|\bprepare",
    "print.enabled": r"\bprint(?!\s+plates?\b)|\bskip|\bdilut(?:e|ions?)\s+only\b|\b(?:only|just)\s+(?:make|do|dilute|prepare|dilutions?)\b|\bstep\b",
}

# Switching a whole step on or off must be what the words ask for, in that direction. The name of the labware
# ("the paper print plate") and a temporal clause ("once the dilutions are made") never count.
_NOT_LABWARE = r"(?!\s+(?:plates?|positions?|columns?|rows?|spots?|heights?|volumes?)\b)"
_STEP_WORDING = {
    "print.enabled": (
        re.compile(rf"\bprint(?:s|ed|ing)?\b{_NOT_LABWARE}", re.I),
        re.compile(
            rf"\b(?:skip(?:ping)?|no|without|omit(?:ting)?|disable|turn(?:ing)?\s+off|leave\s+out|stop|don'?t|do\s+not|"
            rf"not|never|cancel)\s+(?:the\s+|any\s+|all\s+(?:the\s+)?)?print(?:s|ing)?\b{_NOT_LABWARE}|"
            rf"\bprint(?:ing)?\s+(?:step\s+)?(?:is\s+)?(?:off|disabled|skipped)\b|"
            rf"\b(?:dilut(?:e|ions?)|dilution\s+step)\s+only\b|\b(?:only|just)\s+(?:make|do|dilute|prepare|dilutions?)\b", re.I)),
    "dilution.enabled": (
        re.compile(
            r"\b(?:just|only)\s+(?:the\s+)?dilutions?\b|\bdilutions?\s+only\b|\b(?:make|making|prepare|preparing|redo|remake|repeat)\b[^.;!?]{0,60}?\bdilut\w*|\bdilute\b|"
            r"\bdo\s+(?:the\s+)?dilutions?\b|\b(?:turn|switch|put)\s+(?:the\s+)?dilution(?:s|\s+step)?\s+(?:back\s+)?on\b|"
            r"\b(?:enable|include|add)\s+(?:the\s+)?dilution|\bdilution\s+step\s+(?:back\s+)?on\b", re.I),
        re.compile(
            r"\b(?:don'?t|do\s+not|dont|no\s+need\s+to)\s+dilute\b|"
            r"\b(?:skip(?:ping)?|no|without|omit(?:ting)?|disable|turn(?:ing)?\s+off|leave\s+out|(?:don'?t|do\s+not|not|no\s+need\s+to)\s+"
            r"(?:make|do|prepare|redo))\s+(?:making\s+|preparing\s+)?(?:the\s+|any\s+|all\s+(?:the\s+)?|new\s+)?dilut\w*|"
            r"\bdilution\s+(?:step\s+)?(?:is\s+)?(?:off|disabled|skipped)\b|\bprint(?:s|ing)?\s+only\b|\b(?:only|just)\s+print(?:s|ing)?\b|"
            r"\balready\s+(?:been\s+)?(?:made|prepared|done|mixed|diluted)\b|\b(?:made|prepared|done)\s+already\b|"
            r"\bdilutions?(?:\s+(?:from|in|of|for|at|on)\b(?:\s+[\w-]+){1,4})?\s+(?:are|were|is|was|have\s+been|has\s+been)\s+"
            r"(?:already\s+|all\s+)?(?:made|prepared|done|mixed|filled|ready)\b|"
            # samples that already exist: "my samples are in column 6", "I only have sample in A11", "take the samples in
            # plate column 2", "what I've already got in the plate", "don't remake anything"
            r"\b(?:my|our|the)\s+samples?\s+(?:are|is)\s+(?:already\s+)?(?:in|on|sitting)\b|"
            r"\b(?:i|we)\s+(?:only\s+|already\s+)?(?:have|got)\s+(?:the\s+|my\s+|our\s+)?samples?\s+(?:already\s+)?in\b|"
            r"\b(?:take|use|print|spot)\s+(?:the\s+|my\s+|our\s+)?samples?\s+(?:already\s+)?in\b|"
            r"\bwhat\s+(?:i|we)(?:'ve|\s+have)\s+(?:already\s+)?got\b|\b(?:don'?t|do\s+not|dont|no\s+need\s+to)\s+remake\b|"
            r"\b(?:i|we)\s+(?:don'?t|do\s+not|dont)\s+(?:want|need)\s+(?:any\s+|the\s+|new\s+)?dilut\w*",
            re.I)),
}
_STEP_REFUSAL = {
    ("print.enabled", False): ('You did not ask to skip printing, so I did not turn printing off. To make the dilutions '
                               'without printing, say "skip printing".'),
    ("print.enabled", True): ('You did not ask to print in this run, so I did not turn printing on. To print, say '
                              '"print the dilutions".'),
    ("dilution.enabled", False): ('You did not say that the dilutions are already made or ask to skip making them, so I '
                                  'did not turn the dilution step off.'),
    ("dilution.enabled", True): ('You did not ask to make the dilutions in this run, so I did not turn the dilution step '
                                 'on. To make them, say "make the dilutions".'),
}
_STEP_CONCERNS = {
    ("print.enabled", False): "you did not ask to skip printing",
    ("print.enabled", True): "you did not ask to print in this run",
    ("dilution.enabled", False): "you did not say the samples are already in the plate or ask to skip the dilutions",
    ("dilution.enabled", True): "you did not ask to make the dilutions in this run",
}
# "plate columns 1, 3 and 5", "columns 1, 3 and 5 of the plate": several plate columns named at once
_NOT_A_COLUMN_AFTER = r"(?!\s*(?:µl|ul|ml|x\b|×|%|drops?|droplets?|replicates?|times|dilutions?|columns?|rows?|wells?))"
_PLATE_COLUMN_LIST = re.compile(
    rf"\bplate\s+columns\s+(\d{{1,2}}(?:\s*(?:,|&|\band\b|\bor\b)\s*\d{{1,2}})+)\b{_NOT_A_COLUMN_AFTER}|"
    rf"\bcolumns\s+(\d{{1,2}}(?:\s*(?:,|&|\band\b|\bor\b)\s*\d{{1,2}})+)\s+(?:of|in|on|from)\s+the\s+"
    r"(?:96[-\s]?well\s+|dilution\s+)?plate\b", re.I)
SELECTION_PATHS = frozenset({"rows", "paper_rows", "paper_columns", "print_map", "drops_at"})
# The fields each selection sets: a selection whose fields the scientist asked to keep ("keep my dilution wells the
# same") is left out like any kept change. "rows" is where the dilutions are (plate rows); "paper_rows" where they print.
SELECTION_FIELDS = {
    "rows": frozenset({"dilution.rows", "dilution.start_row"}),
    "paper_rows": frozenset({"print.paper_rows"}),
    "paper_columns": frozenset({"print.paper_start_column", "print.replicates"}),
    "print_map": frozenset({"print.source_map"}),
    "drops_at": frozenset({"print.source_map"}),
}
# concerns that mean "this value is not in the current message": the scientist's earlier words may still hold it
_GROUNDING_CONCERNS = {"those factors are not in your request", "not found in your request"}


# Mentions that name a place ON labware, not the labware as something to move.
_INCIDENTAL = re.compile(
    r"\b(?:paper|plate)\s+(?:positions?|columns?|rows?|wells?|spots?)\b|\bwells?\s+[A-H]\d{1,2}\b|"
    r"\b(?:on|onto|to)\s+(?:the\s+)?paper\b|\bprint(?:ing|ed|s)?\s+(?:on|onto)\s+(?:the\s+)?paper\b", re.I)
_MOVED_OBJECT = r"\b(?:move|put|place|take|shift|relocate|swap|slide|remove|load|set)\s+(?:the\s+|both\s+|all\s+)?"
_SLOT_CONTEXT = re.compile(
    r"\bslot\s*#?\s*(\d{1,2})\b|\b(?:to|in|into|at|on|onto)\s+(?:the\s+)?(?:deck\s+)?(?:slot\s+)?(\d{1,2})\b"
    r"(?!\s*(?:µl|ul|ml|mm|x\b|×|%|drops?|droplets?|dilutions?|times|replicates?|columns?|rows?|wells?|tips?|\.\d))",
    re.I)


def named_labware(text: str) -> set[str]:
    """Labware the text names as deck objects ("move the tips" counts; "tip A1" and "paper position" do not)."""
    from src.agents.dye_demo.intent import labware_mentions

    deck_text = _INCIDENTAL.sub(" ", text)
    roles = {role for role, _, _ in labware_mentions(deck_text)}
    if re.search(_MOVED_OBJECT + r"tips\b(?!\s*racks?)", deck_text, re.I):
        roles.add("tiprack")
    if re.search(_MOVED_OBJECT + r"(?:vials|tubes|bottles)\b", deck_text, re.I):
        roles.add("tuberack")
    if re.search(r"\b(?:both|the|two|all)\s+racks\b", deck_text, re.I):
        roles.update({"tuberack", "tiprack"})
    return roles


def slot_numbers(text: str) -> set[int]:
    """Numbers written as deck slots ("slot 8", "to 8", "in 6"), not counts or volumes ("1 drop")."""
    return {int(next(group for group in match.groups() if group)) for match in _SLOT_CONTEXT.finditer(text)}


_ROW_LIST = re.compile(r"\brows?\b((?:\s*(?:,|&|-|–|\band\b|\bto\b|\bthrough\b|\bthru\b|\bor\b)?\s*\b[a-h1-8]\b)+)", re.I)
_ROW_PHRASES = re.compile(
    r"\b(?:rows?|top|first|second|third|fourth|fifth|sixth|seventh|eighth|1st|2nd|3rd|4th|5th|6th|7th|8th|well|wells)\b",
    re.I,
)


_ROW_ORDINAL_WORDS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth")


def _start_row_stated(row: str, text: str) -> bool:
    """A row named in words, the number or ordinal attached to "row" ("row 2", "the second row", "the 2nd row", "the
    top row"): a count elsewhere in the message ("2 drops") never names a row."""
    row = row.upper()
    if row not in ROWS:
        return False
    number = ROWS.index(row) + 1
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(number, "th")
    patterns = [rf"\brow\s*#?\s*{number}\b", rf"\b(?:{_ROW_ORDINAL_WORDS[number - 1]}|{number}{suffix})\s+row\b"]
    if row == "A":
        patterns.append(r"\btop\s+row\b")
    if row == ROWS[-1]:
        patterns.append(r"\b(?:bottom|last)\s+row\b")
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _rows_stated(rows: list[str], text: str) -> bool:
    """Check if the selected rows are supported by the scientist's words (letter, number, or natural phrase)."""
    if not rows:
        return False
    named = {letter.upper() for match in _ROW_LIST.finditer(text) for letter in re.findall(r"\b[a-h]\b", match.group(1), re.I)}
    named |= {token[0] for token in well_tokens(text)}
    named |= {letter.upper() for letter in re.findall(r"\b[A-H]\b", text, re.I)}
    if all(r in named for r in rows):
        return True

    from src.agents.dye_demo.model import ROWS
    # a row as a number ("rows 1 3 5") only in words about rows: "2 drops" never names row B
    nums_in_text = set(re.findall(r"\b[1-8]\b", text)) if re.search(r"\brows?\b", text, re.I) else set()
    if nums_in_text and all(str(ROWS.index(r) + 1) in nums_in_text for r in rows):
        return True
    if len(rows) == 1 and str(ROWS.index(rows[0]) + 1) in nums_in_text:
        return True

    if _ROW_PHRASES.search(text):
        return True

    return False


def _columns_stated(columns: list[int], text: str) -> bool:
    """The first and last selected paper column are numbers in a sentence about columns ("columns 1 2 and 3", "4-6")."""
    return bool(re.search(r"\bcol(?:umn)?s?\b", text, re.I)) and numbers_mentioned([columns[0], columns[-1]], text)


_ROW_THEN_COLUMN = re.compile(r"\brow\s+([a-h]|[1-8])\b[^.;!?]{0,24}?\bcol(?:umn)?\s+(\d{1,2})\b", re.I)
_COLUMN_THEN_ROW = re.compile(r"\bcol(?:umn)?\s+(\d{1,2})\b[^.;!?]{0,24}?\brow\s+([a-h]|[1-8])\b", re.I)


def wells_named(text: str) -> set[str]:
    """Plate wells a text names: "A11", or "row 1 column 11" / "column 11 row A" (rows as letters or 1-8)."""
    named = {token.upper() for token in well_tokens(text)}

    def row_letter(value: str) -> str:
        return ROWS[int(value) - 1] if value.isdigit() else value.upper()

    for match in _ROW_THEN_COLUMN.finditer(text):
        named.add(f"{row_letter(match.group(1))}{int(match.group(2))}")
    for match in _COLUMN_THEN_ROW.finditer(text):
        named.add(f"{row_letter(match.group(2))}{int(match.group(1))}")
    return named


def made_not_printed(before: dict[str, Any], after: dict[str, Any]) -> tuple[list[str], list[str], str] | None:
    """(made wells, printed wells, plate column) when a plan makes its dilution series and then prints only some of
    those wells through a print map - "I only have sample in A11, use that everywhere" read with the dilution step
    still on would dispense into the scientist's sample in A11 before printing it. Physical state only the scientist
    knows (is the sample already there?), so it is asked, never assumed. None when the plan was already like this
    before this proposal (asked, or accepted, then)."""
    plan = build_plan(after)
    if not (plan.do_dilution and plan.do_print and plan.mapped and plan.print_sources):
        return None
    made = [well.well for well in plan.wells]
    printed = [source.well for source in plan.print_sources]
    if not all(source.made_here for source in plan.print_sources) or set(made) <= set(printed):
        return None
    earlier = build_plan(before)
    if earlier.do_dilution and earlier.mapped and [source.well for source in earlier.print_sources] == printed \
            and [well.well for well in earlier.wells] == made:
        return None
    return made, printed, str(plan.plate_column)


def _sources_stated(entries: list[dict[str, Any]], text: str) -> bool:
    """Every source well of a print map is named in the words (where it prints is Python's arithmetic): as a well
    ("A11", "column 11 row 1"), or as a well of a plate column the words name ("the samples in plate column 2")."""
    named = wells_named(text)
    columns = {int(number) for number in re.findall(
        r"\bplate\s+columns?\s+(\d{1,2})\b|\bcolumns?\s+(\d{1,2})\s+(?:of|in|on)\s+the\s+(?:96[-\s]?well\s+|dilution\s+)?"
        r"plate\b", text, re.I) for number in number if number}
    return all(entry["source"] in named or (entry["source"][1:].isdigit() and int(entry["source"][1:]) in columns)
               for entry in entries)


_PAPER_LAYOUT_PATHS = {"print.replicates", "print.paper_start_column"}
_COUNT_QUESTIONS = {
    "print.replicates": "How many total copies of each print condition should be printed?",
    "print.droplets_per_spot": "How many drops should be stacked on each paper position?",
}
# Proposals that restore stored values or book-keep a finished run are not read against the words of a request.
_NO_COLUMN_CHECK_SOURCES = {"rollback", "post-run", "history"}      # values from a record, not from the words




def fingerprint(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def lab_owned_view(config: dict[str, Any]) -> dict[str, Any]:
    """The configuration with every conversation-editable field removed."""
    view = deepcopy(config)
    for canonical in EDITABLE_FIELDS:
        try:
            set_path(view, resolve_path(view, canonical), None)
        except FieldError:
            continue
    return view


def _same(a: Any, b: Any) -> bool:
    return fingerprint(a) == fingerprint(b) or a == b


def _map_prints_series(config: dict[str, Any]) -> bool:
    """The print map prints exactly the dilutions this run makes (its source wells are the series wells)."""
    entries = print_map(config) or []
    plan = build_plan(config)
    return bool(entries) and plan.do_dilution and \
        {entry["source"] for entry in entries} == {well.well for well in plan.wells}


def _follow_series(old: list[tuple[str, float]], new: list[tuple[str, float]]) -> dict[str, str] | None:
    """{old well: new well} for each dilution of a series that moved: matched by dilution factor when every factor is
    known and distinct and every new dilution finds its old one (a dilution left out has no new well), else by position
    in the series when the counts agree (new factors in new rows: the i-th dilution keeps the i-th one's positions);
    None when it cannot be told which dilution went where."""
    old_factors, new_factors = [factor for _, factor in old], [factor for _, factor in new]
    if all(factor > 0 for factor in old_factors + new_factors) and len(set(old_factors)) == len(old_factors) \
            and len(set(new_factors)) == len(new_factors):
        well_of = {factor: well for well, factor in new}
        by_factor = {well: well_of[factor] for well, factor in old if factor in well_of}
        if set(by_factor.values()) == {well for well, _ in new}:
            return by_factor
    if len(old) == len(new):
        return {old_well: new_well for (old_well, _), (new_well, _) in zip(old, new)}
    return None


def merged_prepared(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any] | None:
    """The prepared-dilutions record after `new` (a run that made dilutions, or a report about some wells): the wells it
    names get its factors, and every other recorded well still holds what it held. A second run in plate column 10 does
    not empty column 11 (the record was replaced, so a third run could fill A11-E11 again). None stays "no dilutions
    in the plate" (the plate was replaced)."""
    if not isinstance(new, dict) or not isinstance(old, dict) or not old.get("wells"):
        return deepcopy(new)
    named = set(new.get("wells", []))
    kept = [(well, factor) for well, factor in zip(old.get("wells", []), old.get("factors", [])) if well not in named]
    if not kept:
        return deepcopy(new)
    sources = {well: (old.get("sources") or {}).get(well, old.get("source", "reported")) for well, _ in kept}
    sources.update({well: new.get("source", "reported") for well in new.get("wells", [])})
    pairs = sorted(kept + list(zip(new.get("wells", []), new.get("factors", []))),
                   key=lambda pair: (int(pair[0][1:]), ROWS.index(pair[0][0])))
    wells = [well for well, _ in pairs]
    groups: dict[str, list[str]] = {}
    for well in wells:
        groups.setdefault(sources[well], []).append(well)
    source = next(iter(groups)) if len(groups) == 1 else "; ".join(
        f"{how} ({', '.join(named_wells)})" for how, named_wells in groups.items())
    return {**deepcopy(new), "wells": wells, "factors": [factor for _, factor in pairs], "source": source,
            "sources": {well: sources[well] for well in wells}}


def _tidy(value: float) -> int | float:
    value = round(float(value), 4)
    return int(value) if value.is_integer() else value


_NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"
_UNIT_LIST = re.compile(
    rf"(?<![\w.])({_NUMBER}(?:\s*(?:,\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+|\s*&\s*|\s*/\s*){_NUMBER})*)\s*"
    r"(µL|uL|µl|ul|microlit\w*|mL|ml|millilit\w*)(?![\w])", re.I)


def stated_volumes(text: str) -> tuple[list[tuple[float, float, str]], list[float]]:
    """([(µL, number as written, unit)], [numbers written without a volume unit]).

    A unit written once after a list applies to the whole list: "5, 10 and 15 µL".
    """
    with_unit: list[tuple[float, float, str]] = []
    covered: list[tuple[int, int]] = []
    for match in _UNIT_LIST.finditer(text):
        unit = match.group(2)
        for number in re.findall(_NUMBER, match.group(1)):
            with_unit.append((volume_in_microlitres(f"{number} {unit}"), float(number), unit))
        covered.append(match.span())
    without_unit = [float(match.group(1)) for match in _QUANTITY.finditer(text)
                    if not match.group(2) and not any(start <= match.start() < end for start, end in covered)]
    return with_unit, without_unit


@dataclass(frozen=True)
class FieldChange:
    path: str
    before: Any
    after: Any
    kind: str = "requested"
    evidence: str = ""
    why: str = ""
    verified: bool = True
    concern: str = ""


@dataclass
class Proposal:
    id: int
    base_revision: int
    request: str
    changes: tuple[FieldChange, ...]
    before: dict[str, Any]
    after: dict[str, Any]
    report: Report
    explanation: str = ""
    notes: tuple[str, ...] = ()
    source: str = "conversation"
    physical: dict[str, Any] = field(default_factory=dict)
    replaces: int | None = None
    title: str = "PROPOSED PLAN"
    origin: str = ""

    @property
    def empty(self) -> bool:
        return not self.changes and not self.physical

    @property
    def deck_changed(self) -> bool:
        return any(change.path.startswith("deck.") for change in self.changes)

    @property
    def paths(self) -> list[str]:
        return [change.path for change in self.changes]

    def signature(self) -> str:
        return fingerprint([[change.path, change.after] for change in self.changes] + [self.physical])


class ProposalRejected(ValueError):
    """A proposal that cannot be offered; the state is unchanged.

    `question` is what to ask the scientist next, `yes_text` is what a yes to that
    question adds to the request, when a plain yes/no answers it. `fix_changes` are
    field changes a yes to the question would propose instead (still shown as a
    proposal that needs its own yes).
    """

    def __init__(self, message: str, *, kind: str = "invalid", conflicts: Iterable[DeckConflict] = (),
                 before: dict[str, Any] | None = None, after: dict[str, Any] | None = None,
                 question: str = "", yes_text: str = "", fix_changes: list[dict[str, Any]] | None = None,
                 path: str = ""):
        super().__init__(message)
        self.kind = kind
        self.conflicts = list(conflicts)
        self.before = before
        self.after = after
        self.question = question
        self.yes_text = yes_text
        self.fix_changes = fix_changes
        # The one proposed change this is about ("deck.plate.slot", or a selection such as "print_map"), when the
        # rest of the request was fine: the session keeps the rest and asks only about this part.
        self.path = path


class StaleProposal(RuntimeError):
    """A proposal built against an older revision of the experiment."""


class ExperimentState:
    def __init__(self, config: dict[str, Any], *, printed_positions: Iterable[str] = (), first_proposal_id: int = 1,
                 first_run: int = 1):
        """A new experiment. `first_proposal_id` and `first_run` continue a session's numbering after Reset Demo, so
        a proposal or run number never means two things in one session log (executed_config_run<N>.yaml included)."""
        self._config = deepcopy(config)
        self.revision = 0
        self.history: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.printed_positions: set[str] = set(printed_positions)
        self.deck_changed_since_run = False
        self.physical: dict[str, Any] = {"dilutions_prepared": None,
                                         "plate_id": 1, "paper_id": 1, "tip_rack_id": 1}
        self.lab_owned_fingerprint = fingerprint(lab_owned_view(self._config))
        self.snapshots: list[dict[str, Any]] = [self._snapshot()]
        self._next_proposal_id = first_proposal_id
        self._first_run = first_run

    @property
    def next_proposal_id(self) -> int:
        return self._next_proposal_id

    @property
    def next_run(self) -> int:
        """The number the next recorded run gets."""
        return self._first_run + len(self.runs)

    def _snapshot(self) -> dict[str, Any]:
        return {"revision": self.revision, "config": deepcopy(self._config), "physical": deepcopy(self.physical)}

    @property
    def config(self) -> dict[str, Any]:
        return deepcopy(self._config)

    def fingerprint(self) -> str:
        return fingerprint(self._config)

    def full_fingerprint(self) -> str:
        return fingerprint({"config": self._config, "physical": self.physical, "revision": self.revision})

    def lab_owned_intact(self) -> bool:
        return fingerprint(lab_owned_view(self._config)) == self.lab_owned_fingerprint

    def validate(self) -> Report:
        return validate(self._config, printed_positions=self.printed_positions)

    # ── proposals ────────────────────────────────────────────────────────────

    def propose(self, raw_changes: Iterable[dict[str, Any]], *, request: str, explanation: str = "",
                notes: Iterable[str] = (), source: str = "conversation", superseded: str = "",
                restrict_paths: Iterable[str] | None = None, physical: dict[str, Any] | None = None,
                replaces: int | None = None, title: str = "PROPOSED PLAN", origin: str = "",
                context: str = "", history: str = "", dry_run: bool = False, referents: Iterable[str] = (),
                preserved: Iterable[str] = (), recent_paths: Iterable[str] = (), known_sources: Iterable[str] = (),
                accepted: Iterable[str] = (), semantic_only: bool = False) -> Proposal:
        """`request` is the scientist's own words for this request; values must come from them. `history` is their own
        words earlier in this conversation: a value found only there is accepted as carried over and shown for checking
        ("same thing but columns 4-6" keeps the rows and drops of the request it revises). `context` (the question half
        of a mixed message, "why are we using 8 dilutions, and change it to 4") may only show which field is meant.
        `dry_run` validates without numbering a proposal (an offer the scientist has not accepted yet).

        Grounding (grounding.py) is semantic: `referents` are the labware discussed most recently ("put that in slot 8"),
        `preserved` the fields the scientist asked to keep (a change to one of them is dropped with a note), and
        `recent_paths` the fields of the last request (they settle "column 7" as a paper or plate column). A change the
        words do not support raises ProposalRejected with `path` set, so the session can ask about that change alone.
        `known_sources` are the print-source wells of the proposal being revised and of the current plan: a print map
        that uses them again ("sorry, I meant columns 2, 5 and 6") is not inventing wells. `accepted` names plan checks
        the scientist already answered for this request ("made_not_printed")."""
        before = self.config
        after = deepcopy(before)
        structured = [self._as_print_map(raw, before) for raw in self._drops(raw_changes, before)]
        raw_changes = (structured if semantic_only else
                       self._rows_as_selection(self._series_pairs(structured, before), before))
        restrict = tuple(restrict_paths or ())
        final_text = request.replace(superseded, " ") if superseded else request
        notes = list(notes)
        changes: dict[str, FieldChange] = {}
        from_selections: set[str] = set()          # fields a selection (rows, paper columns, print map) produced
        kept = set(preserved)
        self._grounding = {"referents": tuple(referents), "recent_paths": tuple(recent_paths),
                           "moves": self._planned_moves(raw_changes, before), "request": request, "kept": tuple(kept),
                           "known_sources": tuple(known_sources), "accepted": tuple(accepted)}

        def add(raw: dict[str, Any], checked: tuple[bool, str] | None = None) -> None:
            canonical = canonicalize_path(before, raw.get("path", ""))
            try:
                _add(raw, canonical, checked)
            except ProposalRejected as exc:
                if not exc.path and canonical in EDITABLE_FIELDS and exc.kind not in {"deck_conflict", "invalid_plan"}:
                    exc.path = canonical
                raise

        def _add(raw: dict[str, Any], canonical: str, checked: tuple[bool, str] | None) -> None:
            reason = lab_owned_reason(canonical)
            if reason:
                raise ProposalRejected(f"{canonical} cannot be changed here: {reason}.", kind="lab_owned")
            label, normalize = EDITABLE_FIELDS[canonical]
            kind = "dependent" if raw.get("kind") == "dependent" else "requested"
            if kind == "requested" and canonical in kept:
                if canonical in _SLOT_PATHS:
                    # "The plate can stay where it is but move the thing with the stock samples to 8" read as a plate
                    # move: the words for this move name other labware, so it is asked about - never just dropped
                    others = labware_cues(str(raw.get("evidence") or "")) - {canonical.split(".")[1]}
                    others -= {path.split(".")[1] for path in kept if path.startswith("deck.")}
                    if others:
                        value = raw.get("value")
                        where = "OFF DECK" if is_off_deck(value) else f"slot {value}"
                        raise ProposalRejected(
                            f"You asked to keep the {label.lower()} as it is, and the words for this move name other "
                            "labware.", kind="ambiguous", path=canonical, question=f"Which labware should go to {where}?")
                # CONTRADICTED: the scientist asked to keep this; the rest of the request goes ahead
                note = f"You asked to keep the {label.lower()} as it is, so I left it unchanged."
                if note not in notes:
                    notes.append(note)
                return
            if restrict and canonical not in restrict and kind == "requested":
                allowed = ", ".join(field_label(path).lower() for path in restrict)
                raise ProposalRejected(f"You asked me to change only the {allowed}, but this would also change the "
                                       f"{label.lower()}. Nothing was changed.", kind="outside_restriction")
            try:
                actual = resolve_path(before, canonical)
                current = get_path(before, actual)
                value = self._value_for(canonical, raw, current, normalize)
            except FieldError as exc:
                raise ProposalRejected(f"{label}: {exc}", kind="invalid_value") from exc
            if semantic_only and kind == "requested":
                self._explicit_contradiction(canonical, value, raw, request)
            expected = raw.get("expected_before")
            if kind == "requested" and expected not in (None, ""):
                try:
                    expected_value = normalize(expected)
                except FieldError:
                    expected_value = expected
                if not _same(expected_value, current):
                    raise ProposalRejected(
                        f"The {label.lower()} is currently {self._show(canonical, current)}, not "
                        f"{self._show(canonical, expected_value)}, so I did not change anything. Tell me the new value "
                        "you want.", kind="stale_information")
            if canonical in changes:
                if _same(changes[canonical].after, value):
                    return
                if not (checked is not None and canonical in from_selections):
                    raise ProposalRejected(f"two different values were proposed for the {label.lower()}",
                                           kind="invalid_value")
                # a later selection lays out what an earlier one chose ("A11 for all prints" + "paper columns 2, 4
                # and 6"): it was expanded from the plan WITH the earlier one, so its value is the combined one
                if _same(current, value):
                    set_path(after, actual, value)
                    del changes[canonical]
                    return
            if _same(current, value):
                if kind == "requested" and checked is None and not semantic_only:
                    # "Start tips at A10" answered with the current A1 is not "already set": the model contradicted
                    # the scientist. Only contradictions are raised; an echoed unchanged field is fine.
                    try:
                        self._verify_value(canonical, label, value, raw, request, final_text, superseded, before)
                    except ProposalRejected as exc:
                        if exc.kind in {"well_mismatch", "unit_mismatch", "corrected"}:
                            raise
                return
            verified, concern = checked if checked is not None else (True, "")
            if kind == "requested" and checked is None:
                verified, concern = ((True, "") if semantic_only else
                                     self._verify(canonical, label, value, raw, request, final_text, superseded, before,
                                                  context=context, history=history))
            set_path(after, actual, value)
            changes[canonical] = FieldChange(canonical, current, value, kind, str(raw.get("evidence") or ""),
                                             str(raw.get("why") or ""), verified, concern)
            if checked is not None:
                from_selections.add(canonical)

        selections = [raw for raw in raw_changes if str(raw.get("path", "")).strip().lower() in SELECTION_PATHS]
        for raw in raw_changes:
            if not any(raw is selection for selection in selections):
                add(raw)
        has_paper_rows = any(str(raw.get("path", "")).strip().lower() == "paper_rows" for raw in selections)
        has_paper_cols = any(str(raw.get("path", "")).strip().lower() == "paper_columns" for raw in selections)
        if has_paper_rows and has_paper_cols and print_map(after) is not None:
            set_path(after, resolve_path(after, "print.source_map"), None)
        for raw in selections:
            # expanded against the plan with this proposal's other changes (a new drop volume list, for example)
            selection = str(raw.get("path", "")).strip().lower()
            held = sorted(SELECTION_FIELDS.get(selection, frozenset()) & kept)
            if selection in kept or held:
                # CONTRADICTED, as for a field: "keep my dilution wells the same" keeps the plate rows whatever
                # selection would have moved them; the rest of the request goes ahead
                for path in held:
                    note = f"You asked to keep the {field_label(path).lower()} as it is, so I left it unchanged."
                    if note not in notes:
                        notes.append(note)
                continue
            try:
                expanded, note, checked = self._expand_selection(raw, after, final_text, history,
                                                                 factors_changed="dilution.factors" in changes,
                                                                 semantic_only=semantic_only)
            except ProposalRejected as exc:
                if not exc.path:
                    exc.path = selection
                raise
            if note not in notes:
                notes.append(note)
            for item in expanded:
                add({"evidence": str(raw.get("evidence") or ""), **item}, checked)
        for item in self._placed_prints(before, after, changes):
            add(item, (True, ""))
        if not semantic_only:
            for item in self._destinations_kept(before, after, set(changes)):
                add(item, (True, ""))
        if semantic_only:
            has_paper_rows = any(str(raw.get("path", "")).strip().lower() == "paper_rows" for raw in raw_changes) or "print.paper_rows" in changes
            has_paper_cols = any(str(raw.get("path", "")).strip().lower() == "paper_columns" for raw in raw_changes) or "print.paper_start_column" in changes
            if has_paper_rows and has_paper_cols and print_map(before) is not None:
                set_path(after, resolve_path(after, "print.source_map"), None)
            placement_requested = bool({"print.paper_rows", "print.paper_start_column", "print.replicates",
                                        "print.source_map"} & set(changes)) or any(
                str(raw.get("path", "")).strip().lower() in
                {"paper_rows", "paper_columns", "print_map", "drops_at"} for raw in raw_changes)
            if placement_requested and steps_enabled(after)[1]:
                default_drops = int(after["print"].get("droplets_per_spot", 1))
                if print_map(after) is None:
                    # Resolve row/column/replicate shorthand once, before validation.
                    # The map records the exact source, destination, volume and drops
                    # that the current plan would print, in operation order.
                    mapped: list[dict[str, Any]] = []
                    for operation in build_plan(after).operations:
                        if operation.kind != "print":
                            continue
                        key = (operation.source, operation.volume_ul, operation.droplets)
                        entry = next((item for item in mapped if item["_key"] == key), None)
                        if entry is None:
                            entry = {"_key": key, "source": operation.source, "positions": [],
                                     "volume_ul": operation.volume_ul}
                            if operation.droplets != default_drops:
                                entry["drops"] = {}
                            mapped.append(entry)
                        entry["positions"].append(operation.destination)
                        if "drops" in entry:
                            entry["drops"][operation.destination] = operation.droplets
                    for entry in mapped:
                        entry.pop("_key")
                    if mapped:
                        add({"path": "print.source_map", "value": mapped, "kind": "dependent",
                             "why": "the requested paper placement is stored as exact print destinations"},
                            (True, ""))
                if print_map(after) is not None and explicit_paper_rows(after) is not None:
                    add({"path": "print.paper_rows", "value": None, "kind": "dependent",
                         "why": "the print map now names every paper destination"}, (True, ""))
                if print_map(after) is not None and "print.replicates" not in changes:
                    entries = print_map(after) or []
                    counts = {len(entry["positions"]) for entry in entries}
                    uniform_drops = all(len(set((entry.get("drops") or {}).get(position, default_drops)
                                                for position in entry["positions"])) == 1 for entry in entries)
                    if len(counts) == 1 and uniform_drops:
                        add({"path": "print.replicates", "value": counts.pop(), "kind": "dependent",
                             "why": "the explicit map determines the number of copies per condition"}, (True, ""))
        physical = deepcopy(physical or {})
        if "dilution.prepared_volume_ul" in changes and after["dilution"].get("prepared_volume_ul") is not None:
            # A statement about this plan's prepared wells updates those wells only;
            # it must not invalidate unrelated recorded volumes in the plate.
            volume = float(after["dilution"]["prepared_volume_ul"])
            volumes = deepcopy(physical.get(WELL_VOLUMES, self.physical.get(WELL_VOLUMES) or {}))
            plan_for_volume = build_plan(after)
            wells = {source.well for source in plan_for_volume.print_sources if not source.made_here}
            wells.update(well.well for well in plan_for_volume.wells if not plan_for_volume.do_dilution)
            for well in wells:
                volumes[well] = {"volume_ul": volume, "plate_id": physical.get("plate_id", self.physical.get("plate_id", 1)),
                                 "source": "operator stated prepared volume",
                                 "timestamp_utc": datetime.now(timezone.utc).isoformat()}
            if wells:
                physical[WELL_VOLUMES] = volumes
                if source in {"conversation", "llm-first"}:
                    source = "print-only-assumption"
        if physical.get("dilutions_prepared") in (PREPARED_FROM_PLAN, ASSUMED_FROM_PLAN):
            # "The dilutions are already made ... their factors are 2x, 5x and 10x": record the wells and factors
            # of the plan after this proposal's own changes, not of the plan before them.
            resulting = build_plan(after)
            series = resulting.wells
            if not series and print_map(after) is not None:
                # the reported dilutions printed through a print map: the record is the dilution series itself
                unmapped = deepcopy(after)
                unmapped["print"]["source_map"] = None
                series = build_plan(unmapped).wells
            # the other wells already recorded still hold what they held
            physical["dilutions_prepared"] = merged_prepared(self.physical.get("dilutions_prepared"), {
                "wells": [well.well for well in series], "factors": [well.factor for well in series],
                "total_volume_ul": resulting.total_volume_ul,
                "source": "assumed for print-only proposal; confirmed on approval"
                          if physical["dilutions_prepared"] == ASSUMED_FROM_PLAN else "reported by the operator"})
        if print_map(after) is not None and steps_enabled(after)[1] and source not in _NO_COLUMN_CHECK_SOURCES:
            # A print map that prints from wells this run does not make: the scientist's statement that they hold the
            # sample is recorded when this proposal is applied (that approval is the confirmation; nothing asks again).
            recorded = set((self.physical.get(SOURCES_PRESENT) or {}))
            prepared = self.physical.get("dilutions_prepared") or {}
            if isinstance(prepared, dict):
                recorded |= set(prepared.get("wells", []))
            reported = physical.get("dilutions_prepared")              # recorded by this same proposal
            if isinstance(reported, dict):
                recorded |= set(reported.get("wells", []))
            new = [item.well for item in build_plan(after).print_sources
                   if not item.made_here and item.well not in recorded]
            if new:
                present = deepcopy(self.physical.get(SOURCES_PRESENT) or {})
                present.update({well: {"source": "stated by the operator; recorded on approval"} for well in new})
                physical[SOURCES_PRESENT] = present
                if source in {"conversation", "llm-first"}:
                    # like a print-only run's assumed dilutions: a statement about the plate, recorded only on approval
                    source = "print-only-assumption"
        proposal = Proposal(0 if dry_run else self._next_proposal_id, self.revision, request, tuple(changes.values()),
                            before, after, Report(), explanation, tuple(notes), source, physical, replaces, title, origin)
        # Checked before "already set": "print in paper columns 3 and 4" answered with values that are already set,
        # while the plan prints columns 1-2, is a contradiction, not nothing to do. A partial approval is checked only
        # when the changes the scientist kept move the printed columns.
        if not semantic_only and source not in _NO_COLUMN_CHECK_SOURCES and (
                source != "partial-approval" or paper_columns_printed(before) != paper_columns_printed(after)):
            conflict = column_conflict(final_text, after)
            if conflict is not None:
                raise ProposalRejected(f"{conflict.message} Nothing was changed.", kind=conflict.kind,
                                       question=conflict.question, fix_changes=conflict.fix, before=before,
                                       after=after)
        if proposal.empty:
            return proposal
        # In LLM-first demo mode, allow contextual inferences to be shown to the user as proposals.
        # Deterministic physical validation and user approval (Apply/Discard) remain 100% preserved.
        unmentioned = [change for change in proposal.changes
                       if change.kind == "requested" and change.concern.startswith("you did not mention")]
        if unmentioned and len(unmentioned) == len(proposal.changes) and not proposal.physical and source not in {"conversation", "print-only-assumption"}:
            fields = ", ".join(field_label(change.path).lower() for change in unmentioned)
            raise ProposalRejected(f"your message did not mention the {fields}, so I did not propose changing it; "
                                   "anything you did mention is already set", kind="unmentioned_fields")
        conflicts = deck_conflicts(before, after)
        if conflicts:
            raise ProposalRejected("that deck change collides with labware already on the deck",
                                   kind="deck_conflict", conflicts=conflicts, before=before, after=after)
        self._check_prerequisites(before, after, proposal.physical)
        report = validate(after, printed_positions=() if physical.get("paper_id") != self.physical.get("paper_id")
                          else self.printed_positions)
        if report.errors:
            raise ProposalRejected("that would not be a valid run:\n- " + "\n- ".join(report.error_messages()),
                                   kind="invalid_plan", before=before, after=after)
        if source in {"conversation", "llm-first", "print-only-assumption"} and "made_not_printed" not in set(accepted):
            # a valid run that may still dispense into the scientist's own sample: asked before it is proposed
            unprinted = made_not_printed(before, after)
            if unprinted is not None:
                made, printed, column = unprinted
                names = ", ".join(printed[:-1]) + f" and {printed[-1]}" if len(printed) > 1 else printed[0]
                plural = "s" if len(printed) > 1 else ""
                raise ProposalRejected(
                    f"This plan would make all {len(made)} dilutions in plate column {column} "
                    f"({positions_text(made, limit=len(made) + 1)}), "
                    f"which also fills plate well{plural} {names}, and then print only {names}.",
                    kind="made_not_printed", before=before, after=after,
                    question=f"Is your sample already in plate well{plural} {names}? Yes: skip making the dilutions "
                             f"and print what is already there. No: make the {len(made)} dilutions first, then print "
                             f"{names}.")
        proposal.report = report
        if not dry_run:
            self._next_proposal_id += 1
        return proposal

    @staticmethod
    def _as_print_map(raw: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """A print map the model wrote to the print.source_map field in the print_map selection's own terms
        ({"source": "A11", "positions": "all"}, a count, columns, or sources and a total): the same request, so it is
        expanded as that selection. A value the field itself holds (explicit positions, or null) is left as it is.
        Likewise a list of paper columns written to the lab-owned paper width, print.paper_columns - a width is one
        number, so a list can only be the paper-column selection (2026-09-27 X03.2: [2, 3] there refused the request).
        Paper rows written to print.paper_rows are the paper-row selection: expanded against the plan like the paper
        columns (a plan printing from a print map moves its sources to those rows; a dilution series gets them)."""
        if canonicalize_path(config, raw.get("path", "")) == "print.paper_columns" \
                and isinstance(raw.get("value"), (list, tuple)) and str(raw.get("op") or "set").lower() == "set":
            return {**raw, "path": "paper_columns"}
        if canonicalize_path(config, raw.get("path", "")) == "print.paper_rows" \
                and raw.get("value") not in (None, "", []) and str(raw.get("op") or "set").lower() == "set":
            return {**raw, "path": "paper_rows"}
        if canonicalize_path(config, raw.get("path", "")) != "print.source_map" or raw.get("value") is None \
                or str(raw.get("op") or "set").lower() != "set":
            return raw
        try:
            normalize_source_map(raw["value"])
        except (FieldError, TypeError, ValueError, AttributeError):
            return {**raw, "path": "print_map"}
        return raw

    @staticmethod
    def _destinations_kept(before: dict[str, Any], after: dict[str, Any], changed: set[str]) -> list[dict[str, Any]]:
        """Dependent changes that keep WHERE the dilutions print independent of WHERE they are made.

        Plate rows (dilution.rows) and paper destinations (print.paper_rows, or the print map) are separate settings;
        a proposal that changes one must not silently change the other. When this proposal moves the dilution series
        and leaves the print destinations alone:
          * a print map that prints exactly this run's dilutions follows them to their new wells - each dilution keeps
            its paper positions (a map left pointing at the old wells would print from wells nothing fills);
          * explicit paper rows follow the dilutions they belong to when some dilutions are left out ("only the 5x and
            10x": the 5x keeps its paper row).
        A print map names every paper position, so explicit paper rows go when a map is in force.
        """
        items: list[dict[str, Any]] = []
        old, new = build_plan(before), build_plan(after)
        old_series = [(well.well, well.factor) for well in old.wells]
        new_series = [(well.well, well.factor) for well in new.wells]
        moved = old.do_dilution and new.do_dilution and old_series != new_series
        entries = print_map(after) or []
        if moved and "print.source_map" not in changed and entries \
                and {entry["source"] for entry in entries} == {well for well, _ in old_series}:
            follow = _follow_series(old_series, new_series)
            if follow:
                kept = [{**entry, "source": follow[entry["source"]]} for entry in entries if entry["source"] in follow]
                if kept:
                    items.append({"path": "print.source_map", "value": kept, "kind": "dependent",
                                  "why": "the print map follows the dilutions it prints; their paper positions stay "
                                         "the same"})
        paper = explicit_paper_rows(after)
        if paper is not None and "print.paper_rows" not in changed and not new.mapped \
                and len(paper) == len(old.rows) and len(new.rows) != len(old.rows):
            by_factor = {factor: row for (_, factor), row in zip(row_factors(old.rows, old.factors), paper)
                         if factor > 0}
            realigned = [by_factor.get(factor) for _, factor in row_factors(new.rows, new.factors)]
            if realigned and None not in realigned and len(set(realigned)) == len(realigned) \
                    and realigned == sorted(realigned, key=ROWS.index):
                items.append({"path": "print.paper_rows", "value": realigned, "kind": "dependent",
                              "why": "each dilution keeps the paper row it prints on"})
        if paper is not None and (entries or any(item["path"] == "print.source_map" and item["value"]
                                                 for item in items)):
            items.append({"path": "print.paper_rows", "value": None, "kind": "dependent",
                          "why": "the print map names every paper position"})
        return items

    def _placed_prints(self, before: dict[str, Any], after: dict[str, Any],
                       changes: dict[str, FieldChange], *, occupied_override: set[str] | None = None
                       ) -> list[dict[str, Any]]:
        """Prints the side-by-side paper layout cannot place, placed on free paper positions (placement.py).

        A replicate count (print.replicates) says how many times each sample prints, never where. The default layout
        puts the replicates side by side from the first paper column and is kept whenever it fits. When it would need
        a paper column past the paper's edge, or when a print map is in force (its positions do not follow a count),
        the plan becomes an explicit print map instead: every print the layout does place stays where it is and the
        others go on the nearest free positions, never on one an earlier live run printed. Only a paper with no room
        left is refused - a real limit, unlike the old "column 13" refusal of a default layout. A first paper column
        the scientist names anchors the first print of each sample; the other copies go on the nearest free positions
        (3 replicates from column 12 print in columns 12, 11 and 10)."""
        changed = set(changes)
        requested = {path for path, change in changes.items() if change.kind == "requested"}
        if "print.source_map" in changed or not steps_enabled(after)[1]:
            return []
        occupied = set(self.printed_positions if occupied_override is None else occupied_override)
        width = int((after.get("print") or {}).get("paper_columns", 12) or 12)
        try:
            if print_map(after) is not None:
                if "print.paper_start_column" in requested:
                    entries, new = map_at_anchor(after, occupied, width=width)
                elif "print.replicates" in changed:
                    entries, new = map_with_count(before, after, occupied, width=width)
                else:
                    return []
                count = int(after["print"].get("replicates", 1))
                why = (f"each sample now prints {count} time{'s' if count != 1 else ''}: its prints stay where they are"
                       + (f" and the new ones go on the nearest free paper positions ({describe_new(new)})" if new
                          else ", keeping the first ones"))
            else:
                placed = series_as_map(before, after, occupied, width=width)
                if placed is None:
                    return []
                entries, new = placed
                past = max(int(spot["column"]) for spot in paper_layout(after, include_overflow=True))
                why = (f"side by side from paper column {after['print'].get('paper_start_column', 1)} would need paper "
                       f"column {past}, past the paper's {width} columns, so the prints stay where they are and the "
                       f"new ones go on the nearest free paper positions ({describe_new(new)})")
        except PlacementError as exc:
            raise ProposalRejected(f"{exc}. Nothing was changed.", kind="paper_layout",
                                   question=getattr(exc, "question", "") or "Which paper positions should they print "
                                                                             "on?") from exc
        items = [{"path": "print.source_map", "value": entries, "kind": "dependent", "why": why}]
        if explicit_paper_rows(after) is not None:
            items.append({"path": "print.paper_rows", "value": None, "kind": "dependent",
                          "why": "the print map names every paper position"})
        return items

    @staticmethod
    def _drops(raw_changes: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
        """"op": "drop" takes a change out of a request being merged (a clarification answer, a revision); both merges
        consume it before a proposal is made. One that reaches a proposal names a setting of the plan: dropping the
        print map returns printing to each dilution on its own paper row (the field's null); dropping anything else
        changes nothing. 2026-09-27 GUI check: "use 2x and 4x dilutions, print in paper columns 2 and 3" on a print-only
        plan came back with the print map dropped and was refused as an unknown change operation."""
        kept = []
        for raw in raw_changes:
            if str(raw.get("op") or "").lower() != "drop":
                kept.append(raw)
            elif canonicalize_path(config, raw.get("path", "")) in {"print_map", "print.source_map"} \
                    and print_map(config) is not None:
                kept.append({"path": "print.source_map", "value": None, "evidence": str(raw.get("evidence") or ""),
                             "kind": "dependent", "why": "the print map was dropped"})
        return kept

    @staticmethod
    def _series_pairs(raw_changes: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
        """Dilution factors and plate rows named in one request are pairs, in the order the scientist gave them: "10x
        in E11, 2x in A11 and 5x in C11" is factors [10, 2, 5] with rows [E, A, C]. The plan pairs the factors with the
        plate rows top to bottom (dilution.rows is kept in plate order), so both lists are put in plate-row order
        together - rows [A, C, E] with factors [2, 5, 10]. Sorting the rows alone put 10x in A11, 2x in C11 and 5x in
        E11.

        Rows written as wells keep their plate column: "make them in A5, C5 and E5" of a plan in column 11 is plate
        column 5 as well (the column was dropped without a word). The series is made in one plate column, so wells in
        different columns are a question, never a guess."""
        def items_of(value: Any) -> list[Any] | None:
            if isinstance(value, (list, tuple)):
                return list(value)
            if isinstance(value, str):
                return [part for part in re.split(r"\s*(?:,|;|&|\band\b|\s)\s*", value.strip(), flags=re.I) if part]
            return None

        def row_item(item: Any) -> tuple[str, int | None] | None:
            """'E' -> (E, None), 'E11' -> (E, 11), 5 -> (E, None) (rows 1-8 are A-H); anything else -> None."""
            if isinstance(item, bool):
                return None
            text = str(item).strip().upper()
            if text in ROWS:
                return text, None
            if text.isdigit() and 1 <= int(text) <= len(ROWS):
                return ROWS[int(text) - 1], None
            match = re.fullmatch(r"([A-H])(\d{1,2})", text)
            if match and 1 <= int(match.group(2)) <= 12:
                return match.group(1), int(match.group(2))
            return None

        def is_set(raw: dict[str, Any]) -> bool:
            return str(raw.get("op") or "set").lower() == "set"

        changes = list(raw_changes)
        factors_at = next((index for index, raw in enumerate(changes)
                           if canonicalize_path(config, raw.get("path", "")) == "dilution.factors" and is_set(raw)
                           and isinstance(raw.get("value"), (list, tuple))), None)
        column_at = next((index for index, raw in enumerate(changes)
                          if canonicalize_path(config, raw.get("path", "")) == "dilution.plate_column" and is_set(raw)),
                         None)
        for index, raw in enumerate(list(changes)):
            path = str(raw.get("path", "")).strip().lower()
            if not is_set(raw) or (path != "rows" and canonicalize_path(config, raw.get("path", "")) != "dilution.rows"):
                continue
            items = items_of(raw.get("value"))
            parsed = [row_item(item) for item in items] if items else []
            if not parsed or None in parsed:
                continue                        # another form ("first 3", "rows 1-3"): read by the selection as before
            letters = [letter for letter, _ in parsed]
            columns = sorted({column for _, column in parsed if column is not None})
            if len(columns) > 1:
                wells = ", ".join(str(item).strip().upper() for item in items)
                raise ProposalRejected(
                    f"The dilution series is made in one plate column, and {wells} are in plate columns "
                    f"{', '.join(map(str, columns))}. Nothing was changed.", kind="selection", path="rows",
                    question="Which plate column should hold the dilutions?")
            if columns:
                column = columns[0]
                stated = changes[column_at].get("value") if column_at is not None else None
                if stated is not None and str(stated).strip() and \
                        re.sub(r"(?i)^column\s*", "", str(stated).strip()) != str(column):
                    raise ProposalRejected(
                        f"The wells named are in plate column {column}, and the request also names plate column "
                        f"{stated}. Nothing was changed.", kind="selection", path="rows",
                        question="Which plate column should hold the dilutions?")
                if column_at is None and str(config["dilution"].get("plate_column", "")).strip() != str(column):
                    changes.append({"path": "dilution.plate_column", "value": str(column), "kind": "dependent",
                                    "evidence": str(raw.get("evidence") or ""),
                                    "why": f"the wells named are in plate column {column}"})
                    column_at = len(changes) - 1
            factors = list(changes[factors_at]["value"]) if factors_at is not None else None
            if factors is not None and len(factors) == len(letters) and len(set(letters)) == len(letters):
                order = sorted(range(len(letters)), key=lambda position: ROWS.index(letters[position]))
                changes[factors_at] = {**changes[factors_at], "value": [factors[position] for position in order]}
                letters = [letters[position] for position in order]
                factors_at = None               # paired once: a second row list in the request does not reorder them
            changes[index] = {**raw, "value": letters}
        return changes

    @staticmethod
    def _rows_as_selection(raw_changes: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
        """A dilution.rows list with no factor change, whose length does not match the current factors (3 rows for 8
        factors), can only mean the rows selection - these rows of the current series, their factors kept (2026-09-27
        85-test D05: rows C, E, G for "3x 6x and 12x" written to the field made the run invalid and was refused)."""
        if any(canonicalize_path(config, raw.get("path", "")) == "dilution.factors" for raw in raw_changes):
            return raw_changes
        count = len(factors_of(config))
        return [{**raw, "path": "rows"}
                if canonicalize_path(config, raw.get("path", "")) == "dilution.rows"
                and isinstance(raw.get("value"), (list, tuple)) and len(raw["value"]) != count
                and str(raw.get("op") or "set").lower() == "set" else raw
                for raw in raw_changes]

    def _planned_moves(self, raw_changes: list[dict[str, Any]], before: dict[str, Any]) -> dict[str, tuple[Any, str]]:
        """{role: (slot, evidence)} for every labware move in a request (for swaps and for telling moves apart)."""
        moves: dict[str, tuple[Any, str]] = {}
        for raw in raw_changes:
            canonical = canonicalize_path(before, raw.get("path", ""))
            if canonical in _SLOT_PATHS:
                try:
                    moves[canonical.split(".")[1]] = (normalize_slot(raw.get("value")), str(raw.get("evidence") or ""))
                except (FieldError, TypeError, ValueError):
                    continue
        return moves

    def _swap_supports(self, role: str, value: Any, before: dict[str, Any], text: str) -> bool:
        """'Swap the plate and the vial rack': each goes where the other one is, with no slot number in the words."""
        if not re.search(r"\b(?:swap|switch|exchange|trade|interchange|swop|flip)\b", text, re.I):
            return False
        moves = getattr(self, "_grounding", {}).get("moves", {})
        for other, (other_value, _) in moves.items():
            if other != role and slot_of(before, other) == value and other_value == slot_of(before, role):
                return True
        return False

    @staticmethod
    def _rows_follow_named_factors(rows: list[str], text: str, config: dict[str, Any]) -> bool:
        """'Forget the 10x one' / 'print only the 5x and 10x': the rows are the current dilutions without (or with
        only) the factors the words name."""
        named = {float(match) for match in re.findall(r"(\d+(?:\.\d+)?)\s*[x×]", text, re.I)}
        if not named:
            return False
        wells = build_plan(config).wells
        without = [well.row for well in wells if not any(abs(well.factor - factor) < 1e-9 for factor in named)]
        only = [well.row for well in wells if any(abs(well.factor - factor) < 1e-9 for factor in named)]
        return rows in (without, only)

    def _expand_selection(self, raw: dict[str, Any], config: dict[str, Any], text: str,
                          history: str, *, factors_changed: bool = False,
                          semantic_only: bool = False) -> tuple[list[dict[str, Any]], str, tuple[bool, str]]:
        """A row, paper-column or print-map selection as field changes, checked against the scientist's words."""
        path = str(raw.get("path", "")).strip().lower()
        grounding = getattr(self, "_grounding", {})
        if path == "paper_columns" and print_map(config) is not None:
            config = deepcopy(config)
            config.setdefault("print", {})["source_map"] = None
        try:
            if path == "rows":
                items: list[Any] = selected_rows(raw.get("value"))
                if factors_changed:
                    # The factors of this same request decide the series; the rows only say where it goes. The rows a
                    # new factor list occupies anyway ("make 3x and 6x" + rows A, B) add nothing.
                    factors = config["dilution"]["factors"]
                    start = str(config["dilution"].get("start_row", "A")).upper()
                    first = ROWS.index(start) if start in ROWS else 0
                    if items == list(ROWS[first:first + len(factors)]):
                        return [], "", (True, "")
                    why = "the rows named for the new dilution factors"
                    return ([{"path": "dilution.rows", "value": items, "kind": "dependent", "why": why},
                             {"path": "dilution.start_row", "value": items[0], "kind": "dependent", "why": why}],
                            f"The new dilutions go in rows {', '.join(items)}.", (True, ""))
                stated = _rows_stated
                changes, note = expand_rows(items, config)
                if self._rows_follow_named_factors(items, text, config):
                    stated = lambda rows, words: True  # noqa: E731 - "forget the 10x one" names the rows' factor
                if print_map(config) is not None and not _map_prints_series(config):
                    # a print map of OTHER wells: printing follows the selected dilutions again. (A map that prints
                    # this run's dilutions follows them to their new rows instead: ExperimentState._destinations_kept.)
                    changes.append({"path": "print.source_map", "value": None, "kind": "dependent",
                                    "why": "the selected dilutions print again instead of the print map's wells"})
                what = f"rows {', '.join(items)}" if len(items) > 1 else f"row {items[0]}"
            elif path == "paper_rows":
                # WHERE the samples print: the plate wells do not change (expand_paper_rows)
                items = selected_paper_rows(raw.get("value"))
                stated = _rows_stated
                changes, note = expand_paper_rows(items, config)
                what = f"paper rows {', '.join(items)}" if len(items) > 1 else f"paper row {items[0]}"
            elif path == "print_map":
                split = None if "unstated_split" in grounding.get("accepted", ()) else \
                    unstated_split(raw.get("value"), f"{text}\n{history}")
                if split is not None:
                    raise split                 # asked like a total with no split (the even split offered as yes)
                changes, note = expand_print_map(raw.get("value"), config, occupied=self.printed_positions)
                items = next(change["value"] for change in changes if change["path"] == "print.source_map")
                stated = _sources_stated
                wells = [entry["source"] for entry in items]
                what = f"plate well{'s' if len(wells) > 1 else ''} {', '.join(wells)}"
            elif path == "drops_at":
                # how many drops land on particular paper positions: the positions must be the scientist's own words
                changes, note = expand_drops_at(raw.get("value"), config)
                items = positions_with_drops(raw.get("value"))[0]
                stated = lambda positions, words: all(  # noqa: E731
                    re.search(rf"\b{position[0]}\s*-?\s*0*{position[1:]}\b", words, re.I) for position in positions)
                what = f"paper position{'s' if len(items) > 1 else ''} {', '.join(items)}"
            else:
                items = selected_columns(raw.get("value"))
                if semantic_only and raw.get("target", "paper") != "paper":
                    raise ProposalRejected("paper_columns needs target='paper'; use dilution.plate_column for the "
                                           "source plate", kind="invalid_value", path="paper_columns")
                if not semantic_only and column_side_unclear(
                        text, recent_paths=grounding.get("recent_paths", ()), config=self._config):
                    words = columns_words(items).replace("paper ", "")
                    raise ProposalRejected(
                        "It is not clear whether that is a paper column or a plate column.", kind="ambiguous",
                        path="paper_columns",
                        question=f"Do you mean paper {words} (where the drops print) or plate {words} (where the "
                                 "dilutions are)?")
                # the column numbers in any written form ("columns 1 3 5", "one three and five", "first and third")
                stated = lambda columns, words: (_columns_stated(columns, words)  # noqa: E731
                                                 or value_stated([columns[0], columns[-1]], words))
                # one column without "only" says where the prints start, not how many (as the text's own column
                # request reads it: columns.paper_column_mentions), so the replicate count stays
                mode = str(raw.get("mode", "anchor" if len(items) == 1 else "exact")).lower()
                if semantic_only and mode not in {"anchor", "exact"}:
                    raise ProposalRejected("paper placement mode must be 'anchor' or 'exact'",
                                           kind="invalid_value", path="paper_columns")
                anchor = (len(items) == 1 and mode == "anchor") if semantic_only else \
                    (len(items) == 1 and (items[0],) not in paper_column_request(text).exact)
                changes, note = expand_paper_columns(items, config, anchor=anchor)
                what = columns_phrase(items)
        except SelectionError as exc:
            if path == "print_map" and exc.fix is not None:
                # possible but not specified (two wells, ten prints): offer the even split as a yes/no question
                raise ProposalRejected(f"{exc}", kind="print_split", question=exc.question,
                                       fix_changes=[{"path": "print_map", "value": exc.fix,
                                                     "evidence": str(raw.get("evidence") or "")}]) from exc
            if path in {"print_map", "drops_at"}:
                raise ProposalRejected(f"{exc}", kind="print_map", question=exc.question) from exc
            raise ProposalRejected(f"{exc} Nothing was changed.", kind="paper_layout" if path == "paper_columns"
                                   else "selection", question=exc.question) from exc
        if semantic_only or stated(items, text):
            return changes, note, (True, "")
        if history and stated(items, history):
            return changes, note, (False, CARRIED_OVER)
        if path == "paper_columns" and self._earlier_columns(items):
            return changes, note, (False, EARLIER_REVISION)   # the columns an earlier revision printed
        if path == "print_map":
            known = set(grounding.get("known_sources", ()))
            if known and all(entry["source"] in known for entry in items):
                # the wells of the proposal being revised or of the current plan, used again: not new values
                return changes, note, (False, CARRIED_OVER)
            raise ProposalRejected(f"You did not name {what} as the well to print from, so I did not use "
                                   f"{'them' if len(items) > 1 else 'it'}. Nothing was changed.", kind="needs_value",
                                   question="Which plate well holds the sample to print?")
        noun = {"rows": "plate rows", "paper_rows": "paper rows"}.get(path, "paper columns")
        raise ProposalRejected(f"You did not name {what}, so I did not choose them. Nothing was changed.",
                               kind="needs_value", question=f"Which {noun} should this run use?")

    @staticmethod
    def _show(canonical: str, value: Any) -> str:
        from src.agents.dye_demo.render import format_value

        return format_value(canonical, value)

    def _value_for(self, canonical: str, raw: dict[str, Any], current: Any, normalize) -> Any:
        op = str(raw.get("op") or "set").strip().lower()
        if op == "none" and canonical == "print.replicates":
            return 1
        if op == "set":
            if "value" not in raw:
                raise FieldError("no value was given")
            if raw["value"] is None and canonical in _NULLABLE:
                return None
            return normalize(raw["value"])
        if current is None:
            raise FieldError("it has no current value to work from")
        if op in {"scale", "multiply"}:
            factor = float(raw.get("factor"))
            if factor <= 0:
                raise FieldError("the scale factor must be positive")
            if isinstance(current, list):
                return normalize([_tidy(item * factor) for item in current])
            return normalize(_tidy(float(current) * factor))
        if op == "add":
            amount = float(raw.get("amount"))
            if isinstance(current, list):
                raise FieldError("cannot add to a list of values")
            return normalize(_tidy(float(current) + amount))
        if op == "scale_each":
            if canonical != "dilution.factors":
                raise FieldError("scale_each only applies to the dilution factors")
            factor = float(raw.get("factor"))
            if factor <= 0:
                raise FieldError("the scale factor must be positive")
            return normalize([_tidy(item * factor) for item in current])
        if op == "set_count":
            if canonical != "dilution.factors":
                raise FieldError("set_count only applies to the number of dilutions")
            count = int(float(raw.get("count")))
            factors = [float(item) for item in current]
            if count < 1:
                raise FieldError("at least one dilution is needed")
            if count > 8:
                raise FieldError(f"a series has 1 to 8 dilutions (one per row of a single plate column), so {count} "
                                 "dilutions are not possible in one run")
            if count <= len(factors):
                return normalize([_tidy(item) for item in factors[:count]])
            ratios = {round(b / a, 6) for a, b in zip(factors, factors[1:]) if a > 0}
            if len(factors) >= 2 and len(ratios) == 1 and next(iter(ratios)) > 1:
                ratio = next(iter(ratios))
                while len(factors) < count:
                    factors.append(factors[-1] * ratio)
                return normalize([_tidy(item) for item in factors])
            startup = [float(item) for item in (self.snapshots[0]["config"].get("dilution") or {}).get("factors", [])]
            example = (" - for example " + ", ".join(fmt_factor(item) for item in startup[:count])
                       + " (the start of the standard series)") if len(startup) >= count else ""
            raise FieldError(f"I can't choose the extra dilution factors for you; say all {count} factors{example}")
        raise FieldError(f"unknown change operation {op!r}")

    def _verify(self, canonical: str, label: str, value: Any, raw: dict[str, Any], request: str, final_text: str,
                superseded: str, before: dict[str, Any], *, context: str = "", history: str = "") -> tuple[bool, str]:
        """Refuse strict values the scientist did not state; flag softer ones and fields never mentioned.

        A field the request never mentions is flagged first, whatever its value: asking "where should the
        paper go?" about a change the scientist never asked for would be misleading. Switching a whole step on or
        off is never just flagged: it is refused unless the words ask for it.

        `history` is the scientist's own earlier words in this conversation. A setting named there resolves a reference
        ("how many drops?" ... "make it 3"); a value or step direction found only there is accepted as carried over
        ("same thing but columns 4-6") and shown for checking.
        """
        carried = False
        mentioned = f"{request}\n{context}" if context else request
        if canonical in {"dilution.plate_column", "print.paper_start_column"} and column_side_unclear(
                request, recent_paths=getattr(self, "_grounding", {}).get("recent_paths", ()), config=before):
            # "Use column 3.": the words do not say plate or paper, and nothing earlier settles it
            raise ProposalRejected(
                "It is not clear whether that is a paper column or a plate column.", kind="ambiguous",
                question=f"Do you mean paper column {value} (where the drops print) or plate column {value} (where "
                         "the dilutions are)?")
        hint = _FIELD_HINTS.get(canonical)
        if hint and canonical not in _STEP_WORDING and not re.search(hint, mentioned, re.I) \
                and not (history and re.search(hint, history, re.I)):
            return False, f"you did not mention the {label.lower()}"
        # a step switched on or off is checked by what the words ask for that step (below), not by keywords
        if canonical in _STEP_WORDING and not self._step_stated(canonical, value, mentioned) \
                and not self._step_quoted(canonical, value, str(raw.get("evidence") or ""), request):
            # A whole step switched on or off that the words do not ask for ("Print the first three rows onto paper
            # columns 10 to 12" read as "skip the dilutions"): shown for checking, never silently accepted
            if history and self._step_stated(canonical, value, history):
                return False, CARRIED_OVER
            return False, _STEP_CONCERNS[(canonical, bool(value))]
        if canonical in _SLOT_PATHS:
            # Which labware: the model resolves the words; a reference with no referent (or two) is a question.
            role = canonical.split(".")[1]
            grounding = getattr(self, "_grounding", {})
            moves = grounding.get("moves", {})
            claimed = {other for other, (_, evidence) in moves.items()
                       if other != role and other in labware_cues(evidence)}
            # labware the scientist asked to keep is not what "the other sample holder" moves
            claimed |= {path.split(".")[1] for path in grounding.get("kept", ()) if path.startswith("deck.")}
            # The scientist's own words only: `context` may hold a replaced proposal's summary, which names labware the
            # scientist did not name here. What was discussed recently counts through `referents` ("it" = that one).
            status, question = referent(role, str(raw.get("evidence") or ""), request,
                                        referents=grounding.get("referents", ()), claimed=claimed)
            if status == "ambiguous" and self._swap_supports(role, value, before, mentioned):
                # "swap them: plate to 7, rack to 4": the rack that goes where the plate was is the one in slot 7
                status = "supported"
            if status == "unverified":
                return False, question
            if status != "supported":
                where = "OFF DECK" if is_off_deck(value) else f"slot {value}"
                hint = ""
                # "Put the dilutions in slot 3" names no labware at all: perhaps a column was meant. (When the words name
                # labware that is ambiguous - "the rack" - the question is only which one.)
                if not is_off_deck(value) and question == "Which labware do you mean?" and re.search(
                        r"\bdilut|\bprint|\bcolumns?\b|\brows?\b|\bwells?\b|\bdrops?\b", mentioned, re.I):
                    hint = (f' Deck slots hold labware. If you meant a column, say "plate column {value}" (where the '
                            f'dilutions are made) or "paper column {value}" (where the drops print).')
                raise ProposalRejected(
                    f"I am not sure which labware should go to {where}.{hint}", kind="ambiguous",
                    question=(f"{question} (It would go to {where}.)" if question != "Which labware do you mean?"
                              else f"Which labware should go to {where}?"))
        try:
            verified, concern = self._verify_value(canonical, label, value, raw, request, final_text, superseded, before)
        except ProposalRejected as exc:
            if exc.kind == "needs_value" and self._earlier_value(canonical, value):
                return False, EARLIER_REVISION
            if not history or exc.kind != "needs_value":
                raise
            try:
                self._verify_value(canonical, label, value, raw, history, history, "", before)
            except ProposalRejected:
                raise exc from None
            return False, CARRIED_OVER
        if not verified and concern in _GROUNDING_CONCERNS and self._earlier_value(canonical, value):
            return False, EARLIER_REVISION
        if not verified and history and concern in _GROUNDING_CONCERNS \
                and self._verify_value(canonical, label, value, raw, history, history, "", before)[0]:
            return False, CARRIED_OVER
        if carried:
            return False, CARRIED_OVER
        return verified, concern

    @staticmethod
    def _explicit_contradiction(canonical: str, value: Any, raw: dict[str, Any], request: str) -> None:
        """Reject a direct conflict with an explicit scientist value, without requiring lexical support.

        The absence of a token is never a reason to reject a structured LLM change.
        """
        if canonical == "print.replicates" and re.search(r"\bno\s+replicates?\b", request, re.I):
            return  # the canonical one total print is what "no repeats" means
        if canonical == "print.replicates":
            match = re.search(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
                              r"(?:total\s+)?replicates?\b", request, re.I)
            if match:
                stated = numbers_in(match.group(1))
                if len(stated) == 1 and float(value) not in stated:
                    raise ProposalRejected(f"You asked for {match.group(1)} replicates, but the proposal used "
                                           f"{value}.", kind="contradiction")
        if canonical in _WELL_PATHS:
            evidence = str(raw.get("evidence") or "")
            if evidence and quoted_in(evidence, request):
                names = well_tokens(evidence)
                if len(names) == 1 and str(value).upper() not in names:
                    raise ProposalRejected(f"You named {next(iter(names))}, but the proposal used {value}.",
                                           kind="contradiction")
        if canonical == "print.source_map" and value and re.search(r"\b(?:on|onto|at)\b", request, re.I):
            named = well_tokens(request)
            sources = {entry["source"] for entry in value}
            destinations = {position for entry in value for position in entry["positions"]}
            expected = named - sources
            if expected and not expected <= destinations:
                raise ProposalRejected("The paper positions in the proposal differ from the positions you named.",
                                       kind="contradiction")
        if canonical in _VOLUME_PATHS and value is not None:
            volumes, _ = stated_volumes(request)
            stated = {round(item[0], 6) for item in volumes}
            proposed = value if isinstance(value, list) else [value]
            if len(stated) == 1 and len(proposed) == 1 and round(float(proposed[0]), 6) not in stated:
                raise ProposalRejected(f"You stated {next(iter(stated)):g} µL, but the proposal used "
                                       f"{float(proposed[0]):g} µL.", kind="contradiction")

    def _earlier_value(self, canonical: str, value: Any) -> bool:
        """A value an earlier approved change of this session set for this field: restoring it ("the factors I asked
        for at the very beginning") is state, not an invented value - shown under its own heading for checking. The
        startup values do not count: an old plan the model brings back unasked stays "not in what you typed".
        2026-09-27: turns that far back are outside the words grounding reads."""
        return any(change["path"] == canonical and _same(change["after"], value)
                   for record in self.history for change in record["changes"])

    def _earlier_columns(self, columns: list[int]) -> bool:
        """Paper columns printed after an earlier approved change of this session ("the paper columns we had before
        I changed them"); not the startup layout and not the current one."""
        return any(paper_columns_printed(snapshot["config"]) == list(columns) for snapshot in self.snapshots[1:-1])

    @staticmethod
    def _step_stated(canonical: str, value: Any, text: str) -> bool:
        """Whether the words switch this step in this direction. "Don't move the paper print plate" or "once the
        dilutions are made" never switch a step off."""
        turn_on, turn_off = _STEP_WORDING[canonical]
        wording = TEMPORAL_FUTURE.sub(" ", text)
        wording = re.sub(r"(?i)\b(?:paper\s+)?print(?:ing)?\s+plates?\b", " ", wording)
        if value is False:
            return bool(turn_off.search(wording))
        return bool(turn_on.search(turn_off.sub(" ", wording)))

    @staticmethod
    def _step_quoted(canonical: str, value: Any, evidence: str, request: str) -> bool:
        """Turning a step ON quoted from the scientist's own words, whatever the spelling ("do mkae dilutions agian"):
        the quote names the step and does not negate it. Turning a step off always needs the words above."""
        if value is not True or not quoted_in(evidence, request):
            return False
        word = r"\bdilut" if canonical == "dilution.enabled" else r"\bprint"
        return bool(re.search(word, evidence, re.I)) and not _STEP_WORDING[canonical][1].search(evidence) \
            and not re.search(r"\b(?:don'?t|do\s+not|dont|no|not|never|without|skip)\b", evidence, re.I)

    def _verify_value(self, canonical: str, label: str, value: Any, raw: dict[str, Any], request: str,
                      final_text: str, superseded: str, before: dict[str, Any]) -> tuple[bool, str]:
        op = str(raw.get("op") or "set").strip().lower()
        if op == "none" and canonical == "print.replicates":
            return True, ""
        if op != "set":
            parameter = raw.get("factor", raw.get("amount", raw.get("count")))
            magnitude = abs(float(parameter)) if parameter is not None else None
            if magnitude is None or not (numbers_mentioned(magnitude, final_text)
                                         or value_stated(magnitude, final_text)):
                raise ProposalRejected(f"I could not find how much to change the {label.lower()} by in what you said, "
                                       "so nothing was changed.", kind="needs_value",
                                       question=f"By how much should the {label.lower()} change?")
            return True, ""
        if canonical in _SLOT_PATHS:
            role = canonical.split(".")[1]
            if is_off_deck(value):
                stated = bool(re.search(r"\boff\b|\bremov\w*\b|\bout\s+of\b", final_text, re.I))
            else:
                # the slot number in any written form ("slot 5", "at 5", "-> 5", "fifth position", "five"), or a
                # swap that puts this labware where another one moving in the same request was. The model's quote is
                # read without the words a self-correction replaced, as the request is ("Put the plate in 2, actually
                # make that 6.": "make that 6." is the quote; the 2 is not evidence)
                evidence = str(raw.get("evidence") or "")
                if superseded:
                    evidence = evidence.replace(superseded, " ")
                stated = (int(value) in slot_numbers(final_text)
                          or slot_value_supported(value, evidence, final_text)
                          or self._swap_supports(role, value, before, final_text))
            if not stated:
                if role == "tuberack" and not steps_enabled(before)[0] and "tuberack" not in named_labware(final_text):
                    return True, ""
                self._corrected(value, superseded, label)
                free = ", ".join(str(slot) for slot in free_slots(before)) or "none"
                raise ProposalRejected(
                    f"You did not say where the {LABWARE_NAMES[role]} should go, so I did not choose a slot for it.",
                    kind="needs_value",
                    question=f"Where should the {LABWARE_NAMES[role]} go? Free slots: {free}, or OFF DECK.")
            return True, ""
        if canonical in _WELL_PATHS:
            if value not in well_tokens(final_text):
                near = conflicting_well_reference(str(value), final_text)
                if near:
                    raise ProposalRejected(f"You typed {near}, but the proposal used {value} for the {label.lower()}. "
                                           "Nothing was changed.", kind="well_mismatch",
                                           question=f"Should the {label.lower()} be {near}?", yes_text=near)
                self._corrected(value, superseded, label)
                numbered = _NUMBERED_VIAL.search(final_text)
                if canonical != "tips.start_tip" and numbered:
                    # "vial 5": no vial has that name, and "the fifth vial" depends on the order counted - never guessed
                    material = material_label(before, "sample" if canonical == "materials.sample.vial" else "solvent")
                    raise ProposalRejected(
                        f'You said "{numbered.group(0)}". Vials are named A1-B4 (A1-A4 and B1-B4), so I did not pick one '
                        f"for the {label.lower()}.", kind="needs_value",
                        question=f"Which vial holds the {material} (A1-B4)?")
                raise ProposalRejected(f"You did not name the {label.lower()}, so I did not pick one.",
                                       kind="needs_value", question=f"Which {label.lower()} exactly?")
            return True, ""
        if canonical in _VOLUME_PATHS:
            if value is None:
                return True, ""
            values = value if isinstance(value, list) else [value]
            with_unit, without_unit = stated_volumes(final_text)
            for volume in values:
                if any(abs(volume - microlitres) < 1e-6 for microlitres, _, _ in with_unit):
                    continue
                written = [(microlitres, number, unit) for microlitres, number, unit in with_unit
                           if abs(number - volume) < 1e-6]
                if written:
                    microlitres, number, unit = written[0]
                    raise ProposalRejected(
                        f"You said {fmt_num(number)} {unit} (= {fmt_num(microlitres)} µL), but the proposal used "
                        f"{fmt_num(volume)} µL for the {label.lower()}. Nothing was changed.", kind="unit_mismatch")
                if any(abs(volume - number) < 1e-6 for number in without_unit):
                    raise ProposalRejected(
                        f"You gave {fmt_num(volume)} without a unit, so I did not assume one.", kind="missing_unit",
                        question=f"Do you mean {fmt_num(volume)} µL for the {label.lower()}?",
                        yes_text=f"({fmt_num(volume)} µL)")
                self._corrected(volume, superseded, label)
                raise ProposalRejected(f"You did not state the {label.lower()}, so I did not choose one.",
                                       kind="needs_value", question=f"What {label.lower()} do you want, in µL?")
            return True, ""
        if canonical == "dilution.plate_column":
            listed = sorted({int(number) for match in _PLATE_COLUMN_LIST.finditer(final_text)
                             for number in re.findall(r"\d{1,2}", match.group(1) or match.group(2))})
            if len(listed) > 1:
                # one run makes and prints its series from ONE plate column: the model picking one of several named
                # columns would silently drop the others
                raise ProposalRejected(
                    f"You named plate columns {', '.join(map(str, listed))}, but a run uses its dilution series from one "
                    "plate column, so I did not pick one of them.", kind="needs_value",
                    question="Which plate column should this run use? (To print from wells in several plate columns, "
                             "name the wells, for example \"print A1 and A3\".)")
        if canonical in _COUNT_PATHS:
            if canonical == "print.replicates" and (
                    str(raw.get("op") or "").lower() == "none"
                    or raw.get("value") == 0 and numbers_mentioned(0, final_text)
                    # 1 is the floor - each condition printed once - which is what "no replicates" or "remove the
                    # replicates" asks for: stated when the words name the replicates and give no other count
                    or value == 1 and not numbers_in(final_text)
                    and re.search(_FIELD_HINTS["print.replicates"], final_text, re.I)):
                return True, ""
            if canonical in _PAPER_LAYOUT_PATHS:
                gap = gap_conflict(final_text)
                if gap is not None:
                    raise ProposalRejected(gap.message, kind=gap.kind, question=GAP_QUESTION)
                named = paper_column_request(final_text)
                if named.exact or (canonical == "print.paper_start_column" and named.starts):
                    # "Print in paper columns 3 and 4" states the first paper column and, with the drop volumes, the
                    # replicate count. propose() then requires the printed columns to be exactly those columns.
                    return True, ""
            if not (numbers_mentioned(value, final_text) or value_stated(value, final_text)):
                self._corrected(value, superseded, label)
                raise ProposalRejected(f"You did not state the {label.lower()}, so I did not choose one.",
                                       kind="needs_value",
                                       question=_COUNT_QUESTIONS.get(canonical, f"What should the {label.lower()} be?"))
            return True, ""
        if canonical == "dilution.start_row":
            # "row D", "D", or the row in other words ("the second row", "the 3rd row", "row 2", "the top row")
            if not _start_row_stated(str(value), final_text) and not re.search(
                    rf"\brow\s+{value}\b|\b{value}\b", final_text.replace(f"row {str(value).lower()}", f"row {value}")):
                raise ProposalRejected("You did not state the starting row, so I did not choose one.",
                                       kind="needs_value", question="Which plate row (A-H) should the series start at?")
            return True, ""
        if canonical == "dilution.factors":
            if not (numbers_mentioned(value, final_text) or value_stated(value, final_text)):
                return False, "those factors are not in your request"
            return True, ""
        if not evidence_supported(str(raw.get("evidence") or ""), request):
            return False, "not found in your request"
        return True, ""

    @staticmethod
    def _corrected(value: Any, superseded: str, label: str) -> None:
        if not superseded:
            return
        token_hit = (str(value) in well_tokens(superseded)) if isinstance(value, str) else numbers_mentioned(value, superseded)
        if token_hit:
            raise ProposalRejected(f"You corrected yourself, so I will not use {value} for the {label.lower()}. "
                                   "Nothing was changed.", kind="corrected",
                                   question=f"What should the {label.lower()} be?")

    def _check_prerequisites(self, before: dict[str, Any], after: dict[str, Any], physical: dict[str, Any]) -> None:
        """Printing without making dilutions checks for factor conflicts on recorded wells."""
        do_dilution_before, do_print_before = steps_enabled(before)
        do_dilution_after, do_print_after = steps_enabled(after)
        if not do_print_after or do_dilution_after:
            return
        if print_map(after) is not None:
            return          # a print map names its own source wells; there is no dilution series to compare
        record = physical["dilutions_prepared"] if "dilutions_prepared" in physical else self.physical.get(
            "dilutions_prepared")
        if record is None:
            return
        needed = {well.well: well.factor for well in build_plan(after).wells if well.factor > 0}
        recorded = dict(zip(record.get("wells", []), record.get("factors", [])))
        mismatched = [f"{well} ({fmt_factor(needed[well])} planned, {fmt_factor(recorded[well])} recorded)"
                      for well in needed if well in recorded and abs(float(recorded[well]) - float(needed[well])) > 1e-9]
        if mismatched:
            # "Everything is already mixed, just print" after the plan changed: the scientist may be telling me the
            # plate now holds this plan's dilutions. Asked, never assumed: a yes records them for these wells.
            wells = ", ".join(needed)
            factors = ", ".join(fmt_factor(factor) for factor in needed.values())
            raise ProposalRejected(
                "This print-only plan does not match the dilutions recorded as prepared: different factors in "
                + ", ".join(mismatched) + ". Nothing was changed.", kind="prerequisite",
                question=f"Do plate wells {wells} now hold this plan's dilutions ({factors})?")

    def apply(self, proposal: Proposal, *, operator: str) -> dict[str, Any]:
        if proposal.base_revision != self.revision or proposal.before != self._config:
            raise StaleProposal(
                f"that proposal was prepared for revision {proposal.base_revision}, but the experiment is "
                f"now at revision {self.revision}; nothing was applied"
            )
        after = deepcopy(self._config)
        for change in proposal.changes:
            set_path(after, resolve_path(after, change.path), change.after)
        if after != proposal.after:
            raise StaleProposal("that proposal no longer matches the experiment; nothing was applied")
        report = validate(after, printed_positions=() if proposal.physical.get("paper_id") !=
                          self.physical.get("paper_id") and "paper_id" in proposal.physical
                          else self.printed_positions)
        if report.errors:
            raise ProposalRejected("that would not be a valid run:\n- " + "\n- ".join(report.error_messages()),
                                   kind="invalid_plan")
        if fingerprint(lab_owned_view(after)) != self.lab_owned_fingerprint:
            raise ProposalRejected("lab-owned settings would change; nothing was applied", kind="lab_owned")
        self._config = after
        if "paper_id" in proposal.physical and proposal.physical["paper_id"] != self.physical.get("paper_id"):
            self.printed_positions.clear()
        for key, value in proposal.physical.items():
            self.physical[key] = deepcopy(value)
        self.revision += 1
        if proposal.deck_changed:
            self.deck_changed_since_run = True
        record = {
            "revision": self.revision,
            "proposal_id": proposal.id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "operator": operator,
            "source": proposal.source,
            "request": proposal.request,
            "explanation": proposal.explanation,
            "changes": [
                {"path": change.path, "label": field_label(change.path), "before": change.before,
                 "after": change.after, "kind": change.kind, "why": change.why,
                 "verified": change.verified, "concern": change.concern}
                for change in proposal.changes
            ],
            "physical": deepcopy(proposal.physical),
        }
        self.history.append(record)
        self.snapshots.append(self._snapshot())
        return record

    # ── rollback ─────────────────────────────────────────────────────────────

    def rollback_changes(self, target_revision: int, prefix: str | None = None) -> list[dict[str, Any]]:
        """Field changes that restore editable values from a stored snapshot (never from memory)."""
        if not 0 <= target_revision < len(self.snapshots):
            raise ProposalRejected(f"There is no revision {target_revision}; the experiment is at revision "
                                   f"{self.revision}.", kind="no_such_revision")
        target = self.snapshots[target_revision]["config"]
        changes = []
        for canonical in EDITABLE_FIELDS:
            if prefix and not canonical.startswith(prefix):
                continue
            try:
                now = get_path(self._config, resolve_path(self._config, canonical))
                then = get_path(target, resolve_path(target, canonical))
            except FieldError:
                continue
            if _same(now, then) or (then is None and canonical not in _NULLABLE):
                continue
            changes.append({"path": canonical, "value": then, "kind": "dependent",
                            "why": f"restores the value stored at revision {target_revision}"})
        return changes

    # ── runs ─────────────────────────────────────────────────────────────────

    def run_blockers(self) -> list[str]:
        """Physical reasons the current plan must not run again as it is."""
        plan = build_plan(self._config)
        prepared = self.physical.get("dilutions_prepared")
        if plan.do_dilution and prepared:
            overlap = sorted(set(well.well for well in plan.wells) & set(prepared.get("wells", [])))
            if overlap:
                sources = prepared.get("sources") or {}
                how = "; ".join(dict.fromkeys(sources.get(well, prepared.get("source", "reported")) for well in overlap))
                return [f"Plate wells {', '.join(overlap)} already hold dilutions ({how}). Making "
                        "them again would add liquid to full wells. Skip the dilution step, use another plate column, "
                        "or tell me the plate was replaced."]
        present = self.physical.get(SOURCES_PRESENT) or {}
        if plan.do_dilution and present:
            overlap = sorted(set(well.well for well in plan.wells) & set(present))
            if overlap:
                return [f"Plate well{'s' if len(overlap) > 1 else ''} {', '.join(overlap)} already hold"
                        f"{'' if len(overlap) > 1 else 's'} sample you told me about. Making dilutions there would add "
                        "liquid to it. Use another plate column or rows, or tell me the plate was replaced."]
        if plan.do_dilution:
            occupied = {well: volume for well, volume in self.recorded_volumes().items() if volume > 0}
            overlap = sorted(set(well.well for well in plan.wells) & set(occupied))
            if overlap:
                return ["Current plate well(s) " + ", ".join(f"{well} ({occupied[well]:g} µL)" for well in overlap) +
                        " already contain liquid. Making another dilution there would add to it. Choose empty "
                        "wells or record a clean replacement plate."]
        used = set(self.physical.get(TIPS_USED) or ())
        again = [assignment.tip for assignment in plan.tips if assignment.tip in used]
        if again:
            last = max(TIP_ORDER.index(tip) for tip in used)
            unused = TIP_ORDER[last + 1] if last + 1 < len(TIP_ORDER) else None
            where = (f"Set the starting tip to {unused} (the first tip after the used ones), or tell me a fresh tip "
                     "rack is loaded." if unused else "Load a fresh tip rack and tell me it is loaded.")
            several = len(again) > 1
            return [f"{'Tips' if several else 'Tip'} {', '.join(again)} {'were' if several else 'was'} already used by "
                    f"an earlier run of this session, so {'they are' if several else 'it is'} no longer "
                    f"{'fresh tips' if several else 'a fresh tip'} in the rack. " + where]
        drawn = recorded_liquid_errors(self._config, self.recorded_volumes())
        if drawn:
            return [f"Not enough liquid is recorded in the plate: {drawn[0]}."]
        return []

    def recorded_volumes(self) -> dict[str, float]:
        """Latest liquid volume for each well on the current plate, from runs or operator reports."""
        return {well: float(entry["volume_ul"]) for well, entry in (self.physical.get(WELL_VOLUMES) or {}).items()
                if int(entry.get("plate_id", self.physical.get("plate_id", 1))) == self.physical.get("plate_id", 1)}

    def _volumes_after_run(self, plan, run: int) -> dict[str, dict[str, Any]]:
        """The liquid record after a live run of `plan` succeeded: wells it made hold the final volume, and every well
        it printed from lost what the print step drew (starting from the recorded volume when there is one)."""
        known = self.recorded_volumes()
        volumes = deepcopy(self.physical.get(WELL_VOLUMES) or {})
        stamp = {"revision": self.revision, "run": run, "plate_id": self.physical.get("plate_id", 1),
                 "source": "completed live run", "timestamp_utc": datetime.now(timezone.utc).isoformat()}
        if plan.do_dilution:
            for well in plan.wells:
                known[well.well] = plan.total_volume_ul
                volumes[well.well] = {"volume_ul": plan.total_volume_ul, **stamp}
        if plan.do_print:
            for source in plan.print_sources:
                left = known.get(source.well, source.start_ul) - source.draw_ul
                origin = "completed live run" if source.well in known or source.made_here else "estimated from plan"
                volumes[source.well] = {"volume_ul": round(max(0.0, left), 3), **stamp, "source": origin}
        return volumes

    def record_run(self, *, simulate: bool, exit_code: int, printed: Iterable[str],
                   tips_used: list[str], operator: str, prepared: dict[str, Any] | None = None,
                   status: str | None = None, robot: dict[str, Any] | None = None) -> dict[str, Any]:
        """`status` is succeeded, failed, aborted (stopped part-way by the operator) or interrupted before start.
        Only a run that succeeded on the real robot updates what is physically recorded (tips, wells, paper)."""
        record = {
            "run": self.next_run,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "operator": operator,
            "mode": "SIMULATION" if simulate else "LIVE",
            "exit_code": exit_code,
            "status": status or ("succeeded" if exit_code == 0 else "failed"),
            "revision": self.revision,
            "tips_used": list(tips_used),
            "printed_positions": sorted(printed),
            "plate_id": self.physical.get("plate_id", 1),
            "paper_id": self.physical.get("paper_id", 1),
            "tip_rack_id": self.physical.get("tip_rack_id", 1),
        }
        if robot:
            record["robot"] = deepcopy(robot)
        self.runs.append(record)
        if not simulate and exit_code == 0:
            self.printed_positions.update(printed)
            self.deck_changed_since_run = False
            plan = build_plan(self._config)           # the plan this run carried out (the session runs the state)
            self.physical[WELL_VOLUMES] = self._volumes_after_run(plan, record["run"])
            if prepared:
                self.physical["dilutions_prepared"] = merged_prepared(
                    self.physical.get("dilutions_prepared"),
                    dict(prepared, source=f"made by run {record['run']}", plate_id=self.physical.get("plate_id", 1)))
            if tips_used:
                used = set(self.physical.get(TIPS_USED) or ()) | set(tips_used)
                self.physical[TIPS_USED] = sorted(used, key=TIP_ORDER.index)
        return record

    def advance_tip_after_run(self, next_tip: str, run: int, operator: str) -> None:
        """Record the next unused tip as a consequence of a completed live run."""
        before = self._config["tips"]["start_tip"]
        if before == next_tip:
            return
        self._config["tips"]["start_tip"] = next_tip
        self.revision += 1
        self.history.append({"revision": self.revision, "proposal_id": None,
                             "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "operator": operator, "source": "post-run-automatic",
                             "request": f"run {run} used tips", "explanation": "next unused tip after a live run",
                             "changes": [{"path": "tips.start_tip", "label": field_label("tips.start_tip"),
                                          "before": before, "after": next_tip, "kind": "physical",
                                          "why": f"run {run} used the earlier tip", "verified": True,
                                          "concern": ""}], "physical": {}})
        self.snapshots.append(self._snapshot())
