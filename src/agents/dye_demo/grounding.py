"""Semantic grounding of proposed changes: SUPPORTED, AMBIGUOUS or CONTRADICTED.

Gemini reads the scientist's words and returns structured changes. Nothing here decides what a sentence means; these
checks only ask three structural questions about what the model returned:

  SUPPORTED     a value the model used is backed by the scientist's words in any written form (5, five, fifth, 5th,
                "-> 5"), or follows from the plan itself (a swap puts each labware where the other one was); a labware
                reference has exactly one plausible referent.
  AMBIGUOUS     the words give no value, or a reference fits more than one labware ("put it at 5" with several
                candidates, "the sample holder" when both the plate and the vial rack hold samples). The session keeps
                the rest of the request and asks one focused question.
  CONTRADICTED  the scientist asked to keep what the change touches ("don't change the paper location"). The change is
                dropped; the rest of the request goes ahead.

Physical and protocol validity (real slots, wells, volumes, collisions, tips) is checked separately in validation.py
and is never relaxed here.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from src.agents.dye_demo.model import LABWARE_NAMES

# ── numbers in any written form ─────────────────────────────────────────────────

_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "hundred": 100, "a hundred": 100, "one hundred": 100,
}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9,
    "tenth": 10, "eleventh": 11, "twelfth": 12,
}
_DIGITS = re.compile(r"(?<![A-Za-z])[-+]?(\d+(?:\.\d+)?)(?:st|nd|rd|th)?\b", re.I)
_NUMBER_WORD = re.compile(r"\b(" + "|".join(sorted(map(re.escape, {**_WORDS, **_ORDINALS}), key=len, reverse=True))
                          + r")\b", re.I)


def number_values(text: str) -> set[float]:
    """Every number a text states, whatever form it is written in: 5, 5.0, five, fifth, 5th, "-> 5", "@5"."""
    values = {float(match.group(1)) for match in _DIGITS.finditer(text)}
    for match in _NUMBER_WORD.finditer(text):
        word = match.group(1).lower()
        values.add(float(_WORDS.get(word, _ORDINALS.get(word, 0))))
    return values


def value_stated(value: Any, text: str) -> bool:
    """A number (or every number of a list) appears in the text in some written form."""
    stated = number_values(text)
    items = value if isinstance(value, (list, tuple)) else [value]
    try:
        return all(float(item) in stated for item in items)
    except (TypeError, ValueError):
        return False


_NUMBER_TOKEN = (r"(?:\d{1,2}(?:st|nd|rd|th)?|" + "|".join(sorted(map(re.escape, {**_WORDS, **_ORDINALS}), key=len,
                                                               reverse=True)) + r")")
_LOCATION_NOUN = r"(?:slot|position|place|location|spot|area|bay|deck\s+position|deck\s+slot|deck\s+location|pos)"
_NOT_A_SLOT = (r"(?!\s*(?:µl|ul|ml|mm|x\b|×|%|drops?|droplets?|times|replicates?|columns?|rows?|wells?|tips?|prints?|"
               r"dilutions?|-?\s*well))")
_LABWARE_NOUN = r"(?:plate|microplate|paper|sheet|rack|tip\s*rack|tiprack|tube\s*rack|tray|box|holder)s?"
_SLOT_PHRASES = (
    # "rack 10", "plate #5", "paper 8": a labware name followed by its slot (the fragment people type)
    re.compile(rf"\b{_LABWARE_NOUN}\s*(?:#|no\.?|number)?\s*(?P<n>\d{{1,2}})\b{_NOT_A_SLOT}", re.I),
    # "slot 5", "position eleven", "location number 5", "deck position 11"
    re.compile(rf"\b{_LOCATION_NOUN}\s*(?:#|no\.?|number)?\s*(?P<n>{_NUMBER_TOKEN})\b", re.I),
    # "the fifth place on the deck", "the 5th position", "fifth deck position"
    re.compile(rf"\b(?P<n>{_NUMBER_TOKEN})\s+(?:deck\s+)?{_LOCATION_NOUN}\b", re.I),
    # "to 5", "at five", "in 8", "into 3", "over at 9", "-> 10", "→ 10", "@ 4", "= 4", ": 4"
    re.compile(rf"(?:\b(?:to|at|in|into|onto|on)\b|->|→|=>|@|=|:)\s*(?:the\s+)?(?P<n>{_NUMBER_TOKEN})\b"
               r"(?!\s*(?:µl|ul|ml|mm|x\b|×|%|drops?|droplets?|times|replicates?|columns?|rows?|wells?|tips?|prints?))",
               re.I),
)
_LOCATION_WORDS = re.compile(rf"\b{_LOCATION_NOUN}\b|->|→|@|\b(?:to|at|in|into)\b", re.I)


def slot_value_supported(value: Any, evidence: str, text: str) -> bool:
    """The slot number is written as a location in any form: "slot 5", "at 5", "to five", "-> 5", "position eleven",
    "the fifth place on the deck". A bare count elsewhere ("3 drops") never supports slot 3."""
    try:
        wanted = float(int(value))
    except (TypeError, ValueError):
        return False
    quoted = quoted_in(evidence, text)
    for source in ((evidence,) if quoted else ()) + (text,):
        for pattern in _SLOT_PHRASES:
            for match in pattern.finditer(source):
                if wanted in number_values(match.group("n")):
                    return True
    # the model's own quote of the scientist ("move teh sample rack to locaiton 6"): its number is the slot, however the
    # place word is spelled - unless the number is a count, a volume or a column/row/well/tip/vial number. A number
    # that ends the sentence ("actually make that 6.") is still a whole number; only "6.5" is not.
    if quoted:
        for match in re.finditer(r"(?<![\w.])(\d{1,2})(?!\w|\.\d)", evidence):
            before = evidence[:match.start()]
            if re.search(r"\b(?:columns?|rows?|wells?|tips?|vials?|col|#)\s*$", before, re.I):
                continue
            if re.match(_NOT_A_SLOT, evidence[match.end():]) is None:
                continue
            if float(match.group(1)) == wanted:
                return True
    return False


def normalized(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s>-]", " ", str(text).lower()).split())


def quoted_in(evidence: str, text: str) -> bool:
    """The model's evidence really is (close to) something the scientist wrote."""
    evidence, text = normalized(evidence), normalized(text)
    return bool(evidence) and len(evidence) >= 3 and evidence in text


