"""Deterministic turn analysis for the dye demo conversation.

    USER LANGUAGE
        -> normalize_text()   typos, voice transcription, well and unit spellings (all recorded)
        -> analyze_turn()     question / hypothetical / quoted / negated / report / instruction / ...
        -> TurnAnalysis       only `actionable` text may ever produce a proposed change

The LLM still extracts structured changes from instructions and answers questions,
but it never decides whether a turn may change the experiment. A question, a
hypothetical, a quotation, a pasted document, a negation, a physical-state report,
an injection attempt or a third-party approval cannot become a proposal on its own,
whatever the model returns.
"""
from __future__ import annotations

import ast
import operator
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from src.agents.dye_demo.language import TEMPORAL_FUTURE, Ambiguity, Option, parse_confirmation, wants_to_run
from src.agents.dye_demo.model import LABWARE_NAMES, OFF_DECK, field_label, fmt_factor, is_off_deck, slot_of
from src.agents.dye_demo.plan import steps_enabled

# ── normalization ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Normalized:
    original: str
    text: str
    corrections: tuple[tuple[str, str], ...] = ()


_TYPOS = {
    "slto": "slot", "solt": "slot", "sloot": "slot", "slott": "slot", "lsot": "slot",
    "wlel": "well", "welll": "well", "weel": "well",
    "dilutoin": "dilution", "dilutin": "dilution", "diltuion": "dilution", "dillution": "dilution",
    "dilusion": "dilution", "diution": "dilution", "dilutoins": "dilutions", "dilutons": "dilutions",
    "dillutions": "dilutions", "diltuions": "dilutions",
    "blowuot": "blowout", "blowotu": "blowout", "blwout": "blowout", "blowot": "blowout", "bloout": "blowout",
    "pipete": "pipette", "pippette": "pipette", "pipett": "pipette",
    "aspriate": "aspirate", "asprate": "aspirate", "aspirat": "aspirate",
    "dispence": "dispense", "dispnse": "dispense", "dipsense": "dispense",
    "vail": "vial", "vails": "vials", "tpis": "tips", "tipps": "tips",
    "papr": "paper", "paepr": "paper", "papre": "paper",
    "plte": "plate", "palte": "plate", "plaet": "plate",
    "rakc": "rack", "rcak": "rack",
    "colum": "column", "colunm": "column", "collumn": "column", "coloumn": "column", "coulmn": "column",
    "rwo": "row", "replicat": "replicate", "repliacte": "replicate", "replciates": "replicates",
    "droplts": "droplets", "dorps": "drops", "drosp": "drops", "dropps": "drops",
    "voluem": "volume", "volme": "volume", "mxing": "mixing", "dekc": "deck", "dcek": "deck",
}
_VOCABULARY = {"slot", "slots", "well", "wells", "dilution", "dilutions", "blowout", "pipette", "aspirate",
               "dispense", "vial", "vials", "plate", "plates", "paper", "rack", "column", "columns",
               "replicate", "replicates", "droplet", "droplets", "drops", "volume", "mixing", "deck", "tips",
               "factor", "factors", "print", "printing"}
_REAL_WORDS = {"form", "from", "spot", "stop", "post", "tops", "pots", "lost", "slow", "silt", "list"}
_PHRASES = (
    (re.compile(r"\bcrystal\s+violent\b", re.I), "crystal violet"),
    (re.compile(r"\bpaper\s+thing(?:y|ie)?\b", re.I), "paper print plate"),
    (re.compile(r"\btip\s+box(?:es)?\b", re.I), "tip rack"),
    (re.compile(r"\bblow[\s-]+out\b", re.I), "blowout"),
)
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_HOMOPHONES = {"won": 1, "too": 2, "to": 2, "tree": 3, "for": 4, "fore": 4, "ate": 8}


def _transposition(word: str) -> str | None:
    lower = word.lower()
    if len(lower) < 4 or lower in _VOCABULARY or lower in _REAL_WORDS:
        return None
    for target in _VOCABULARY:
        if len(target) != len(lower):
            continue
        diffs = [index for index, (a, b) in enumerate(zip(lower, target)) if a != b]
        if len(diffs) == 2 and diffs[1] == diffs[0] + 1 and lower[diffs[0]] == target[diffs[1]] \
                and lower[diffs[1]] == target[diffs[0]]:
            return target
    return None


def normalize_text(text: str) -> Normalized:
    """Canonical spelling for analysis; every meaning-bearing correction is recorded."""
    original = text
    value = unicodedata.normalize("NFKC", text)
    value = (value.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
             .replace("μ", "µ").replace("\t", " "))
    corrections: list[tuple[str, str]] = []

    def fix_word(match: re.Match) -> str:
        word = match.group(0)
        replacement = _TYPOS.get(word.lower()) or _transposition(word)
        if replacement and replacement != word.lower():
            corrections.append((word, replacement))
            return replacement
        return word

    value = re.sub(r"[A-Za-z][A-Za-z']*", fix_word, value)
    for pattern, replacement in _PHRASES:
        for found in pattern.findall(value):
            if found.lower() != replacement:
                corrections.append((found, replacement))
        value = pattern.sub(replacement, value)

    def voice_to_eight(match: re.Match) -> str:
        corrections.append((match.group(0), "to 8"))
        return "to 8"

    value = re.sub(r"\b(?:to|too|two)\s+(?:ate|eight)\b", voice_to_eight, value, flags=re.I)

    def word_number(match: re.Match) -> str:
        number = _WORD_NUMBERS.get(match.group(2).lower()) or _HOMOPHONES.get(match.group(2).lower())
        replacement = f"{match.group(1)} {number}"
        corrections.append((match.group(0), replacement))
        return replacement

    value = re.sub(r"\b(slot|column|position|spot|square)\s+(one|two|three|four|five|six|seven|eight|nine|"
                   r"ten|eleven|twelve)\b", word_number, value, flags=re.I)
    value = re.sub(r"\b(slot|column)\s+(won|too|to|tree|for|fore|ate)(?=\s*(?:$|[.,!?;]|please\b|now\b|instead\b|"
                   r"for\s+(?:me|us)\b|thanks?\b|thank\s+you\b))", word_number, value, flags=re.I)

    def spoken_well(match: re.Match) -> str:
        number = _WORD_NUMBERS.get(match.group(3).lower(), match.group(3))
        replacement = f"{match.group(1)} {match.group(2).upper()}{int(number)}"
        corrections.append((match.group(0), replacement))
        return replacement

    value = re.sub(r"\b(tip|well|vial|position)\s+([A-Ha-h])\s+(one|two|three|four|five|six|seven|eight|nine|"
                   r"ten|eleven|twelve|\d{1,2})\b", spoken_well, value, flags=re.I)
    value = re.sub(r"\brow\s+([A-Ha-h])\s*,?\s*(?:and\s+)?col(?:umn)?\s+0*(\d{1,2})\b",
                   lambda m: f"{m.group(1).upper()}{int(m.group(2))}", value, flags=re.I)
    value = re.sub(r"\b([A-Ha-h])[-_]0*(\d{1,2})\b", lambda m: f"{m.group(1).upper()}{int(m.group(2))}", value)
    value = re.sub(r"\b([A-Ha-h])0+([1-9]\d?)\b", lambda m: f"{m.group(1).upper()}{int(m.group(2))}", value)
    value = re.sub(r"\b([a-h])(\d{1,2})\b", lambda m: f"{m.group(1).upper()}{m.group(2)}", value)
    value = re.sub(r"(\d)\s*(?:µl|ul|microlit(?:er|re)s?)\b", r"\1 µL", value, flags=re.I)
    value = re.sub(r"(\d)\s*(?:ml|millilit(?:er|re)s?)\b", r"\1 mL", value, flags=re.I)
    value = re.sub(r"[ ]{2,}", " ", value)
    return Normalized(original, value, tuple(corrections))


# ── vocabulary ──────────────────────────────────────────────────────────────────

_VERBS = ("move", "put", "place", "set", "change", "use", "make", "switch", "swap", "take", "remove", "add",
          "increase", "decrease", "raise", "lower", "reduce", "print", "dilute", "skip", "update", "shift",
          "relocate", "replace", "return", "stack", "mix", "start", "begin", "double", "halve", "triple",
          "rename", "name", "call", "label", "load", "bump", "drop", "keep", "leave", "enable", "disable", "include",
          "exclude", "go with", "try", "do", "turn", "cut", "fill", "restore", "undo", "revert", "apply",
          "prepare", "create", "configure", "ensure", "reverse", "assign")
_VERB_ALT = "|".join(sorted((re.escape(verb).replace(r"\ ", r"\s+") for verb in _VERBS), key=len, reverse=True))
_FILLER = (r"(?:(?:ok|okay|so|now|then|and|also|please|actually|alright|right|um|uh|hmm|hey|oh|yes|yeah|no|"
           r"wait|sorry|fine|great|cool|good|well,|just)[,\s]+)*")
_ACTION = re.compile(rf"\b(?:{_VERB_ALT})\b", re.I)
_IMPERATIVE = re.compile(rf"^\s*{_FILLER}(?:please\s+)?(?:just\s+)?(?:{_VERB_ALT})\b", re.I)
_POLITE_REQUEST = re.compile(
    rf"^\s*{_FILLER}(?:(?:can|could|would|will)\s+you\s+(?:please\s+|kindly\s+|just\s+|now\s+)?"
    rf"(?:(?:{_VERB_ALT})\b|mind\s+(?:moving|putting|changing|setting|using|making|switching|taking|removing|"
    rf"adding|increasing|decreasing|skipping|updating|swapping))|(?:can|could|may)\s+(?:i|we)\s+(?:please\s+)?"
    rf"(?:get|have)\s+(?:\d|one|two|three|four|five|six|seven|eight|a\s+(?:new|fresh)))", re.I)
_DESIRE = re.compile(
    rf"^\s*{_FILLER}(?:i|we)\s+(?:(?:would|'d|do)\s+)?(?:want|need|would\s+like|'d\s+like|prefer|wanna|gotta|"
    rf"have\s+to|must|should|will)\b", re.I)
_DESIRE_TO_KNOW = re.compile(
    rf"^\s*{_FILLER}(?:i|we)\s+(?:(?:would|'d|do)\s+)?(?:want|need|would\s+like|'d\s+like|wanna)\s+to\s+(?:make\s+sure|"
    rf"be\s+sure|check|confirm|know|understand|see|verify|double[-\s]?check|ask|learn|clarify|avoid)\b", re.I)
_LETS = re.compile(
    rf"^\s*{_FILLER}let'?s\s+(?!say\b|not\b|see\b|think\b|talk\b|discuss\b|imagine\b|pretend\b|suppose\b)"
    rf"(?:{_VERB_ALT})\b", re.I)
# "Yes, I approve proposal #1." names the proposal it approves.
_NAMED_APPROVAL = re.compile(
    r"^\s*(?:(?:yes|ok(?:ay)?|sure)[,.!]?\s+)?(?:(?:i|we)\s+)?(?:approve|confirm|accept|apply)\s+(?:(?:the\s+)?proposal\s*)?"
    r"(?:number\s+|no\.?\s*)?#\s*(\d+)\s*[.!]*\s*$|^\s*yes[,.!]?\s+(?:to\s+|for\s+)?(?:the\s+)?proposal\s*(?:number\s+)?#?\s*"
    r"(\d+)\s*[.!]*\s*$", re.I)
