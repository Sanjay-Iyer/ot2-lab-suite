"""Shared vocabulary for the conversational dye demo (scripts/ai_dye_demo.py).

Labware roles, the allowlist of fields a conversation may change, value
normalisation, and the few geometry facts the deterministic checks need.
Nothing in this module talks to an LLM or to a robot.
"""
from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

REPO = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO / "configs" / "workflows" / "defaults" / "ai_agent_dilution_print_demo.yaml"
MACHINE_PROFILE = REPO / "configs" / "machines" / "ot2_standard_printing_p20_v1.yaml"
LABWARE_DIR = REPO / "labware"

ROWS = tuple("ABCDEFGH")
DECK_SLOTS = tuple(range(1, 12))          # slot 12 is the fixed trash
OFF_DECK = "OFF_DECK"
EPSILON_UL = 0.01

LABWARE_ROLES = ("plate", "paper", "tuberack", "tiprack")
LABWARE_NAMES = {
    "plate": "96-well dilution plate",
    "paper": "Paper print plate",
    "tuberack": "Vial rack",
    "tiprack": "P20 tip rack",
}
MATERIAL_ROLES = ("sample", "solvent")
VIAL_NAMES = tuple(f"{row}{column}" for row in "AB" for column in range(1, 5))
TIP_ORDER = tuple(f"{row}{column}" for column in range(1, 13) for row in ROWS)
TIP_POLICIES = {
    "single_tip": "one tip for the entire run (every transfer, mix and print; liquids can carry over)",
    "per_liquid": "one tip per liquid (a tip is reused only for the same liquid)",
    "new_tip_every_transfer": "a new tip for every transfer and every printed position",
}


class FieldError(ValueError):
    """A proposed value that cannot be used for its field."""


# A proposed value found only in the scientist's earlier messages ("same thing but columns 4-6" keeps the rows of the
# request it revises): accepted, and shown for checking.
CARRIED_OVER = "carried over from earlier in the conversation"
# A proposed value the plan had after an earlier approved change of this session ("the factors I asked for at the very
# beginning"): state, not an invented value - accepted, and shown for checking under its own heading.
EARLIER_REVISION = "the value from an earlier revision of this session"


# ── formatting shared by the plan, the checks and the renderers ─────────────────

def fmt_num(value: float) -> str:
    number = float(value)
    if number and abs(number) < 0.1:
        return f"{number:.3g}"          # 0.005 mL must not be shown as 0.01 mL
    return f"{number:.2f}".rstrip("0").rstrip(".")


def positions_text(positions: Iterable[str], *, limit: int = 8) -> str:
    """'A1-A10' for a run along a row or down a column, else the positions (shortened past `limit`)."""
    names = list(positions)
    if not names:
        return "none"
    rows, columns = [name[0] for name in names], [int(name[1:]) for name in names]
    if len(names) > 1 and len(set(rows)) == 1 and columns == list(range(columns[0], columns[0] + len(names))):
        return f"{names[0]}-{names[-1]}"
    if len(names) > 1 and len(set(columns)) == 1 and \
            [ROWS.index(row) for row in rows] == list(range(ROWS.index(rows[0]), ROWS.index(rows[0]) + len(names))):
        return f"{names[0]}-{names[-1]}"
    if len(names) > limit:
        return ", ".join(names[:limit - 2]) + f", … , {names[-1]}"
    return ", ".join(names)


def fmt_ul(value: float) -> str:
    return f"{fmt_num(value)} µL"


def fmt_factor(value: float) -> str:
    return f"{fmt_num(value)}×"


def format_slot(value: Any) -> str:
    return "OFF DECK" if is_off_deck(value) else f"Slot {value}"


# ── value normalisation ─────────────────────────────────────────────────────────

_UNIT = re.compile(r"\s*(µl|μl|ul|microlit\w*|mm|x|×|fold)\s*$", re.I)


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool):
        raise FieldError(f"{what} must be a number, got {value!r}")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(_UNIT.sub("", value.strip()))
        except ValueError as exc:
            raise FieldError(f"{what} must be a number, got {value!r}") from exc
    else:
        raise FieldError(f"{what} must be a number, got {value!r}")
    if not math.isfinite(number):
        raise FieldError(f"{what} must be a finite number, got {value!r}")
    return number