# ── labware references ──────────────────────────────────────────────────────────
# Words that point at one labware in THIS lab. They are only used to see whether a reference has a referent; the
# model resolves any phrasing it understands, and an unrecognised description leads to a question, never a refusal.

LABWARE_CUES: dict[str, re.Pattern] = {
    "tiprack": re.compile(r"\btip\s*(?:rack|box|boxes|racks)\b|\btipracks?\b|\btips\b", re.I),
    "paper": re.compile(
        r"\bpaper\b(?!\s+(?:columns?|positions?|rows?))|\bsubstrate\b|\bsheet\b|\bprint(?:ing)?\s+surface\b|"
        r"\bthing\s+(?:we'?re|we\s+are|we|i'?m|i)\s+print(?:ing)?\s+(?:on|onto|to)\b|\bsers\b", re.I),
    "tuberack": re.compile(
        r"\bvials?\b|\bstock\b|\b(?:vial|tube|source|stock)\s+(?:rack|holder|box)\b|\bsample\s+racks?\b|\btube\s*rack\b",
        re.I),
    "plate": re.compile(
        r"(?<!paper\s)(?<!print\s)\bplates?\b|\bmicro\s*plates?\b|\bwell\s*(?:plate|tray)\b|\b96\b|\btrays?\b|"
        r"\b(?:we\s+)?print(?:ing)?\s+from\b|\bthe\s+wells\b", re.I),
}
# "everything", "all the labware": every labware is named
_EVERYTHING = re.compile(r"\beverything\b|\ball\s+(?:the\s+|of\s+the\s+)?labware\b|\ball\s+of\s+(?:it|them)\b", re.I)
# Terms that name more than one labware here: both the 96-well plate and the vial rack hold samples, and a bare "rack"
# is the vial rack or the tip rack.
AMBIGUOUS_TERMS: dict[re.Pattern, set[str]] = {
    re.compile(r"\bsample\s+holders?\b", re.I): {"plate", "tuberack"},
    # the dye stock (vial rack) and the diluted samples (plate) are both "my samples"
    re.compile(r"\bwhere\s+(?:the|my|our)\s+samples?\s+(?:are|is)\b|\bthing\s+(?:holding|with)\s+(?:the|my|our)\s+"
               r"samples\b", re.I): {"plate", "tuberack"},
    re.compile(r"(?<!vial\s)(?<!tube\s)(?<!tip\s)(?<!sample\s)\bholders?\b", re.I): {"plate", "tuberack", "paper"},
    re.compile(r"(?<!vial\s)(?<!tube\s)(?<!tip\s)(?<!tip)(?<!source\s)(?<!stock\s)(?<!sample\s)\bracks?\b", re.I): {
        "tuberack", "tiprack"},
}
_PRONOUN = re.compile(r"\b(?:it|that|this|them|those|these|there|the\s+other\s+one|that\s+one|this\s+one)\b", re.I)
_OTHER = re.compile(r"\b(?:the\s+)?other\s+(?:sample\s+)?(?:holders?|racks?|ones?)\b|\banother\s+(?:holder|rack)\b", re.I)