# "Can you set that up as a proposal for me?" asks for what the agent does anyway; it is not a question to answer.
_PROPOSAL_META = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|so|and|now|then)[,\s]+)*(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+)?"
    r"(?:set\s+(?:that|this|it|these|them)\s+up(?:\s+as\s+(?:a\s+)?proposal)?|(?:make|generate|create|draft|prepare|show|"
    r"give)\s+(?:me\s+)?(?:a\s+|the\s+|that\s+|this\s+)?proposal(?:\s+for\s+(?:this|that|it|these|them))?|propose\s+"
    r"(?:this|that|it|these|them|the\s+(?:change|changes|configuration|move|swap))(?:\s+(?:change|configuration))?)"
    r"(?:\s+for\s+me)?(?:\s+please)?\s*[?.!]*\s*$", re.I)


def named_approval(text: str) -> int | None:
    match = _NAMED_APPROVAL.match(text)
    return int(next(group for group in match.groups() if group)) if match else None
_INTERROGATIVE = re.compile(
    rf"^\s*{_FILLER}(?:why|what|what's|whats|how|when|where|which|who|whose|whom|is|are|was|were|am|does|"
    rf"do\s+(?:you|i|we|they)|did|has|have\s+(?:you|i|we)|should|would|could|can|will|shall|may|might|"
    rf"isn'?t|aren'?t|doesn'?t|tell\s+me|explain|describe|define|remind\s+me|walk\s+me\s+through|show\s+me|"
    rf"help\s+me\s+understand|any\s+idea|do\s+you\s+know|i\s+wonder|wondering)\b", re.I)
_HYPOTHETICAL_START = re.compile(
    rf"^\s*{_FILLER}(?:what\s+if|what\s+happens\s+if|what\s+would\s+happen|suppose|supposing|imagine|pretend|"
    rf"hypothetically|let'?s\s+say|say\s+(?:we|i)\b|in\s+theory|assume|assuming|if\b|would\s+it\s+(?:be|work|"
    rf"help|matter|make)|would\s+(?:that|this)\b|could\s+(?:we|i)\b|should\s+(?:we|i)\b|can\s+(?:we|i)\b|"
    rf"is\s+it\s+(?:possible|ok|okay|safe|better|allowed|worth)|how\s+about|what\s+about|for\s+example|"
    rf"e\.g\.|for\s+instance|as\s+an\s+example)", re.I)
_HYPOTHETICAL_ANY = re.compile(
    r"\b(?:what\s+(?:would|will)\s+happen|what\s+if|hypothetical(?:ly)?|suppose|imagine|pretend|let'?s\s+say|"
    r"in\s+theory|i\s+(?:was\s+)?wonder(?:ing)?|just\s+curious|out\s+of\s+curiosity|would\s+it\s+be\s+better)\b",
    re.I)
_FIELD_WORD = re.compile(
    r"\b(?:plates?|paper|racks?|tip\s*racks?|tips?|vials?|dilutions?|drops?|droplets?|replicates?|columns?|"
    r"rows?|volume|µL|mL|slots?|mix(?:es|ing)?|reps?|factors?|dye|water|cv|off\s+deck|policy|spots?|wells?)\b|"
    r"\b\d+(?:\.\d+)?\s*(?:x\b|×)", re.I)
_VALUE_TOKEN = re.compile(r"\b(?:\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*[x×]|[A-H]\d{1,2}|row\s+[A-H]|off(?:\s+deck)?|twice|"
                          r"double|half|triple|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
                          r"(?:\b|(?<=[x×]))", re.I)

# ── whole-message patterns ──────────────────────────────────────────────────────

_CANCEL = re.compile(
    r"^\s*(?:(?:no|nope|oh|ok|okay|actually|wait|hmm|sorry|um|uh)[,.!\s]+)*"
    r"(?:never\s*mind|nevermind|nvm|cancel(?:\s+(?:that|it|this|the\s+(?:change|proposal|request)))?|"
    r"forget\s+(?:it|that|about\s+(?:it|that))|scratch\s+that|disregard(?:\s+(?:that|it))?|ignore\s+that|"
    r"don'?t\s+do\s+(?:that|it)|leave\s+it(?:\s+as\s+(?:it\s+)?(?:is|was))?|drop\s+(?:it|that)|no\s+changes?|"
    r"keep\s+(?:it|everything)\s+as\s+(?:it\s+)?(?:is|was)|stop\s+that|abort(?:\s+that)?)"
    r"(?:[,.!\s]+(?:please|thanks|thank\s+you))?[.!\s]*$", re.I)
_UNCERTAIN = re.compile(
    r"^\s*(?:ok|okay|k|kk|sure|sure\?|looks\s+good|look\s+good|probably|i\s+guess|i\s+think\s+so|maybe|fine|"
    r"that'?s\s+fine|thats\s+fine|sounds\s+good|alright|all\s+right|yeah|yep|yup|ya|right|cool|great|perfect|"
    r"good|hmm+|i\s+suppose|why\s+not|if\s+you\s+say\s+so|seems\s+(?:ok|okay|fine|good|right)|go\s+for\s+it|"
    r"whatever|fine\s+by\s+me|works\s+for\s+me|ok\s+i\s+guess|yes\?|y\?)\s*[.!?]*\s*$", re.I)
_START_OVER = re.compile(
    r"\b(?:start(?:ing)?\s+(?:over|again|afresh|fresh|from\s+scratch|from\s+the\s+beginning)|restart|"
    r"reset(?:\s+(?:everything|it\s+all|all|the\s+(?:experiment|plan|session|conversation|state)))?|"
    r"from\s+scratch|begin\s+again|clean\s+slate|do\s+over|redo\s+everything)\b", re.I)
_UNDO = re.compile(
    r"\b(?:undo|revert|roll\s*back|rollback|go\s+back\s+(?:one|a|1|by\s+one)\s+(?:revision|step|change|version)|"
    r"go\s+back\s+to\s+(?:revision|version)\s+\d+|restore\s+(?:the\s+)?(?:original|initial|startup|starting|"
    r"default|previous|old)|put\s+(?:it|everything)\s+back\s+(?:the\s+way|how)\s+it\s+was|"
    r"take\s+back\s+(?:my\s+)?(?:last\s+)?change)\b", re.I)
_UNDO_TARGET = re.compile(r"\b(?:revision|version)\s+(\d+)\b", re.I)
_GO_BACK_ALONE = re.compile(r"^\s*(?:(?:ok|okay|no|wait|um|hmm)[,\s]+)*(?:go\s+back|back|previous|undo\s+that|take\s+it\s+back)"
                            r"\s*[.!]*\s*$", re.I)
_ACKNOWLEDGEMENT = re.compile(r"(?i)(?:ok|okay|so|now|then|alright|all\s+right|right|yes|yeah|great|thanks|thank\s+you|"
                              r"cool|sure|hmm+|well|um|uh|oh|hey|hi|hello|fine|good|perfect|got\s+it|understood|"
                              r"i\s+see|nice|awesome)(?:[,\s]+(?:ok|okay|so|now|then|thanks|great))*\W*")
_RESTORE_GROUP = re.compile(
    r"\brestore\s+(?:the\s+)?(?:original|initial|startup|starting|default)\s+(dilution|deck|labware|print(?:ing)?|"
    r"tip|mix(?:ing)?|liquid|material|vial)s?\b", re.I)
_UNDO_GROUPS = {"dilution": "dilution.", "deck": "deck.", "labware": "deck.", "print": "print.",
                "printing": "print.", "tip": "tips.", "mix": "mixing.", "mixing": "mixing.",
                "liquid": "materials.", "material": "materials.", "vial": "materials."}
_HISTORY = re.compile(
    r"\b(?:what\s+(?:have|did)\s+(?:i|we)\s+chang|what(?:'s|\s+has|\s+have)?\s+(?:been\s+)?changed|"
    r"what\s+(?:was|were)\s+(?:the\s+)?(?:original|initial|starting|startup|first|old|previous)|"
    r"(?:still|remain(?:s|ed)?)\s+(?:the\s+)?same|unchanged|same\s+as\s+(?:at\s+)?(?:startup|the\s+start|"
    r"the\s+beginning|before)|(?:after|since)\s+revision\s+\d+|what\s+revision|which\s+revision|"
    r"(?:show|list|give)\s+(?:me\s+)?(?:the\s+|my\s+)?(?:change\s+)?history|history\s+of\s+(?:the\s+)?changes|"
    r"(?:show|list)\s+(?:me\s+)?(?:my|the|all)\s+changes|what\s+did\s+(?:it|the\s+\w+(?:\s+\w+)?)\s+used\s+to\s+be)",
    re.I)
_INJECTION = re.compile(
    r"\b(?:ignore\s+(?:the\s+|all\s+|any\s+|your\s+|previous\s+|prior\s+|earlier\s+|those\s+|these\s+)*"
    r"(?:confirmations?|confirmation\s+system|instructions?|rules|safety|system(?:\s+prompt)?|validation|"
    r"guard\s*rails?|checks?|prompt)|skip\s+(?:the\s+)?(?:confirmation|approval|proposal|validation|checks?|safety)|"
    r"bypass|circumvent|override\s+(?:the\s+)?(?:confirmation|safety|validation|checks?|rules)|"
    r"without\s+(?:asking|confirmation|confirming|approval|showing\s+(?:me\s+)?(?:the\s+)?proposal)|"
    r"don'?t\s+(?:show|ask)(?:\s+me)?(?:\s+(?:the|a|for))?\s+(?:proposal|confirmation|approval|permission)|"
    r"just\s+(?:apply|do|run|change|execute)\s+(?:it|them|that|this)(?:\s+now)?|"
    r"apply\s+(?:it|them|that|this|the\s+changes?)\s+(?:now|immediately|right\s+away)|"
    r"pretend\s+(?:that\s+)?(?:i|we)\s+(?:already\s+|have\s+already\s+)?(?:said|typed|approved|confirmed|agreed)|"
    r"assume\s+(?:that\s+)?(?:i|we)\s+(?:said|approved|confirmed|agree)|"
    r"consider\s+(?:it|this|that)\s+(?:approved|confirmed)|treat\s+(?:this|that|it)\s+as\s+(?:a\s+)?(?:yes|approval)|"
    r"(?:treat|count|take|read)\s+(?:my|the)\s+next\s+(?:message|reply|answer|input|thing\s+i\s+(?:say|type))\s+as\s+"
    r"(?:a\s+)?(?:yes|approval|confirmation)|(?:count|take)\s+(?:this|that|it)\s+as\s+(?:a\s+)?(?:yes|approval)|"
    r"from\s+now\s+on[,\s]+(?:just\s+)?(?:apply|approve|say\s+yes|skip)|(?:you\s+can|go\s+ahead\s+and)\s+"
    r"(?:skip|bypass)|"
    r"auto[-\s]?(?:approve|confirm|apply)|i\s+pre-?approve|approve\s+everything|"
    r"you\s+(?:have|got)\s+(?:my\s+)?permission|developer\s+mode|admin\s+mode|jailbreak|"
    r"no\s+need\s+(?:to|for)\s+(?:ask|confirm|confirmation|approval))", re.I)