def _integer(value: Any, what: str) -> int:
    number = _number(value, what)
    if not number.is_integer():
        raise FieldError(f"{what} must be a whole number, got {value!r}")
    return int(number)


def normalize_slot(value: Any) -> int | str:
    """A deck slot 1-11, or OFF_DECK for labware physically removed from the robot."""
    if isinstance(value, str):
        text = re.sub(r"[\s_\-]+", " ", value.strip().upper())
        if text in {"OFF DECK", "OFFDECK", "OFF THE DECK", "REMOVED", "OFF"}:
            return OFF_DECK
        match = re.fullmatch(r"(?:DECK\s+)?(?:SLOT\s*)?(\d{1,2})", text)
        if not match:
            raise FieldError(f"a deck location is a slot 1-11 or OFF DECK, got {value!r}")
        slot = int(match.group(1))
    else:
        slot = _integer(value, "a deck slot")
    if slot == 12:
        raise FieldError("slot 12 is the trash; labware goes in slots 1-11")
    if slot not in DECK_SLOTS:
        raise FieldError(f"deck slots are 1-11, got {value!r}")
    return slot


def is_off_deck(value: Any) -> bool:
    return isinstance(value, str) and value.strip().upper() == OFF_DECK


_ROW_COLUMN = re.compile(r"row\s+([A-Za-z])\s*,?\s*(?:and\s+)?col(?:umn)?\s+0*(\d{1,3})", re.I)
_WELL_FORM = re.compile(r"([A-Za-z])\s*[-_ ]?\s*0*(\d{1,3})")
_REVERSED_WELL = re.compile(r"0*(\d{1,2})\s*[-_ ]?\s*([A-Za-z])")


def parse_well_name(value: Any, *, rows: str = "ABCDEFGH", columns: int = 12, what: str = "a well") -> str:
    """Canonical well name: A1, a1, A01, A-1 and "row A column 1" all give A1.

    A10 stays A10. A reversed name (3A) or anything outside the labware (Z14, A25) is
    rejected; nothing is guessed.
    """
    text = str(value).strip()
    match = _ROW_COLUMN.fullmatch(text) or _WELL_FORM.fullmatch(text)
    if not match:
        reversed_name = _REVERSED_WELL.fullmatch(text)
        if reversed_name:
            raise FieldError(f"{what} is written row letter first (for example "
                             f"{reversed_name.group(2).upper()}{int(reversed_name.group(1))}), got {value!r}")
        raise FieldError(f"{what} looks like A1, got {value!r}")
    row, column = match.group(1).upper(), int(match.group(2))
    if row not in rows or not 1 <= column <= columns:
        raise FieldError(f"{what} must be {rows[0]}1-{rows[-1]}{columns}, got {value!r}")
    return f"{row}{column}"


def normalize_vial(value: Any) -> str:
    try:
        return parse_well_name(value, rows="AB", columns=4, what="a vial")
    except FieldError as exc:
        raise FieldError(f"vials are A1-B4 on the 8-vial rack, got {value!r}") from exc


def normalize_tip(value: Any) -> str:
    try:
        return parse_well_name(value, rows="ABCDEFGH", columns=12, what="a tip")
    except FieldError as exc:
        # keep the hint for a reversed name ("1G"); "Z99" needs only the range, not the same sentence twice
        hint = f" ({exc})" if "row letter first" in str(exc) else ""
        raise FieldError(f"tips are A1-H12 on the 96-tip rack, got {value!r}{hint}") from exc


def normalize_row(value: Any) -> str:
    text = re.sub(r"^ROW\s*", "", str(value).strip().upper())
    if text not in ROWS:
        raise FieldError(f"plate rows are A-H, got {value!r}")
    return text


def normalize_plate_column(value: Any) -> str:
    column = _integer(re.sub(r"(?i)^column\s*", "", str(value).strip()), "a plate column")
    if not 1 <= column <= 12:
        raise FieldError(f"plate columns are 1-12, got {value!r}")
    return str(column)


def normalize_paper_column(value: Any) -> int:
    column = _integer(re.sub(r"(?i)^column\s*", "", str(value).strip()), "a paper column")
    if not 1 <= column <= 12:
        raise FieldError(f"paper columns are 1-12, got {value!r}")
    return column


def _tidy(number: float) -> int | float:
    return int(number) if float(number).is_integer() else float(number)


