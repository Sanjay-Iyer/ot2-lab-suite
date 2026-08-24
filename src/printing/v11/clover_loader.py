"""Version 11 clover print - YAML to flattened executor CONFIG.

One job: turn a human/agent-edited YAML file into the single flat mapping that
``src/protocols/printing/11_clover_print.py`` carries between its CONFIG
sentinels. Nothing here imports opentrons, so the home laptop can resolve and
validate a whole run with no robot and no simulator.

Everything a scientist may ask for is resolved BEFORE anything reaches the
robot. The contract is configs/templates/11_parameter_reference.yaml.

THE GEOMETRY RULE (the one thing that is easy to get wrong)
-----------------------------------------------------------
``half_width_mm`` / ``half_height_mm`` are offsets FROM THE CLOVER CENTRE, so
two opposing droplets end up TWICE that far apart::

    d1 = (-half_width, +half_height)     d2 = (+half_width, +half_height)
    d3 = (-half_width, -half_height)     d4 = (+half_width, -half_height)

    half_width_mm 1.0  ->  2.0 mm between d1 and d2
    half_width_mm 2.5  ->  5.0 mm between d1 and d2

``separation_x_mm`` / ``separation_y_mm`` are the convenience inputs in ACTUAL
droplet-to-droplet millimetres. This loader HALVES them into
half_width_mm / half_height_mm and writes BOTH forms into the resolved config,
so a plan can report the real physical distance without redoing the arithmetic.

``rotation_deg`` rotates those four offsets about the clover centre with a plain
2-D rotation. The d1..d4 corner model itself never changes.

Absolute droplet position (a pure translation chain, so it is identical in
paper-local millimetres here and in deck millimetres inside the executor)::

    droplet = paper well centre(reference)
            + (x_offset_mm, y_offset_mm)
            + rotated droplet offset
"""
from __future__ import annotations

import math
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .labware import (
    COLUMNS,
    LABWARE,
    PAPER,
    PIPETTE_DEFAULTS,
    ROWS,
    TIPRACK,
    V11ConfigError,
    normalize_well,
    paper_bounds,
    paper_well_xy,
    resolve_labware_block,
    resolve_paper_block,
    resolve_pipetting,
    resolve_tips,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Tips are consumed in the tiprack's own well order, which is column-major
#: (A1, B1 ... H1, A2 ...). Same order the executor gets from wells_by_name().
TIP_WELL_ORDER = [f"{row}{column}" for column in COLUMNS for row in ROWS]

SCHEMA = "v11-clover-print"
DEFAULT_MACHINE_PROFILE = "configs/machines/ot2_standard_printing_p20_v1.yaml"
MACHINE_PROFILE_DIR = "configs/machines"

DROPLET_KEYS = ("d1", "d2", "d3", "d4")

#: What the operator usually loads, per registered labware. Only used when the
#: config is silent; an explicit loaded_volume_ul always wins.
DEFAULT_LOADED_VOLUME_UL = {"vial_rack": 5000.0, "corning_plate": 300.0, "well_plate": 300.0}
DEFAULT_MINIMUM_REMAINING_UL = {"vial_rack": 100.0, "corning_plate": 20.0, "well_plate": 20.0}

#: Physically validated droplet release behaviour. Laboratory-owned: it lives in
#: the machine profile, never in the agent-facing parameters.
DEFAULT_RELEASE = {"pre_air_chase_ul": 0.0, "push_out_ul": 3.0, "blow_out": True}

GEOMETRY_KEYS = frozenset(
    {
        "half_width_mm",
        "half_height_mm",
        "separation_x_mm",
        "separation_y_mm",
        "x_offset_mm",
        "y_offset_mm",
        "rotation_deg",
    }
)
CLOVER_KEYS = frozenset(
    {"name", "reference", "reference_well", "source_well", "geometry", "layers"}
)
SOURCE_KEYS = frozenset(
    {
        "type",
        "labware",
        "slot",
        "well",
        "wells",
        "aspirate_height_mm",
        "loaded_volume_ul",
        "minimum_remaining_ul",
        "material",
        "park_height_mm",
    }
)
TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "workflow",
        "protocol_label",
        "description",
        "notes",
        "machine_profile",
        "run_modes",
        "pipette",
        "source",
        "paper",
        "printing",
        "pipetting",
        "geometry",
        "clovers",
        "layers",
        "timing",
        "tips",
        "tiprack",
        "validation",
    }
)