_AUTHORITY = re.compile(
    r"\b(?:stephen|my\s+(?:boss|pi|supervisor|manager|advisor|labmate|colleague)|the\s+(?:boss|pi|lab\s+manager|"
    r"manager|developers?|admin|administrator|supervisor|previous\s+(?:ai|assistant|agent|session|bot)|"
    r"other\s+(?:ai|assistant|agent))|someone|somebody|he|she|they|management|the\s+lab|another\s+user)\b"
    r"[^.?!\n]{0,40}?\b(?:already\s+)?(?:approved|said\s+(?:yes|it'?s\s+(?:ok|okay|fine|approved))|said\s+ok(?:ay)?|"
    r"okayed|ok'?d|signed\s+off|gave\s+(?:the\s+|their\s+|his\s+|her\s+)?(?:ok|okay|go-?ahead|approval|permission)|"
    r"confirmed\s+(?:it|this|that)|authori[sz]ed|told\s+(?:you|me)\s+to\s+(?:skip|ignore|apply|run|approve|bypass))",
    re.I)
_APPROVAL_QUESTION = re.compile(
    r"\b(?:if\s+i\s+(?:say|type)\s+(?:yes|no)|what\s+(?:does|happens\s+(?:when|if)\s+i\s+(?:say|type))\s+(?:yes|no)|"
    r"why\s+does\s+(?:typing\s+)?yes|what\s+am\s+i\s+approving|what\s+(?:is|'s)\s+(?:pending|waiting)|"
    r"(?:type|say)\s+yes\s+(?:to\s+)?what)\b", re.I)
_MODEL_QUESTION = re.compile(
    r"\b(?:what|which)\s+(?:ai\s+|language\s+|llm\s+)?(?:model|llm)\s+(?:are\s+you|is\s+this|do\s+you\s+use|"
    r"powers\s+you|runs\s+you|is\s+running)|\bwho\s+(?:made|built|trained|created)\s+you\b|"
    r"\bare\s+you\s+(?:gemini|gpt|chatgpt|claude|an?\s+ai|a\s+bot|human)\b", re.I)
_DROP_COUNT = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|a\s+single|single)"
_UNSUPPORTED = (
    (re.compile(r"\bserial(?:ly)?\b.*\bdilut|\bdilut\w*\b.*\bserial", re.I),
     "This demo makes independent dilutions straight from the dye stock (each well is made on its own), "
     "not serial dilutions from one well to the next, so there is no 'next serial dilution' step to run. For a "
     "series of concentrations, give the fold factors instead (for example \"make dilutions of 2×, 5× and 10×\")."),
    (re.compile(r"\bprint\s+(?:only\s+)?(?:the\s+)?(?:\d+(?:\.\d+)?\s*[x×]|[x×]\s*\d+)(?:\s+(?:dilution|well|one))?\b|"
                r"\bprint\s+only\s+(?:the\s+|one\s+)", re.I),
     "This demo prints every dilution in the plan, one paper row each; it cannot print only one or some of them. "
     "You could change the plan to just those dilutions instead."),
    (re.compile(r"\b(?:second|another|two|2|more\s+than\s+one|multiple|different)\s+(?:dyes?|samples?|reagents?|"
                r"stocks?)\b", re.I),
     "This demo uses one dye (the sample) and one diluent (water); a second dye is not supported."),
    (re.compile(rf"\b{_DROP_COUNT}\s+(?:stacked\s+|separate\s+)?drops?\b[^.;?!]{{0,60}}?\bcolumns?\b[^.;?!]{{0,60}}?"
                rf"\b{_DROP_COUNT}\s+(?:stacked\s+|separate\s+)?drops?\b[^.;?!]{{0,40}}?\bcolumns?\b", re.I),
     "One run uses the same number of drops in every paper column; different drop counts per column need two runs."),
    (re.compile(r"\b(?:aspirat\w*|pull\w*|draw\w*|suck\w*|take|taking)\b[^.?!]{0,60}\bfrom\s+(?:the\s+)?paper\b|"
                r"\bpaper(?:\s+print)?(?:\s+plate)?\b[^.?!]{0,50}\bas\s+(?:the\s+)?source\b|"
                r"\bfrom\s+(?:the\s+)?paper\b[^.?!]{0,50}\b(?:back\s+)?(?:in)?to\s+(?:the\s+)?(?:dilution\s+|96[-\s]?well\s+)?"
                r"plate\b|\b(?:reverse|reversing|swap|swapping|flip|flipping|invert|inverting|chang\w*)\s+(?:the\s+)?"
                r"(?:previous\s+)?(?:source|destination|direction|flow|liquid\s+path)s?(?:\s+and\s+(?:the\s+)?"
                r"(?:source|destination)s?)?\b(?!\s+(?:vial|well|column|row|slot))|\bback\s+into\s+the\s+vials?\b", re.I),
     "The liquid path is fixed: dilutions are made from the dye and water vials into the dilution plate, and printing "
     "goes from the dilution plate onto the paper. Nothing is drawn from the paper or put back into the vials. Labware "
     "can move to other slots, but the path itself cannot be reversed."),
)
_LIQUID_PATH_REASON = _UNSUPPORTED[-1][1]
_PRINT_ONE_REASON = _UNSUPPORTED[1][1]
_FACTOR_WORD = re.compile(r"(\d+(?:\.\d+)?)\s*[x×]|[x×]\s*(\d+(?:\.\d+)?)", re.I)

# ── clause patterns ─────────────────────────────────────────────────────────────

_NEGATION = re.compile(
    rf"^\s*{_FILLER}(?:please\s+)?(?:don'?t|do\s+not|dont|never|no\s+need\s+to|stop|avoid|let'?s\s+not|"
    rf"we\s+(?:don'?t|do\s+not)\s+(?:need|want)\s+to|i\s+(?:don'?t|do\s+not|dont)\s+(?:want|need|think\s+we\s+"
    rf"(?:need|should)))\b", re.I)
# "Actually, keep the plate where it is": like a negation, nothing about that setting changes.
_KEEP_AS_IS = re.compile(
    rf"^\s*{_FILLER}(?:please\s+)?(?:just\s+)?(?:keep|leave)\s+(?!(?:it|everything|that|this)\b)(?:the\s+|my\s+|our\s+)?"
    rf"[a-z0-9µ][a-z0-9µ\s-]{{1,40}}?\s+(?:where\s+(?:it|they)\s+(?:is|are|was|were)|as\s+(?:it|they)\s+(?:is|are|was|"
    rf"were)|unchanged|alone|the\s+same|as\s+before|in\s+place|in\s+(?:its|their)\s+(?:current\s+)?(?:place|slot|position|"
    rf"spot))(?:\s+(?:please|for\s+now|then))?\s*[.!]*\s*$", re.I)
_DOUBLE_NEGATIVE = re.compile(
    r"\b(?:don'?t|do\s+not|dont|never|not)\s+(?:\w+\s+)?not\b|\b(?:don'?t|do\s+not|dont)\s+(?:think|believe|"
    r"suppose)\b[^.?!]*\b(?:shouldn'?t|should\s+not|don'?t|won'?t|can'?t|not|never)\b|\bnot\s+(?:un\w+|without)\b|"
    r"\bno\s+(?:reason\s+)?not\s+to\b|\bnot\s+that\s+(?:i|we)\s+(?:don'?t|do\s+not|dont|didn'?t|won'?t)\b|"
    r"\b(?:isn'?t|is\s+not|wasn'?t)\s+(?:that\s+)?(?:i|we)\s+(?:don'?t|do\s+not|dont)\b", re.I)
# "Let me know when the dilutions are ready": a request to be told, never a report that they are.
_NOTIFY_WHEN = re.compile(
    rf"^\s*{_FILLER}(?:please\s+)?(?:(?:let|tell|notify|ping|alert|warn|remind)\s+(?:me|us)(?:\s+know)?|(?:give|send)\s+"
    rf"(?:me|us)\s+a\s+(?:shout|ping|heads[-\s]?up|note|message|signal))\s+(?:when|once|as\s+soon\s+as|after|if|whether|"
    rf"by\s+the\s+time)\b", re.I)
# "Make sure the dilutions are mixed": a state of the dilutions to check or to reach, not a report that it is so.
# ("Make sure the plate is in slot 8" stays an instruction.)
_CHECK_THAT = re.compile(
    rf"^\s*{_FILLER}(?:please\s+)?(?:make\s+sure|ensure|check|confirm|verify|double[-\s]?check)\s+(?:that\s+)?"
    rf"(?:the|all|my|our|each|every|those|these)\s+(?:[\w×-]+\s+){{0,3}}?(?:dilutions?|wells?|dilution\s+series)\s+"
    rf"(?:is|are)\b", re.I)
# "I'll move the rack to slot 6 later": the scientist will move it; the plan may or may not follow.
_FUTURE_PHYSICAL = re.compile(
    r"^\s*(?:(?:ok|okay|so|then|well|fyi|btw)[,\s]+)*(?:i|we|stephen|someone|he|she|they)\s*(?:will|'ll|shall|am\s+going\s+"
    r"to|'m\s+going\s+to|are\s+going\s+to|'re\s+going\s+to|is\s+going\s+to|'s\s+going\s+to|plan\s+to|intend\s+to|"
    r"gonna)\s+(?:physically\s+|manually\s+|go\s+and\s+|probably\s+|also\s+)*(?:move|put|place|take|swap|load|remove|"
    r"relocate|shift|slide|carry)\b", re.I)
_FUTURE_SUBJECT = re.compile(
    r"^\s*(?:(?:ok|okay|so|then|well|fyi|btw)[,\s]+)*(?:i|we|stephen|someone|he|she|they)\s*(?:will|'ll|shall|am\s+going\s+"
    r"to|'m\s+going\s+to|are\s+going\s+to|'re\s+going\s+to|is\s+going\s+to|'s\s+going\s+to|plan\s+to|intend\s+to|"
    r"gonna)\s+(?:physically\s+|manually\s+|go\s+and\s+|probably\s+|also\s+)*", re.I)
_FUTURE_TIME = re.compile(
    r"\s*\b(?:later(?:\s+on)?|tomorrow|soon|in\s+a\s+(?:minute|bit|second|moment|while)|after\s+(?:lunch|this|that|"
    r"the\s+meeting)|before\s+the\s+run|myself|ourselves|by\s+hand|manually|next)\b", re.I)
_EXCEPT = re.compile(
    r"\b(?:don'?t|do\s+not|dont)\s+(?:change|touch|modify|alter)\s+(?:anything|any\s+(?:other\s+)?"
    r"(?:settings?|parameters?)|the\s+rest)\s+(?:except|but|other\s+than|besides|apart\s+from)\s+(.+)$", re.I)
_ANYMORE = re.compile(r"\b(?:anymore|any\s+more|instead|no\s+longer)\b", re.I)
_STRONG_CORRECTION = re.compile(r"\s*(?:[—–]|-{1,2})?\s*,?\s*\b(?:sorry|oops|i\s+meant|i\s+mean|make\s+that|"
                                r"correction|scratch\s+that|no\s+wait)\b[,:]?\s+", re.I)
_WEAK_CORRECTION = re.compile(r"(?:\s*[—–]\s*|\s+-{1,2}\s+|\s*[,;]\s*|\.{2,}\s*)\b(?:no|nope|actually|wait|"
                              r"rather|err?|um)\b[,:]?\s+", re.I)