def normalize_factors(value: Any) -> list[int | float]:
    items = value if isinstance(value, (list, tuple)) else [value]
    factors = [_tidy(_number(item, "a dilution factor")) for item in items]
    if not factors:
        raise FieldError("dilution factors must name at least one factor")
    return factors


_VOLUME = re.compile(
    r"([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)\s*"
    r"(µl|μl|ul|microlit(?:er|re)s?|ml|millilit(?:er|re)s?|nl|nanolit(?:er|re)s?|l|lit(?:er|re)s?)?",
    re.I,
)
_VOLUME_SCALE = {"µl": 1.0, "ul": 1.0, "micro": 1.0, "ml": 1000.0, "milli": 1000.0, "nl": 0.001,
                 "nano": 0.001, "l": 1_000_000.0, "lit": 1_000_000.0}


def volume_in_microlitres(value: Any, what: str = "a volume") -> float:
    """5, "5 µL", "5 uL" and "0.005 mL" are all 5.0; other units are rejected, never guessed."""
    if isinstance(value, bool):
        raise FieldError(f"{what} must be a number, got {value!r}")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        match = _VOLUME.fullmatch(value.strip())
        if not match:
            raise FieldError(f"{what} must look like 5 µL, got {value!r}")
        unit = (match.group(2) or "µl").lower().replace("μ", "µ")
        key = next(prefix for prefix in ("µl", "ul", "micro", "ml", "milli", "nl", "nano", "lit", "l")
                   if unit.startswith(prefix))
        number = float(match.group(1)) * _VOLUME_SCALE[key]
    else:
        raise FieldError(f"{what} must be a number, got {value!r}")
    if not math.isfinite(number):
        raise FieldError(f"{what} must be a finite number, got {value!r}")
    return round(number, 6)


def normalize_volume(value: Any) -> float:
    volume = volume_in_microlitres(value)
    if volume <= 0:
        raise FieldError(f"a volume must be greater than 0, got {value!r}")
    return float(volume)


def normalize_optional_volume(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "none", "null"}):
        return None
    return normalize_volume(value)


def normalize_drop_volumes(value: Any) -> float | list[float]:
    if isinstance(value, (list, tuple)):
        volumes = [normalize_volume(item) for item in value]
        if not volumes:
            raise FieldError("a droplet volume list cannot be empty")
        return volumes[0] if len(volumes) == 1 else volumes
    return normalize_volume(value)


def normalize_count(value: Any) -> int:
    count = _integer(value, "a count")
    if count < 1:
        raise FieldError(f"a count must be at least 1, got {value!r}")
    return count


def normalize_drops(value: Any) -> int:
    """Drops on one paper position: 1-20 dispenses onto the same spot (never more paper positions)."""
    count = _integer(value, "a drop count")
    if not 1 <= count <= 20:
        raise FieldError(f"drops per paper position are 1-20, got {value!r}")
    return count


def normalize_air_gap(value: Any) -> float:
    """The air gap after each vial aspiration, in µL: 0 (off) up to 5 µL; "on" is the lab default volume."""
    if isinstance(value, bool) or str(value).strip().lower() in {"on", "off", "true", "false", "yes", "no"}:
        return float(default_liquid_handling().get("air_gap_ul", 1.0)) if normalize_bool(value) else 0.0
    volume = volume_in_microlitres(value, "the air gap")
    if not 0 <= volume <= 5:
        raise FieldError(f"the air gap is 0 (off) to 5 µL, got {value!r}")
    return float(volume)



def normalize_replicates(value: Any) -> int:
    """Zero repeats still prints each condition once; a negative count is invalid."""
    count = _integer(value, "total replicates")
    if count < 0:
        raise FieldError(f"total replicates cannot be negative, got {value!r}")
    return max(1, count)


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "on", "1"}:
        return True
    if text in {"false", "no", "off", "0"}:
        return False
    raise FieldError(f"expected true or false, got {value!r}")


def normalize_policy(value: Any) -> str:
    text = re.sub(r"[\s\-]+", "_", str(value).strip().lower())
    if text in {"single_tip", "single", "one_tip", "single_tip_entire_run", "one_tip_entire_run",
                "one_tip_for_entire_run", "same_tip"}:
        return "single_tip"
    if text in {"per_liquid", "reuse", "reuse_per_liquid", "one_tip_per_liquid"}:
        return "per_liquid"
    if text in {"new_tip_every_transfer", "new_tip", "always_replace", "always_new",
                "fresh_tip", "never_reuse", "no_reuse"}:
        return "new_tip_every_transfer"
    raise FieldError(f"tip policy is single_tip, per_liquid or new_tip_every_transfer, got {value!r}")


