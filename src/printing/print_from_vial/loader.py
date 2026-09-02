"""Load a minimal print-from-vial config, flattened with its machine profile.

The experiment YAML never restates deck slots, calibrated heights, or the
validated print-release air handling -- it references the SAME laboratory-owned
machine profile the standard printing workflow uses
(``configs/machines/ot2_standard_printing_p20_v1.yaml``) via
``src.printing.standard.loader.load_machine_profile``. Only genuinely
experiment-owned choices (source vial, droplet volume, targets, repeats, delay,
tip behavior) live in this file's YAML.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from ..config import resolve_repo_path
from ..source_config import SourceConfigError, resolve_source
from ..standard.loader import ExperimentJobLoadError, load_machine_profile


class PrintFromVialLoadError(ValueError):
    """A print-from-vial configuration file is not valid."""


REQUIRED_LABWARE_ROLES = ("paper", "tiprack")


def load_print_from_vial_config(reference: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and flatten one print-from-vial YAML into an executor-ready CONFIG.

    Returns ``(config, run_modes)``. ``config`` matches exactly what
    ``01_print_from_vial.py``'s embedded ``CONFIG`` block expects.
    """
    path = resolve_repo_path(reference)
    if not path.is_file():
        raise PrintFromVialLoadError(f"print-from-vial config not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise PrintFromVialLoadError(f"print-from-vial config must be a mapping: {path}")
    loaded = deepcopy(loaded)

    loaded.pop("schema_version", None)
    machine_profile_ref = loaded.pop("machine_profile", None)
    if not machine_profile_ref:
        raise PrintFromVialLoadError(
            f"{path} must reference a registered machine_profile"
        )
    try:
        machine = load_machine_profile(machine_profile_ref)
    except ExperimentJobLoadError as exc:
        raise PrintFromVialLoadError(str(exc)) from exc

    labware_by_role = {item["role"]: item for item in machine.get("labware", [])}
    missing_roles = [role for role in REQUIRED_LABWARE_ROLES if role not in labware_by_role]
    if missing_roles:
        raise PrintFromVialLoadError(
            f"machine profile {machine_profile_ref} has no {missing_roles} labware role(s)"
        )
    paper = labware_by_role["paper"]
    tiprack = labware_by_role["tiprack"]
    try:
        release = machine["print_release"]
        pipette = machine["pipette"]
    except KeyError as exc:
        raise PrintFromVialLoadError(
            f"machine profile {machine_profile_ref} is missing {exc}"
        ) from exc

    protocol_label = loaded.pop("protocol_label", "standard_print")
    run_modes = loaded.pop("run_modes", {}) or {}
    source = loaded.pop("source", {}) or {}
    printing = loaded.pop("printing", {}) or {}
    tips = loaded.pop("tips", {}) or {}
    print_groups = loaded.pop("print_groups", None)
    # Optional destination override: the machine profile supplies the paper
    # labware, but more than one deck slot can hold a paper substrate, so an
    # experiment may pick one slot or mirror the same pattern onto several.
    substrate = loaded.pop("substrate", {}) or {}
    # Optional finishing pass: a second liquid applied after every layer is
    # done. Passed straight through; the executor validates it.
    overprint = loaded.pop("overprint", None)
    # `targets:` remains accepted as the one-group shorthand.
    legacy_targets = loaded.pop("targets", None)

    if loaded:
        raise PrintFromVialLoadError(
            f"{path} has unexpected top-level key(s): {sorted(loaded)}"
        )

    try:
        resolved_source = resolve_source(source)
    except SourceConfigError as exc:
        raise PrintFromVialLoadError(f"{path}: {exc}") from exc

    if print_groups is None:
        if not legacy_targets:
            raise PrintFromVialLoadError(
                f"{path}: declare print_groups (or a plain targets list)"
            )
        print_groups = [
            {
                "targets": legacy_targets,
                "droplets": int(printing.get("droplets_per_target", 1)),
            }
        ]
    if not print_groups:
        raise PrintFromVialLoadError(f"{path}: print_groups must not be empty")

    default_source_well = resolved_source["wells"][0]
    normalized_groups = []
    for index, group in enumerate(print_groups, start=1):
        if not isinstance(group, dict):
            raise PrintFromVialLoadError(
                f"{path}: print_groups[{index}] must be a mapping"
            )
        targets = group.get("targets") or group.get("wells")
        if not targets:
            raise PrintFromVialLoadError(
                f"{path}: print_groups[{index}] must declare targets"
            )
        normalized_group = {
            "source_well": str(
                group.get("source_well") or default_source_well
            ).upper(),
            "source_wells_by_slot": group.get("source_wells") or {},
            "targets": [str(t).upper() for t in targets],
            "droplets": int(group.get("droplets", 1)),
        }
        # Optional per-group print height. Absent (the normal case) the group
        # prints at the machine profile's validated print_release height, so
        # nothing changes. Present, it overrides that standoff for this group's
        # targets only -- which is how a height sweep puts a different
        # dispense height on each row of one column. The executor validates it.
        group_height = group.get("dispense_height_mm")
        if group_height is not None:
            try:
                normalized_group["dispense_height_mm"] = float(group_height)
            except (TypeError, ValueError) as exc:
                raise PrintFromVialLoadError(
                    f"{path}: print_groups[{index}].dispense_height_mm must be "
                    f"a number, got {group_height!r}"
                ) from exc
        normalized_groups.append(normalized_group)

    if "slot" in substrate and "slots" in substrate:
        raise PrintFromVialLoadError(
            f"{path}: substrate must declare slot or slots, not both"
        )
    raw_paper_slots = substrate.get("slots")
    if raw_paper_slots is None:
        raw_paper_slots = [substrate.get("slot", paper["slot"])]
    if (
        not isinstance(raw_paper_slots, list)
        or not raw_paper_slots
        or any(isinstance(slot, bool) for slot in raw_paper_slots)
    ):
        raise PrintFromVialLoadError(
            f"{path}: substrate.slots must be a non-empty list of deck slots"
        )
    try:
        paper_slots = [int(slot) for slot in raw_paper_slots]
    except (TypeError, ValueError) as exc:
        raise PrintFromVialLoadError(
            f"{path}: substrate slots must be integers"
        ) from exc
    if len(set(paper_slots)) != len(paper_slots):
        raise PrintFromVialLoadError(f"{path}: substrate slots must be unique")

    paper_roles = ["paper"] + [
        f"paper_{index}" for index in range(2, len(paper_slots) + 1)
    ]
    paper_deck = {
        role: {
            "slot": slot,
            "load_name": substrate.get("labware", paper["load_name"]),
            "namespace": paper.get("namespace"),
            "version": paper.get("version"),
        }
        for role, slot in zip(paper_roles, paper_slots)
    }

    raw_source_wells = substrate.get("source_wells") or {}
    if not isinstance(raw_source_wells, dict):
        raise PrintFromVialLoadError(
            f"{path}: substrate.source_wells must map paper slots to source wells"
        )
    source_wells_by_slot: dict[int, str] = {}
    for raw_slot, raw_well in raw_source_wells.items():
        if isinstance(raw_slot, bool):
            raise PrintFromVialLoadError(
                f"{path}: substrate.source_wells keys must be deck-slot integers"
            )
        try:
            slot = int(raw_slot)
        except (TypeError, ValueError) as exc:
            raise PrintFromVialLoadError(
                f"{path}: substrate.source_wells keys must be deck-slot integers"
            ) from exc
        source_wells_by_slot[slot] = str(raw_well).upper()
    unknown_source_slots = sorted(set(source_wells_by_slot) - set(paper_slots))
    if unknown_source_slots:
        raise PrintFromVialLoadError(
            f"{path}: substrate.source_wells contains non-paper slot(s) "
            f"{unknown_source_slots}"
        )
    if source_wells_by_slot and set(source_wells_by_slot) != set(paper_slots):
        missing_source_slots = sorted(set(paper_slots) - set(source_wells_by_slot))
        raise PrintFromVialLoadError(
            f"{path}: substrate.source_wells is missing paper slot(s) "
            f"{missing_source_slots}"
        )
    paper_sources = {
        role: source_wells_by_slot.get(slot, default_source_well)
        for role, slot in zip(paper_roles, paper_slots)
    }
    undeclared_paper_sources = sorted(
        set(paper_sources.values()) - set(resolved_source["wells"])
    )
    if undeclared_paper_sources:
        raise PrintFromVialLoadError(
            f"{path}: paper source well(s) must be listed in source.wells: "
            f"{', '.join(undeclared_paper_sources)}"
        )

    for index, group in enumerate(normalized_groups, start=1):
        raw_group_sources = group.pop("source_wells_by_slot")
        if not raw_group_sources:
            continue
        if not isinstance(raw_group_sources, dict):
            raise PrintFromVialLoadError(
                f"{path}: print_groups[{index}].source_wells must map paper "
                "slots to source wells"
            )
        group_sources_by_slot: dict[int, str] = {}
        for raw_slot, raw_well in raw_group_sources.items():
            if isinstance(raw_slot, bool):
                raise PrintFromVialLoadError(
                    f"{path}: print_groups[{index}].source_wells keys must be "
                    "deck-slot integers"
                )
            try:
                slot = int(raw_slot)
            except (TypeError, ValueError) as exc:
                raise PrintFromVialLoadError(
                    f"{path}: print_groups[{index}].source_wells keys must be "
                    "deck-slot integers"
                ) from exc
            group_sources_by_slot[slot] = str(raw_well).upper()
        if set(group_sources_by_slot) != set(paper_slots):
            raise PrintFromVialLoadError(
                f"{path}: print_groups[{index}].source_wells must map exactly "
                f"the paper slots {paper_slots}"
            )
        group["source_wells"] = {
            role: group_sources_by_slot[slot]
            for role, slot in zip(paper_roles, paper_slots)
        }
        undeclared_group_sources = sorted(
            set(group["source_wells"].values()) - set(resolved_source["wells"])
        )
        if undeclared_group_sources:
            raise PrintFromVialLoadError(
                f"{path}: print_groups[{index}] source well(s) must be listed "
                f"in source.wells: {', '.join(undeclared_group_sources)}"
            )

    config: dict[str, Any] = {
        "protocol_label": str(protocol_label),
        "deck": {
            "source": resolved_source["deck_spec"],
            **paper_deck,
            "tiprack_p20": {
                "slot": int(tiprack["slot"]),
                "load_name": tiprack["load_name"],
            },
        },
        "paper_roles": paper_roles,
        "paper_sources": paper_sources,
        "pipette": {"name": pipette["name"], "mount": pipette["mount"]},
        "source": {
            "type": resolved_source["type"],
            "wells": resolved_source["wells"],
            "material": resolved_source["material"],
            "loaded_volume_ul": resolved_source["loaded_volume_ul"],
            "minimum_remaining_ul": resolved_source["minimum_remaining_ul"],
            "aspirate_height_mm": resolved_source["aspirate_height_mm"],
            "park_height_mm": float(source.get("park_height_mm", 5.0)),
        },
        "printing": {
            "droplet_volume_ul": float(printing.get("droplet_volume_ul", 5.0)),
            # Run-level print height. Defaults to the machine profile's
            # validated print_release standoff. An experiment may override it
            # for the whole run (a print group's own dispense_height_mm still
            # wins over this for that group's targets). Overriding here is a
            # deliberate, recorded experiment choice -- it does NOT edit the
            # laboratory-owned profile, which stays the default for every other
            # config.
            "dispense_height_mm": float(
                printing.get("dispense_height_mm", release["dispense_height_mm"])
            ),
            "pre_air_chase_ul": float(release.get("pre_air_chase_ul", 0.0) or 0.0),
            "air_gap_ul": float(release.get("trailing_air_gap_ul", 0.0) or 0.0),
            "air_gap_height_mm": float(release.get("air_gap_height_mm", 0.0) or 0.0),
            "push_out_ul": float(release.get("push_out_ul", 0.0) or 0.0),
            "blow_out": bool(release.get("blow_out", True)),
            "inter_drop_delay_s": float(printing.get("inter_drop_delay_s", 0.0) or 0.0),
            "inter_layer_delay_s": float(
                printing.get("inter_layer_delay_s", 0.0) or 0.0
            ),
            "initial_delay_s": float(
                printing.get("initial_delay_s", 0.0) or 0.0
            ),
            "layer_number_offset": int(
                printing.get("layer_number_offset", 0) or 0
            ),
            # Optional post-dispense dwell: after the drop is released and
            # blown out at the print height, lower the tip to `height_mm` above
            # the same paper well and hold there for `hold_s` before moving on.
            # Absent, nothing changes -- the tip leaves straight from the print
            # height, exactly as before. Passed through; the executor validates.
            "post_dispense_dwell": printing.get("post_dispense_dwell"),
        },
        "print_groups": normalized_groups,
        "tips": {
            "print_tip": str(tips.get("print_tip", "A1")).upper(),
            "return_tips": bool(tips.get("return_tips", True)),
            "pipette_tip_reuse": bool(tips.get("pipette_tip_reuse", True)),
            # Opt-in: one tip carried across every layer source well instead of
            # one tip per well. The executor's pre-flight rejects it unless
            # pipette_tip_reuse is true.
            "single_tip_all_sources": bool(
                tips.get("single_tip_all_sources", False)
            ),
        },
        "flow_rates": {"p20": pipette.get("flow_rates", {})},
        "safety": {"p20_max_volume_ul": float(pipette["maximum_volume_ul"])},
    }
    if overprint:
        if not isinstance(overprint, dict):
            raise PrintFromVialLoadError(f"{path}: overprint must be a mapping")
        source_well = str(overprint.get("source_well") or "").upper()
        if source_well and source_well not in resolved_source["wells"]:
            raise PrintFromVialLoadError(
                f"{path}: overprint source well {source_well} must be listed in "
                f"source.wells ({', '.join(resolved_source['wells'])})"
            )
        config["overprint"] = {
            "source_well": source_well,
            "targets": [str(t).upper() for t in (overprint.get("targets") or [])],
            "droplets": int(overprint.get("droplets", 1)),
            "delay_s": float(overprint.get("delay_s", 0.0) or 0.0),
        }
    return config, run_modes