_BOTH_RACKS = re.compile(r"\b(?:both|two|all)\s+(?:the\s+|of\s+the\s+)?racks\b|\bthe\s+racks\b", re.I)


def labware_cues(text: str) -> set[str]:
    cues = {role for role, pattern in LABWARE_CUES.items() if pattern.search(text)}
    if _BOTH_RACKS.search(text):
        cues |= {"tuberack", "tiprack"}
    if _EVERYTHING.search(text):
        cues |= set(LABWARE_CUES)
    return cues


def ambiguous_candidates(text: str) -> set[str]:
    """The labware an ambiguous term in the text could mean (empty when it has none)."""
    found: set[str] = set()
    for pattern, roles in AMBIGUOUS_TERMS.items():
        if pattern.search(text):
            found |= roles
    return found


def _name(role: str) -> str:
    """'vial rack', 'paper print plate', 'P20 tip rack', '96-well dilution plate' (in the middle of a sentence)."""
    name = LABWARE_NAMES[role]
    return name if name[:1].isdigit() or name.startswith("P20") else name[:1].lower() + name[1:]


def referent(role: str, evidence: str, message: str, *, referents: Iterable[str] = (),
             claimed: Iterable[str] = ()) -> tuple[str, str]:
    """('supported' | 'unverified' | 'ambiguous', why) for a labware move the model attributed to `role`.

    'unverified' means the words name other labware and not this one: the model may have invented the move, so it is
    shown flagged for checking (as any unsupported value is) rather than asked about.

    `evidence` is the scientist's words the model quoted for this change; `referents` the labware discussed most
    recently (for "it" / "that"); `claimed` the labware other moves in the same request clearly name, which removes
    them from the candidates of an ambiguous term ("move the sample holder to 6 and the well tray to 3").
    """
    quoted = quoted_in(evidence, message)
    if role in labware_cues(evidence if quoted else message) or role in labware_cues(message):
        return "supported", ""
    words = evidence if quoted and (labware_cues(evidence) or ambiguous_candidates(evidence)) else message
    cues = labware_cues(words)
    candidates = ambiguous_candidates(words)
    if candidates:
        remaining = candidates - set(claimed)
        if _OTHER.search(words):
            # "Wherever the well plate is, leave it there. Move the other sample holder to ten.": "the other" is not
            # the labware the message names elsewhere
            remaining -= labware_cues(message)
        if remaining == {role}:
            return "supported", ""
        options = [f"the {_name(item)}" for item in sorted(remaining or candidates)]
        names = options[0] if len(options) == 1 else ", ".join(options[:-1]) + " or " + options[-1]
        return "ambiguous", f"Which do you mean: {names}?"
    if cues:
        # the words name other labware than the one the model moved
        return "unverified", f"you did not mention moving the {_name(role)}"
    recent = [item for item in dict.fromkeys(referents) if item in LABWARE_NAMES]
    if len(recent) == 1 and recent[0] == role:
        return "supported", ""                 # "put that in slot 8 instead", right after moving it
    if _PRONOUN.search(words) or not words.strip():
        return "ambiguous", "Which labware do you mean?"
    return "ambiguous", "Which labware do you mean?"


# ── "keep this as it is": changes the scientist ruled out ────────────────────────