def normalize_label(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not 1 <= len(text) <= 40 or not re.fullmatch(r"[\w ()%.,+/×\-]+", text):
        raise FieldError(f"a material name is 1-40 plain characters, got {value!r}")
    return text


def normalize_rows(value: Any) -> list[str]:
    from src.agents.dye_demo.natural import selected_rows
    if isinstance(value, (list, tuple)):
        result = [str(v).strip().upper() for v in value if str(v).strip().upper() in ROWS]
        if result:
            return sorted(set(result), key=ROWS.index)
    if isinstance(value, str):
        return selected_rows(value)
    raise FieldError(f"selected rows must be a list of row letters (A-H), got {value!r}")


_PAPER_ROW = re.compile(r"(?:paper\s+)?(?:row\s*)?([A-Ha-h]|[1-8])")


def normalize_paper_rows(value: Any) -> list[str] | None:
    """print.paper_rows: the PAPER rows the dilution series prints on, top to bottom in series order (the first
    dilution prints on the first of them). Independent of dilution.rows, the PLATE rows the dilutions are made in.

    None (or "none"/"default") returns to the default: each dilution prints on the paper row with its own plate-row
    letter. Rows are letters A-H or numbers 1-8; a row that does not exist on the paper is refused, never dropped."""
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "none", "null", "default"}):
        return None
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        words = re.sub(r"(?i)\b(?:paper|rows?)\b", " ", str(value))
        items = re.split(r"\s*(?:,|;|&|\band\b)\s*|\s+", words.strip())
    rows: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        match = _PAPER_ROW.fullmatch(text)
        if not match:
            raise FieldError(f"paper rows are A-H (or 1-8), got {item!r}")
        token = match.group(1).upper()
        rows.append(ROWS[int(token) - 1] if token.isdigit() else token)
    if not rows:
        raise FieldError("paper rows must name at least one row (A-H)")
    return sorted(set(rows), key=ROWS.index)


PLATE_COLUMNS = 12
PAPER_COLUMNS = 12


def plate_well(value: Any) -> str:
    """A 96-well plate well (A1-H12), with the reason in plain words when it does not exist."""
    try:
        return parse_well_name(value, columns=PLATE_COLUMNS, what="a plate well")
    except FieldError:
        raise FieldError(f"I can't use {str(value).strip()!r}: the 96-well plate has rows A-H and columns 1-12, so its "
                         "wells are A1-H12") from None


def paper_position(value: Any) -> str:
    """A paper position (A1-H12 on the 96-position paper), with the reason in plain words when it does not exist."""
    try:
        return parse_well_name(value, columns=PAPER_COLUMNS, what="a paper position")
    except FieldError:
        raise FieldError(f"I can't print on {str(value).strip()!r}: the paper has rows A-H and columns 1-12, so its "
                         "positions are A1-H12") from None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [part for part in re.split(r"[\s,;]+", str(value).strip()) if part]


def positions_with_drops(value: Any) -> tuple[list[str], dict[str, int]]:
    """Paper positions, and any drop counts written with them: ["A1", "A2"], "A1 A2", {"A1": 1, "A2": 3} or
    [{"position": "A2", "drops": 3}, "B5"] (a position without a count takes the plan's drops per position)."""
    if isinstance(value, dict):
        items: list[Any] = [{"position": position, "drops": drops} for position, drops in value.items()]
    else:
        items = _as_list(value)
    positions: list[str] = []
    drops: dict[str, int] = {}
    for item in items:
        if isinstance(item, dict):
            raw = next((item[key] for key in ("position", "paper_position", "well", "to") if key in item), None)
            position = paper_position(raw)
            if item.get("drops") not in (None, ""):
                drops[position] = normalize_drops(item["drops"])
        else:
            position = paper_position(item)
        positions.append(position)
    return positions, drops