_QUOTE_SPANS = re.compile(r'"[^"\n]{1,400}"|(?<![\w])\'[^\'\n]{3,400}\'(?![\w])')
_BLOCK_HEADER = re.compile(
    r"^\s*(?:the\s+|our\s+|my\s+|this\s+|an?\s+)?(?:sop|protocol|procedure|instructions?|guide|log|logs|email|"
    r"e-mail|message|notes?|document|doc|readme|slack|chat|transcript|script|code|config|yaml|output|"
    r"previous\s+(?:instructions?|message|session|conversation|plan)|stephen|he|she|they|it|old\s+plan)\b"
    r"[^:\n]{0,60}:\s*$", re.I)
_INLINE_SAYS = re.compile(
    r"\b(?:the\s+)?(?:sop|protocol|procedure|guide|instructions?|email|e-mail|log|notes?|doc(?:ument)?|readme|"
    r"message|stephen|he|she|they|someone|my\s+(?:boss|pi|notes)|the\s+(?:previous|old|last)\s+(?:plan|message|"
    r"instructions?|session))\s+(?:says|said|reads|read|wrote|writes|states|stated|told\s+(?:me|us)(?:\s+to)?)"
    r"\s*:?\s+(.+)$", re.I)
_LIST_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*•]|step\s+\d+[:.)]?)\s+\S", re.I)
_EMAIL_HEADER = re.compile(r"^\s*(?:from|to|subject|sent|date|cc|bcc|re|fwd?|reply-to)\s*:\s*\S", re.I)
_LOG_LINE = re.compile(r'^\s*(?:\{"|\[\d{4}-\d{2}-\d{2}|\d{4}-\d{2}-\d{2}[T ]\d|(?:from|to|subject|sent|date|cc):\s|'
                       r'(?:you|agent|user|assistant|confirm|clarify)>\s)', re.I)
_CLAIM = re.compile(
    rf"^\s*{_FILLER}(?:the|our|my)\s+([a-z][a-z\s-]{{2,40}}?)\s+(?:is|are|was|were)\s+(?:currently\s+|now\s+|"
    rf"still\s+|set\s+to\s+|at\s+)?([^\s].{{0,30}}?)\s*[.!]*\s*$", re.I)

# physical-state reports ("I moved it myself", "it is actually in slot 6")
_REPORT_ACTION = re.compile(
    r"\b(?:i|we|someone|somebody|stephen|he|she|they|the\s+tech|my\s+(?:labmate|colleague))\s+(?:have\s+|had\s+|"
    r"'ve\s+|'d\s+)?(?:already\s+|just\s+|manually\s+|actually\s+|physically\s+)*(?:moved|put|placed|relocated|"
    r"shifted|swapped|took|removed|pulled|lifted|loaded|replaced|changed|made|prepared|mixed|filled|emptied|"
    r"refilled|set\s+up|swapped\s+out)\b", re.I)
_REPORT_STATE = re.compile(
    r"\b(?:is|are)\s+(?:actually|already|now|currently|really|physically|still|in\s+fact|sitting|back)\s+(?:in|at|on|off)\b|"
    r"\b(?:is|are)\s+(?:now\s+|already\s+)?off\s+(?:the\s+)?(?:deck|robot)\b", re.I)
_REPORT_DILUTIONS = re.compile(
    r"\b(?:dilutions?|dilution\s+series|wells?)(?:\s+(?:from|in|of|for|at|on)\b(?:\s+(?!(?:are|were|is|was|have|has)\b)"
    r"[\w-]+){1,4})?\s+(?:are|were|is|was|have\s+been|has\s+been)\s+(?:already\s+|all\s+)?"
    r"(?:made|prepared|done|mixed|filled|ready)\b|\b(?:already|manually)\s+(?:made|prepared|did)\s+(?:all\s+)?"
    r"(?:of\s+)?(?:the\s+)?(?:\w+\s+){0,2}dilutions?\b|\b(?:made|prepared|did)\s+(?:all\s+)?(?:of\s+)?(?:the\s+)?"
    r"(?:\w+\s+){0,2}dilutions?\s+(?:manually|by\s+hand|already|myself|ourselves|earlier|yesterday|before)\b", re.I)
_PREPARED_VOLUME_TAIL = re.compile(
    r"(?:[,;]\s*(?:and\s+|so\s+)?|\s+and\s+)((?:each|every|all|the)\s+(?:wells?|dilutions?)\b[^,;.]*?\d+(?:\.\d+)?\s*"
    r"(?:µL|uL|mL)\b[^,;.]*)", re.I)
_REPORT_TIPS = re.compile(
    r"\btips?(?:\s+rack)?\s+(?:were|was|have\s+been|has\s+been|are|is)\s+(?:already\s+)?(?:changed|replaced|"
    r"swapped|refilled|reloaded|new|fresh)\b|\b(?:put\s+in|loaded|installed|swapped\s+in)\s+a\s+(?:new|fresh|full)\s+"
    r"tip\s+rack\b", re.I)
_REPORT_PLATE_REPLACED = re.compile(
    r"\b(?:new|fresh|clean|empty)\s+(?:dilution\s+|96[-\s]?well\s+|well\s+)?plate\b(?!\s+(?:column|row|well))|"
    r"\b(?:replaced|swapped|changed|emptied|cleaned|washed)\s+(?:out\s+)?(?:the\s+)?(?:dilution\s+|96[-\s]?well\s+|"
    r"well\s+)?(?:plate|wells)\b|\bwells?\s+(?:are|were)\s+(?:now\s+)?(?:empty|emptied|clean)\b", re.I)
_REPORT_IDIOM = re.compile(
    r"\b(?:changed\s+(?:my|our|his|her|their)\s+minds?|made\s+(?:a|an)\s+(?:mistake|error|typo)|mixed\s+(?:it|them|"
    r"things)?\s*up|took\s+(?:a\s+)?(?:look|note|break)|put\s+(?:it|that)\s+wrong|set\s+up\s+the\s+(?:meeting|call))\b",
    re.I)
LABWARE_PATTERNS = (
    ("tiprack", re.compile(r"\btip\s*racks?\b|\btips?\s+rack\b|\btiprack\b", re.I)),
    ("paper", re.compile(r"\bpaper(?:\s+print)?(?:\s+plate)?\b|\bprint\s+plate\b", re.I)),
    ("tuberack", re.compile(r"\b(?:vial|tube)\s*racks?\b|\bvials\b|\bbottles?\b|\bracks?\b", re.I)),
    ("plate", re.compile(r"\b(?:96[-\s]?well\s+|dilution\s+|well\s+)?plates?\b", re.I)),
)
_SLOT_NUMBER = re.compile(r"\bslot\s*(\d{1,2})\b|\b(?:in|into|to|at|on)\s+(?:deck\s+)?(?:slot\s+)?(\d{1,2})\b", re.I)
_OFF = re.compile(r"\boff(?:\s+(?:of\s+)?(?:the\s+)?(?:deck|robot))?\b|\bout\s+of\s+the\s+robot\b|\bremoved\b", re.I)

# references and quantities
_PRONOUN_OBJECT = re.compile(
    r"\b(?:move|put|place|shift|relocate|take|remove|swap|slide|set|bring|push|carry)\s+(it|that|this|them|those|"
    r"these|the\s+other\s+one|the\s+same\s+one|same\s+one|that\s+one|this\s+one)\b", re.I)
_USED_TO_BE = re.compile(r"\bwhere\s+(?:the\s+)?([a-z0-9\s-]+?)\s+(?:used\s+to\s+be|was\s+before|was|had\s+been|sat|"
                         r"used\s+to\s+sit)\b", re.I)
_RELATIVE_SLOT = re.compile(r"\bone\s+slot\s+(?:(?:over\s+)?to\s+the\s+|over\s+)?(right|left|back|forward|front|up|"
                            r"down|behind)\b", re.I)
# "the same spot" alone describes stacked drops ("three drops on the same spot"); only "as before" makes it a reference
_SAME_AS_BEFORE = re.compile(r"\b(?:the\s+)?same\s+(?:one|slot|tip|vial|well)(?:\s+as\s+(?:before|last\s+time|earlier))?\b|"
                             r"\b(?:the\s+)?same\s+(?:place|spot|position)\s+as\s+(?:before|last\s+time|earlier)\b|"
                             r"\bas\s+before\b|\blike\s+last\s+time\b", re.I)
_SLOT_ONLY_VERB = re.compile(r"^\s*" + _FILLER + r"(?:use|put\s+(?:it|that)\s+(?:in|into|to|at)|move\s+(?:it|that)\s+"
                             r"(?:to|into)|go\s+with|try|make\s+it)\s+(?:deck\s+)?slot\s+(\d{1,2})\b", re.I)
_BARE_NUMBER = re.compile(
    rf"^\s*{_FILLER}(?:use|make\s+(?:it|that)|set\s+(?:it|that)\s+to|change\s+(?:it|that)\s+to|try|go\s+with|"
    rf"put\s+(?:it|that)\s+(?:in|at|to|on)|put\s+(?:it|that)|start\s+(?:at|from|with)|do|pick|choose|it'?s|"
    rf"make\s+that|how\s+about)?\s*(\d+(?:\.\d+)?)\s*(?:instead|please|then|now)?\s*[.!]*\s*$", re.I)
_RELATIVE_ALONE = re.compile(
    rf"^\s*{_FILLER}(?:use|make\s+it|do|try|give\s+(?:it|me))?\s*(?:twice\s+as\s+much|twice\s+the\s+amount|"
    rf"double(?:\s+it)?|half(?:\s+as\s+much)?|half\s+the\s+amount|triple(?:\s+it)?|more|less|a\s+bit\s+more|"
    rf"a\s+bit\s+less|a\s+little\s+(?:more|less)|the\s+same\s+amount)\s*[.!]*\s*$", re.I)
_VAGUE = re.compile(
    r"\b(a\s+couple(?:\s+of)?|couple\s+of|a\s+few|several|some\s+more|some|a\s+bunch\s+of|a\s+lot\s+of|lots\s+of|"
    r"many|a\s+bit\s+more|a\s+little\s+more|more|fewer|less|extra)\s+(?:more\s+)?(drops?|droplets?|dilutions?|"
    r"replicates?|columns?|tips?|mix(?:es|ing)?|reps|µL|times)\b", re.I)
