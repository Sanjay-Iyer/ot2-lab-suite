"""Deterministic reading of what the scientist typed.

Commands, run go-aheads, yes/no confirmations, questions, and wording that could
point at the wrong physical thing. None of this is left to the LLM: an uncertain
reply is never approval, and an ambiguous location is confirmed before any
interpretation happens.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.agents.dye_demo.model import LABWARE_NAMES, VIAL_NAMES, is_off_deck, material_label, slot_of

TRIGGER = "run"

RUN_VERB = re.compile(r"\b(run|go|start|execute|proceed)\b", re.I)
EDIT_SIGNAL = re.compile(
    r"\d|\b(ul|µl|microlit\w*|slot|column|row|well|vial|tip|drop\w*|dilut\w*|paper|"
    r"plate|mix\w*|volume|replicate\w*|rack)\b",
    re.I,
)
HEDGE = re.compile(
    r"\?|\b(should|could|would|can|shall|may|might|maybe|if|whether|not|don'?t|dont|"
    r"wait|hold|stop|later|before|after|when|why|how|what)\b",
    re.I,
)
UNSURE = re.compile(
    r"\b(i (don'?t|do not) know|i dont know|not sure|no idea|whatever|anything|default|"
    r"example|standard|typical|suggest|recommend|surprise me|you (pick|choose|decide))\b",
    re.I,
)
_INTERROGATIVE = re.compile(
    r"^\s*(why|what|how|when|where|which|who|whose|is|are|was|were|does|do|did|should|"
    r"would|could|can|will|shall|may|might|isn'?t|aren'?t|doesn'?t)\b",
    re.I,
)
# "once the dilutions are made", "as soon as they're ready", "after I've loaded a fresh tip rack": when something will
# happen, not a report that it already has. Past tense ("after the dilutions were made") and "already" stay reports.
TEMPORAL_FUTURE = re.compile(
    r"\b(?:once|as\s+soon\s+as|when|whenever|until|till|by\s+the\s+time|before|after)\s+"
    r"(?![^,.;!?]*\balready\b)"
    r"(?:the|all|my|our|those|these|they|it|each|every|i|we|you|someone|he|she)\b(?:\s+[\w'-]+){0,6}?\s*"
    r"(?:\bis\b|\bare\b|'s\b|'re\b|\bgets?\b|\bwill\s+be\b|'ll\s+be\b|\bhave\s+been\b|\bhas\s+been\b|'ve\b|"
    r"\bhave\b|\bhas\b)[^,.;!?]*",
    re.I,
)
_YES = {"y", "yes", "yes please", "confirm", "confirmed", "apply", "apply it", "yes apply",
        "approve", "approved", "yes confirm"}
_NO = {"n", "no", "cancel", "discard", "reject", "no thanks", "dont apply", "don't apply",
       "do not apply", "no discard"}


_RUN_WORDS = {"run", "go", "start", "execute", "proceed"}
# Every other word of a go-ahead must come from this list. An allowlist, not a blocklist:
# "start over", "go back", "start printing" and "proceed to undo" contain a run verb but
# are not go-aheads, and nothing outside this vocabulary can ever start the robot.
_RUN_FILLER = {"ahead", "it", "now", "the", "protocol", "experiment", "ok", "okay", "yes", "looks",
               "look", "good", "this", "is", "lets", "let's", "let", "us", "great", "please", "alright",
               "fine", "sounds", "then", "so", "perfect", "all", "right", "thanks", "thank", "you"}


def wants_to_run(text: str) -> bool:
    """A short, unhedged go-ahead made only of run words and harmless filler."""
    if "?" in text or HEDGE.search(text):
        return False
    words = re.findall(r"[a-z']+|\d+", text.lower())
    if not 0 < len(words) <= 6 or not any(word in _RUN_WORDS for word in words):
        return False
    return all(word in _RUN_WORDS or word in _RUN_FILLER for word in words)


def parse_confirmation(text: str) -> str | None:
    """'yes', 'no', or None. Only explicit words count: 'sure?', 'I think so' are None."""
    normalized = re.sub(r"[,.!]", " ", text.lower())
    normalized = " ".join(normalized.split())
    if normalized in _YES:
        return "yes"
    if normalized in _NO:
        return "no"
    return None


def looks_like_question(text: str) -> bool:
    return text.strip().endswith("?") or bool(_INTERROGATIVE.match(text))


def parse_ask(text: str) -> str | None:
    """The question after /ask, '' for a bare /ask, None when this is not ask mode."""
    match = re.match(r"^\s*/ask\b(.*)$", text, re.I | re.S)
    return match.group(1).strip() if match else None


# ── evidence: does the request actually say this? ───────────────────────────────

def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9µ]+(?:\.[0-9]+)?", text.lower().replace("×", "x"))


def evidence_supported(evidence: str, request: str) -> bool:
    words = _tokens(evidence)
    if not words:
        return False
    present = set(_tokens(request))
    return sum(word in present for word in words) / len(words) >= 0.6


_WELL_TOKEN = re.compile(r"\b([A-Ha-h])\s?(\d{1,2})\b")


def well_tokens(text: str) -> set[str]:
    return {f"{row.upper()}{int(number)}" for row, number in _WELL_TOKEN.findall(text)}


def conflicting_well_reference(value: str, request: str) -> str | None:
    """A well/tip/vial the request names that is easy to confuse with `value` (A1 vs A10)."""
    tokens = well_tokens(request)
    value = str(value).upper()
    if not tokens or value in tokens:
        return None
    near = sorted(token for token in tokens
                  if token[0] == value[0] and (token.startswith(value) or value.startswith(token)))
    return near[0] if near else None


_NUMBER_WORDS = {
    "once": 1, "one": 1, "single": 1, "twice": 2, "two": 2, "double": 2, "thrice": 3, "three": 3,
    "triple": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "half": 0.5, "halve": 0.5, "quarter": 0.25,
}


def _number_tokens(text: str) -> set[float]:
    numbers = {float(match) for match in re.findall(r"\d+(?:\.\d+)?", text)}
    numbers.update(float(_NUMBER_WORDS[word]) for word in re.findall(r"[a-z]+", text.lower())
                   if word in _NUMBER_WORDS)
    return numbers


def numbers_mentioned(value: Any, request: str) -> bool:
    """Every number in `value` appears in the request (for requested numeric changes)."""
    values = value if isinstance(value, (list, tuple)) else [value]
    numbers = _number_tokens(request)
    wanted = []
    for item in values:
        if isinstance(item, bool):
            return True
        if isinstance(item, (int, float)):
            wanted.append(float(item))
        elif isinstance(item, str) and re.fullmatch(r"\d+(?:\.\d+)?", item.strip()):
            wanted.append(float(item))
    return all(number in numbers for number in wanted)


# ── ambiguous physical wording ──────────────────────────────────────────────────

@dataclass(frozen=True)
class Option:
    key: str
    label: str
    replacement: str


@dataclass(frozen=True)
class Ambiguity:
    start: int
    end: int
    term: str
    question: str
    options: tuple[Option, ...]
    yes_no: bool = False
    auto_note: str = ""          # set when the deck itself resolves the wording

    @property
    def automatic(self) -> bool:
        return bool(self.auto_note)


_QUALIFIERS = {"paper", "plate", "well", "vial", "tip", "deck", "rack", "print"}
_MOVE = re.compile(r"\b(move|put|place|shift|relocate|swap|set|transfer)\b", re.I)
_LABWARE_WORD = re.compile(r"\b(plate|paper|rack|tiprack|tray|labware)\b", re.I)
_LOCATION = re.compile(
    r"\b(?P<noun>spot|place|position|location|hole|space|pos|square|bay|box|area|section)\s*(?:#|no\.?|number)?\s*"
    r"(?P<value>[A-Ha-h]\s?\d{1,2}|\d{1,2})\b",
    re.I,
)
_BOTTLES_OFF = re.compile(r"\b(?:take|put|move|get)\s+(?:the\s+)?(?:bottles|vials|tubes)\s+off\b(?:\s+(?:the\s+)?"
                          r"(?:deck|robot))?", re.I)
_MOVE_IN_NUMBER = re.compile(r"\b(?:in|into)\s+(\d{1,2})\b(?!\s*(?:µl|ul|ml|mm|x|×|%|drops?|times|dilutions?|wells?))",
                             re.I)
_LABWARE_NUMBER = re.compile(r"^\s*(?:the\s+)?(plate|paper(?:\s+print)?(?:\s+plate)?|(?:vial\s+|tip\s+)?rack|"
                             r"dilution\s+plate|tip\s*rack)\s+(\d{1,2})\s*[.!]*\s*$", re.I)
_REAGENT_ALIAS = re.compile(r"\b(cv|crystal\s+violet|gentian\s+violet|methyl\s+violet)\b", re.I)
_NAMING = re.compile(r"\b(?:call|called|name|named|label|labell?ed|rename|renamed)\b", re.I)
_CONTAINER = re.compile(r"\b(tray|bottle|tube|jar|container|beaker|reservoir|trough)s?\b", re.I)
_HOLE = re.compile(r"\bholes?\b", re.I)
_COLUMN = re.compile(r"\bcolumn\s+(\d{1,2})\b", re.I)
_MOVE_TO_NUMBER = re.compile(r"\bto\s+(\d{1,2})\b(?!\s*(?:µl|ul|mm|x|×|%|drops?|times))", re.I)
_PLATE = re.compile(r"\bplate\b", re.I)
_DILUTION_NUMBER = re.compile(r"^\s*(?:the\s+)?dilution\s+(?:number\s+|no\.?\s*|#\s*)?(\d{1,2})\s*[.!]*\s*$", re.I)
_VIAL_NUMBER = re.compile(r"\bvial\s+(?:number\s+|no\.?\s*|#\s*)?(\d{1,2})\b(?!\s*(?:µl|ul|ml|%))", re.I)
_SETTING_WORD = re.compile(r"\b(?:volume|drops?|droplets?|dilutions?|factors?|replicates?|mix(?:es|ing)?|reps|"
                           r"columns?|rows?|tips?|vials?|µL|uL|mL|policy)\b", re.I)
_SLOT_REF = re.compile(r"\b(?:from|in|at)\s+(?:deck\s+)?(?:slot\s+)?(\d{1,2})\b", re.I)
_PAPER_SIDE = re.compile(r"\b(print\w*|paper|drops?|droplets?|deposit\w*)\b", re.I)
_PLATE_SIDE = re.compile(r"\b(dilut\w*|plate|wells?|series)\b", re.I)


def _previous_word(text: str, index: int) -> str:
    words = re.findall(r"[A-Za-z0-9\-]+", text[:index])
    return words[-1].lower() if words else ""


def _numbered(*options: tuple[str, str]) -> tuple[Option, ...]:
    return tuple(Option(str(number), label, replacement)
                 for number, (label, replacement) in enumerate(options, start=1))


def find_ambiguities(text: str, config: dict[str, Any]) -> list[Ambiguity]:
    """Materially ambiguous location wording, in the order it appears."""
    found: list[Ambiguity] = []
    move_context = bool(_MOVE.search(text) or _LABWARE_WORD.search(text))

    bottles = _BOTTLES_OFF.search(text)
    if bottles:
        found.append(Ambiguity(bottles.start(), bottles.end(), bottles.group(0),
                               f'You said "{bottles.group(0)}". Did you mean take the VIAL RACK off the deck '
                               "(OFF DECK)?",
                               (Option("yes", "the vial rack, OFF DECK", "take the vial rack off the deck"),),
                               yes_no=True))

    dilution = _DILUTION_NUMBER.match(text)
    if dilution and 1 <= int(dilution.group(1)) <= 8:
        count = int(dilution.group(1))
        found.append(Ambiguity(dilution.start(), dilution.end(), dilution.group(0).strip(),
                               f'You said "{dilution.group(0).strip()}". Do you mean use {count} dilution'
                               f"{'s' if count != 1 else ''} in the series? (One dilution on its own cannot be "
                               "chosen or printed separately.)",
                               (Option("yes", f"{count} dilutions", f"use {count} dilutions"),), yes_no=True))

    for match in _VIAL_NUMBER.finditer(text):
        number = int(match.group(1))
        options = []
        if 1 <= number <= 11:
            options.append((f"the VIAL RACK in deck SLOT {number}", f"the vial rack in deck slot {number}"))
        if 1 <= number <= len(VIAL_NAMES):
            options.append((f"vial {VIAL_NAMES[number - 1]} (vial number {number} on the 8-vial rack)",
                            f"vial {VIAL_NAMES[number - 1]}"))
        if options:
            found.append(Ambiguity(match.start(), match.end(), match.group(0),
                                   f'You said "{match.group(0)}". Vials are named A1-B4. Which do you mean?',
                                   _numbered(*options)))

    fragment = _LABWARE_NUMBER.match(text)
    if fragment and 1 <= int(fragment.group(2)) <= 11:
        found.append(Ambiguity(fragment.start(), fragment.end(), fragment.group(0).strip(),
                               f'You said "{fragment.group(0).strip()}". Did you mean put the {fragment.group(1)} in '
                               f"OT-2 deck SLOT {int(fragment.group(2))}?",
                               (Option("yes", f"deck SLOT {int(fragment.group(2))}",
                                       f"move the {fragment.group(1)} to deck slot {int(fragment.group(2))}"),),
                               yes_no=True))

    naming = _NAMING.search(text)       # "Call the dye crystal violet" defines the name; there is nothing to confirm
    for match in _REAGENT_ALIAS.finditer(text):
        labels = " ".join(material_label(config, role).lower() for role in ("sample", "solvent")) if config else ""
        if naming or "violet" in labels or re.search(r"\bcv\b", labels):
            continue
        sample = material_label(config, "sample") if config else "dye"
        found.append(Ambiguity(match.start(), match.end(), match.group(0),
                               f'You said "{match.group(0)}". Is that the {sample} (the sample in this experiment)?',
                               (Option("yes", f"the {sample}", f"the {sample}"),), yes_no=True))

    for match in _LOCATION.finditer(text):
        if _previous_word(text, match.start()) in _QUALIFIERS:
            continue
        term, value = match.group(0), re.sub(r"\s+", "", match.group("value")).upper()
        if value.isdigit():
            number = int(value)
            if move_context and 1 <= number <= 11:
                found.append(Ambiguity(match.start(), match.end(), term,
                                       f'You said "{term}." Did you mean OT-2 deck SLOT {number}?',
                                       (Option("yes", f"deck SLOT {number}", f"deck slot {number}"),),
                                       yes_no=True))
                continue
            options = []
            if number <= 11:
                options.append((f"OT-2 deck SLOT {number}", f"deck slot {number}"))
            if number <= 12:
                options += [(f"PAPER column {number} (where drops print)", f"paper column {number}"),
                            (f"PLATE column {number} (where dilutions are made)", f"plate column {number}")]
            if options:
                found.append(Ambiguity(match.start(), match.end(), term,
                                       f'You said "{term}." Which do you mean?', _numbered(*options)))
            continue
        options = [(f"PAPER position {value}", f"paper position {value}"),
                   (f"PLATE well {value}", f"plate well {value}")]
        if value in VIAL_NAMES:
            options.append((f"VIAL {value} in the vial rack", f"vial {value}"))
        options.append((f"TIP {value} in the tip rack", f"tip {value}"))
        found.append(Ambiguity(match.start(), match.end(), term,
                               f'You said "{term}." Which do you mean?', _numbered(*options)))

    for match in _CONTAINER.finditer(text):
        word = match.group(1).lower()
        if _previous_word(text, match.start()) == "tip":
            continue
        if word == "tray":
            options = _numbered(("the P20 TIP RACK", "tip rack"), ("the VIAL RACK", "vial rack"),
                                ("the PAPER print plate", "paper print plate"),
                                ("the 96-well DILUTION plate", "dilution plate"))
        else:
            options = _numbered(("a VIAL in the 8-vial rack", "vial"),
                                ("a WELL of the 96-well dilution plate", "plate well"))
        found.append(Ambiguity(match.start(), match.end(), match.group(0),
                               f'You said "{match.group(0)}." Which do you mean?', options))

    for match in _HOLE.finditer(text):
        if any(item.start <= match.start() < item.end for item in found):
            continue
        found.append(Ambiguity(match.start(), match.end(), match.group(0),
                               f'You said "{match.group(0)}." Which do you mean?',
                               _numbered(("a WELL of the 96-well dilution plate", "plate well"),
                                         ("a VIAL in the vial rack", "vial"),
                                         ("a PAPER position", "paper position"))))

    paper_side = bool(_PAPER_SIDE.search(text))
    plate_side = bool(_PLATE_SIDE.search(re.sub(r"(?i)paper\s+(print\s+)?plate", "paper", text)))
    for match in _COLUMN.finditer(text):
        number = int(match.group(1))
        if not 1 <= number <= 12:
            continue
        prev = _previous_word(text, match.start())
        if prev in _QUALIFIERS or prev in {"source", "destination", "first", "starting"}:
            continue
        after = text[match.end():match.end() + 25].lower()
        if re.match(r"^\s*(?:on|of|in|for|from|to)\s+(?:the\s+)?(paper|plate|well|vial|tip|deck|rack)\b", after):
            continue
        if paper_side and not plate_side:
            continue
        if plate_side and not paper_side:
            continue
        snippet = text[max(0, match.start() - 25):min(len(text), match.end() + 25)].lower()
        if ("paper" in snippet and "plate" in snippet) or "source" in snippet or "destination" in snippet:
            continue
        found.append(Ambiguity(match.start(), match.end(), match.group(0),
                               f'You said "{match.group(0)}." Which column do you mean?',
                               _numbered((f"PAPER column {number} (where drops print)", f"paper column {number}"),
                                         (f"PLATE column {number} (where dilutions are made)", f"plate column {number}"))))

    if _MOVE.search(text) or fragment:
        # "set the drop volume to 10" names its setting; only labware moves (or no setting at all) get the slot question
        setting_named = bool(_SETTING_WORD.search(text)) and not _LABWARE_WORD.search(text)
        for pattern, word in ((_MOVE_TO_NUMBER, "to"), (_MOVE_IN_NUMBER, "in")):
            if not _MOVE.search(text) or setting_named:
                break
            for match in pattern.finditer(text):
                if _previous_word(text, match.start(1)) in {"slot", "column", "row", "well", "tip", "vial"}:
                    continue
                number = int(match.group(1))
                if 1 <= number <= 11 and not any(item.start <= match.start() < item.end for item in found):
                    found.append(Ambiguity(match.start(), match.end(), match.group(0),
                                           f'You said "{match.group(0)}." Did you mean OT-2 deck SLOT {number}?',
                                           (Option("yes", f"deck SLOT {number}", f"{word} deck slot {number}"),),
                                           yes_no=True))
        found.extend(labware_word_ambiguities(text, config))

    found.sort(key=lambda item: item.start)
    result: list[Ambiguity] = []
    for item in found:
        if not result or item.start >= result[-1].end:
            result.append(item)
    return result


_RACK = re.compile(r"\brack\b", re.I)


def labware_word_ambiguities(text: str, config: dict[str, Any]) -> list[Ambiguity]:
    """A bare 'plate' or 'rack' names two labware: the dilution or paper print plate, the vial or tip rack."""
    return _word_ambiguities(text, config, _PLATE, "plate", ("plate", "paper"),
                             {"paper", "print", "well", "96-well", "dilution", "96"},
                             {"plate": ("the 96-well DILUTION plate", "dilution plate"),
                              "paper": ("the PAPER print plate", "paper print plate")}) \
        + _word_ambiguities(text, config, _RACK, "rack", ("tuberack", "tiprack"),
                            {"vial", "tube", "tip", "tips", "8-vial", "p20", "both", "two"},
                            {"tuberack": ("the VIAL rack", "vial rack"), "tiprack": ("the P20 TIP rack", "tip rack")})


def _word_ambiguities(text: str, config: dict[str, Any], pattern: re.Pattern, word: str, roles: tuple[str, str],
                      qualifiers: set[str], names: dict[str, tuple[str, str]]) -> list[Ambiguity]:
    found = []
    slot_refs = [int(number) for number in _SLOT_REF.findall(text)]
    for match in pattern.finditer(text):
        before = _previous_word(text, match.start())
        after = text[match.end():match.end() + 8].lower()
        if before in qualifiers or after.lstrip().startswith(("well", "column", "row", "position")):
            continue
        if not config:
            continue
        candidates = [role for role in roles if not is_off_deck(slot_of(config, role))]
        resolved = [role for role in candidates if slot_of(config, role) in slot_refs]
        if len(resolved) == 1:
            role = resolved[0]
            found.append(Ambiguity(
                match.start(), match.end(), match.group(0), "",
                (Option("1", LABWARE_NAMES[role], names[role][1]),),
                auto_note=f'I read "{word}" as the {LABWARE_NAMES[role]}, because that is what is in '
                          f"slot {slot_of(config, role)}.",
            ))
            continue
        found.append(Ambiguity(
            match.start(), match.end(), match.group(0),
            f'You said "{word}." Which {word} do you mean?',
            _numbered(*((f"{names[role][0]} ({_where(config, role)})", names[role][1]) for role in roles)),
        ))
    return found


def _where(config: dict[str, Any], role: str) -> str:
    slot = slot_of(config, role)
    return "off deck" if is_off_deck(slot) else f"slot {slot}"


def resolve_answer(ambiguity: Ambiguity, answer: str) -> Option | None | str:
    """The chosen option, 'reject' when the scientist says none of these, or None if unclear."""
    text = " ".join(answer.lower().split()).strip(" .!")
    if ambiguity.yes_no:
        verdict = parse_confirmation(text)
        if verdict == "yes":
            return ambiguity.options[0]
        return "reject" if verdict == "no" else None
    if text in {"none", "neither", "no", "none of these", "cancel"}:
        return "reject"
    for option in ambiguity.options:
        if text == option.key or text == option.replacement.lower():
            return option
    matches = [option for option in ambiguity.options
               if text and text in option.label.lower()]
    return matches[0] if len(matches) == 1 else None


def apply_option(text: str, ambiguity: Ambiguity, option: Option) -> str:
    return text[:ambiguity.start] + option.replacement + text[ambiguity.end:]