def normalize_source_map(value: Any) -> list[dict[str, Any]]:
    """print.source_map: which plate well prints on which paper positions, in print order.

        [{"source": "A11", "positions": ["A1", "B1", ...]}, {"source": "B11", "positions": [...], "volume_ul": 3},
         {"source": "C11", "positions": ["A2", "A3"], "drops": {"A3": 3}}]

    One source may feed any number of positions, and any number of sources may be used; nothing here ties the sources
    to the dilution factors. Every well and position must exist, and no paper position may be printed twice.
    "drops" (optional) is how many drops land on a position - several dispenses onto that ONE spot, never more
    positions. A number applies to every position of the entry; a position without a count takes
    print.droplets_per_spot. The canonical form lists only the counts that were given.
    """
    entries = value if isinstance(value, (list, tuple)) else [value]
    result: list[dict[str, Any]] = []
    used: dict[str, str] = {}
    for item in entries:
        if not isinstance(item, dict):
            raise FieldError(f"each print-map entry names a source well and its paper positions, got {item!r}")
        raw_source = next((item[key] for key in ("source", "well", "from", "source_well") if key in item), None)
        raw_positions = next((item[key] for key in ("positions", "to", "destinations", "paper_positions")
                              if key in item), None)
        if raw_source in (None, "") or raw_positions in (None, "", [], {}):
            raise FieldError("each print-map entry needs a source well and at least one paper position")
        source = plate_well(raw_source)
        positions, drops = positions_with_drops(raw_positions)
        for position in positions:
            if position in used:
                raise FieldError(f"paper position {position} is printed twice (from {used[position]} and {source}); "
                                 "each paper position takes one sample")
            used[position] = source
        raw_drops = item.get("drops")
        if isinstance(raw_drops, dict):
            for position, count in raw_drops.items():
                position = paper_position(position)
                if position not in positions:
                    raise FieldError(f"{position} gets a drop count but {source} does not print on it")
                drops[position] = normalize_drops(count)
        elif raw_drops not in (None, ""):
            count = normalize_drops(raw_drops)
            drops.update({position: count for position in positions})
        entry: dict[str, Any] = {"source": source, "positions": positions}
        if item.get("volume_ul") not in (None, ""):
            entry["volume_ul"] = normalize_volume(item["volume_ul"])
        if drops:
            entry["drops"] = {position: drops[position] for position in positions if position in drops}
        result.append(entry)
    if not result:
        raise FieldError("a print map needs at least one source well")
    return result


# ── the fields a conversation may change ────────────────────────────────────────
# Canonical paths use the material ROLE (sample/solvent); resolve_path() maps them
# to the configured material key. Everything absent from this table is lab-owned.

EDITABLE_FIELDS: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "deck.plate.slot": ("96-well dilution plate location", normalize_slot),
    "deck.paper.slot": ("Paper print plate location", normalize_slot),
    "deck.tuberack.slot": ("Vial rack location", normalize_slot),
    "deck.tiprack.slot": ("P20 tip rack location", normalize_slot),
    "materials.sample.vial": ("Dye (sample) vial", normalize_vial),
    "materials.solvent.vial": ("Water (solvent) vial", normalize_vial),
    "materials.sample.label": ("Dye (sample) name", normalize_label),
    "materials.solvent.label": ("Water (solvent) name", normalize_label),
    "dilution.enabled": ("Make dilutions in this run", normalize_bool),
    "dilution.factors": ("Dilution factors", normalize_factors),
    "dilution.plate_column": ("Dilution plate column", normalize_plate_column),
    "dilution.start_row": ("Dilution start row", normalize_row),
    "dilution.rows": ("Dilution plate rows", normalize_rows),
    "dilution.total_volume_ul": ("Final volume per dilution", normalize_volume),
    "dilution.prepared_volume_ul": ("Volume now in each prepared well", normalize_optional_volume),
    "mixing.enabled": ("Mix each dilution after it is made", normalize_bool),
    "mixing.reps": ("Mixes per dilution well", normalize_count),
    "mixing.volume_ul": ("Mixing volume", normalize_volume),
    "liquid_handling.air_gap_ul": ("Air gap after each vial aspiration", normalize_air_gap),
    "liquid_handling.blow_out": ("Blow-out after each plate dispense", normalize_bool),
    "liquid_handling.well_plate_shake.enabled": ("Shake after dispense (96-well plate)", normalize_bool),
    "print.enabled": ("Print in this run", normalize_bool),
    "print.droplet_volume_ul": ("Drop volume", normalize_drop_volumes),
    "print.droplets_per_spot": ("Drops per paper position", normalize_drops),
    "print.replicates": ("Total replicates (prints of each condition)", normalize_replicates),
    "print.paper_start_column": ("First paper column", normalize_paper_column),
    "print.paper_rows": ("Paper rows (print destinations)", normalize_paper_rows),
    "print.source_map": ("Print map (plate well → paper positions)", normalize_source_map),
    "tips.start_tip": ("Starting tip", normalize_tip),
    "tips.return_tips": ("Return used tips to the rack", normalize_bool),
    "tips.policy": ("Tip policy", normalize_policy),
}

