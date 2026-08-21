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
from ..standard.loader import ExperimentJobLoadError, load_machine_profile


class PrintFromVialLoadError(ValueError):
    """A print-from-vial configuration file is not valid."""


REQUIRED_LABWARE_ROLES = ("vial_rack", "paper", "tiprack")


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
    vial_rack = labware_by_role["vial_rack"]
    paper = labware_by_role["paper"]
    tiprack = labware_by_role["tiprack"]
    try:
        release = machine["print_release"]
        pipette = machine["pipette"]
    except KeyError as exc:
        raise PrintFromVialLoadError(
            f"machine profile {machine_profile_ref} is missing {exc}"
        ) from exc

    protocol_label = loaded.pop("protocol_label", "print_from_vial")
    run_modes = loaded.pop("run_modes", {}) or {}
    source = loaded.pop("source", {}) or {}
    printing = loaded.pop("printing", {}) or {}
    targets = loaded.pop("targets", None)
    tips = loaded.pop("tips", {}) or {}

    if loaded:
        raise PrintFromVialLoadError(
            f"{path} has unexpected top-level key(s): {sorted(loaded)}"
        )
    if "well" not in source:
        raise PrintFromVialLoadError(f"{path}: source must declare a 'well'")
    if not targets:
        raise PrintFromVialLoadError(f"{path}: targets must list at least one paper well")

    config: dict[str, Any] = {
        "protocol_label": str(protocol_label),
        "deck": {
            "source": {
                "slot": int(vial_rack["slot"]),
                "load_name": vial_rack["load_name"],
                "namespace": vial_rack.get("namespace"),
                "version": vial_rack.get("version"),
            },
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
            "well": str(source["well"]).upper(),
            "material": str(source.get("material", "unlabeled liquid")),
            "loaded_volume_ul": float(source.get("loaded_volume_ul", 0.0)),
            "minimum_remaining_ul": float(source.get("minimum_remaining_ul", 0.0)),
            "aspirate_height_mm": float(vial_rack.get("aspirate_height_mm") or 4.0),
        },
        "printing": {
            "droplet_volume_ul": float(printing.get("droplet_volume_ul", 5.0)),
            "droplets_per_target": int(printing.get("droplets_per_target", 1)),
            "dispense_height_mm": float(release["dispense_height_mm"]),
            "pre_air_chase_ul": float(release.get("pre_air_chase_ul", 0.0) or 0.0),
            "air_gap_ul": float(release.get("trailing_air_gap_ul", 0.0) or 0.0),
            "air_gap_height_mm": float(release.get("air_gap_height_mm", 0.0) or 0.0),
            "push_out_ul": float(release.get("push_out_ul", 0.0) or 0.0),
            "blow_out": bool(release.get("blow_out", True)),
            "inter_drop_delay_s": float(printing.get("inter_drop_delay_s", 0.0) or 0.0),
        },
        "targets": [str(t).upper() for t in targets],
        "tips": {
            "print_tip": str(tips.get("print_tip", "A1")).upper(),
            "return_tips": bool(tips.get("return_tips", True)),
        },
        "flow_rates": {"p20": pipette.get("flow_rates", {})},
        "safety": {
            "p20_max_volume_ul": float(pipette["maximum_volume_ul"]),
            "expected_source_slot": int(vial_rack["slot"]),
        },
    }
    return config, run_modes