class CloverConfigError(V11ConfigError):
    """A Version 11 clover print configuration is missing, malformed or unsafe."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CloverConfigError(f"{label} must be numeric, got {value!r}")
    result = float(value)
    if math.isnan(result) or math.isinf(result):
        raise CloverConfigError(f"{label} must be finite, got {value!r}")
    return result


def _positive(value: Any, label: str) -> float:
    result = _number(value, label)
    if result <= 0:
        raise CloverConfigError(f"{label} must be > 0, got {result:g}")
    return result


def _non_negative(value: Any, label: str) -> float:
    result = _number(value, label)
    if result < 0:
        raise CloverConfigError(f"{label} must be >= 0, got {result:g}")
    return result


def _count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CloverConfigError(f"{label} must be an integer >= 1, got {value!r}")
    if value < 1:
        raise CloverConfigError(f"{label} must be an integer >= 1, got {value!r}")
    return int(value)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CloverConfigError(f"{label} must be a mapping, got {type(value).__name__}")
    return dict(value)


def _reject_unknown(data: dict[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise CloverConfigError(
            f"{label} has unknown field(s): {', '.join(unknown)}; known: "
            f"{', '.join(sorted(allowed))}"
        )


def _repo_path(reference: str | Path) -> Path:
    path = Path(reference)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise CloverConfigError(
            f"path must resolve inside the repository: {reference}"
        ) from exc
    return path


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:  # pragma: no cover - _repo_path already rejects this
        return str(path)


# --------------------------------------------------------------------------- #
# Machine profile (laboratory-owned; never invented by an agent)
# --------------------------------------------------------------------------- #

def load_machine_profile(reference: str | Path | None = None) -> dict[str, Any]:
    """Read the registered profile that owns the pipette and release behaviour.

    Only the values the parameter reference does NOT define are taken from here:
    pipette identity and capacity, the tiprack slot, and the validated droplet
    release behaviour (pre-air chase, push-out, blow-out). Flow rates, air gap
    and delays keep the documented parameter-reference defaults, which agree with
    every registered profile today.
    """
    path = _repo_path(reference or DEFAULT_MACHINE_PROFILE)
    registered = _repo_path(MACHINE_PROFILE_DIR)
    if path.parent != registered or path.suffix.lower() != ".yaml":
        raise CloverConfigError(
            "machine_profile must reference a registered laboratory profile in "
            f"{MACHINE_PROFILE_DIR}: {reference}"
        )
    if not path.is_file():
        raise CloverConfigError(f"machine profile not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    machine = _mapping(loaded.get("machine"), "machine profile 'machine'")
    if not machine:
        raise CloverConfigError(
            f"machine profile must have a 'machine' section: {path}"
        )

    pipette = _mapping(machine.get("pipette"), "machine profile pipette")
    release = _mapping(machine.get("print_release"), "machine profile print_release")
    tiprack_slot = TIPRACK["usual_slot"]
    for entry in machine.get("labware") or []:
        if isinstance(entry, dict) and str(entry.get("role")) == "tiprack":
            tiprack_slot = int(entry.get("slot", tiprack_slot))

    return {
        "path": _relative(path),
        "pipette": {
            "name": str(pipette.get("name", PIPETTE_DEFAULTS["name"])),
            "mount": str(pipette.get("mount", PIPETTE_DEFAULTS["mount"])),
            "max_volume_ul": float(
                pipette.get("maximum_volume_ul", PIPETTE_DEFAULTS["max_volume_ul"])
            ),
            "min_volume_ul": float(
                pipette.get("minimum_volume_ul", PIPETTE_DEFAULTS["min_volume_ul"])
            ),
        },
        "tiprack_slot": tiprack_slot,
        "release": {
            "pre_air_chase_ul": float(
                release.get("pre_air_chase_ul", DEFAULT_RELEASE["pre_air_chase_ul"])
            ),
            "push_out_ul": float(release.get("push_out_ul", DEFAULT_RELEASE["push_out_ul"])),
            "blow_out": bool(release.get("blow_out", DEFAULT_RELEASE["blow_out"])),
        },
    }


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #

def merge_geometry(
    base: dict[str, Any] | None, override: dict[str, Any] | None
) -> dict[str, Any]:
    """Per-clover geometry over global geometry, one axis at a time.

    A per-clover ``separation_x_mm`` replaces an inherited ``half_width_mm`` (and
    the reverse), so the two spellings of the same axis can never be inherited
    into a contradiction.
    """
    merged = {k: v for k, v in _mapping(base, "geometry").items() if v is not None}
    extra = {k: v for k, v in _mapping(override, "geometry").items() if v is not None}
    for half_key, separation_key in (
        ("half_width_mm", "separation_x_mm"),
        ("half_height_mm", "separation_y_mm"),
    ):
        if half_key in extra:
            merged.pop(separation_key, None)
        if separation_key in extra:
            merged.pop(half_key, None)
    merged.update(extra)
    return merged


def _resolve_half(
    data: dict[str, Any], half_key: str, separation_key: str, label: str
) -> float:
    """Resolve one axis to a HALF-separation, halving a separation_* input."""
    half = data.get(half_key)
    separation = data.get(separation_key)
    if separation is not None:
        actual = _positive(separation, f"{label}.{separation_key}")
        resolved = actual / 2.0
        if half is not None:
            declared = _positive(half, f"{label}.{half_key}")
            if abs(declared * 2.0 - actual) > 1e-9:
                raise CloverConfigError(
                    f"{label}: {separation_key} {actual:g} mm means {half_key} "
                    f"{resolved:g} mm, but {half_key} says {declared:g} mm. "
                    f"{half_key} is HALF the separation; give one form or the other."
                )
        return resolved
    if half is not None:
        return _positive(half, f"{label}.{half_key}")
    return 1.0


def resolve_geometry(raw: dict[str, Any] | None, *, label: str) -> dict[str, Any]:
    """Resolve a geometry block into both the half and the actual-separation form."""
    data = {k: v for k, v in _mapping(raw, label).items() if v is not None}
    _reject_unknown(data, GEOMETRY_KEYS, label)

    half_width = _resolve_half(data, "half_width_mm", "separation_x_mm", label)
    half_height = _resolve_half(data, "half_height_mm", "separation_y_mm", label)
    rotation = _number(data.get("rotation_deg", 0.0), f"{label}.rotation_deg")
    # A negative or >360 angle is the same physical pattern; normalise instead of
    # rejecting so "-45" and "315" both land inside the documented 0-360 range.
    rotation = rotation % 360.0
    return {
        "half_width_mm": half_width,
        "half_height_mm": half_height,
        # Both forms, so a plan can report the real droplet-to-droplet distance.
        "separation_x_mm": half_width * 2.0,
        "separation_y_mm": half_height * 2.0,
        "x_offset_mm": _number(data.get("x_offset_mm", 0.0), f"{label}.x_offset_mm"),
        "y_offset_mm": _number(data.get("y_offset_mm", 0.0), f"{label}.y_offset_mm"),
        "rotation_deg": rotation,
    }


def droplet_offsets(geometry: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """The four droplet offsets from the clover centre, after rotation.

    Identical arithmetic to the executor's own engine; the resolved values are
    written into the config so the executor can cross-check itself.
    """
    half_width = float(geometry["half_width_mm"])
    half_height = float(geometry["half_height_mm"])
    offsets = {
        "d1": (-half_width, half_height),
        "d2": (half_width, half_height),
        "d3": (-half_width, -half_height),
        "d4": (half_width, -half_height),
    }
    rotation = float(geometry.get("rotation_deg", 0.0) or 0.0)
    if rotation % 360.0 == 0.0:
        return offsets
    theta = math.radians(rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return {
        key: (x * cos_t - y * sin_t, x * sin_t + y * cos_t)
        for key, (x, y) in offsets.items()
    }


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #

def _resolve_pipette(raw: dict[str, Any] | None, profile: dict[str, Any]) -> dict[str, Any]:
    data = _mapping(raw, "pipette")
    _reject_unknown(data, frozenset({"name", "mount", "max_volume_ul"}), "pipette")
    owned = profile["pipette"]
    name = str(data.get("name", owned["name"]))
    if name != owned["name"]:
        raise CloverConfigError(
            f"pipette.name must be {owned['name']} (from {profile['path']}), got {name!r}"
        )
    mount = str(data.get("mount", owned["mount"])).lower()
    if mount not in ("left", "right"):
        raise CloverConfigError(f"pipette.mount must be left or right, got {mount!r}")
    max_volume = _positive(
        data.get("max_volume_ul", owned["max_volume_ul"]), "pipette.max_volume_ul"
    )
    if abs(max_volume - owned["max_volume_ul"]) > 1e-9:
        raise CloverConfigError(
            f"pipette.max_volume_ul is laboratory-owned and must be "
            f"{owned['max_volume_ul']:g} (from {profile['path']}), got {max_volume:g}"
        )
    return {
        "name": name,
        "mount": mount,
        "max_volume_ul": max_volume,
        "min_volume_ul": owned["min_volume_ul"],
    }


def _resolve_source(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = _mapping(raw, "source")
    _reject_unknown(data, SOURCE_KEYS, "source")
    resolved = resolve_labware_block(data, label="source", default_type="vial_rack")
    key = resolved["type"]
    if "loaded_volume_ul" not in data:
        resolved["loaded_volume_ul"] = DEFAULT_LOADED_VOLUME_UL[key]
    if "minimum_remaining_ul" not in data:
        resolved["minimum_remaining_ul"] = DEFAULT_MINIMUM_REMAINING_UL[key]
    if resolved["loaded_volume_ul"] <= 0:
        raise CloverConfigError("source.loaded_volume_ul must be > 0")
    if resolved["loaded_volume_ul"] > resolved["well_max_volume_ul"] + 1e-9:
        raise CloverConfigError(
            f"source.loaded_volume_ul {resolved['loaded_volume_ul']:g} exceeds the "
            f"{LABWARE[key]['load_name']} well capacity "
            f"{resolved['well_max_volume_ul']:g}"
        )
    resolved["park_height_mm"] = _non_negative(
        data.get("park_height_mm", 5.0), "source.park_height_mm"
    )
    resolved["well"] = resolved["wells"][0]
    return resolved


def _resolve_timing(raw: dict[str, Any] | None) -> dict[str, float]:
    data = _mapping(raw, "timing")
    _reject_unknown(
        data,
        frozenset({"inter_drop_delay_s", "inter_layer_delay_s", "inter_clover_delay_s"}),
        "timing",
    )
    return {
        "inter_drop_delay_s": _non_negative(
            data.get("inter_drop_delay_s", 0.0), "timing.inter_drop_delay_s"
        ),
        "inter_layer_delay_s": _non_negative(
            data.get("inter_layer_delay_s", 5.0), "timing.inter_layer_delay_s"
        ),
        "inter_clover_delay_s": _non_negative(
            data.get("inter_clover_delay_s", 0.0), "timing.inter_clover_delay_s"
        ),
    }


def _resolve_validation(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = _mapping(raw, "validation")
    _reject_unknown(
        data, frozenset({"droplet_radius_mm", "boundary_check"}), "validation"
    )
    return {
        "droplet_radius_mm": _non_negative(
            data.get("droplet_radius_mm", 1.5), "validation.droplet_radius_mm"
        ),
        # Kept for the record only. A droplet off the paper is a physical mess,
        # not an experimental judgement call, so the boundary check is enforced
        # whatever this says; setting it false only silences the plan wording.
        "boundary_check": bool(data.get("boundary_check", True)),
    }


# --------------------------------------------------------------------------- #
# Whole config
# --------------------------------------------------------------------------- #

def resolve_clover_config(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve one parsed YAML mapping into ``(CONFIG, run_modes)``.

    Every failure is a :class:`CloverConfigError`, including the ones raised by
    the shared labware helpers, so one except clause covers a whole load.
    """
    try:
        return _resolve_clover_config(payload)
    except CloverConfigError:
        raise
    except V11ConfigError as exc:
        raise CloverConfigError(str(exc)) from exc