LAB_OWNED_FIELDS = {
    "print.z_mm": "print release height above the paper",
    "print.air_gap_ul": "trailing air gap of the print cycle",
    "print.air_gap_height_mm": "air-gap height",
    "print.push_out_ul": "push-out volume",
    "print.blow_out": "print blow-out",
    "print.post_dispense_delay_s": "post-drop dwell",
    "print.paper_columns": "paper width",
    "dilution.max_transfer_ul": "largest single P20 transfer",
    "liquid_handling.plate_aspirate_height_mm": "plate aspirate height",
    "liquid_handling.plate_dispense_height_mm": "plate dispense height",
    "liquid_handling.plate_mix_height_mm": "plate mixing height",
    "liquid_handling.well_plate_shake.radius": "shake radius",
    "liquid_handling.well_plate_shake.v_offset_mm": "shake height",
    "liquid_handling.well_plate_shake.speed_mm_s": "shake speed",
    "liquid_handling.well_plate_shake.cycles": "shake cycles",
}
LAB_OWNED_ROOTS = {
    "pipette": "the pipette",
    "safety": "the safety limits",
    "flow_rates": "the flow rates",
    "run_modes": "the run modes",
    "protocol_version": "the protocol version",
    "session": "the session record",
    "liquid_handling": "the plate heights and droplet-release geometry",
}

_DECK_ALIASES = {
    "plate": "plate", "well_plate": "plate", "wellplate": "plate", "dilution_plate": "plate",
    "paper": "paper", "paper_plate": "paper", "paper_print_plate": "paper", "print_plate": "paper",
    "tuberack": "tuberack", "vial_rack": "tuberack", "vialrack": "tuberack",
    "tube_rack": "tuberack", "rack": "tuberack",
    "tiprack": "tiprack", "tip_rack": "tiprack", "tips": "tiprack",
}
_FIELD_ALIASES = {
    "tips.rack_slot": "deck.tiprack.slot",
    "tips.slot": "deck.tiprack.slot",
    "print.volume_ul": "print.droplet_volume_ul",
    "print.drop_volume_ul": "print.droplet_volume_ul",
    "print.drops_per_spot": "print.droplets_per_spot",
    "print.drops_per_location": "print.droplets_per_spot",
    "print.drops_per_position": "print.droplets_per_spot",
    "print.total_replicates": "print.replicates",
    "total_replicates": "print.replicates",
    "mixing.enable": "mixing.enabled",
    "air_gap": "liquid_handling.air_gap_ul",
    "air_gap_ul": "liquid_handling.air_gap_ul",
    "liquid_handling.air_gap": "liquid_handling.air_gap_ul",
    "dilution.air_gap_ul": "liquid_handling.air_gap_ul",
    "blow_out": "liquid_handling.blow_out",
    "blowout": "liquid_handling.blow_out",
    "dilution.blow_out": "liquid_handling.blow_out",
    "dilution.blow_out_after_dispense": "liquid_handling.blow_out",
    "shake": "liquid_handling.well_plate_shake.enabled",
    "well_plate_shake": "liquid_handling.well_plate_shake.enabled",
    "well_plate_shake_enabled": "liquid_handling.well_plate_shake.enabled",
    "well_plate_droplet_release_enabled": "liquid_handling.well_plate_shake.enabled",
    "liquid_handling.shake": "liquid_handling.well_plate_shake.enabled",
    "liquid_handling.well_plate_shake": "liquid_handling.well_plate_shake.enabled",
    "liquid_handling.well_plate_shake_enabled": "liquid_handling.well_plate_shake.enabled",
    "mixing.height_mm": "liquid_handling.plate_mix_height_mm",
    "print.aspirate_height_mm": "liquid_handling.plate_aspirate_height_mm",
    "dilution.dispense_height_mm": "liquid_handling.plate_dispense_height_mm",
    "print.rows": "print.paper_rows",
    "print.paper_row": "print.paper_rows",
    "print.destination_rows": "print.paper_rows",
    "print_rows": "paper_rows",
    "dilution.plate_rows": "dilution.rows",
}


