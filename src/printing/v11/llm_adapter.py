"""Version 11 LLM adapters - the ONLY place that touches a cloud LLM.

Everything below the adapter (agent_core) is plain Python, so the home laptop
can drive the whole agent with ScriptedAdapter / RuleBasedAdapter and never
authenticate to anything. The work laptop uses VertexAdapter, which reuses the
repository's existing, already-configured connection via Config.get_llm().

    adapter.interpret(text, history) -> structured intent dict
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Protocol

from .labware import LABWARE

#: Rendered from the registry so prompt text can never drift from the hardware.
_LABWARE_LINES = "\n".join(
    f'              {"| " if index else "  "}"{key}" ({spec["description"]}, '
    f'usual slot {spec["usual_slot"]})'
    for index, (key, spec) in enumerate(LABWARE.items())
)

INTENT_SCHEMA = f"""Return ONLY a JSON object (no prose, no code fence) describing what the
scientist asked for. Include ONLY keys they actually determined; omit everything
else so the template default stands.

  "workflow": "dilution" | "standard_print" | "clover_print"

  Shared:
    "pipette_tip_reuse": true/false   ("reuse the tip" true; "fresh/clean tip
                                       every drop" false)
    "return_tips": true/false         ("return the tips" / "drop used tips")
    "start_tip": e.g. "A1"
    "air_gap_ul": number
    "aspirate_flow_rate_ul_s", "dispense_flow_rate_ul_s": numbers
    "post_aspirate_delay_s", "post_dispense_delay_s": numbers

  Source labware (same shape for stock_/diluent_/destination_/source_ prefixes):
    "<p>_type": one of the registered labware keys below
{_LABWARE_LINES}
              ("corning_plate" is the existing/normal/regular 96-well plate;
               "well_plate" is the BRAND Ref. 781662 plate)
    "<p>_slot": integer 1-11
    "<p>_well" or "<p>_wells": e.g. "C11" / ["A1","A2"]
    "<p>_loaded_volume_ul", "<p>_minimum_remaining_ul", "<p>_aspirate_height_mm"

  standard_print:
    "paper_slot": 5 or 11
    "print_height_mm": number  ("closer to the paper" = smaller)
    "droplet_volume_ul": number
    "targets": ["A1","B1"]  OR  "target_selection": {{"column": 3}} /
               {{"column": 1, "rows": ["A","B","C"]}} / {{"row": "B", "columns": [1,2]}}
    "groups": [{{"targets": [...] or "target_selection": {{...}},
                "droplets": N, "source_well": "A1"}}]
    "droplets_per_target": N
    "replicates": N
    "order": "layer_major" | "target_major"
    "inter_drop_delay_s", "inter_layer_delay_s", "inter_target_delay_s": numbers

  clover_print:
    "paper_slot", "print_height_mm", "droplet_volume_ul"
    "clover_references": ["B3"]  or
    "clovers": [{{"reference":"B3","source_well":"A1","layers":2}}]
    "separation_x_mm" / "separation_y_mm": the ACTUAL droplet-to-droplet
        distance the user asked for, in mm. PREFER THESE over half values.
        "2 mm apart" -> separation_x_mm 2, separation_y_mm 2.
    "half_width_mm" / "half_height_mm": only if the user speaks in half-offsets.
    "scale_separation": number. "make it tighter" -> 0.5; "twice as wide" -> 2.
    "x_offset_mm", "y_offset_mm", "rotation_deg": numbers
    "layers": N
    "inter_drop_delay_s", "inter_layer_delay_s", "inter_clover_delay_s"

  dilution:
    "mode": "single" | "series"
    "stock_volume_ul", "diluent_volume_ul", "final_volume_ul": numbers
    "dilution_factor": number  ("5x dilution", "1 to 5" -> 5)
    "series_wells": ["A1","A2"]  or
    "series_start_well": "A1", "series_direction": "row"|"column",
    "series_steps": N
    "transfer_volume_ul": number
    "max_chunk_ul": number
    "mix_enabled": true/false, "mix_cycles": N, "mix_volume_ul": number
    "after_transfer_s", "after_mix_s": numbers

Rules:
- Never invent labware outside vial_rack / corning_plate / well_plate.
- "column N" means the number; rows are letters A-H.
- If a required value is genuinely missing, still return what you have and add
  "needs": ["what you still need"].
"""


class LLMAdapter(Protocol):
    def interpret(self, text: str, history: list[Any] | None = None) -> dict[str, Any]:
        ...


def extract_json(text: str) -> dict[str, Any]:
    """Pull one JSON object out of a model reply."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"no JSON object in reply: {cleaned[:300]}")
    return json.loads(cleaned[start : end + 1])


class ScriptedAdapter:
    """Replay a fixed list of intents. Home-laptop testing, no cloud."""

    def __init__(self, intents: list[dict[str, Any]]) -> None:
        self._intents = list(intents)
        self._index = 0

    def interpret(self, text: str, history: list[Any] | None = None) -> dict[str, Any]:
        if self._index >= len(self._intents):
            raise IndexError(f"ScriptedAdapter ran out of intents at turn {self._index}")
        intent = self._intents[self._index]
        self._index += 1
        return dict(intent)


class CallableAdapter:
    """Wrap any function of (text, history) -> intent dict."""

    def __init__(self, fn: Callable[[str, list[Any] | None], dict[str, Any]]) -> None:
        self._fn = fn

    def interpret(self, text: str, history: list[Any] | None = None) -> dict[str, Any]:
        return self._fn(text, history)


class RuleBasedAdapter:
    """A small deterministic English parser for offline testing.

    Deliberately NOT a production path - it covers the phrasings used by the
    home-laptop test suite so the agent core can be exercised without a cloud
    LLM. The work laptop always uses VertexAdapter.
    """

    _LABWARE_WORDS = [
        (r"\bbrand\b|\b781662\b", "well_plate"),
        (r"\bcorning\b|\bnormal 96\b|\bregular 96\b|\bexisting 96\b|\bnormal plate\b",
         "corning_plate"),
        (r"\bvial\b|\btube ?rack\b|\bvial rack\b|\b20 ?ml\b", "vial_rack"),
    ]

    def interpret(self, text: str, history: list[Any] | None = None) -> dict[str, Any]:
        low = text.lower()
        intent: dict[str, Any] = {}

        if re.search(r"\bclover\b", low):
            intent["workflow"] = "clover_print"
        elif re.search(
            r"\bdilut|\bserial\b|\bstock\b|\bdiluent\b|\bmix\b|\bfinal volume\b", low
        ):
            intent["workflow"] = "dilution"
        elif re.search(r"\bprint\b|\bdroplet\b|\bdrop\b", low):
            intent["workflow"] = "standard_print"

        for pattern, key in self._LABWARE_WORDS:
            if re.search(pattern, low):
                prefix = "stock" if intent.get("workflow") == "dilution" else "source"
                intent[f"{prefix}_type"] = key
                break

        slot = re.search(r"\bslot (\d+)", low)
        if slot:
            value = int(slot.group(1))
            if re.search(r"paper", low):
                intent["paper_slot"] = value
            else:
                prefix = "stock" if intent.get("workflow") == "dilution" else "source"
                intent[f"{prefix}_slot"] = value
        paper_slot = re.search(r"paper (?:in )?slot (\d+)", low)
        if paper_slot:
            intent["paper_slot"] = int(paper_slot.group(1))

        well = re.search(r"\b(?:from|pull from|well)\s+([a-h]\d{1,2})\b", low)
        if well:
            prefix = "stock" if intent.get("workflow") == "dilution" else "source"
            intent[f"{prefix}_well"] = well.group(1).upper()

        column = re.search(r"\bcolumn (\d+)", low)
        if column and intent.get("workflow") != "clover_print":
            intent["target_selection"] = {"column": int(column.group(1))}

        volume = re.search(r"([\d.]+)\s*(?:ul|µl|microliter|micro liter)", low)
        if volume:
            intent["droplet_volume_ul"] = float(volume.group(1))

        drops = re.search(
            r"\b(one|two|three|four|five|\d+)\s+(?:[\d.]+\s*(?:ul|µl)\s+)?drops?\b", low
        )
        if drops:
            words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
            token = drops.group(1)
            intent["droplets_per_target"] = words.get(token, None) or int(token)

        layer_delay = re.search(
            r"(?:wait|delay)\s+(?:about\s+)?([\d.]+)\s*(?:s|sec|second)", low)
        if layer_delay and re.search(r"layer|between drops|drying", low):
            intent["inter_layer_delay_s"] = float(layer_delay.group(1))

        if re.search(r"reuse (?:the )?(?:pipette )?tip|same tip", low):
            intent["pipette_tip_reuse"] = True
        if re.search(r"fresh tip|clean tip|new tip (?:for )?(?:every|each)", low):
            intent["pipette_tip_reuse"] = False

        reference = re.search(r"\b(?:around|near|at)\s+([a-h]\d{1,2})\b", low)
        if reference and intent.get("workflow") == "clover_print":
            intent["clover_references"] = [reference.group(1).upper()]
        separation = re.search(r"([\d.]+)\s*mm", low)
        if separation and intent.get("workflow") == "clover_print":
            value = float(separation.group(1))
            intent["separation_x_mm"] = value
            intent["separation_y_mm"] = value
        if re.search(r"tighter|closer together", low):
            intent["scale_separation"] = 0.5
        if re.search(r"twice as wide|wider", low):
            intent["scale_separation"] = 2.0

        factor = re.search(r"(\d+)\s*x dilution|1 ?(?:to|:) ?(\d+)", low)
        if factor:
            intent["dilution_factor"] = float(factor.group(1) or factor.group(2))
        final = re.search(r"final volume (?:of )?([\d.]+)", low)
        if final:
            intent["final_volume_ul"] = float(final.group(1))
        if re.search(r"\bserial\b", low):
            intent["mode"] = "series"
        if re.search(r"don'?t mix|no mixing|skip mix", low):
            intent["mix_enabled"] = False
        return intent


class VertexAdapter:
    """Production adapter: the repository's existing GCloud/Vertex connection.

    Imports Config lazily so this module stays importable on a machine with no
    cloud credentials at all.
    """

    def __init__(self, temperature: float = 0) -> None:
        from src.core.config import Config

        self._llm = Config.get_llm(temperature=temperature)

    def interpret(self, text: str, history: list[Any] | None = None) -> dict[str, Any]:
        messages = [("system", INTENT_SCHEMA), *(history or []), ("human", text)]
        reply = self._llm.invoke(messages)
        return extract_json(getattr(reply, "content", str(reply)))
