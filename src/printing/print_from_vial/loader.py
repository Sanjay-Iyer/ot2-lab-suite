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
        normalized_groups.append(
            {
                "source_well": str(
                    group.get("source_well") or default_source_well
                ).upper(),
                "targets": [str(t).upper() for t in targets],
                "droplets": int(group.get("droplets", 1)),
            }
        )

    config: dict[str, Any] = {
        "protocol_label": str(protocol_label),
        "deck": {
            "source": resolved_source["deck_spec"],
            "paper": {
                "slot": int(paper["slot"]),
                "load_name": paper["load_name"],
                "namespace": paper.get("namespace"),
                "version": paper.get("version"),
            },
            "tiprack_p20": {
                "slot": int(tiprack["slot"]),
                "load_name": tiprack["load_name"],
            },
        },
        "pipette": {"name": pipette["name"], "mount": pipette["mount"]},
        "source": {
            "type": resolved_source["type"],
            "wells": resolved_source["wells"],
            "material": resolved_source["material"],
            "loaded_volume_ul": resolved_source["loaded_volume_ul"],
            "minimum_remaining_ul": resolved_source["minimum_remaining_ul"],
            "aspirate_height_mm": resolved_source["aspirate_height_mm"],
        },
        "printing": {
            "droplet_volume_ul": float(printing.get("droplet_volume_ul", 5.0)),
            "dispense_height_mm": float(release["dispense_height_mm"]),
            "pre_air_chase_ul": float(release.get("pre_air_chase_ul", 0.0) or 0.0),
            "air_gap_ul": float(release.get("trailing_air_gap_ul", 0.0) or 0.0),
            "air_gap_height_mm": float(release.get("air_gap_height_mm", 0.0) or 0.0),
            "push_out_ul": float(release.get("push_out_ul", 0.0) or 0.0),
            "blow_out": bool(release.get("blow_out", True)),
            "inter_drop_delay_s": float(printing.get("inter_drop_delay_s", 0.0) or 0.0),
            "inter_layer_delay_s": float(
                printing.get("inter_layer_delay_s", 0.0) or 0.0
            ),
        },
        "print_groups": normalized_groups,
        "tips": {
            "print_tip": str(tips.get("print_tip", "A1")).upper(),
            "return_tips": bool(tips.get("return_tips", True)),
            "pipette_tip_reuse": bool(tips.get("pipette_tip_reuse", True)),
        },
        "flow_rates": {"p20": pipette.get("flow_rates", {})},
        "safety": {"p20_max_volume_ul": float(pipette["maximum_volume_ul"])},
    }
    return config, run_modes