def material_key(config: dict[str, Any], role: str) -> str | None:
    for key, spec in (config.get("materials") or {}).items():
        if isinstance(spec, dict) and spec.get("role") == role:
            return key
    return None


def material_spec(config: dict[str, Any], role: str) -> dict[str, Any]:
    key = material_key(config, role)
    return dict(config["materials"][key]) if key else {}


def material_label(config: dict[str, Any], role: str) -> str:
    key = material_key(config, role)
    if not key:
        return role
    return str(config["materials"][key].get("label") or key)


def canonicalize_path(config: dict[str, Any], raw_path: Any) -> str:
    """Map a proposed path onto the canonical form used by EDITABLE_FIELDS."""
    path = re.sub(r"\s+", "", str(raw_path)).strip(".").lower()
    path = _FIELD_ALIASES.get(path, path)
    parts = path.split(".")
    if parts[0] == "deck" and len(parts) >= 2:
        parts[1] = _DECK_ALIASES.get(parts[1], parts[1])
    if parts[0] == "materials" and len(parts) >= 2:
        key = parts[1]
        if key not in MATERIAL_ROLES:
            spec = (config.get("materials") or {}).get(key)
            if isinstance(spec, dict) and spec.get("role") in MATERIAL_ROLES:
                parts[1] = spec["role"]
            elif key in {"dye", "stock", "sample_stock"}:
                parts[1] = "sample"
            elif key in {"water", "diluent"}:
                parts[1] = "solvent"
    return ".".join(parts)


def lab_owned_reason(canonical_path: str) -> str | None:
    """Why a canonical path cannot be changed in conversation, or None if editable."""
    if canonical_path in EDITABLE_FIELDS:
        return None
    if canonical_path in LAB_OWNED_FIELDS:
        return (f"the {LAB_OWNED_FIELDS[canonical_path]} is lab-owned: it is calibrated "
                "on the instrument")
    root = canonical_path.split(".")[0]
    if root in LAB_OWNED_ROOTS:
        return f"{LAB_OWNED_ROOTS[root]} is lab-owned"
    parts = canonical_path.split(".")
    if parts[0] == "materials" and parts[-1] in {"aspirate_height_mm", "role"}:
        return "vial aspirate heights and material roles are lab-owned: calibrated on the instrument"
    if parts[0] == "deck" and parts[-1] in {"load_name", "namespace", "version"}:
        return "the labware types are lab-owned"
    return "it is not a setting this demo can change"


def resolve_path(config: dict[str, Any], canonical_path: str) -> str:
    """The real config path for a canonical path (material role -> material key)."""
    parts = canonical_path.split(".")
    if parts[0] == "materials" and len(parts) == 3 and parts[1] in MATERIAL_ROLES:
        key = material_key(config, parts[1])
        if key is None:
            raise FieldError(f"no material has role {parts[1]!r}")
        parts[1] = key
    return ".".join(parts)


def field_label(canonical_path: str) -> str:
    entry = EDITABLE_FIELDS.get(canonical_path)
    return entry[0] if entry else canonical_path


