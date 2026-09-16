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
    gap_conflict,
    listed_paper_columns,
    paper_column_request,
    paper_columns_printed,
)
from src.agents.dye_demo.language import (
    TEMPORAL_FUTURE,
    conflicting_well_reference,
    evidence_supported,
    numbers_mentioned,
    well_tokens,
)
from src.agents.dye_demo.model import (
    CARRIED_OVER,
    EDITABLE_FIELDS,
    LABWARE_NAMES,
    FieldError,
    canonicalize_path,
    field_label,
    fmt_factor,
    fmt_num,
    get_path,
    is_off_deck,
    lab_owned_reason,
    resolve_path,
    set_path,
    volume_in_microlitres,
)
from src.agents.dye_demo.natural import SelectionError, expand_paper_columns, expand_rows, selected_columns, selected_rows
from src.agents.dye_demo.plan import build_plan, steps_enabled
from src.agents.dye_demo.validation import DeckConflict, Report, deck_conflicts, free_slots, validate

_SLOT_PATHS = {"deck.plate.slot", "deck.paper.slot", "deck.tuberack.slot", "deck.tiprack.slot"}
_WELL_PATHS = {"tips.start_tip", "materials.sample.vial", "materials.solvent.vial"}
_VOLUME_PATHS = {"print.droplet_volume_ul", "dilution.total_volume_ul", "dilution.prepared_volume_ul",
                 "mixing.volume_ul"}
_COUNT_PATHS = {"print.droplets_per_spot", "print.replicates", "mixing.reps", "print.paper_start_column",
                "dilution.plate_column"}
_NULLABLE = {"dilution.prepared_volume_ul", "materials.sample.label", "materials.solvent.label"}
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
            r"(?:already\s+|all\s+)?(?:made|prepared|done|mixed|filled|ready)\b", re.I)),
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
SELECTION_PATHS = frozenset({"rows", "paper_columns"})
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
    nums_in_text = set(re.findall(r"\b[1-8]\b", text))
    if all(str(ROWS.index(r) + 1) in nums_in_text for r in rows):
        return True
    if len(rows) == 1 and str(ROWS.index(rows[0]) + 1) in nums_in_text:
        return True

    if _ROW_PHRASES.search(text):
        return True

    return False


def _columns_stated(columns: list[int], text: str) -> bool:
    """The first and last selected paper column are numbers in a sentence about columns ("columns 1 2 and 3", "4-6")."""
    return bool(re.search(r"\bcol(?:umn)?s?\b", text, re.I)) and numbers_mentioned([columns[0], columns[-1]], text)


_PAPER_LAYOUT_PATHS = {"print.replicates", "print.paper_start_column"}
_COUNT_QUESTIONS = {
    "print.replicates": "How many side-by-side replicate paper columns should each drop volume print?",
    "print.droplets_per_spot": "How many drops should be stacked on each paper position?",
}
# Proposals that restore stored values or book-keep a finished run are not read against the words of a request.
_NO_COLUMN_CHECK_SOURCES = {"rollback", "post-run"}




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
                 question: str = "", yes_text: str = "", fix_changes: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.kind = kind
        self.conflicts = list(conflicts)
        self.before = before
        self.after = after
        self.question = question
        self.yes_text = yes_text
        self.fix_changes = fix_changes


class StaleProposal(RuntimeError):
    """A proposal built against an older revision of the experiment."""