_PRESERVE_VERB = r"(?:change|changing|move|moving|touch|touching|modify|modifying|alter|altering|adjust|adjusting|" \
                 r"swap|swapping|shift|shifting|relocate|relocating|mess\s+with|update|updating)"
_PRESERVE = (
    re.compile(rf"\b(?:don'?t|do\s+not|dont|never|no\s+need\s+to|without|not)\s+(?:\w+\s+)?{_PRESERVE_VERB}\b"
               r"\s+(?P<what>[^.;!?]*?)(?=\s*(?:[,.;!?]|\bbut\b|\band\b|\bjust\b|\bonly\b|$))", re.I),
    re.compile(r"\b(?:keep|leave)\s+(?P<what>(?:the|my|our)?\s*[\w\s-]{1,40}?)\s+(?:where\s+(?:it|they)\s+(?:is|are)|"
               r"as\s+(?:it|they)\s+(?:is|are)|alone|the\s+same|as\s+is|unchanged|in\s+place|at\s+\d+|in\s+slot\s+\d+)",
               re.I),
    re.compile(r"\b(?P<what>(?:the|my|our)\s+[\w\s-]{1,40}?)\s+(?:can\s+|should\s+|will\s+|must\s+)?(?:stay|stays|remain|"
               r"remains)\s+(?:where\s+(?:it|they)\s+(?:is|are)|put|the\s+same|as\s+(?:it|they)\s+(?:is|are)|unchanged)",
               re.I),
)


def preserved_paths(text: str, config: dict[str, Any]) -> set[str]:
    """Fields the words say to keep: "don't change the paper location", "leave the plate where it is", "the plate can
    stay where it is". A negated STEP ("don't remake the dilutions") is not a kept field: it asks to skip that step."""
    from src.agents.dye_demo.intent import fields_mentioned

    kept: set[str] = set()
    for pattern in _PRESERVE:
        for match in pattern.finditer(text):
            what = match.group("what")
            if re.search(r"\b(?:except|other\s+than|besides|apart\s+from|but)\b", what, re.I):
                continue            # "don't change anything except the drops" keeps everything BUT the drops
            # "don't move the rack": both racks stay (keeping is the safe reading of an ambiguous name)
            roles = labware_cues(what) | ambiguous_candidates(what)
            kept.update(f"deck.{role}.slot" for role in roles)
            if not roles:
                kept.update(path for path in fields_mentioned(what) if not path.startswith("deck."))
    return kept


# ── "column N" with nothing to say which labware ────────────────────────────────

_PAPER_SIDE = re.compile(r"\b(?:paper|print\w*|spot\w*|deposit\w*|drops?|droplets?|substrate|sers)\b", re.I)
_PLATE_SIDE = re.compile(r"\b(?:plate|dilut\w*|source|wells?|series|samples?\s+(?:are|is)\s+in)\b", re.I)
_BARE_COLUMN = re.compile(r"\bcol(?:umn)?s?\s*#?\s*\d{1,2}\b", re.I)


def column_side_unclear(message: str, *, recent_paths: Iterable[str] = (), config: dict[str, Any] | None = None) -> bool:
    """'Use column 3.': nothing in the words, the conversation or the plan says plate column or paper column. The plan
    settles it when the columns named are the printed paper columns and not the plate column ("I don't want column 3
    anymore" while the plan prints paper column 3 from plate column 11), or the other way round."""
    if not _BARE_COLUMN.search(message) or _PAPER_SIDE.search(message) or _PLATE_SIDE.search(message):
        return False
    recent = set(recent_paths)
    if recent & {"print.paper_start_column", "print.replicates", "dilution.plate_column", "print.source_map"}:
        return False
    if config:
        from src.agents.dye_demo.columns import paper_columns_printed

        named = {int(number) for number in re.findall(r"\bcol(?:umn)?s?\s*#?\s*(\d{1,2})\b", message, re.I)}
        try:
            printed = set(paper_columns_printed(config))
            plate = int(str((config.get("dilution") or {}).get("plate_column", "0")))
        except (TypeError, ValueError):
            return True
        on_paper, on_plate = named & printed, named & {plate}
        if (on_paper and not on_plate) or (on_plate and not on_paper):
            return False
    return True