def get_path(config: dict[str, Any], path: str, default: Any = None) -> Any:
    node: Any = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_path(config: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = config
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    if value is None:
        node.pop(parts[-1], None)
    else:
        node[parts[-1]] = deepcopy(value)


# ── deck helpers ────────────────────────────────────────────────────────────────

def slot_of(config: dict[str, Any], role: str) -> Any:
    return ((config.get("deck") or {}).get(role) or {}).get("slot")


def occupancy(config: dict[str, Any]) -> dict[int, list[str]]:
    """Deck slot -> labware roles in it (more than one role is a collision)."""
    slots: dict[int, list[str]] = {}
    for role in LABWARE_ROLES:
        slot = slot_of(config, role)
        if isinstance(slot, int) and not isinstance(slot, bool):
            slots.setdefault(slot, []).append(role)
    return dict(sorted(slots.items()))


def off_deck_roles(config: dict[str, Any]) -> list[str]:
    return [role for role in LABWARE_ROLES if is_off_deck(slot_of(config, role))]


def required_roles(do_dilution: bool, do_print: bool) -> set[str]:
    """Labware that must be ON the deck for the enabled steps."""
    roles: set[str] = set()
    if do_dilution or do_print:
        roles.update({"plate", "tiprack"})
    if do_dilution:
        roles.add("tuberack")
    if do_print:
        roles.add("paper")
    return roles


# ── geometry ────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def well_geometry(load_name: str) -> tuple[float, float, float] | None:
    """(diameter_mm, depth_mm, max_volume_ul) of a circular well from labware JSON."""
    try:
        data = json.loads((LABWARE_DIR / f"{load_name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    wells = data.get("wells") or {}
    first = wells.get("A1") or next(iter(wells.values()), None)
    if not isinstance(first, dict) or first.get("shape") != "circular":
        return None
    return (float(first["diameter"]), float(first["depth"]),
            float(first.get("totalLiquidVolume", 0.0)))


def circle_area_mm2(diameter_mm: float) -> float:
    return math.pi * (float(diameter_mm) / 2.0) ** 2


# ── loading ─────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _default_liquid_handling() -> str:
    data = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8")) or {}
    return json.dumps(data.get("liquid_handling") or {})


def default_liquid_handling() -> dict[str, Any]:
    """The liquid_handling section of the demo's default YAML - the one place its values are written down."""
    return json.loads(_default_liquid_handling())


def _merged(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        result[key] = _merged(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) \
            else deepcopy(value)
    return result


def liquid_handling(config: dict[str, Any]) -> dict[str, Any]:
    """The physical liquid-handling settings of a plan: plate heights (mm above the well bottom), the air gap after each
    vial aspiration, blow-out and the well-plate shake. The plan, the checks and the rendered plan read them here; the
    protocol reads the same section of its embedded config. A plan without the section uses the default YAML's."""
    own = config.get("liquid_handling")
    return _merged(default_liquid_handling(), own if isinstance(own, dict) else {})


# older plans kept these settings elsewhere (heights and blow-out moved into liquid_handling on 2026-09-28)
_LEGACY_LIQUID_HANDLING = {
    "plate_aspirate_height_mm": ("print", "aspirate_height_mm"),
    "plate_mix_height_mm": ("mixing", "height_mm"),
    "blow_out": ("dilution", "blow_out_after_dispense"),
}
_RETIRED_KEYS = (("dilution", "solvent_dispense_from_top_mm"), ("dilution", "sample_dispense_from_top_mm"))


def normalize_loaded_config(config: dict[str, Any]) -> dict[str, Any]:
    """Canonical shapes for the fields the checks compare (slots, tip policy, the liquid-handling section)."""
    config = deepcopy(config)
    for role in LABWARE_ROLES:
        spec = (config.get("deck") or {}).get(role)
        if isinstance(spec, dict) and "slot" in spec:
            try:
                spec["slot"] = normalize_slot(spec["slot"])
            except FieldError:
                pass    # left as-is; validation names the problem
    tips = config.setdefault("tips", {})
    tips.setdefault("policy", "per_liquid")
    own = config.get("liquid_handling") if isinstance(config.get("liquid_handling"), dict) else {}
    for key, (section, old) in _LEGACY_LIQUID_HANDLING.items():
        holder = config.get(section)
        if isinstance(holder, dict) and old in holder:
            value = holder.pop(old)
            own.setdefault(key, value)
    for section, old in _RETIRED_KEYS:
        if isinstance(config.get(section), dict):
            config[section].pop(old, None)
    if "liquid_handling" in config or own or any(section in config for section in ("dilution", "print")):
        config["liquid_handling"] = liquid_handling({"liquid_handling": own})
    if isinstance(config.get("mixing"), dict):
        config["mixing"].setdefault("enabled", True)
    printing = config.get("print")
    if isinstance(printing, dict) and printing.get("source_map") not in (None, "", []):
        try:
            printing["source_map"] = normalize_source_map(printing["source_map"])   # the one shape the run embeds
        except FieldError:
            pass    # left as-is; validation names the problem
    return config


def load_config(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return normalize_loaded_config(data)


def load_machine_profile(path: Path = MACHINE_PROFILE) -> dict[str, Any]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    return data if isinstance(data, dict) else {}