class ExperimentState:
    def __init__(self, config: dict[str, Any], *, printed_positions: Iterable[str] = ()):
        self._config = deepcopy(config)
        self.revision = 0
        self.history: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.printed_positions: set[str] = set(printed_positions)
        self.deck_changed_since_run = False
        self.physical: dict[str, Any] = {"dilutions_prepared": None}
        self.lab_owned_fingerprint = fingerprint(lab_owned_view(self._config))
        self.snapshots: list[dict[str, Any]] = [self._snapshot()]
        self._next_proposal_id = 1

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
                context: str = "", history: str = "", dry_run: bool = False) -> Proposal:
        """`request` is the scientist's own words for this request; values must come from them. `history` is their own
        words earlier in this conversation: a value found only there is accepted as carried over and shown for checking
        ("same thing but columns 4-6" keeps the rows and drops of the request it revises). `context` (the question half
        of a mixed message, "why are we using 8 dilutions, and change it to 4") may only show which field is meant.
        `dry_run` validates without numbering a proposal (an offer the scientist has not accepted yet)."""
        before = self.config
        after = deepcopy(before)
        raw_changes = list(raw_changes)
        restrict = tuple(restrict_paths or ())
        final_text = request.replace(superseded, " ") if superseded else request
        notes = list(notes)
        changes: dict[str, FieldChange] = {}

        def add(raw: dict[str, Any], checked: tuple[bool, str] | None = None) -> None:
            canonical = canonicalize_path(before, raw.get("path", ""))
            reason = lab_owned_reason(canonical)
            if reason:
                raise ProposalRejected(f"{canonical} cannot be changed here: {reason}.", kind="lab_owned")
            label, normalize = EDITABLE_FIELDS[canonical]
            kind = "dependent" if raw.get("kind") == "dependent" else "requested"
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
                if not _same(changes[canonical].after, value):
                    raise ProposalRejected(f"two different values were proposed for the {label.lower()}",
                                           kind="invalid_value")
                return
            if _same(current, value):
                if kind == "requested" and checked is None:
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
                verified, concern = self._verify(canonical, label, value, raw, request, final_text, superseded, before,
                                                 context=context, history=history)
            set_path(after, actual, value)
            changes[canonical] = FieldChange(canonical, current, value, kind, str(raw.get("evidence") or ""),
                                             str(raw.get("why") or ""), verified, concern)

        selections = [raw for raw in raw_changes if str(raw.get("path", "")).strip().lower() in SELECTION_PATHS]
        for raw in raw_changes:
            if not any(raw is selection for selection in selections):
                add(raw)
        for raw in selections:
            # expanded against the plan with this proposal's other changes (a new drop volume list, for example)
            expanded, note, checked = self._expand_selection(raw, after, final_text, history)
            if note not in notes:
                notes.append(note)
            for item in expanded:
                add({"evidence": str(raw.get("evidence") or ""), **item}, checked)
        physical = deepcopy(physical or {})
        if physical.get("dilutions_prepared") in (PREPARED_FROM_PLAN, ASSUMED_FROM_PLAN):
            # "The dilutions are already made ... their factors are 2x, 5x and 10x": record the wells and factors
            # of the plan after this proposal's own changes, not of the plan before them.
            resulting = build_plan(after)
            physical["dilutions_prepared"] = {
                "wells": [well.well for well in resulting.wells], "factors": [well.factor for well in resulting.wells],
                "total_volume_ul": resulting.total_volume_ul,
                "source": "assumed for print-only proposal; confirmed on approval"
                          if physical["dilutions_prepared"] == ASSUMED_FROM_PLAN else "reported by the operator"}
        proposal = Proposal(0 if dry_run else self._next_proposal_id, self.revision, request, tuple(changes.values()),
                            before, after, Report(), explanation, tuple(notes), source, physical, replaces, title, origin)
        # Checked before "already set": "print in paper columns 3 and 4" answered with values that are already set,
        # while the plan prints columns 1-2, is a contradiction, not nothing to do. A partial approval is checked only
        # when the changes the scientist kept move the printed columns.
        if source not in _NO_COLUMN_CHECK_SOURCES and (
                source != "partial-approval" or paper_columns_printed(before) != paper_columns_printed(after)):
            conflict = column_conflict(final_text, after)
            if conflict is not None:
                raise ProposalRejected(f"{conflict.message} Nothing was changed.", kind=conflict.kind,
                                       question=conflict.question, fix_changes=conflict.fix, before=before,
                                       after=after)
        if proposal.empty:
            return proposal
        unmentioned = [change for change in proposal.changes
                       if change.kind == "requested" and change.concern.startswith("you did not mention")]
        if unmentioned and len(unmentioned) == len(proposal.changes) and not proposal.physical:
            # Every remaining change is to a field the request never mentions (typically the model adding
            # something, or the actual request already being set): nothing the scientist asked for is left.
            fields = ", ".join(field_label(change.path).lower() for change in unmentioned)
            raise ProposalRejected(f"your message did not mention the {fields}, so I did not propose changing it; "
                                   "anything you did mention is already set", kind="unmentioned_fields")
        conflicts = deck_conflicts(before, after)
        if conflicts:
            raise ProposalRejected("that deck change collides with labware already on the deck",
                                   kind="deck_conflict", conflicts=conflicts, before=before, after=after)
        self._check_prerequisites(before, after, proposal.physical)
        report = validate(after, printed_positions=self.printed_positions)
        if report.errors:
            raise ProposalRejected("that would not be a valid run:\n- " + "\n- ".join(report.error_messages()),
                                   kind="invalid_plan", before=before, after=after)
        proposal.report = report
        if not dry_run:
            self._next_proposal_id += 1
        return proposal

    def _expand_selection(self, raw: dict[str, Any], config: dict[str, Any], text: str,
                          history: str) -> tuple[list[dict[str, Any]], str, tuple[bool, str]]:
        """A row or paper-column selection as field changes, checked against the scientist's words."""
        path = str(raw.get("path", "")).strip().lower()
        try:
            if path == "rows":
                items: list[Any] = selected_rows(raw.get("value"))
                stated = _rows_stated
                changes, note = expand_rows(items, config)
                what = f"rows {', '.join(items)}" if len(items) > 1 else f"row {items[0]}"
            else:
                items = selected_columns(raw.get("value"))
                stated = _columns_stated
                changes, note = expand_paper_columns(items, config)
                what = columns_phrase(items)
        except SelectionError as exc:
            raise ProposalRejected(f"{exc} Nothing was changed.", kind="paper_layout" if path == "paper_columns"
                                   else "selection", question=exc.question) from exc
        if stated(items, text):
            return changes, note, (True, "")
        if history and stated(items, history):
            return changes, note, (False, CARRIED_OVER)
        raise ProposalRejected(f"You did not name {what}, so I did not choose them. Nothing was changed.",
                               kind="needs_value", question=f"Which {'rows' if path == 'rows' else 'paper columns'} "
                                                            "should this run use?")

    @staticmethod
    def _show(canonical: str, value: Any) -> str:
        from src.agents.dye_demo.render import format_value

        return format_value(canonical, value)

    def _value_for(self, canonical: str, raw: dict[str, Any], current: Any, normalize) -> Any:
        op = str(raw.get("op") or "set").strip().lower()
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
        hint = _FIELD_HINTS.get(canonical)
        if hint and not re.search(hint, mentioned, re.I) and not (history and re.search(hint, history, re.I)):
            return False, f"you did not mention the {label.lower()}"
        if canonical in _SLOT_PATHS:
            role = canonical.split(".")[1]
            named = named_labware(mentioned)
            if role not in named and not (history and role in named_labware(history)):
                if role == "tuberack" and not steps_enabled(before)[0]:
                    return False, f"you did not mention moving the {LABWARE_NAMES[role]}"
                if named:
                    return False, f"you did not mention moving the {LABWARE_NAMES[role]}"
                where = "OFF DECK" if is_off_deck(value) else f"slot {value}"
                message = f"You did not name the {LABWARE_NAMES[role]}, so I did not move it to {where}."
                if not is_off_deck(value) and re.search(r"\bdilut|\bprint|\bcolumns?\b|\brows?\b|\bwells?\b|\bdrops?\b",
                                                        mentioned, re.I):
                    message += (f' Deck slots hold labware. If you meant a column, say "plate column {value}" (where the '
                                f'dilutions are made) or "paper column {value}" (where the drops print).')
                raise ProposalRejected(
                    message,
                    kind="labware_not_stated",
                    question=f'Which labware should go to {where}? For example: "move the vial rack to {where}".')
        try:
            verified, concern = self._verify_value(canonical, label, value, raw, request, final_text, superseded, before)
        except ProposalRejected as exc:
            if not history or exc.kind != "needs_value":
                raise
            try:
                self._verify_value(canonical, label, value, raw, history, history, "", before)
            except ProposalRejected:
                raise exc from None
            return False, CARRIED_OVER
        if not verified and history and concern in _GROUNDING_CONCERNS \
                and self._verify_value(canonical, label, value, raw, history, history, "", before)[0]:
            return False, CARRIED_OVER
        if carried:
            return False, CARRIED_OVER
        return verified, concern

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

    def _verify_value(self, canonical: str, label: str, value: Any, raw: dict[str, Any], request: str,
                      final_text: str, superseded: str, before: dict[str, Any]) -> tuple[bool, str]:
        op = str(raw.get("op") or "set").strip().lower()
        if op != "set":
            parameter = raw.get("factor", raw.get("amount", raw.get("count")))
            if parameter is None or not numbers_mentioned(float(parameter), final_text):
                raise ProposalRejected(f"I could not find how much to change the {label.lower()} by in what you said, "
                                       "so nothing was changed.", kind="needs_value",
                                       question=f"By how much should the {label.lower()} change?")
            return True, ""
        if canonical in _SLOT_PATHS:
            role = canonical.split(".")[1]
            if is_off_deck(value):
                stated = bool(re.search(r"\boff\b|\bremov\w*\b|\bout\s+of\b", final_text, re.I))
            else:
                stated = int(value) in slot_numbers(final_text)
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
        if canonical in _COUNT_PATHS:
            if canonical in _PAPER_LAYOUT_PATHS:
                gap = gap_conflict(final_text)
                if gap is not None:
                    raise ProposalRejected(gap.message, kind=gap.kind, question=GAP_QUESTION)
                named = paper_column_request(final_text)
                if named.exact or (canonical == "print.paper_start_column" and named.starts):
                    # "Print in paper columns 3 and 4" states the first paper column and, with the drop volumes, the
                    # replicate count. propose() then requires the printed columns to be exactly those columns.
                    return True, ""
            if not numbers_mentioned(value, final_text):
                self._corrected(value, superseded, label)
                raise ProposalRejected(f"You did not state the {label.lower()}, so I did not choose one.",
                                       kind="needs_value",
                                       question=_COUNT_QUESTIONS.get(canonical, f"What should the {label.lower()} be?"))
            return True, ""
        if canonical == "dilution.start_row":
            if not re.search(rf"\brow\s+{value}\b|\b{value}\b", final_text.replace(f"row {str(value).lower()}",
                                                                                  f"row {value}")):
                raise ProposalRejected("You did not state the starting row, so I did not choose one.",
                                       kind="needs_value", question="Which plate row (A-H) should the series start at?")
            return True, ""
        if canonical == "dilution.factors":
            if not numbers_mentioned(value, final_text):
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
        record = physical["dilutions_prepared"] if "dilutions_prepared" in physical else self.physical.get(
            "dilutions_prepared")
        if record is None:
            return
        needed = {well.well: well.factor for well in build_plan(after).wells}
        recorded = dict(zip(record.get("wells", []), record.get("factors", [])))
        mismatched = [f"{well} ({fmt_factor(needed[well])} planned, {fmt_factor(recorded[well])} recorded)"
                      for well in needed if well in recorded and abs(float(recorded[well]) - float(needed[well])) > 1e-9]
        if mismatched:
            raise ProposalRejected(
                "This print-only plan does not match the dilutions recorded as prepared: different factors in "
                + ", ".join(mismatched) + ". Nothing was changed.", kind="prerequisite")

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
        report = validate(after, printed_positions=self.printed_positions)
        if report.errors:
            raise ProposalRejected("that would not be a valid run:\n- " + "\n- ".join(report.error_messages()),
                                   kind="invalid_plan")
        if fingerprint(lab_owned_view(after)) != self.lab_owned_fingerprint:
            raise ProposalRejected("lab-owned settings would change; nothing was applied", kind="lab_owned")
        self._config = after
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
                return [f"Plate wells {', '.join(overlap)} already hold dilutions ({prepared.get('source')}). Making "
                        "them again would add liquid to full wells. Skip the dilution step, use another plate column, "
                        "or tell me the plate was replaced."]
        return []

    def record_run(self, *, simulate: bool, exit_code: int, printed: Iterable[str],
                   tips_used: list[str], operator: str, prepared: dict[str, Any] | None = None,
                   status: str | None = None, robot: dict[str, Any] | None = None) -> dict[str, Any]:
        """`status` is succeeded, failed, aborted (stopped part-way by the operator) or interrupted before start.
        Only a run that succeeded on the real robot updates what is physically recorded (tips, wells, paper)."""
        record = {
            "run": len(self.runs) + 1,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "operator": operator,
            "mode": "SIMULATION" if simulate else "LIVE",
            "exit_code": exit_code,
            "status": status or ("succeeded" if exit_code == 0 else "failed"),
            "revision": self.revision,
            "tips_used": list(tips_used),
            "printed_positions": sorted(printed),
        }
        if robot:
            record["robot"] = deepcopy(robot)
        self.runs.append(record)
        if not simulate and exit_code == 0:
            self.printed_positions.update(printed)
            self.deck_changed_since_run = False
            if prepared:
                self.physical["dilutions_prepared"] = dict(prepared, source=f"made by run {record['run']}")
        return record