def _resolve_clover_config(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise CloverConfigError("a clover print config must be a mapping")
    data = deepcopy(payload)
    _reject_unknown(data, TOP_LEVEL_KEYS, "config")

    workflow = str(data.get("workflow", "clover_print"))
    if workflow != "clover_print":
        raise CloverConfigError(
            f"workflow must be clover_print for this loader, got {workflow!r}"
        )

    run_modes_raw = _mapping(data.get("run_modes"), "run_modes")
    _reject_unknown(run_modes_raw, frozenset({"dry_run", "do_print"}), "run_modes")
    run_modes = {
        "dry_run": bool(run_modes_raw.get("dry_run", False)),
        "do_print": bool(run_modes_raw.get("do_print", True)),
    }

    profile = load_machine_profile(data.get("machine_profile"))
    pipette = _resolve_pipette(data.get("pipette"), profile)
    source = _resolve_source(data.get("source"))

    paper_raw = _mapping(data.get("paper"), "paper")
    _reject_unknown(paper_raw, frozenset({"slot", "print_height_mm", "labware"}), "paper")
    paper = resolve_paper_block(paper_raw)

    pipetting_raw = _mapping(data.get("pipetting"), "pipetting")
    _reject_unknown(
        pipetting_raw,
        frozenset(
            {
                "aspirate_flow_rate_ul_s",
                "dispense_flow_rate_ul_s",
                "air_gap_ul",
                "air_gap_height_mm",
                "post_aspirate_delay_s",
                "post_dispense_delay_s",
            }
        ),
        "pipetting",
    )
    pipetting = resolve_pipetting(pipetting_raw)

    tips_raw = _mapping(data.get("tips"), "tips")
    _reject_unknown(
        tips_raw,
        frozenset({"pipette_tip_reuse", "return_tips", "start_tip"}),
        "tips",
    )
    tips = resolve_tips(tips_raw)
    timing = _resolve_timing(data.get("timing"))
    validation = _resolve_validation(data.get("validation"))
    warnings: list[str] = []

    # -- printing volumes ---------------------------------------------------- #
    printing_raw = _mapping(data.get("printing"), "printing")
    _reject_unknown(printing_raw, frozenset({"droplet_volume_ul"}), "printing")
    droplet_volume = _positive(
        printing_raw.get("droplet_volume_ul", 5.0), "printing.droplet_volume_ul"
    )
    release = profile["release"]
    piston = release["pre_air_chase_ul"] + droplet_volume + pipetting["air_gap_ul"]
    if piston > pipette["max_volume_ul"] + 1e-9:
        raise CloverConfigError(
            f"printing.droplet_volume_ul {droplet_volume:g} uL + air gap "
            f"{pipetting['air_gap_ul']:g} uL"
            + (
                f" + pre-air chase {release['pre_air_chase_ul']:g} uL"
                if release["pre_air_chase_ul"]
                else ""
            )
            + f" = {piston:g} uL, above the {pipette['max_volume_ul']:g} uL "
            f"{pipette['name']} capacity"
        )
    if droplet_volume < pipette["min_volume_ul"] - 1e-9:
        warnings.append(
            f"droplet_volume_ul {droplet_volume:g} is below the {pipette['name']} "
            f"minimum {pipette['min_volume_ul']:g} uL; the delivered volume will "
            "not be accurate"
        )

    # -- deck ---------------------------------------------------------------- #
    tiprack_raw = _mapping(data.get("tiprack"), "tiprack")
    _reject_unknown(tiprack_raw, frozenset({"slot", "load_name"}), "tiprack")
    tiprack_slot = int(tiprack_raw.get("slot", profile["tiprack_slot"]))
    if not 1 <= tiprack_slot <= 11:
        raise CloverConfigError(f"tiprack.slot must be 1-11, got {tiprack_slot}")
    declared_tiprack = tiprack_raw.get("load_name")
    if declared_tiprack and str(declared_tiprack) != TIPRACK["load_name"]:
        raise CloverConfigError(
            f"tiprack.load_name must be {TIPRACK['load_name']}, got {declared_tiprack!r}"
        )
    deck = {
        "source": source["deck_spec"],
        "paper": paper["deck_spec"],
        "tiprack_p20": {"slot": tiprack_slot, "load_name": TIPRACK["load_name"]},
    }
    slots = [spec["slot"] for spec in deck.values()]
    if len(set(slots)) != len(slots):
        raise CloverConfigError(
            "deck slots must be unique: source "
            f"{deck['source']['slot']}, paper {deck['paper']['slot']}, tiprack "
            f"{deck['tiprack_p20']['slot']}"
        )

    # -- clovers ------------------------------------------------------------- #
    raw_clovers = data.get("clovers")
    if raw_clovers is None:
        raw_clovers = [{"reference": "B3"}]
    if isinstance(raw_clovers, dict):
        raw_clovers = [raw_clovers]
    if not isinstance(raw_clovers, list) or not raw_clovers:
        raise CloverConfigError("clovers must be a nonempty list of clover entries")

    global_geometry_raw = _mapping(data.get("geometry"), "geometry")
    _reject_unknown(global_geometry_raw, GEOMETRY_KEYS, "geometry")
    default_layers = _count(data.get("layers", 1), "layers")
    bounds = paper_bounds()
    radius = validation["droplet_radius_mm"]

    clovers: list[dict[str, Any]] = []
    violations: list[str] = []
    for index, entry in enumerate(raw_clovers, start=1):
        if not isinstance(entry, dict):
            raise CloverConfigError(f"clovers[{index}] must be a mapping")
        _reject_unknown(entry, CLOVER_KEYS, f"clovers[{index}]")
        name = str(entry.get("name") or f"clover_{index:02d}")
        raw_reference = entry.get("reference", entry.get("reference_well"))
        if raw_reference is None:
            raise CloverConfigError(f"clovers[{index}] ({name}) is missing reference")
        reference = normalize_well(raw_reference, label=f"clovers[{index}].reference")
        if reference not in PAPER["wells"]:
            raise CloverConfigError(
                f"{name}: reference {reference} does not exist on {PAPER['load_name']}"
            )

        geometry = resolve_geometry(
            merge_geometry(global_geometry_raw, entry.get("geometry")),
            label=f"{name}.geometry",
        )
        layers = _count(entry.get("layers", default_layers), f"{name}.layers")

        source_well = entry.get("source_well")
        if source_well is None:
            source_well = source["wells"][0]
        else:
            source_well = normalize_well(source_well, label=f"{name}.source_well")
            if source_well not in source["wells"]:
                raise CloverConfigError(
                    f"{name}.source_well {source_well} is not one of the declared "
                    f"source wells ({', '.join(source['wells'])})"
                )

        offsets = droplet_offsets(geometry)
        well_x, well_y = paper_well_xy(reference)
        centre_x = well_x + geometry["x_offset_mm"]
        centre_y = well_y + geometry["y_offset_mm"]
        for key in DROPLET_KEYS:
            dx, dy = offsets[key]
            x, y = centre_x + dx, centre_y + dy
            # A droplet outside the usable paper box is ALWAYS a hard failure.
            if (
                x - radius < bounds["min_x"] - 1e-9
                or x + radius > bounds["max_x"] + 1e-9
                or y - radius < bounds["min_y"] - 1e-9
                or y + radius > bounds["max_y"] + 1e-9
            ):
                violations.append(
                    f"{name}.{key} at paper x {x:.2f} y {y:.2f} (droplet radius "
                    f"{radius:g} mm) falls outside the usable paper box "
                    f"x [{bounds['min_x']:.2f}, {bounds['max_x']:.2f}] "
                    f"y [{bounds['min_y']:.2f}, {bounds['max_y']:.2f}] mm"
                )

        clovers.append(
            {
                "name": name,
                "reference": reference,
                "source_well": source_well,
                "layers": layers,
                "geometry": geometry,
                # Post-rotation offsets FROM THE CENTRE. Frame independent, so the
                # executor can recompute them and cross-check exactly.
                "droplet_offsets": {
                    key: [offsets[key][0], offsets[key][1]] for key in DROPLET_KEYS
                },
            }
        )

    names = [clover["name"] for clover in clovers]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise CloverConfigError(
            f"clover names must be unique; repeated: {', '.join(duplicates)}"
        )
    if violations:
        raise CloverConfigError(
            "clover droplets fall outside the usable paper area:\n- "
            + "\n- ".join(violations)
        )

    # -- consumption and tips ------------------------------------------------ #
    deposits = sum(clover["layers"] for clover in clovers) * len(DROPLET_KEYS)
    per_well: dict[str, float] = {}
    for clover in clovers:
        used = clover["layers"] * len(DROPLET_KEYS) * droplet_volume
        per_well[clover["source_well"]] = per_well.get(clover["source_well"], 0.0) + used
    loaded = source["loaded_volume_ul"]
    reserve = source["minimum_remaining_ul"]
    for well, required in sorted(per_well.items()):
        if loaded - required < reserve - 1e-9:
            raise CloverConfigError(
                f"source well {well} needs {required:g} uL of print liquid plus a "
                f"{reserve:g} uL reserve, but loaded_volume_ul is {loaded:g}"
            )

    distinct_sources = []
    for clover in clovers:
        if clover["source_well"] not in distinct_sources:
            distinct_sources.append(clover["source_well"])
    tips_required = deposits if not tips["pipette_tip_reuse"] else len(distinct_sources)
    start_index = TIP_WELL_ORDER.index(tips["start_tip"])
    if start_index + tips_required > TIPRACK["capacity"]:
        raise CloverConfigError(
            f"this run needs {tips_required} tip(s) from {tips['start_tip']}, which "
            f"runs past the {TIPRACK['capacity']}-tip rack"
            + (
                " (pipette_tip_reuse is false, which takes a fresh tip per droplet)"
                if not tips["pipette_tip_reuse"]
                else ""
            )
        )

    config = {
        "schema": SCHEMA,
        "protocol_label": str(data.get("protocol_label", "11_clover_print")),
        "machine_profile": profile["path"],
        "deck": deck,
        "pipette": pipette,
        "source": {
            "type": source["type"],
            "wells": source["wells"],
            "well": source["well"],
            "material": source["material"],
            "aspirate_height_mm": source["aspirate_height_mm"],
            "park_height_mm": source["park_height_mm"],
            "loaded_volume_ul": loaded,
            "minimum_remaining_ul": reserve,
            "well_depth_mm": source["well_depth_mm"],
            "well_max_volume_ul": source["well_max_volume_ul"],
        },
        "paper": {
            "print_height_mm": paper["print_height_mm"],
            "edge_margin_mm": float(PAPER["edge_margin_mm"]),
        },
        "printing": {
            "droplet_volume_ul": droplet_volume,
            # Laboratory-owned release behaviour, from the machine profile.
            "pre_air_chase_ul": release["pre_air_chase_ul"],
            "push_out_ul": release["push_out_ul"],
            "blow_out": release["blow_out"],
        },
        "pipetting": pipetting,
        "geometry_default": resolve_geometry(global_geometry_raw, label="geometry"),
        "layers": default_layers,
        "clovers": clovers,
        "timing": timing,
        "tips": tips,
        "validation": validation,
        "totals": {
            "clovers": len(clovers),
            "droplets_per_clover": len(DROPLET_KEYS),
            "deposits": deposits,
            "liquid_ul": deposits * droplet_volume,
            "per_source_well_ul": {well: per_well[well] for well in sorted(per_well)},
            "tips_required": tips_required,
        },
        "warnings": warnings,
    }
    return config, run_modes


def load_clover_config(reference: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load one repository-relative YAML file into ``(CONFIG, run_modes)``."""
    path = _repo_path(reference)
    if not path.is_file():
        raise CloverConfigError(f"clover print config not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise CloverConfigError(f"clover print config must be a mapping: {path}")
    return resolve_clover_config(loaded)


def resolved_droplet_positions(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Absolute paper-local droplet coordinates, for plans, plots and tests.

    The executor works in deck millimetres; the only difference is the constant
    slot origin, because every step of the chain is a translation.
    """
    positions = []
    for clover in config["clovers"]:
        well_x, well_y = paper_well_xy(clover["reference"])
        centre = (
            well_x + clover["geometry"]["x_offset_mm"],
            well_y + clover["geometry"]["y_offset_mm"],
        )
        droplets = {
            key: (centre[0] + offset[0], centre[1] + offset[1])
            for key, offset in clover["droplet_offsets"].items()
        }
        positions.append(
            {
                "name": clover["name"],
                "reference": clover["reference"],
                "centre": centre,
                "droplets": droplets,
                "separation_x_mm": clover["geometry"]["separation_x_mm"],
                "separation_y_mm": clover["geometry"]["separation_y_mm"],
            }
        )
    return positions
