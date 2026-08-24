"""Version 11 dilution loader: YAML -> flattened executor CONFIG.

Everything convenient is resolved HERE so the deterministic executor
(src/protocols/printing/11_general_dilution.py) only ever sees explicit values:

  * ``single.dilution_factor`` + ``single.final_volume_ul``  ->  explicit
    ``stock_volume_ul`` / ``diluent_volume_ul`` (5x of 100 uL -> 20 + 80),
  * ``series.start_well`` / ``direction`` / ``steps``        ->  explicit wells,
  * every labware role                                       ->  a deck spec,
    validated wells, an aspirate height and a sufficiency budget.

Stock, diluent and destination each pick their labware independently from the
Version 11 registry (src/printing/v11/labware.py). Roles may share one physical
labware and slot; the executor loads each distinct slot exactly once.

Standalone on purpose: nothing here imports the Version 1 dilution modules.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from ..config import resolve_repo_path
from .labware import (
    BY_LOAD_NAME,
    LABWARE,
    PIPETTE_DEFAULTS,
    TIPRACK,
    V11ConfigError,
    chunk_volume,
    normalize_well,
    resolve_labware_block,
    resolve_pipetting,
    resolve_series_wells,
    resolve_tips,
)


class DilutionLoadError(V11ConfigError):
    """A Version 11 dilution configuration file is not valid."""


#: Opentrons hands out tips column by column: A1, B1, ... H1, A2, ...
TIP_ORDER = [f"{row}{column}" for column in range(1, 13) for row in "ABCDEFGH"]

#: What the operator is assumed to have physically loaded, per labware family.
DEFAULT_LOADED_VOLUME_UL = {"vial_rack": 5000.0}
DEFAULT_MINIMUM_REMAINING_UL = {"vial_rack": 100.0}
PLATE_LOADED_VOLUME_UL = 300.0
PLATE_MINIMUM_REMAINING_UL = 20.0

#: Top-level keys a dilution config may carry.
_KNOWN_KEYS = {
    "protocol_label", "run_modes", "machine_profile", "notes", "description",
    "pipette", "pipetting", "tips", "stock_source", "diluent_source",
    "destination", "mode", "single", "series", "transfer", "mix", "delays",
}

_TOLERANCE = 1e-6


def _round(value: float) -> float:
    return float(round(float(value) + 0.0, 6))


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DilutionLoadError(f"{label} must be a mapping")
    return dict(value)


def _positive(value: Any, label: str, *, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DilutionLoadError(f"{label} must be a number, got {value!r}") from exc
    if number < 0 or (number == 0 and not allow_zero):
        raise DilutionLoadError(
            f"{label} must be {'>= 0' if allow_zero else '> 0'}, got {number:g}"
        )
    return number


def _block_type(block: dict[str, Any], default_type: str) -> str:
    """Best-effort registry key so defaults can be filled in before validation."""
    key = block.get("type")
    if not key:
        declared = block.get("labware")
        key = BY_LOAD_NAME.get(str(declared)) if declared else default_type
    return key if key in LABWARE else default_type


def _prepare_source(
    raw: Any, *, label: str, default_type: str, default_well: str,
    default_material: str,
) -> dict[str, Any]:
    """Fill in the registry-driven defaults, then resolve and validate."""
    block = _mapping(raw, label)
    key = _block_type(block, default_type)
    if not block.get("wells") and not block.get("well"):
        block["well"] = default_well
    if not block.get("material"):
        block["material"] = default_material
    if block.get("loaded_volume_ul") is None:
        block["loaded_volume_ul"] = DEFAULT_LOADED_VOLUME_UL.get(
            key, PLATE_LOADED_VOLUME_UL
        )
    if block.get("minimum_remaining_ul") is None:
        block["minimum_remaining_ul"] = DEFAULT_MINIMUM_REMAINING_UL.get(
            key, PLATE_MINIMUM_REMAINING_UL
        )
    return resolve_labware_block(block, label=label, default_type=default_type)


def _resolve_single(single: dict[str, Any]) -> dict[str, float | None]:
    """single.dilution_factor / final_volume_ul -> explicit stock + diluent."""
    factor = single.get("dilution_factor")
    final = single.get("final_volume_ul")
    stock = single.get("stock_volume_ul")
    diluent = single.get("diluent_volume_ul")

    if factor is not None:
        factor = float(factor)
        if factor <= 1:
            raise DilutionLoadError(
                f"single.dilution_factor must be > 1, got {factor:g}"
            )
        final_volume = _positive(
            100.0 if final is None else final, "single.final_volume_ul"
        )
        resolved_stock = _round(final_volume / factor)
        resolved_diluent = _round(final_volume - resolved_stock)
        for name, given in (("stock_volume_ul", stock), ("diluent_volume_ul", diluent)):
            expected = resolved_stock if name.startswith("stock") else resolved_diluent
            if given is not None and abs(float(given) - expected) > _TOLERANCE:
                raise DilutionLoadError(
                    f"single.dilution_factor {factor:g} of {final_volume:g} uL resolves "
                    f"to {resolved_stock:g} uL stock + {resolved_diluent:g} uL diluent, "
                    f"but single.{name} says {float(given):g}. Remove one of them."
                )
        return {
            "stock_volume_ul": resolved_stock,
            "diluent_volume_ul": resolved_diluent,
            "final_volume_ul": _round(final_volume),
            "dilution_factor": _round(factor),
            "resolved_from": "dilution_factor",
        }

    stock_volume = _positive(20.0 if stock is None else stock, "single.stock_volume_ul")
    diluent_volume = _positive(
        80.0 if diluent is None else diluent, "single.diluent_volume_ul",
        allow_zero=True,
    )
    total = _round(stock_volume + diluent_volume)
    if final is None:
        final_volume = total
    else:
        final_volume = _positive(final, "single.final_volume_ul")
        if abs(final_volume - total) > _TOLERANCE:
            raise DilutionLoadError(
                f"single.final_volume_ul {final_volume:g} does not equal "
                f"{stock_volume:g} uL stock + {diluent_volume:g} uL diluent "
                f"({total:g} uL)"
            )
    return {
        "stock_volume_ul": _round(stock_volume),
        "diluent_volume_ul": _round(diluent_volume),
        "final_volume_ul": _round(final_volume),
        "dilution_factor": _round(final_volume / stock_volume) if stock_volume else None,
        "resolved_from": "explicit",
    }


def _resolve_series(series: dict[str, Any], destination_wells: list[str]) -> dict[str, Any]:
    """series wells (explicit or start_well/direction/steps) + carried volumes."""
    if series.get("wells") or series.get("start_well"):
        wells = resolve_series_wells(series)
        source = "wells" if series.get("wells") else "start_well"
    elif len(destination_wells) >= 2:
        wells = list(destination_wells)
        source = "destination.wells"
    else:
        raise DilutionLoadError(
            "mode series needs series.wells, series.start_well + steps, or at least "
            "two destination.wells"
        )
    transfer_volume = _positive(
        20.0 if series.get("transfer_volume_ul") is None
        else series["transfer_volume_ul"], "series.transfer_volume_ul",
    )
    diluent_volume = _positive(
        80.0 if series.get("diluent_volume_ul") is None
        else series["diluent_volume_ul"], "series.diluent_volume_ul", allow_zero=True,
    )
    stock_volume = _positive(
        transfer_volume if series.get("stock_volume_ul") is None
        else series["stock_volume_ul"], "series.stock_volume_ul",
    )
    return {
        "wells": wells,
        "resolved_from": source,
        "stock_volume_ul": _round(stock_volume),
        "diluent_volume_ul": _round(diluent_volume),
        "transfer_volume_ul": _round(transfer_volume),
        "final_volume_ul": _round(diluent_volume + transfer_volume),
    }


def _plan_tip_keys(
    *, mode: str, wells: list[str], stock_well: str, diluent_well: str,
    stock_volume: float, diluent_volume: float, transfer_volume: float,
    mix_enabled: bool, chunks: dict[str, int],
) -> list[str]:
    """The executor's tip-request sequence, reproduced for the pre-flight count."""
    requests: list[str] = []
    stock_key = f"stock:{stock_well}"
    diluent_key = f"diluent:{diluent_well}"
    if mode == "single":
        for name in wells:
            if diluent_volume > 0:
                requests.extend([diluent_key] * chunks["diluent"])
            if stock_volume > 0:
                requests.extend([stock_key] * chunks["stock"])
            if mix_enabled:
                requests.append(f"mix:{name}")
    else:
        for _ in wells:
            if diluent_volume > 0:
                requests.extend([diluent_key] * chunks["diluent"])
        if stock_volume > 0:
            requests.extend([stock_key] * chunks["stock"])
        if mix_enabled:
            requests.append(f"mix:{wells[0]}")
        for previous, current in zip(wells, wells[1:]):
            requests.extend([f"series:{previous}"] * chunks["series"])
            if mix_enabled:
                requests.append(f"mix:{current}")
    return requests


