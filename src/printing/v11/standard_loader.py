"""Load a Version 11 standard-print YAML into an executor-ready flat CONFIG.

Everything convenient, implicit or derived is resolved HERE, never on the robot:

  * ``target_selection: {column: 3}`` / ``{row: B, columns: [1, 2, 3]}``
    becomes an explicit list of paper wells.
  * ``replicates: 3`` becomes the resolved target list repeated three times, so
    each replicate is an explicit named paper well. No coordinate is ever
    invented outside the registered paper geometry.
  * ``groups:`` entries inherit the top-level targets, droplet count, replicate
    count and source well when they do not state their own.
  * every physical limit (pipette capacity, tiprack supply, source volume,
    release height, well existence) is checked before a build is produced.

The laboratory-owned release behaviour (pre-air chase, push-out, blow-out) and
the tiprack slot come from the referenced machine profile, exactly as in Version
1; the experiment YAML never restates them.

Pure Python plus PyYAML - no opentrons import, so this runs on the home laptop.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from ..config import resolve_repo_path
from .labware import (
    COLUMNS,
    PAPER,
    PIPETTE_DEFAULTS,
    ROWS,
    TIPRACK,
    V11ConfigError,
    normalize_well,
    resolve_labware_block,
    resolve_paper_block,
    resolve_pipetting,
    resolve_targets,
    resolve_tips,
)


class StandardPrintLoadError(V11ConfigError):
    """A Version 11 standard-print configuration is not valid."""


DEFAULT_MACHINE_PROFILE = "configs/machines/ot2_standard_printing_p20_v1.yaml"

#: Tips are consumed in the order an opentrons tiprack presents them: down a
#: column first (A1, B1 ... H1, A2 ...).
TIP_ORDER = [f"{row}{column}" for column in COLUMNS for row in ROWS]

ORDERS = ("layer_major", "target_major")

#: Physically validated droplet release, used when no machine profile is found.
RELEASE_FALLBACK = {"pre_air_chase_ul": 0.0, "push_out_ul": 3.0, "blow_out": True}

#: Sensible starting stock per source kind (shared.source.loaded_volume_ul).
LOADED_VOLUME_DEFAULTS = {"vial_rack": 5000.0, "corning_plate": 300.0, "well_plate": 300.0}
MINIMUM_REMAINING_DEFAULTS = {"vial_rack": 100.0, "corning_plate": 20.0, "well_plate": 20.0}

TOP_LEVEL_KEYS = {
    "schema_version",
    "workflow",
    "protocol_label",
    "description",
    "notes",
    "machine_profile",
    "run_modes",
    "pipette",
    "source",
    "paper",
    "substrate",
    "printing",
    "targets",
    "target_selection",
    "groups",
    "print_groups",
    "replicates",
    "timing",
    "pipetting",
    "tips",
}

TIMING_KEYS = ("inter_drop_delay_s", "inter_layer_delay_s", "inter_target_delay_s")
TIMING_DEFAULTS = {
    "inter_drop_delay_s": 0.0,
    "inter_layer_delay_s": 5.0,
    "inter_target_delay_s": 0.0,
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise StandardPrintLoadError(f"{label} must be a mapping")
    return dict(value)


def _positive_int(value: Any, label: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise StandardPrintLoadError(f"{label} must be a whole number, got {value!r}")
    number = int(value)
    if number < minimum:
        raise StandardPrintLoadError(f"{label} must be >= {minimum}, got {number}")
    return number


def _load_machine_profile(reference: Any) -> tuple[dict[str, Any], str | None]:
    """Read a machine profile. An explicitly named missing profile is an error."""
    explicit = bool(reference)
    path_reference = str(reference) if explicit else DEFAULT_MACHINE_PROFILE
    try:
        path = resolve_repo_path(path_reference)
    except ValueError as exc:
        raise StandardPrintLoadError(str(exc)) from exc
    if not path.is_file():
        if explicit:
            raise StandardPrintLoadError(f"machine profile not found: {path}")
        return {}, None
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise StandardPrintLoadError(f"machine profile must be a mapping: {path}")
    machine = loaded.get("machine", loaded)
    if not isinstance(machine, dict):
        raise StandardPrintLoadError(f"machine profile has no machine block: {path}")
    return machine, path_reference


def _drop_none(block: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in block.items() if value is not None}


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def load_standard_print_config(
    reference: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load one standard-print YAML and return ``(config, run_modes)``."""
    path = resolve_repo_path(reference)
    if not path.is_file():
        raise StandardPrintLoadError(f"standard-print config not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise StandardPrintLoadError(f"standard-print config must be a mapping: {path}")
    try:
        return resolve_standard_print(loaded)
    except V11ConfigError as exc:
        raise StandardPrintLoadError(f"{path}: {exc}") from exc


def resolve_standard_print(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate and flatten an already-parsed standard-print mapping.

    Every failure - including one raised by a shared helper in ``labware`` -
    leaves this function as a ``StandardPrintLoadError``, so one except clause
    catches the whole validation surface.
    """
    try:
        return _resolve_standard_print(raw)
    except StandardPrintLoadError:
        raise
    except V11ConfigError as exc:
        raise StandardPrintLoadError(str(exc)) from exc


def _resolve_standard_print(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = deepcopy(raw)
    unexpected = sorted(set(raw) - TOP_LEVEL_KEYS)
    if unexpected:
        raise StandardPrintLoadError(
            f"unexpected top-level key(s): {unexpected}; known keys are "
            f"{sorted(TOP_LEVEL_KEYS)}"
        )

    protocol_label = str(raw.get("protocol_label", "standard_print"))
    run_modes_raw = _mapping(raw.get("run_modes"), "run_modes")
    run_modes = {
        "dry_run": bool(run_modes_raw.get("dry_run", False)),
        "do_print": bool(run_modes_raw.get("do_print", True)),
    }

    # -- machine profile (laboratory-owned) ------------------------------------
    machine, profile_reference = _load_machine_profile(raw.get("machine_profile"))
    profile_pipette = _mapping(machine.get("pipette"), "machine.pipette")
    profile_flow = _mapping(profile_pipette.get("flow_rates"), "machine.pipette.flow_rates")
    release = _mapping(machine.get("print_release"), "machine.print_release")
    labware_by_role = {
        str(item.get("role")): item
        for item in (machine.get("labware") or [])
        if isinstance(item, dict)
    }
    tiprack_entry = labware_by_role.get("tiprack") or {}

    # -- pipette ---------------------------------------------------------------
    pipette_block = _mapping(raw.get("pipette"), "pipette")
    pipette_name = str(
        pipette_block.get("name", profile_pipette.get("name", PIPETTE_DEFAULTS["name"]))
    )
    if pipette_name != PIPETTE_DEFAULTS["name"]:
        raise StandardPrintLoadError(
            f"pipette.name must be {PIPETTE_DEFAULTS['name']}, got {pipette_name!r}"
        )
    mount = str(
        pipette_block.get("mount", profile_pipette.get("mount", PIPETTE_DEFAULTS["mount"]))
    ).lower()
    if mount not in ("left", "right"):
        raise StandardPrintLoadError(f"pipette.mount must be left or right, got {mount!r}")
    max_volume = float(
        pipette_block.get(
            "max_volume_ul",
            profile_pipette.get("maximum_volume_ul", PIPETTE_DEFAULTS["max_volume_ul"]),
        )
    )
    min_volume = float(
        pipette_block.get(
            "min_volume_ul",
            profile_pipette.get("minimum_volume_ul", PIPETTE_DEFAULTS["min_volume_ul"]),
        )
    )
    if max_volume <= 0:
        raise StandardPrintLoadError("pipette.max_volume_ul must be > 0")

    # -- source ----------------------------------------------------------------
    raw_source = _mapping(raw.get("source"), "source")
    source = resolve_labware_block(raw_source, label="source", default_type="vial_rack")
    if raw_source.get("loaded_volume_ul") is None:
        source["loaded_volume_ul"] = LOADED_VOLUME_DEFAULTS.get(source["type"], 300.0)
    if raw_source.get("minimum_remaining_ul") is None:
        source["minimum_remaining_ul"] = MINIMUM_REMAINING_DEFAULTS.get(source["type"], 20.0)
    if source["loaded_volume_ul"] <= 0:
        raise StandardPrintLoadError("source.loaded_volume_ul must be > 0")
    if source["loaded_volume_ul"] > source["well_max_volume_ul"] + 1e-9:
        raise StandardPrintLoadError(
            f"source.loaded_volume_ul {source['loaded_volume_ul']:g} exceeds the "
            f"{source['type']} well capacity {source['well_max_volume_ul']:g} uL"
        )

    # -- paper -----------------------------------------------------------------
    paper_block = _mapping(raw.get("paper"), "paper")
    substrate_block = _mapping(raw.get("substrate"), "substrate")
    if substrate_block and paper_block:
        raise StandardPrintLoadError("declare paper: or substrate:, not both")
    paper = resolve_paper_block(paper_block or substrate_block)

    # -- pipetting -------------------------------------------------------------
    # The machine profile supplies laboratory defaults; the YAML may override any
    # of them. post_dispense_delay_s deliberately defaults to 0.0 per the Version
    # 11 parameter reference rather than inheriting the profile's settle time.
    pipetting_defaults = {
        "aspirate_flow_rate_ul_s": profile_flow.get(
            "aspirate_ul_s", PIPETTE_DEFAULTS["aspirate_flow_rate_ul_s"]
        ),
        "dispense_flow_rate_ul_s": profile_flow.get(
            "dispense_ul_s", PIPETTE_DEFAULTS["dispense_flow_rate_ul_s"]
        ),
        "air_gap_ul": release.get("trailing_air_gap_ul", 1.5),
        "air_gap_height_mm": release.get("air_gap_height_mm", 5.0),
    }
    pipetting = resolve_pipetting(
        {**pipetting_defaults, **_drop_none(_mapping(raw.get("pipetting"), "pipetting"))}
    )
    if pipetting["air_gap_ul"] > max_volume:
        raise StandardPrintLoadError(
            f"pipetting.air_gap_ul {pipetting['air_gap_ul']:g} exceeds the pipette "
            f"capacity {max_volume:g} uL"
        )

    release_cfg = {
        "pre_air_chase_ul": float(
            release.get("pre_air_chase_ul", RELEASE_FALLBACK["pre_air_chase_ul"]) or 0.0
        ),
        "push_out_ul": float(
            release.get("push_out_ul", RELEASE_FALLBACK["push_out_ul"]) or 0.0
        ),
        "blow_out": bool(release.get("blow_out", RELEASE_FALLBACK["blow_out"])),
    }
    if release_cfg["pre_air_chase_ul"] < 0 or release_cfg["push_out_ul"] < 0:
        raise StandardPrintLoadError("machine profile release volumes must be >= 0")

    # -- printing --------------------------------------------------------------
    printing = _mapping(raw.get("printing"), "printing")
    droplet_volume = float(printing.get("droplet_volume_ul", 5.0))
    if droplet_volume <= 0:
        raise StandardPrintLoadError("printing.droplet_volume_ul must be > 0")
    if droplet_volume < min_volume:
        raise StandardPrintLoadError(
            f"printing.droplet_volume_ul {droplet_volume:g} is below the "
            f"{pipette_name} minimum {min_volume:g} uL"
        )
    piston = release_cfg["pre_air_chase_ul"] + droplet_volume + pipetting["air_gap_ul"]
    if piston > max_volume + 1e-9:
        raise StandardPrintLoadError(
            f"droplet {droplet_volume:g} uL + air gap {pipetting['air_gap_ul']:g} uL"
            + (
                f" + pre-air chase {release_cfg['pre_air_chase_ul']:g} uL"
                if release_cfg["pre_air_chase_ul"]
                else ""
            )
            + f" needs {piston:g} uL, exceeding the {max_volume:g} uL pipette capacity"
        )

    order = str(printing.get("order", "layer_major")).lower()
    if order not in ORDERS:
        raise StandardPrintLoadError(
            f"printing.order must be one of {list(ORDERS)}, got {order!r}"
        )

    droplets_raw = printing.get("droplets_per_target")
    default_droplets = _positive_int(
        1 if droplets_raw is None else droplets_raw, "printing.droplets_per_target"
    )
    replicates_raw = raw.get("replicates")
    if replicates_raw is None:
        replicates_raw = printing.get("replicates")
    default_replicates = _positive_int(
        1 if replicates_raw is None else replicates_raw, "replicates"
    )

    # -- timing ----------------------------------------------------------------
    # `timing:` is the Version 11 home for the delays; the Version 1 habit of
    # putting them under `printing:` is still accepted so old configs keep working.
    timing_raw = _mapping(raw.get("timing"), "timing")
    unexpected_timing = sorted(set(timing_raw) - set(TIMING_KEYS))
    if unexpected_timing:
        raise StandardPrintLoadError(f"unexpected timing key(s): {unexpected_timing}")
    timing = {}
    for key in TIMING_KEYS:
        value = timing_raw.get(key, printing.get(key, TIMING_DEFAULTS[key]))
        value = float(value if value is not None else TIMING_DEFAULTS[key])
        if value < 0:
            raise StandardPrintLoadError(f"timing.{key} must be >= 0, got {value:g}")
        timing[key] = value

    # -- tips ------------------------------------------------------------------
    tips_raw = _mapping(raw.get("tips"), "tips")
    unexpected_tips = sorted(
        set(tips_raw) - {"pipette_tip_reuse", "return_tips", "start_tip", "print_tip"}
    )
    if unexpected_tips:
        raise StandardPrintLoadError(f"unexpected tips key(s): {unexpected_tips}")
    if "print_tip" in tips_raw and "start_tip" not in tips_raw:
        # Version 1 spelling of the same thing.
        tips_raw["start_tip"] = tips_raw.pop("print_tip")
    tips = resolve_tips(tips_raw)
    if tips["start_tip"] not in TIP_ORDER:
        raise StandardPrintLoadError(
            f"tips.start_tip {tips['start_tip']} is not a well on "
            f"{TIPRACK['load_name']}"
        )

    # -- groups / targets ------------------------------------------------------
    groups = _resolve_groups(
        raw,
        default_droplets=default_droplets,
        default_replicates=default_replicates,
        source_wells=source["wells"],
    )

    # -- budgets ---------------------------------------------------------------
    used_sources: list[str] = []
    for group in groups:
        if group["source_well"] not in used_sources:
            used_sources.append(group["source_well"])

    source_totals: dict[str, float] = {}
    for group in groups:
        deposits = len(group["targets"]) * group["droplets"]
        source_totals[group["source_well"]] = (
            source_totals.get(group["source_well"], 0.0) + deposits * droplet_volume
        )
    total_deposits = sum(len(g["targets"]) * g["droplets"] for g in groups)
    total_volume = total_deposits * droplet_volume

    loaded = source["loaded_volume_ul"]
    reserve = source["minimum_remaining_ul"]
    for well, required in sorted(source_totals.items()):
        if loaded < required + reserve - 1e-9:
            raise StandardPrintLoadError(
                f"source well {well} needs {required:g} uL of print liquid plus a "
                f"{reserve:g} uL reserve, but loaded_volume_ul is {loaded:g}"
            )

    tips_required = len(used_sources) if tips["pipette_tip_reuse"] else total_deposits
    start_index = TIP_ORDER.index(tips["start_tip"])
    available = TIPRACK["capacity"] - start_index
    if tips_required > available:
        raise StandardPrintLoadError(
            f"this run needs {tips_required} tip(s) (pipette_tip_reuse="
            f"{tips['pipette_tip_reuse']}), but only {available} remain from "
            f"{tips['start_tip']} on {TIPRACK['load_name']}"
        )

    # -- deck ------------------------------------------------------------------
    tiprack_slot = int(tiprack_entry.get("slot", TIPRACK["usual_slot"]))
    tiprack_load_name = str(tiprack_entry.get("load_name", TIPRACK["load_name"]))
    deck = {
        "source": source["deck_spec"],
        "paper": paper["deck_spec"],
        "tiprack_p20": {"slot": tiprack_slot, "load_name": tiprack_load_name},
    }
    slots = [int(spec["slot"]) for spec in deck.values()]
    if len(set(slots)) != len(slots):
        raise StandardPrintLoadError(
            f"deck slots must be unique: source {deck['source']['slot']}, paper "
            f"{deck['paper']['slot']}, tiprack {tiprack_slot}"
        )

    config: dict[str, Any] = {
        "protocol_label": protocol_label,
        "workflow": "11_standard_print",
        "machine_profile": profile_reference,
        "deck": deck,
        "pipette": {
            "name": pipette_name,
            "mount": mount,
            "max_volume_ul": max_volume,
            "min_volume_ul": min_volume,
        },
        "source": {
            "type": source["type"],
            "wells": source["wells"],
            "material": source["material"],
            "loaded_volume_ul": float(loaded),
            "minimum_remaining_ul": float(reserve),
            "aspirate_height_mm": source["aspirate_height_mm"],
        },
        "paper": {"print_height_mm": paper["print_height_mm"]},
        "printing": {"droplet_volume_ul": droplet_volume, "order": order},
        "pipetting": pipetting,
        "release": release_cfg,
        "timing": timing,
        "print_groups": groups,
        "tips": tips,
        "safety": {"p20_max_volume_ul": max_volume},
        "plan": {
            "total_deposits": total_deposits,
            "total_volume_ul": total_volume,
            "max_layers": max(g["droplets"] for g in groups),
            "tips_required": tips_required,
            "source_totals": {well: float(v) for well, v in sorted(source_totals.items())},
        },
    }
    return config, run_modes


def _resolve_groups(
    raw: dict[str, Any],
    *,
    default_droplets: int,
    default_replicates: int,
    source_wells: list[str],
) -> list[dict[str, Any]]:
    """Resolve groups / targets / target_selection / replicates into explicit wells."""
    top_targets = raw.get("targets")
    top_selection = raw.get("target_selection")

    raw_groups = raw.get("groups")
    if raw_groups is None:
        raw_groups = raw.get("print_groups")
    if raw_groups is None:
        raw_groups = [{}]
    if isinstance(raw_groups, dict):
        raw_groups = [raw_groups]
    if not isinstance(raw_groups, list) or not raw_groups:
        raise StandardPrintLoadError("groups must be a non-empty list of mappings")

    paper_wells = set(PAPER["wells"])
    resolved: list[dict[str, Any]] = []
    inherited_used: list[bool] = []
    for index, entry in enumerate(raw_groups, start=1):
        label = f"groups[{index}]"
        group = _mapping(entry, label)
        unexpected = sorted(
            set(group)
            - {
                "source_well",
                "targets",
                "wells",
                "target_selection",
                "droplets",
                "replicates",
                "label",
            }
        )
        if unexpected:
            raise StandardPrintLoadError(f"{label} has unexpected key(s): {unexpected}")

        explicit = group.get("targets") or group.get("wells")
        selection = group.get("target_selection")
        if not explicit and not selection:
            explicit, selection = top_targets, top_selection
            inherited_used.append(True)
        else:
            inherited_used.append(False)
        if not explicit and not selection:
            raise StandardPrintLoadError(
                f"{label}: declare targets, target_selection, or a top-level "
                "targets/target_selection to inherit"
            )
        targets = resolve_targets(explicit, selection, label=f"{label}.targets")
        if not targets:
            raise StandardPrintLoadError(f"{label}: resolved to no paper targets")
        off_paper = sorted({t for t in targets if t not in paper_wells})
        if off_paper:
            raise StandardPrintLoadError(
                f"{label}: {', '.join(off_paper)} are not wells on "
                f"{PAPER['load_name']}"
            )

        droplets = group.get("droplets")
        droplets = _positive_int(
            default_droplets if droplets is None else droplets, f"{label}.droplets"
        )
        replicates = group.get("replicates")
        replicates = _positive_int(
            default_replicates if replicates is None else replicates,
            f"{label}.replicates",
        )
        # Replicates are resolved here into repeated explicit positions: the
        # executor only ever sees named paper wells.
        resolved_targets = list(targets) * replicates

        source_well = group.get("source_well")
        source_well = (
            normalize_well(source_well, label=f"{label}.source_well")
            if source_well
            else source_wells[0]
        )
        if source_well not in source_wells:
            raise StandardPrintLoadError(
                f"{label}.source_well {source_well} is not listed in source.wells "
                f"({', '.join(source_wells)})"
            )

        resolved.append(
            {
                "source_well": source_well,
                "targets": resolved_targets,
                "droplets": droplets,
                "replicates": replicates,
            }
        )

    # A top-level targets/target_selection that every group overrides is dead
    # text: the file would show one set of wells while printing another, and a
    # scientist reviewing it before a physical run would be misled.
    if (top_targets or top_selection) and not any(inherited_used):
        raise StandardPrintLoadError(
            "targets/target_selection is declared at the top level but every "
            "group overrides it, so it has no effect. Remove the top-level "
            "block, or remove the per-group targets that shadow it."
        )
    return resolved