_EXPLICIT_COUNT_BEFORE = re.compile(r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*$", re.I)
_DANGLING = {"the", "a", "an", "to", "it", "in", "into", "onto", "on", "at", "of", "for", "from", "with", "and",
             "or", "by", "as", "than", "is", "be", "set", "change", "move", "put", "use", "make", "switch", "swap",
             "take", "remove", "add", "increase", "decrease", "my", "its", "this", "that", "these", "those",
             "some", "which", "same"}
_NEEDS_VALUE = {"deck.plate.slot", "deck.paper.slot", "deck.tuberack.slot", "deck.tiprack.slot",
                "materials.sample.vial", "materials.solvent.vial", "dilution.factors", "dilution.total_volume_ul",
                "dilution.plate_column", "dilution.start_row", "print.droplet_volume_ul",
                "print.droplets_per_spot", "print.replicates", "print.paper_start_column", "tips.start_tip",
                "mixing.reps", "mixing.volume_ul"}
_NO_VALUE_NEEDED = re.compile(
    r"\b(?:off|skip|enable|disable|none|all|new|fresh|reuse|return|trash|back|default|original|same|restore|undo|"
    r"keep|leave|label|name|call|twice|double|half|triple|dilute|more|less|fewer|increase|decrease|raise|lower|"
    r"reduce|halve|every|each|instead|where)\b", re.I)
_RUN_LIKE = re.compile(
    rf"^\s*{_FILLER}(?:please\s+)?(?:start|begin|run|execute|launch|kick\s+off|go\s+ahead\s+and\s+(?:start|run))\s+"
    rf"(?:the\s+)?(?:print(?:ing)?|dilution(?:s)?|robot|experiment|protocol|run|job|it\s+now|everything|process)"
    rf"(?:\s+(?:now|please|immediately|right\s+away))?\s*[.!]*\s*$", re.I)
_DANGLING_NOUNS = {"well", "slot", "tip", "vial", "column", "row", "position"}
_SPLIT = re.compile(
    rf"\s*;\s*|\s*,\s*(?:and\s+|but\s+|so\s+|then\s+)?(?:actually\s+|also\s+|then\s+|please\s+|now\s+|just\s+)*"
    rf"(?=(?:{_VERB_ALT})\b)|\s+(?:and|but|so)\s+(?:then\s+|also\s+|actually\s+|please\s+|now\s+)*"
    rf"(?=(?:{_VERB_ALT})\b)", re.I)
_SENTENCES = re.compile(r"(?<=[.!?])\s+(?=[A-Za-z0-9\"'(])|\n+")

FIELD_TERMS = (
    ("deck.tiprack.slot", re.compile(r"\btip\s*racks?\b|\btiprack\b", re.I)),
    ("deck.paper.slot", re.compile(r"\bpaper(?:\s+print)?\s+plate\b|\bpaper\b(?!\s+(?:column|position|row))", re.I)),
    ("deck.tuberack.slot", re.compile(r"\b(?:vial|tube)\s*racks?\b|\bracks?\b", re.I)),
    ("deck.plate.slot", re.compile(r"\b(?:dilution|well|96[-\s]?well)\s+plate\b|\bplates?\b(?!\s+(?:column|row|well))",
                                   re.I)),
    ("materials.sample.vial", re.compile(r"\b(?:dye|sample|stock|cv)\s+vial\b", re.I)),
    ("materials.solvent.vial", re.compile(r"\b(?:water|solvent|diluent)\s+vial\b", re.I)),
    ("dilution.enabled", re.compile(r"\bdilution\s+step\b|\bmak(?:e|ing)\s+(?:the\s+)?dilutions\b", re.I)),
    ("dilution.total_volume_ul", re.compile(r"\b(?:total|final)\s+volume\b|\bvolume\s+per\s+(?:well|dilution)\b", re.I)),
    ("dilution.plate_column", re.compile(r"\bplate\s+column\b", re.I)),
    ("dilution.start_row", re.compile(r"\b(?:start(?:ing)?\s+)?row\b", re.I)),
    ("dilution.factors", re.compile(r"\bdilutions?\b(?!\s+step)|\bfactors?\b|\bfold\b|\bseries\b", re.I)),
    ("print.droplet_volume_ul", re.compile(r"\bdrop(?:let)?\s+volume\b|\bµL\s+drops?\b|\bdrop\s+size\b", re.I)),
    ("print.paper_start_column", re.compile(r"\bpaper\s+column\b|\bcolumn\b", re.I)),
    ("print.replicates", re.compile(r"\breplicates?\b", re.I)),
    ("print.droplets_per_spot", re.compile(r"\bdrops?\b|\bdroplets?\b", re.I)),
    ("print.enabled", re.compile(r"\bprint(?:ing)?\s+step\b", re.I)),
    ("tips.policy", re.compile(r"\b(?:new|fresh)\s+tips?\b|\btip\s+policy\b|\breus(?:e|ing)\b", re.I)),
    ("tips.return_tips", re.compile(r"\breturn(?:ing)?\s+(?:the\s+)?tips\b|\btrash\b", re.I)),
    ("tips.start_tip", re.compile(r"\b(?:start(?:ing)?\s+)?tips?\b(?!\s*rack)", re.I)),
    ("mixing.volume_ul", re.compile(r"\bmix(?:ing)?\s+volume\b", re.I)),
    ("mixing.reps", re.compile(r"\bmix(?:es|ing)?\b", re.I)),
)
# Paths a pending change belongs to, for matching words like "the plate move" or "the dilutions".
PATH_GROUPS = {
    "deck.plate.slot": ("deck.plate.slot",), "deck.paper.slot": ("deck.paper.slot",),
    "deck.tuberack.slot": ("deck.tuberack.slot",), "deck.tiprack.slot": ("deck.tiprack.slot",),
    "dilution.factors": ("dilution.factors", "dilution.total_volume_ul", "dilution.start_row",
                         "dilution.plate_column", "dilution.enabled", "dilution.prepared_volume_ul"),
}


def has_action_verb(text: str) -> bool:
    return bool(_ACTION.search(text))


_CLAIMED_CHANGE = re.compile(
    r"\b(?:i\s*(?:have|'ve)\s+(?:now\s+|just\s+|already\s+|successfully\s+)?(?:moved|changed|updated|applied|set|removed|"
    r"added|switched|started|run|ran|made|approved|confirmed|reduced|increased)|(?:has|have)\s+(?:now\s+|just\s+)?been\s+"
    r"(?:moved|changed|updated|applied|set|started|removed|approved)|i\s+(?:moved|changed|updated|applied|removed|started|"
    r"approved)\s+(?:the|it|that|this|your)|(?:done|all\s+set)\s*[-,:!.]+\s*(?:i|the|your)\b)", re.I)
# A proposal's explanation is always about the proposed change, so plain past or present passives count too.
_EXPLANATION_CLAIM = re.compile(r"\b(?:is|are|was|were)\s+(?:now\s+)?(?:updated|changed|moved|applied|set\s+to)\b|"
                                r"\b(?:updated|changed|moved|set)\b[^.]*\bas\s+requested\b", re.I)
_NEGATED_BEFORE = re.compile(r"\b(?:nothing|not|never|no|hasn'?t|haven'?t|won'?t|wasn'?t|isn'?t|without|if|once|"
                             r"until|before|after|when)\b[^.!?\n]{0,50}$", re.I)
_MODAL_BEFORE = re.compile(r"\b(?:will|would|should|could|can|may|might|must|to|going\s+to)\s+$", re.I)


def answer_claims_change(answer: str) -> bool:
    """A model answer that says it changed something ("Done - I have moved the plate"). Answers never change state."""
    for match in _CLAIMED_CHANGE.finditer(answer):
        before = answer[max(0, match.start() - 60):match.start()]
        if not _NEGATED_BEFORE.search(before) and not _MODAL_BEFORE.search(before):
            return True
    return False


def explanation_claims_change(explanation: str) -> bool:
    """A proposal explanation written as if the change were already made ("The tip rack slot was updated to 8")."""
    if answer_claims_change(explanation):
        return True
    for match in _EXPLANATION_CLAIM.finditer(explanation):
        before = explanation[max(0, match.start() - 60):match.start()]
        if not _NEGATED_BEFORE.search(before) and not _MODAL_BEFORE.search(before):
            return True
    return False


def wants_something_else(text: str) -> bool:
    """'I don't want four dilutions anymore' asks for a different value without giving one."""
    return bool(_ANYMORE.search(text))


# "print" names the print STEP only as the thing being done or left out; "the paper print plate", "print positions"
# and "print columns" are labware and places, and "don't change the drop volume for printing" is about a setting.
_PRINT_THINGS = r"(?!\s+(?:plates?|positions?|columns?|rows?|spots?|heights?|volumes?|settings?)\b)"
_NEGATED_VERB = (r"\b(?:don'?t|do\s+not|dont|never|no\s+need\s+to|stop|avoid|won'?t|will\s+not|let'?s\s+not|"
                 r"(?:don'?t|do\s+not|dont)\s+(?:want|need|have)\s+to|(?:we|i)\s+(?:don'?t|do\s+not|dont)\s+(?:want|need)"
                 r"\s+to)\s+(?:\w+ly\s+)?")
_PRINT_STEP = re.compile(rf"{_NEGATED_VERB}print(?:ing)?\b{_PRINT_THINGS}|\b(?:no|without|skip(?:ping)?)\s+"
                         rf"(?:the\s+|any\s+)?print(?:s|ing)?\b{_PRINT_THINGS}", re.I)
_DILUTION_STEP = re.compile(rf"{_NEGATED_VERB}(?:dilute\b|(?:make|prepare|do|redo|remake)\s+(?:the\s+|any\s+|all\s+"
                            rf"(?:the\s+)?)?dilutions?\b(?!\s+(?:plate|columns?|rows?|wells?)\b))|\b(?:no|without|skip"
                            rf"(?:ping)?)\s+(?:making\s+)?(?:the\s+|any\s+)?dilutions?\b(?!\s+(?:plate|columns?|rows?|wells?)\b)",
                            re.I)
_STEP_OFF = (
    ("print", re.compile(rf"\b(?:no|without)\s+print(?:s|ing)?\b{_PRINT_THINGS}|\bdilut(?:e|ions?)\s+only\b|"
                         r"\b(?:only|just)\s+(?:make\s+|do\s+)?(?:the\s+)?dilut(?:e|ions?)\b", re.I)),
    ("dilution", re.compile(r"\b(?:no|without)\s+(?:making\s+)?(?:the\s+|any\s+)?dilutions?\b|\bprint(?:ing)?\s+only\b|"
                            rf"\b(?:only|just)\s+print(?:ing)?\b{_PRINT_THINGS}", re.I)),
)


def negated_step(text: str) -> str | None:
    """'print' or 'dilution' when a "don't ..." message leaves out a whole step ("Don't print anything this time.").

    "Don't move the paper print plate" and "keep the print plate in slot 5" are about labware, and "don't change the
    drop volume for printing" is about a setting: none of them is about the print step itself.
    """
    if _PRINT_STEP.search(text):
        return "print"
    return "dilution" if _DILUTION_STEP.search(text) else None


def step_off_request(text: str) -> str | None:
    """The step a message leaves out without saying what to change ("No printing this run, just dilutions.")."""
    steps = {step for step, pattern in _STEP_OFF if pattern.search(text)}
    return steps.pop() if len(steps) == 1 else None


def _plan_prints(config: dict[str, Any]) -> bool:
    try:
        return steps_enabled(config)[1]
    except (TypeError, ValueError, AttributeError):
        return True


def _named_dilutions_reason(clause: str, config: dict[str, Any]) -> str | None:
    """'Print the 5x, 10x and 20x dilutions onto paper column 2', checked against the dilutions the plan makes.

    Naming every dilution of the plan together with how to print them is an ordinary print instruction (None), and so
    is naming every dilution while printing is off in this plan (a request to print them). A dilution the plan does not
    make, or only some of the plan's dilutions, gets an explanation instead.
    """
    try:
        planned = [float(factor) for factor in (config.get("dilution") or {}).get("factors") or []]
        named = sorted({float(first or second) for first, second in _FACTOR_WORD.findall(clause)})
    except (TypeError, ValueError):
        return _PRINT_ONE_REASON
    if not planned or not named:
        return _PRINT_ONE_REASON
    in_plan = ", ".join(fmt_factor(factor) for factor in planned)
    missing = [factor for factor in named if all(abs(factor - item) > 1e-9 for item in planned)]
    if missing:
        return (f"The current plan has no {', '.join(fmt_factor(factor) for factor in missing)} dilution"
                f"{'s' if len(missing) > 1 else ''} (it makes {in_plan}). Printing always uses the dilutions in the plan, "
                "one paper row each, so set the dilution factors first (for example \"make dilutions of "
                f"{', '.join(fmt_factor(factor) for factor in named)}\"), then say how to print them.")
    if len(named) < len({round(factor, 6) for factor in planned}):
        return _PRINT_ONE_REASON
    if _VALUE_TOKEN.search(_FACTOR_WORD.sub(" ", clause)) or not _plan_prints(config):
        return None
    return f"Every dilution in the plan ({in_plan}) already prints, one paper row each."


def match_paths(phrase: str, paths: list[str]) -> list[str]:
    """Pending change paths that a phrase such as 'moving the plate' or 'the dilutions' refers to."""
    return _match_paths(phrase, paths)


def unambiguous_labware(text: str, config: dict[str, Any]) -> list[str]:
    """Labware roles named in text, leaving out a bare 'plate' while the paper print plate is on the deck."""
    roles = []
    for role, start, end in labware_mentions(text):
        if role == "plate" and re.fullmatch(r"(?i)plates?", text[start:end].strip()) and config \
                and not is_off_deck(slot_of(config, "paper")):
            continue
        if role == "tuberack" and re.fullmatch(r"(?i)rack", text[start:end].strip()) and config \
                and not is_off_deck(slot_of(config, "tiprack")):
            continue
        roles.append(role)
    return list(dict.fromkeys(roles))


def fields_mentioned(text: str) -> list[str]:
    found: list[str] = []
    masked = text
    for path, pattern in FIELD_TERMS:
        match = pattern.search(masked)
        if match:
            found.append(path)
            masked = masked[:match.start()] + " " * (match.end() - match.start()) + masked[match.end():]
    return found


def labware_mentions(text: str) -> list[tuple[str, int, int]]:
    """(role, start, end) for each labware named, most specific pattern first."""
    masked, found = text, []
    for role, pattern in LABWARE_PATTERNS:
        for match in pattern.finditer(masked):
            found.append((role, match.start(), match.end()))
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    return sorted(found, key=lambda item: item[1])


# ── results ─────────────────────────────────────────────────────────────────────

@dataclass
class PhysicalFact:
    kind: str                     # location | dilutions_prepared | tips_replaced | unclear
    role: str | None = None
    slot: Any = None
    text: str = ""


@dataclass
class TurnContext:
    config: dict[str, Any]
    pending: bool = False
    pending_paths: tuple[str, ...] = ()
    recent_labware: tuple[str, ...] = ()
    previous_slots: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnAnalysis:
    normalized: Normalized
    kind: str = "chat"
    actionable: str = ""
    informational: str = ""
    superseded: str = ""
    confirmation: str | None = None
    facts: list[PhysicalFact] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    restrict_paths: tuple[str, ...] = ()
    ambiguity: Ambiguity | None = None
    clarification: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.normalized.text

    @property
    def informational_only(self) -> bool:
        return not self.actionable and not self.facts


# ── arithmetic, a deterministic answer for "what is 20 × 5?" ────────────────────

_ARITH_PREFIX = re.compile(r"^\s*(?:what\s*(?:'s|\s+is)|whats|calculate|compute|how\s+much\s+is|solve|evaluate)\s+"
                           r"(.+?)\s*[?=.!]*\s*$", re.I)
_ALLOWED = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
            ast.USub: operator.neg, ast.UAdd: operator.pos}


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED:
        return _ALLOWED[type(node.op)](_evaluate(node.left), _evaluate(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED:
        return _ALLOWED[type(node.op)](_evaluate(node.operand))
    raise ValueError("unsupported expression")


def arithmetic_answer(text: str) -> str | None:
    match = _ARITH_PREFIX.match(text)
    expression = (match.group(1) if match else text.strip().rstrip("?=. ")).lower()
    for word, symbol in (("×", "*"), ("÷", "/"), ("multiplied by", "*"), ("times", "*"), ("divided by", "/"),
                         ("plus", "+"), ("minus", "-"), ("over", "/")):
        expression = expression.replace(word, symbol)
    expression = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", expression)
    if not re.fullmatch(r"[\d.\s+\-*/()]+", expression) or not re.search(r"\d\s*[+\-*/]\s*[\d(]", expression):
        return None
    try:
        value = _evaluate(ast.parse(expression, mode="eval").body)
    except (ValueError, SyntaxError, ZeroDivisionError):
        return None
    shown = f"{value:.6g}" if not float(value).is_integer() else str(int(value))
    return f"{expression.strip()} = {shown}"


# ── clause helpers ──────────────────────────────────────────────────────────────

def _strip_reference_material(text: str, flags: set[str]) -> tuple[str, list[str]]:
    """Remove quotations, pasted documents and logs; return (remaining text, reference parts)."""
    references: list[str] = []
    kept_lines: list[str] = []
    in_block = in_fence = False
    block_lines = 0
    lines = text.split("\n")
    list_runs = [bool(_LIST_LINE.match(line)) for line in lines]
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            references.append(line)
            flags.add("pasted")
            continue
        if in_fence:
            references.append(line)
            continue
        if _BLOCK_HEADER.match(line) or _EMAIL_HEADER.match(line):
            # "The SOP says:" or an email header: what follows (up to a blank line after its
            # content, or the scientist's own question) is reference material.
            if not in_block:
                block_lines = 0
            in_block = True
            references.append(line)
            flags.add("pasted")
            continue
        neighbour_list = (index > 0 and list_runs[index - 1]) or (index + 1 < len(lines) and list_runs[index + 1])
        if (in_block and line.strip() and not (_INTERROGATIVE.match(line) and line.strip().endswith("?"))) \
                or _LOG_LINE.match(line) or (list_runs[index] and neighbour_list):
            references.append(line)
            flags.add("pasted")
            block_lines += 1
            continue
        if not line.strip():
            if block_lines:          # a blank line right after the header does not end the block
                in_block = False
            continue
        in_block = False
        kept_lines.append(line)
    remaining = "\n".join(kept_lines)
    inline = _INLINE_SAYS.search(remaining)
    if inline:
        references.append(inline.group(1))
        flags.add("quoted")
        remaining = remaining[:inline.start(1)] + " (quoted text) " + remaining[inline.end(1):]
    for quote in _QUOTE_SPANS.findall(remaining):
        references.append(quote)
        flags.add("quoted")
    remaining = _QUOTE_SPANS.sub(" (quoted text) ", remaining)
    return remaining, references


def _clauses(text: str) -> list[tuple[str, bool]]:
    """(clause, sentence ends with '?') for each clause of the message.

    A question keeps its conjunctions together ("what if we move the plate and change the
    volume?"), and so does a self-correction ("six dilutions, actually make four").
    """
    result = []
    for sentence in _SENTENCES.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        question = sentence.endswith("?")
        corrected = bool(_split_correction(sentence)[0])
        parts = ([sentence] if question or corrected
                 else [part for part in _SPLIT.split(sentence) if part and part.strip()])
        result.extend((part.strip(), question) for part in parts)
    return result


def _split_correction(clause: str) -> tuple[str, str]:
    """(superseded, final) for 'slot 8 — sorry, I meant slot 6'; superseded is '' when none."""
    last = None
    for pattern in (_STRONG_CORRECTION, _WEAK_CORRECTION):
        for match in pattern.finditer(clause):
            if match.start() > 0 and (last is None or match.start() > last.start()):
                last = match
    if last is None:
        return "", clause
    before, after = clause[:last.start()], clause[last.end():]
    value = re.compile(r"\d|\b[A-H]\d{1,2}\b|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)
    if not value.search(before) or not value.search(after):
        return "", clause
    return before, after


def _incomplete(clause: str) -> bool:
    words = re.findall(r"[a-z0-9µ']+", clause.lower())
    if not words or len(words) > 7 or re.search(r"\d|\b[A-H]\d", clause):
        return False
    last, previous = words[-1], (words[-2] if len(words) > 1 else "")
    if last in _DANGLING:
        return bool(_ACTION.search(clause) or _FIELD_WORD.search(clause))
    return last in _DANGLING_NOUNS and previous in {"use", "to", "in", "into", "at", "from", "set", "change",
                                                    "move", "put", "start", "the", "a", "that", "which"}


def _neighbour_slot(slot: Any, direction: str) -> int | None:
    if not isinstance(slot, int):
        return None
    direction = direction.lower()
    row, column = (slot - 1) // 3, (slot - 1) % 3
    if direction == "right" and column < 2:
        target = slot + 1
    elif direction == "left" and column > 0:
        target = slot - 1
    elif direction in {"back", "behind", "up"} and row < 3:
        target = slot + 3
    elif direction in {"front", "forward", "down"} and row > 0:
        target = slot - 3
    else:
        return None
    return target if target != 12 else None


def _labware_options(config: dict[str, Any]) -> tuple[Option, ...]:
    replacement = {"plate": "the dilution plate", "paper": "the paper print plate", "tuberack": "the vial rack",
                   "tiprack": "the tip rack"}
    options = []
    for number, role in enumerate(("plate", "paper", "tuberack", "tiprack"), start=1):
        slot = slot_of(config, role)
        where = "off deck" if is_off_deck(slot) else f"slot {slot}"
        options.append(Option(str(number), f"the {LABWARE_NAMES[role]} ({where})", replacement[role]))
    return tuple(options)


def _extract_facts(clause: str, ctx: TurnContext) -> list[PhysicalFact]:
    facts: list[PhysicalFact] = []
    if _REPORT_DILUTIONS.search(clause):
        facts.append(PhysicalFact("dilutions_prepared", text=clause))
    if _REPORT_TIPS.search(clause):
        facts.append(PhysicalFact("tips_replaced", text=clause))
    if _REPORT_PLATE_REPLACED.search(clause) and (_REPORT_ACTION.search(clause) or _REPORT_STATE.search(clause)
                                                  or re.search(r"\b(?:is|are|was|were|now)\b", clause, re.I)):
        facts.append(PhysicalFact("plate_replaced", text=clause))
    if facts:
        return facts
    if _REPORT_IDIOM.search(clause) or not (_REPORT_ACTION.search(clause) or _REPORT_STATE.search(clause)):
        return facts
    parts = re.split(r"\s+and\s+(?=(?:the\s+)?(?:vial|tube|tip|paper|dilution|well|96|plate|rack))", clause, flags=re.I)
    for part in parts:
        mentions = labware_mentions(part)
        roles = list(dict.fromkeys(role for role, _, _ in mentions))
        bare_plate = any(role == "plate" and re.fullmatch(r"(?i)plates?", part[start:end].strip())
                         for role, start, end in mentions)
        bare_rack = any(role == "tuberack" and re.fullmatch(r"(?i)rack", part[start:end].strip())
                        for role, start, end in mentions)
        if ctx.config and ((bare_plate and not is_off_deck(slot_of(ctx.config, "paper")))
                           or (bare_rack and not is_off_deck(slot_of(ctx.config, "tiprack")))):
            facts.append(PhysicalFact("unclear", None, None, part))
            continue
        slot_match = _SLOT_NUMBER.search(part)
        slot: Any = int(next(group for group in slot_match.groups() if group)) if slot_match else None
        if slot is None and _OFF.search(part) and re.search(r"\b(?:took|removed|pulled|lifted|off)\b", part, re.I):
            slot = OFF_DECK
        used = _USED_TO_BE.search(part)
        if used and slot is None:
            for role, _, _ in labware_mentions(used.group(1)):
                if role in ctx.previous_slots:
                    slot = ctx.previous_slots[role]
        if len(roles) == 1 and slot is not None:
            facts.append(PhysicalFact("location", roles[0], slot, part))
        elif roles:
            facts.append(PhysicalFact("unclear", roles[0] if len(roles) == 1 else None, slot, part))
    return facts


# ── the analysis ────────────────────────────────────────────────────────────────

def classify_confirmation(text: str) -> str | None:
    verdict = parse_confirmation(text)
    if verdict:
        return verdict
    return "uncertain" if _UNCERTAIN.match(text) else None


def analyze_turn(text: str, context: TurnContext | None = None) -> TurnAnalysis:
    ctx = context or TurnContext(config={})
    normalized = normalize_text(text)
    value = normalized.text.strip()
    analysis = TurnAnalysis(normalized)
    if normalized.corrections:
        analysis.notes.append("I read " + "; ".join(f'"{a}" as "{b}"' for a, b in normalized.corrections) + ".")
    if not value or not re.search(r"[A-Za-z0-9]", value):
        analysis.kind = "empty"
        return analysis
    approved = named_approval(value)
    if approved is not None:
        analysis.kind, analysis.confirmation = "confirmation", "yes"
        analysis.details["proposal_id"] = approved
        return analysis
    analysis.confirmation = classify_confirmation(value)
    if analysis.confirmation in {"yes", "no"}:
        analysis.kind = "confirmation"
        return analysis
    if _CANCEL.match(value):
        analysis.kind = "cancel"
        return analysis
    if analysis.confirmation == "uncertain":
        analysis.kind = "uncertain"
        return analysis
    if _INJECTION.search(value):
        analysis.flags.add("injection")
    if _AUTHORITY.search(value):
        analysis.flags.add("authority")
    if _START_OVER.search(value) and not re.search(r"\d|\b[A-H]\d", value):
        analysis.kind = "start_over"
        return analysis
    if _GO_BACK_ALONE.match(value):
        analysis.kind = "undo"
        analysis.details["undo"] = {"revision": None, "prefix": None, "original": False, "ambiguous": True}
        return analysis
    if wants_to_run(value):
        analysis.kind = "run"
        return analysis
    if _RUN_LIKE.match(value) and "injection" not in analysis.flags:
        analysis.kind = "run_like"
        return analysis

    remaining, references = _strip_reference_material(value, analysis.flags)
    informational: list[str] = list(references)
    actionable: list[str] = []
    superseded: list[str] = []
    negated: list[str] = []
    claims: list[tuple[str, str]] = []
    claim_clauses: list[str] = []
    for clause, in_question in _clauses(remaining):
        plain = clause.replace("(quoted text)", "").strip(" ,.;:-")
        if not re.search(r"[A-Za-z0-9]", plain) or _ACKNOWLEDGEMENT.fullmatch(plain) or _PROPOSAL_META.match(plain):
            continue
        approved = named_approval(plain)
        if approved is not None:
            analysis.details["approval_with_request"] = approved     # "Yes, I approve proposal #1. Now ..."
            continue
        except_match = _EXCEPT.search(clause)
        if except_match:
            target = except_match.group(1).strip(" .!")
            actionable.append(f"change {target}")
            analysis.restrict_paths = tuple(fields_mentioned(target))
            analysis.flags.add("except")
            continue
        if _DOUBLE_NEGATIVE.search(clause):
            analysis.flags.add("double_negative")
            negated.append(clause)
            continue
        if _HYPOTHETICAL_START.match(clause) or _HYPOTHETICAL_ANY.search(clause):
            if _APPROVAL_QUESTION.search(clause):
                analysis.details["approval_question"] = True
            analysis.flags.add("hypothetical")
            informational.append(clause)
            continue
        if _NEGATION.match(clause) or _KEEP_AS_IS.match(clause):
            negated.append(clause)
            continue
        if not in_question and (_NOTIFY_WHEN.match(clause) or _CHECK_THAT.match(clause)):
            # "Let me know when the dilutions are ready." / "Make sure the dilutions are mixed." ask to be told or to
            # check; they are not reports that the dilutions exist.
            analysis.details["notify_when" if _NOTIFY_WHEN.match(clause) else "check_that"] = True
            analysis.flags.add("question")
            informational.append(clause)
            continue
        temporal = None if in_question else TEMPORAL_FUTURE.search(clause)
        if temporal:
            # "Once the dilutions are made, print them onto paper column 2": the order of the steps in the run, not a
            # report that the dilutions already exist. The rest of the clause is read on its own.
            when = temporal.group(0).strip(" ,;")
            rest = re.sub(r"\s+([.,;!?])", r"\1", re.sub(r"\s{2,}", " ", clause[:temporal.start()] + " "
                                                          + clause[temporal.end():]))
            rest = re.sub(r"[\s,;:-]+([.!?]*)$", r"\1", rest).strip(" ,;:-")
            analysis.flags.add("temporal")
            informational.append(when)
            analysis.notes.append(f'I read "{when}" as when that happens in the run, not as a report that it has already '
                                  "happened.")
            if not re.search(r"[A-Za-z0-9]", rest.replace("(quoted text)", "")):
                continue
            clause = rest
        polite = bool(_POLITE_REQUEST.match(clause))
        if not polite and (in_question or _INTERROGATIVE.match(clause)):
            if _HISTORY.search(clause):
                analysis.details["history"] = True
            if _APPROVAL_QUESTION.search(clause):
                analysis.details["approval_question"] = True
            if _MODEL_QUESTION.search(clause):
                analysis.details["model_question"] = True
            arithmetic = arithmetic_answer(clause)
            if arithmetic:
                analysis.details["arithmetic"] = arithmetic
            analysis.flags.add("question")
            informational.append(clause)
            continue
        if _HISTORY.search(clause) and re.match(r"^\s*(?:show|list|give|tell)\b", clause, re.I):
            analysis.details["history"] = True
            informational.append(clause)
            continue
        if _UNDO.search(clause):
            target = _UNDO_TARGET.search(clause)
            group = _RESTORE_GROUP.search(clause)
            analysis.details["undo"] = {
                "revision": int(target.group(1)) if target else None,
                "prefix": _UNDO_GROUPS.get(group.group(1).lower()) if group else None,
                "original": bool(re.search(r"\b(?:original|initial|startup|starting|default)\b", clause, re.I)),
            }
            continue
        if _FUTURE_PHYSICAL.match(clause):
            # Not a report (nothing has moved yet) and not quite an instruction: ask first.
            instruction = _FUTURE_TIME.sub("", _FUTURE_SUBJECT.sub("", clause, count=1)).strip(" ,.;!")
            analysis.details.setdefault("future_plans", []).append((clause, instruction))
            continue
        facts = _extract_facts(clause, ctx)
        if facts:
            analysis.facts.extend(facts)
            tail = _PREPARED_VOLUME_TAIL.search(clause)
            if tail and all(fact.kind == "dilutions_prepared" for fact in facts):
                actionable.append(tail.group(1))     # "..., each well has about 190 µL left" is a value, not a report
            continue
        unsupported = next((reason for pattern, reason in _UNSUPPORTED if pattern.search(clause)), None)
        if unsupported == _PRINT_ONE_REASON and ctx.config:
            unsupported = _named_dilutions_reason(clause, ctx.config)
            if unsupported is None:          # every dilution of the plan is named: an ordinary print instruction
                if not _plan_prints(ctx.config):
                    # printing is off, so this asks for it to be turned on (the session proposes that change itself)
                    analysis.details["enable_print"] = clause
                    analysis.details["enable_print_values"] = bool(_VALUE_TOKEN.search(_FACTOR_WORD.sub(" ", clause)))
                actionable.append(clause)
                continue
        if unsupported:
            analysis.details.setdefault("unsupported", []).append(unsupported)
            # "prepare 4 dilutions, 2x ... using the paper as the source": a request that only adds an impossible liquid
            # path keeps its supported values (slot numbers that only say where plates are do not count as values)
            values = re.sub(r"\b(?:deck\s+)?slot\s*\d{1,2}\b|\b(?:in|at|to|into|from)\s+\d{1,2}\b", " ", clause)
            if unsupported == _LIQUID_PATH_REASON and (_POLITE_REQUEST.match(clause) or _IMPERATIVE.match(clause)
                                                       or _LETS.match(clause) or _DESIRE.match(clause)) \
                    and re.search(r"\d|\b[A-H]\d{1,2}\b", values) and _FIELD_WORD.search(clause):
                actionable.append(clause)
            continue
        claim = _CLAIM.match(clause)
        if claim and not _ACTION.match(clause) and fields_mentioned(claim.group(1)) \
                and not labware_mentions(claim.group(1)):
            claims.append((claim.group(1).strip(), claim.group(2).strip()))
            claim_clauses.append(clause)
            continue
        imperative = (polite or _IMPERATIVE.match(clause) or _LETS.match(clause)
                      or (_DESIRE.match(clause) and not _DESIRE_TO_KNOW.match(clause)
                          and (_FIELD_WORD.search(clause) or _ACTION.search(clause)))
                      or _RELATIVE_ALONE.match(clause)
                      # a short statement of a setting and a value, without a verb: "Sorry, I was wrong, the dye is
                      # in vial B1." (questions, hypotheticals, reports and negations were already taken out above)
                      or (len(clause.split()) <= 12 and _FIELD_WORD.search(clause)
                          and (_VALUE_TOKEN.search(clause) or _VAGUE.search(clause))))
        if imperative:
            before, after = _split_correction(clause)
            if before:
                superseded.append(before)
                analysis.flags.add("self_correction")
            actionable.append(clause)
            continue
        informational.append(clause)
        analysis.flags.add("chat")

    if actionable and claim_clauses:
        # "The first column of tips is already used, start at A2": the statement says which setting is meant
        informational.extend(claim_clauses)
    analysis.actionable = " ".join(actionable).strip()
    analysis.informational = "\n".join(part for part in informational if part.strip()).strip()
    analysis.superseded = " ".join(superseded).strip()
    if claims:
        analysis.details["claims"] = claims
    if negated:
        analysis.details["negated"] = negated

    if "undo" in analysis.details and not analysis.actionable:
        analysis.kind = "undo"
        return analysis
    future = analysis.details.get("future_plans")
    if future and analysis.actionable:
        for clause, _ in future:
            analysis.notes.append(f'You said "{clause}" - a plan for later, so it is not part of this proposal. Say it '
                                  "as an instruction if the plan should change.")
    elif future and not analysis.facts:
        analysis.kind = "future_plan"
        analysis.details["future_instruction"] = future[0][1]
        return analysis
    if analysis.facts:
        analysis.kind = "physical_report"
        return _check_actionable(analysis, ctx) if analysis.actionable else analysis
    if "double_negative" in analysis.flags and not analysis.actionable:
        analysis.kind = "double_negative"
        return analysis
    if analysis.actionable:
        return _check_actionable(analysis, ctx)
    if "unsupported" in analysis.details:
        analysis.kind = "unsupported"
    elif claims:
        analysis.kind = "claim"
    elif negated:
        analysis.kind = "negated"
    elif analysis.details.get("history"):
        analysis.kind = "history"
    elif analysis.informational and not (analysis.flags & {"injection", "authority"}):
        # "3" or "the vial rack" is chat (it may answer a pending question); "explain SERS" is a question
        asked = analysis.flags & {"question", "hypothetical", "quoted", "pasted"} or "?" in value
        analysis.kind = "question" if asked or analysis.details.get("arithmetic") else "chat"
    elif "injection" in analysis.flags:
        analysis.kind = "injection"
    elif "authority" in analysis.flags:
        analysis.kind = "authority"
    else:
        analysis.kind = "question" if analysis.informational else "chat"
    return analysis


def _check_actionable(analysis: TurnAnalysis, ctx: TurnContext) -> TurnAnalysis:
    """Turn a message with instructions into instruction/mixed, or a precise clarification."""
    action = analysis.actionable
    report = analysis.kind == "physical_report"
    base_kind = "mixed" if analysis.informational and not report else ("physical_report" if report else "instruction")
    if _incomplete(action):
        analysis.kind = "incomplete"
        analysis.clarification = f'Your message looks unfinished ("{action.strip()}"). What exactly should change?'
        return analysis
    paths = fields_mentioned(action)
    if (paths and all(path in _NEEDS_VALUE for path in paths) and not _VALUE_TOKEN.search(action)
            and not _NO_VALUE_NEEDED.search(action) and not _VAGUE.search(action)
            and not _PRONOUN_OBJECT.search(action) and not _SAME_AS_BEFORE.search(action)):
        analysis.kind = "incomplete"
        analysis.clarification = f"What should the {field_label(paths[0]).lower()} be? Please give the exact value."
        return analysis
    if _RELATIVE_ALONE.match(action):
        analysis.kind = "ambiguous_quantity"
        analysis.clarification = ('What should that apply to? For example: "twice the drop volume", "half the total '
                                  'volume per dilution" or "one more drop per position".')
        return analysis
    if _PRONOUN_OBJECT.search(action) or _SAME_AS_BEFORE.search(action):
        reference = _resolve_references(analysis, ctx)
        if reference is not None:
            return reference
        action = analysis.actionable
    bare = _BARE_NUMBER.match(action)
    context_fields = set(fields_mentioned(analysis.informational)) if analysis.informational else set()
    if bare and not fields_mentioned(action) and len(context_fields) != 1 and len(ctx.pending_paths) != 1:
        number = bare.group(1)
        analysis.kind = "ambiguous_number"
        options = []
        value = float(number)
        if value.is_integer() and 1 <= value <= 11:
            options.append((f"OT-2 deck SLOT {int(value)} (for which labware? say it again with the labware)",
                            f"deck slot {int(value)}"))
        if 1 <= value <= 18.5:
            options.append((f"a drop volume of {number} µL", f"set the drop volume to {number} µL"))
        if value.is_integer() and 1 <= value <= 8:
            options.append((f"{int(value)} dilutions", f"use {int(value)} dilutions"))
        if value.is_integer() and 1 <= value <= 10:
            options.append((f"{int(value)} drops per paper position", f"print {int(value)} drops per paper position"))
        if value.is_integer() and 1 <= value <= 12:
            options.append((f"paper column {int(value)} as the first print column",
                            f"start printing at paper column {int(value)}"))
        if not options:
            analysis.clarification = f'What does "{number}" refer to? Please say it with its unit or setting.'
            return analysis
        analysis.ambiguity = Ambiguity(0, len(action), number, f'What does "{number}" refer to?',
                                       tuple(Option(str(i), label, replacement)
                                             for i, (label, replacement) in enumerate(options, start=1)))
        analysis.details["replace_whole"] = True
        return analysis
    vague = next((match for match in _VAGUE.finditer(action)
                  if not _EXPLICIT_COUNT_BEFORE.search(action[:match.start()])), None)
    if vague:
        analysis.kind = "vague_quantity"
        noun = vague.group(2)
        analysis.clarification = f'How many {noun} exactly? ("{vague.group(0)}" is not a number I can use.)'
        analysis.details["vague_span"] = (vague.start(1), vague.end(1))
        return analysis
    reference = _resolve_references(analysis, ctx)
    if reference is not None:
        return reference
    analysis.kind = base_kind
    return analysis


def _resolve_references(analysis: TurnAnalysis, ctx: TurnContext) -> TurnAnalysis | None:
    """Pronouns and vague places: resolve only when exactly one referent exists, else ask."""
    action = analysis.actionable
    recent = [role for role in ctx.recent_labware if role]
    named = [role for role, _, _ in labware_mentions(action)]
    pronoun = _PRONOUN_OBJECT.search(action)
    slot_only = _SLOT_ONLY_VERB.match(action) or re.fullmatch(r"\s*(?:deck\s+)?slot\s+(\d{1,2})\s*(?:instead|please)?"
                                                              r"\s*[.!]*\s*", action, re.I)
    same = _SAME_AS_BEFORE.search(action)
    if same:
        analysis.kind = "reference"
        analysis.clarification = (f'"{same.group(0)}" could mean several things. Please name the labware, well or tip '
                                  "and the exact value.")
        return analysis
    referent: str | None = None
    if pronoun or (slot_only and not named):
        candidates = list(dict.fromkeys(recent))
        if len(candidates) == 1 and not named:
            referent = candidates[0]
            term = pronoun.group(1) if pronoun else "that"
            analysis.notes.append(f'I took "{term}" to mean the {LABWARE_NAMES[referent]} (the labware you were '
                                  "just talking about).")
            if pronoun:
                action = action[:pronoun.start(1)] + f"the {LABWARE_NAMES[referent].lower()}" + action[pronoun.end(1):]
            else:
                action = f"move the {LABWARE_NAMES[referent].lower()} to deck slot {slot_only.group(1)}"
        elif not named:
            analysis.kind = "reference"
            term = pronoun.group(1) if pronoun else "it"
            analysis.ambiguity = Ambiguity(pronoun.start(1) if pronoun else 0, pronoun.end(1) if pronoun else 0,
                                           term, f'You said "{term}". Which labware do you mean?',
                                           _labware_options(ctx.config))
            analysis.details["pronoun_rewrite"] = bool(pronoun)
            return analysis
    used = _USED_TO_BE.search(action)
    if used:
        roles = [role for role, _, _ in labware_mentions(used.group(1))]
        slot = ctx.previous_slots.get(roles[0]) if len(roles) == 1 else None
        if slot is None:
            analysis.kind = "reference"
            analysis.clarification = (f'I have no record of where "{used.group(1).strip()}" used to be in this session. '
                                      "Please give the slot number.")
            return analysis
        where = "OFF DECK" if is_off_deck(slot) else f"deck slot {slot}"
        action = action[:used.start()] + ("off the deck" if is_off_deck(slot) else f"in {where}") + action[used.end():]
        analysis.notes.append(f"The {LABWARE_NAMES[roles[0]]} used to be in {where.lower()} (from this session's history).")
    relative = _RELATIVE_SLOT.search(action)
    if relative:
        roles = [role for role, _, _ in labware_mentions(action[:relative.start()])] or ([referent] if referent else [])
        roles = list(dict.fromkeys(role for role in roles if role))
        if len(roles) != 1:
            analysis.kind = "reference"
            analysis.clarification = "Which labware should move one slot over? Please name it and give the slot number."
            return analysis
        current = slot_of(ctx.config, roles[0])
        target = _neighbour_slot(current, relative.group(1))
        if target is None:
            analysis.kind = "reference"
            analysis.clarification = (f"There is no usable slot one {relative.group(1).lower()} of "
                                      f"{'OFF DECK' if is_off_deck(current) else f'slot {current}'} "
                                      "(slot 12 is the trash). Please give the slot number.")
            return analysis
        action = action[:relative.start()] + f"to deck slot {target}" + action[relative.end():]
        analysis.notes.append(f"One slot {relative.group(1).lower()} of slot {current} (seen from the front of the "
                              f"robot) is slot {target}.")
    analysis.actionable = action
    return None


# ── pending proposals: partial approval and selection ──────────────────────────

_PARTIAL = re.compile(
    r"^\s*(?:yes|yeah|ok(?:ay)?|sure|approve|apply)\b[,\s]*(?:to|for|on|with|just|only)?\s*(?:the\s+)?(.+?)\s*"
    r"(?:[,.;]\s*)?\b(?:but|except|and)\s+(?:not|leave|keep|don'?t\s+(?:change|touch|apply)|no|skip|without)\s+"
    r"(?:the\s+)?(.+)$", re.I)
_ONLY = re.compile(r"^\s*(?:(?:yes|ok(?:ay)?|sure|apply|approve)[,\s]+)?(?:only|just)\s+(?:apply\s+|do\s+|approve\s+|"
                   r"keep\s+)?(?:the\s+)?(.+?)\s*[.!]*\s*$", re.I)
_REJECT_THEN_APPROVE = re.compile(
    r"^\s*(?:no|not|don'?t\s+(?:change|touch|apply)|leave|keep|skip|without)\s+(?:the\s+|to\s+)?(.+?)\s*"
    r"(?:[,;]\s*|\s+)(?:but\s+)?(?:yes|ok(?:ay)?|apply|approve|do)\s+(?:to\s+)?(?:the\s+)?(.+?)\s*[.!]*$", re.I)
_SELECT = re.compile(r"^\s*(?:(?:yes|ok(?:ay)?|keep|apply|approve|only|just)[,:\s]+)*(?:changes?\s+|numbers?\s+|#)?"
                     r"(\d{1,2}(?:\s*(?:,|and|&|\s)\s*#?\d{1,2})*)\s*(?:only)?\s*[.!]*\s*$", re.I)


@dataclass
class Selection:
    approved: list[str]
    rejected: list[str]
    unmatched: list[str]


def _match_paths(phrase: str, paths: list[str]) -> list[str]:
    mentioned = fields_mentioned(phrase)
    matched = []
    for path in paths:
        group = next((members for key, members in PATH_GROUPS.items() if path in members), (path,))
        if path in mentioned or any(member in mentioned for member in group):
            matched.append(path)
    return matched


def parse_selection(text: str, pending_paths: list[str]) -> Selection | None:
    """Which pending changes the scientist approved, from explicit words or numbers; None if not a selection."""
    value = normalize_text(text).text.strip()
    numbers = _SELECT.match(value)
    if numbers:
        picked = sorted({int(n) for n in re.findall(r"\d{1,2}", numbers.group(1))})
        if all(1 <= n <= len(pending_paths) for n in picked):
            approved = [pending_paths[n - 1] for n in picked]
            return Selection(approved, [p for p in pending_paths if p not in approved], [])
        return Selection([], [], [numbers.group(1)])
    partial = _PARTIAL.match(value)
    reverse = _REJECT_THEN_APPROVE.match(value)
    only = _ONLY.match(value)
    if partial:
        approve_phrase, reject_phrase = partial.group(1), partial.group(2)
    elif reverse:
        reject_phrase, approve_phrase = reverse.group(1), reverse.group(2)
    elif only:
        approve_phrase, reject_phrase = only.group(1), ""
    else:
        return None
    approved = _match_paths(approve_phrase, pending_paths)
    rejected = _match_paths(reject_phrase, pending_paths) if reject_phrase else []
    unmatched = []
    if not approved:
        unmatched.append(approve_phrase)
    if reject_phrase and not rejected:
        unmatched.append(reject_phrase)
    approved = [path for path in approved if path not in rejected]
    return Selection(approved, [p for p in pending_paths if p not in approved], unmatched)