def _count_tips(requests: list[str], tip_reuse: bool) -> int:
    if not tip_reuse:
        return len(requests)
    count = 0
    active = None
    for key in requests:
        if key != active:
            count += 1
            active = key
    return count


def load_dilution_config(reference: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(config, run_modes)`` ready for the 11_ executor's CONFIG block."""
    path = resolve_repo_path(reference)
    if not path.is_file():
        raise DilutionLoadError(f"dilution config not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise DilutionLoadError(f"dilution config must be a mapping: {path}")
    loaded = deepcopy(loaded)

    unexpected = sorted(set(loaded) - _KNOWN_KEYS)
    if unexpected:
        raise DilutionLoadError(
            f"{path} has unexpected top-level key(s): {unexpected}. Known keys: "
            f"{', '.join(sorted(_KNOWN_KEYS))}"
        )

    protocol_label = str(loaded.get("protocol_label") or "v11_dilution")
    safe_label = "".join(
        char if char.isalnum() or char in "-_" else "_" for char in protocol_label
    ) or "v11_dilution"

    run_modes_raw = _mapping(loaded.get("run_modes"), "run_modes")
    run_modes = {"dry_run": bool(run_modes_raw.get("dry_run", False))}

    # -- pipette ------------------------------------------------------------ #
    pipette_raw = _mapping(loaded.get("pipette"), "pipette")
    pipette_name = str(pipette_raw.get("name", PIPETTE_DEFAULTS["name"]))
    if pipette_name != PIPETTE_DEFAULTS["name"]:
        raise DilutionLoadError(
            f"pipette.name must be {PIPETTE_DEFAULTS['name']}, got {pipette_name!r}"
        )
    mount = str(pipette_raw.get("mount", PIPETTE_DEFAULTS["mount"])).lower()
    if mount not in ("left", "right"):
        raise DilutionLoadError(f"pipette.mount must be left or right, got {mount!r}")
    capacity = _positive(
        pipette_raw.get("max_volume_ul", PIPETTE_DEFAULTS["max_volume_ul"]),
        "pipette.max_volume_ul",
    )
    minimum = _positive(
        pipette_raw.get("min_volume_ul", PIPETTE_DEFAULTS["min_volume_ul"]),
        "pipette.min_volume_ul",
    )

    tips_raw = _mapping(loaded.get("tips"), "tips")
    tips = resolve_tips({k: v for k, v in tips_raw.items() if k != "tiprack_slot"})
    tiprack_slot = int(tips_raw.get("tiprack_slot", TIPRACK["usual_slot"]))
    if not 1 <= tiprack_slot <= 11:
        raise DilutionLoadError(f"tips.tiprack_slot must be 1-11, got {tiprack_slot}")

    pipetting = resolve_pipetting(_mapping(loaded.get("pipetting"), "pipetting"))

    # -- labware roles ------------------------------------------------------ #
    stock = _prepare_source(
        loaded.get("stock_source"), label="stock_source", default_type="vial_rack",
        default_well="A1", default_material="stock",
    )
    diluent = _prepare_source(
        loaded.get("diluent_source"), label="diluent_source", default_type="vial_rack",
        default_well="A2", default_material="water",
    )
    destination_raw = _mapping(loaded.get("destination"), "destination")
    if not destination_raw.get("wells") and not destination_raw.get("well"):
        destination_raw["wells"] = ["B11"]

    mode = str(loaded.get("mode", "single")).lower()
    if mode not in ("single", "series"):
        raise DilutionLoadError(f"mode must be 'single' or 'series', got {mode!r}")

    # The series ladder replaces destination.wells so the labware check covers it.
    series_plan: dict[str, Any] | None = None
    if mode == "series":
        declared = destination_raw.get("wells")
        if not declared and destination_raw.get("well"):
            declared = [destination_raw["well"]]
        preview = [
            normalize_well(w, label="destination well") for w in (declared or [])
        ]
        series_plan = _resolve_series(
            _mapping(loaded.get("series"), "series"), preview
        )
        destination_raw["wells"] = series_plan["wells"]
        destination_raw.pop("well", None)

    destination = resolve_labware_block(
        destination_raw, label="destination", wells_key="wells",
        default_type="well_plate",
    )
    dispense_height = _positive(
        destination_raw.get("dispense_height_mm", 2.0), "destination.dispense_height_mm"
    )
    if dispense_height >= destination["well_depth_mm"]:
        raise DilutionLoadError(
            f"destination.dispense_height_mm must be < the "
            f"{destination['well_depth_mm']:g} mm well depth, got {dispense_height:g}"
        )

    # -- deck sanity -------------------------------------------------------- #
    by_slot: dict[int, str] = {}
    for label, block in (("stock_source", stock), ("diluent_source", diluent),
                         ("destination", destination)):
        slot = int(block["deck_spec"]["slot"])
        load_name = block["deck_spec"]["load_name"]
        if by_slot.setdefault(slot, load_name) != load_name:
            raise DilutionLoadError(
                f"deck slot {slot} cannot hold both {by_slot[slot]} and {load_name} "
                f"({label}); give each labware its own slot"
            )
        if slot == tiprack_slot:
            raise DilutionLoadError(
                f"{label} slot {slot} is the tiprack slot; move one of them"
            )
    if (stock["deck_spec"]["slot"] == diluent["deck_spec"]["slot"]
            and stock["wells"][0] == diluent["wells"][0]):
        raise DilutionLoadError(
            f"stock and diluent are both {stock['wells'][0]} on slot "
            f"{stock['deck_spec']['slot']}; they must be different wells"
        )
    for label, block in (("stock_source", stock), ("diluent_source", diluent)):
        if block["deck_spec"]["slot"] == destination["deck_spec"]["slot"]:
            clash = sorted(set(block["wells"]) & set(destination["wells"]))
            if clash:
                raise DilutionLoadError(
                    f"{label} well(s) {', '.join(clash)} are also destination wells on "
                    f"slot {destination['deck_spec']['slot']}"
                )

    # -- volumes ------------------------------------------------------------ #
    if mode == "single":
        numbers = _resolve_single(_mapping(loaded.get("single"), "single"))
        stock_volume = numbers["stock_volume_ul"]
        diluent_volume = numbers["diluent_volume_ul"]
        transfer_volume = 0.0
        final_volume = numbers["final_volume_ul"]
        dilution_factor = numbers["dilution_factor"]
        resolved_from = numbers["resolved_from"]
        peak_volume = stock_volume + diluent_volume
    else:
        assert series_plan is not None
        stock_volume = series_plan["stock_volume_ul"]
        diluent_volume = series_plan["diluent_volume_ul"]
        transfer_volume = series_plan["transfer_volume_ul"]
        final_volume = series_plan["final_volume_ul"]
        dilution_factor = (
            _round(final_volume / transfer_volume) if transfer_volume else None
        )
        resolved_from = "explicit"   # series volumes are always given directly
        peak_volume = diluent_volume + max(stock_volume, transfer_volume)
        if len(destination["wells"]) < 2:
            raise DilutionLoadError("mode series needs at least two wells")

    for name, volume in (("stock", stock_volume), ("diluent", diluent_volume),
                         ("transfer", transfer_volume)):
        if 0 < volume < minimum:
            raise DilutionLoadError(
                f"{mode}.{name}_volume_ul {volume:g} uL is below the {minimum:g} uL "
                f"{pipette_name} minimum"
            )
    if peak_volume > destination["well_max_volume_ul"] + _TOLERANCE:
        raise DilutionLoadError(
            f"each destination well would hold {peak_volume:g} uL, over the "
            f"{destination['well_max_volume_ul']:g} uL capacity of "
            f"{destination['deck_spec']['load_name']}"
        )

    # -- transfer ----------------------------------------------------------- #
    transfer_raw = _mapping(loaded.get("transfer"), "transfer")
    max_chunk = _positive(transfer_raw.get("max_chunk_ul", 18.0), "transfer.max_chunk_ul")
    if max_chunk > capacity + _TOLERANCE:
        raise DilutionLoadError(
            f"transfer.max_chunk_ul {max_chunk:g} exceeds the {capacity:g} uL "
            "pipette capacity"
        )
    air_gap = float(
        pipetting["air_gap_ul"] if transfer_raw.get("air_gap_ul") is None
        else _positive(transfer_raw["air_gap_ul"], "transfer.air_gap_ul", allow_zero=True)
    )
    if air_gap >= capacity:
        raise DilutionLoadError(
            f"transfer.air_gap_ul {air_gap:g} leaves no room in a {capacity:g} uL pipette"
        )
    on_conflict = str(transfer_raw.get("on_capacity_conflict", "reduce_chunk")).lower()
    if on_conflict not in ("reduce_chunk", "fail"):
        raise DilutionLoadError(
            "transfer.on_capacity_conflict must be 'reduce_chunk' or 'fail', got "
            f"{on_conflict!r}"
        )

    chunk_plan = {}
    for name, volume in (("stock", stock_volume), ("diluent", diluent_volume),
                         ("series", transfer_volume)):
        pieces = chunk_volume(
            volume, max_chunk, air_gap, capacity, on_conflict=on_conflict,
            label=f"transfer ({name})",
        )
        for piece in pieces:
            if piece + air_gap > capacity + _TOLERANCE:
                raise DilutionLoadError(
                    f"transfer ({name}): a {piece:g} uL chunk plus a {air_gap:g} uL air "
                    f"gap exceeds the {capacity:g} uL pipette"
                )
            if piece < minimum - _TOLERANCE:
                raise DilutionLoadError(
                    f"transfer ({name}): a {piece:g} uL chunk is below the {minimum:g} uL "
                    "pipette minimum"
                )
        chunk_plan[name] = len(pieces)

    # -- mix ---------------------------------------------------------------- #
    mix_raw = _mapping(loaded.get("mix"), "mix")
    mix_enabled = bool(mix_raw.get("enabled", True))
    mix_cycles = mix_raw.get("cycles", 5)
    if isinstance(mix_cycles, bool) or not isinstance(mix_cycles, int) or mix_cycles < 0:
        raise DilutionLoadError(f"mix.cycles must be an integer >= 0, got {mix_cycles!r}")
    if mix_enabled and mix_cycles < 1:
        raise DilutionLoadError("mix.cycles must be >= 1 while mix.enabled is true")
    mix_volume = _positive(
        mix_raw.get("volume_ul", 15.0), "mix.volume_ul", allow_zero=not mix_enabled
    )
    if mix_enabled:
        if mix_volume > capacity + _TOLERANCE:
            raise DilutionLoadError(
                f"mix.volume_ul {mix_volume:g} exceeds the {capacity:g} uL pipette capacity"
            )
        if mix_volume < minimum:
            raise DilutionLoadError(
                f"mix.volume_ul {mix_volume:g} is below the {minimum:g} uL pipette minimum"
            )
        if mix_volume > peak_volume + _TOLERANCE:
            raise DilutionLoadError(
                f"mix.volume_ul {mix_volume:g} is more than the {peak_volume:g} uL that "
                "will be in the well"
            )
    mix_aspirate_height = _positive(
        mix_raw.get("aspirate_height_mm", destination["aspirate_height_mm"]),
        "mix.aspirate_height_mm",
    )
    mix_dispense_height = _positive(
        mix_raw.get("dispense_height_mm", dispense_height), "mix.dispense_height_mm"
    )
    for name, height in (("mix.aspirate_height_mm", mix_aspirate_height),
                         ("mix.dispense_height_mm", mix_dispense_height)):
        if height >= destination["well_depth_mm"]:
            raise DilutionLoadError(
                f"{name} must be < the {destination['well_depth_mm']:g} mm well depth, "
                f"got {height:g}"
            )

    # -- delays ------------------------------------------------------------- #
    delays_raw = _mapping(loaded.get("delays"), "delays")
    after_transfer_s = _positive(
        delays_raw.get("after_transfer_s", 0.0), "delays.after_transfer_s", allow_zero=True
    )
    after_mix_s = _positive(
        delays_raw.get("after_mix_s", 0.0), "delays.after_mix_s", allow_zero=True
    )

    # -- sufficiency and tips ----------------------------------------------- #
    wells = destination["wells"]
    if mode == "single":
        stock_needed = _round(stock_volume * len(wells))
        diluent_needed = _round(diluent_volume * len(wells))
    else:
        stock_needed = _round(stock_volume)
        diluent_needed = _round(diluent_volume * len(wells))
    for label, block, needed in (("stock_source", stock, stock_needed),
                                 ("diluent_source", diluent, diluent_needed)):
        loaded_volume = block["loaded_volume_ul"]
        reserve = block["minimum_remaining_ul"]
        if loaded_volume <= 0:
            continue
        if loaded_volume - needed < reserve - _TOLERANCE:
            raise DilutionLoadError(
                f"{label} well {block['wells'][0]} holds {loaded_volume:g} uL, this run "
                f"needs {needed:g} uL and must leave {reserve:g} uL"
            )

    requests = _plan_tip_keys(
        mode=mode, wells=wells, stock_well=stock["wells"][0],
        diluent_well=diluent["wells"][0], stock_volume=stock_volume,
        diluent_volume=diluent_volume, transfer_volume=transfer_volume,
        mix_enabled=mix_enabled and mix_cycles > 0 and mix_volume > 0,
        chunks=chunk_plan,
    )
    tips_needed = _count_tips(requests, tips["pipette_tip_reuse"])
    start_index = TIP_ORDER.index(tips["start_tip"])
    available = TIPRACK["capacity"] - start_index
    if tips_needed > available:
        raise DilutionLoadError(
            f"this run needs {tips_needed} tip(s) but only {available} remain on the "
            f"{TIPRACK['load_name']} from {tips['start_tip']}"
        )

    config: dict[str, Any] = {
        "protocol_label": safe_label,
        "deck": {
            "stock": stock["deck_spec"],
            "diluent": diluent["deck_spec"],
            "destination": destination["deck_spec"],
            "tiprack_p20": {"slot": tiprack_slot, "load_name": TIPRACK["load_name"]},
        },
        "pipette": {
            "name": pipette_name,
            "mount": mount,
            "max_volume_ul": capacity,
            "min_volume_ul": minimum,
        },
        "stock": {
            "well": stock["wells"][0],
            "material": stock["material"],
            "aspirate_height_mm": stock["aspirate_height_mm"],
            "loaded_volume_ul": stock["loaded_volume_ul"],
            "minimum_remaining_ul": stock["minimum_remaining_ul"],
        },
        "diluent": {
            "well": diluent["wells"][0],
            "material": diluent["material"],
            "aspirate_height_mm": diluent["aspirate_height_mm"],
            "loaded_volume_ul": diluent["loaded_volume_ul"],
            "minimum_remaining_ul": diluent["minimum_remaining_ul"],
        },
        "destination": {
            "wells": wells,
            "aspirate_height_mm": destination["aspirate_height_mm"],
            "dispense_height_mm": dispense_height,
            "well_depth_mm": destination["well_depth_mm"],
            "well_max_volume_ul": destination["well_max_volume_ul"],
        },
        "dilution": {
            "mode": mode,
            "stock_volume_ul": stock_volume,
            "diluent_volume_ul": diluent_volume,
            "transfer_volume_ul": transfer_volume,
            "final_volume_ul": final_volume,
            "dilution_factor": dilution_factor,
        },
        "transfer": {
            "max_chunk_ul": max_chunk,
            "air_gap_ul": air_gap,
            "air_gap_height_mm": pipetting["air_gap_height_mm"],
            "on_capacity_conflict": on_conflict,
        },
        "mix": {
            "enabled": mix_enabled,
            "cycles": int(mix_cycles),
            "volume_ul": mix_volume,
            "aspirate_height_mm": mix_aspirate_height,
            "dispense_height_mm": mix_dispense_height,
        },
        "delays": {
            "after_transfer_s": after_transfer_s,
            "after_mix_s": after_mix_s,
            "post_aspirate_delay_s": pipetting["post_aspirate_delay_s"],
            "post_dispense_delay_s": pipetting["post_dispense_delay_s"],
        },
        "tips": {
            "start_tip": tips["start_tip"],
            "return_tips": tips["return_tips"],
            "pipette_tip_reuse": tips["pipette_tip_reuse"],
        },
        "flow_rates": {
            "p20": {
                "aspirate": pipetting["aspirate_flow_rate_ul_s"],
                "dispense": pipetting["dispense_flow_rate_ul_s"],
            }
        },
        "safety": {"p20_max_volume_ul": capacity, "p20_min_volume_ul": minimum},
        "plan": {
            "estimated_tips": tips_needed,
            "volumes_resolved_from": resolved_from,
            "series_resolved_from": series_plan["resolved_from"] if series_plan else None,
            "stock_needed_ul": stock_needed,
            "diluent_needed_ul": diluent_needed,
            "chunks": chunk_plan,
        },
    }
    return config, run_modes
