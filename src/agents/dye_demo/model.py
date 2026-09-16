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
from typing import Any, Callable

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
    "per_liquid": "one tip per liquid (a tip is reused only for the same liquid)",
    "new_tip_every_transfer": "a new tip for every transfer and every printed position",
}


class FieldError(ValueError):
    """A proposed value that cannot be used for its field."""


# A proposed value found only in the scientist's earlier messages ("same thing but columns 4-6" keeps the rows of the
# request it revises): accepted, and shown for checking.
CARRIED_OVER = "carried over from earlier in the conversation"


# ── formatting shared by the plan, the checks and the renderers ─────────────────

def fmt_num(value: float) -> str:
    number = float(value)
    if number and abs(number) < 0.1:
        return f"{number:.3g}"          # 0.005 mL must not be shown as 0.01 mL
    return f"{number:.2f}".rstrip("0").rstrip(".")


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
        raise FieldError(f"tips are A1-H12 on the 96-tip rack, got {value!r} ({exc})") from exc


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
    if text in {"per_liquid", "reuse", "reuse_per_liquid", "one_tip_per_liquid"}:
        return "per_liquid"
    if text in {"new_tip_every_transfer", "new_tip", "always_replace", "always_new",
                "fresh_tip", "never_reuse", "no_reuse"}:
        return "new_tip_every_transfer"
    raise FieldError(f"tip policy is per_liquid or new_tip_every_transfer, got {value!r}")


def normalize_label(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not 1 <= len(text) <= 40 or not re.fullmatch(r"[\w ()%.,+/×\-]+", text):
        raise FieldError(f"a material name is 1-40 plain characters, got {value!r}")
    return text


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
    "dilution.total_volume_ul": ("Final volume per dilution", normalize_volume),
    "dilution.prepared_volume_ul": ("Volume now in each prepared well", normalize_optional_volume),
    "mixing.reps": ("Mixes before each print", normalize_count),
    "mixing.volume_ul": ("Mixing volume", normalize_volume),
    "print.enabled": ("Print in this run", normalize_bool),
    "print.droplet_volume_ul": ("Drop volume", normalize_drop_volumes),
    "print.droplets_per_spot": ("Drops per paper position", normalize_count),
    "print.replicates": ("Replicate paper columns", normalize_count),
    "print.paper_start_column": ("First paper column", normalize_paper_column),
    "tips.start_tip": ("Starting tip", normalize_tip),
    "tips.return_tips": ("Return used tips to the rack", normalize_bool),
    "tips.policy": ("Tip policy", normalize_policy),
}

LAB_OWNED_FIELDS = {
    "print.z_mm": "print release height above the paper",
    "print.aspirate_height_mm": "aspirate height inside the dilution well",
    "print.air_gap_ul": "trailing air gap",
    "print.air_gap_height_mm": "air-gap height",
    "print.push_out_ul": "push-out volume",
    "print.blow_out": "print blow-out",
    "print.post_dispense_delay_s": "post-drop dwell",
    "print.paper_columns": "paper width",
    "dilution.max_transfer_ul": "largest single P20 transfer",
    "dilution.solvent_dispense_from_top_mm": "water dispense height",
    "dilution.sample_dispense_from_top_mm": "dye dispense height",
    "dilution.blow_out_after_dispense": "dilution blow-out",
    "mixing.height_mm": "mixing height",
}
LAB_OWNED_ROOTS = {
    "pipette": "the pipette",
    "safety": "the safety limits",
    "flow_rates": "the flow rates",
    "run_modes": "the run modes",
    "protocol_version": "the protocol version",
    "session": "the session record",
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
    root = canonical_path.split(".")[0]
    if root in LAB_OWNED_ROOTS:
        return f"{LAB_OWNED_ROOTS[root]} is lab-owned"
    if canonical_path in LAB_OWNED_FIELDS:
        return (f"the {LAB_OWNED_FIELDS[canonical_path]} is lab-owned: it is calibrated "
                "on the instrument")
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

def normalize_loaded_config(config: dict[str, Any]) -> dict[str, Any]:
    """Canonical shapes for the fields the checks compare (slots, tip policy)."""
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
