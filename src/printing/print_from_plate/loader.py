"""Load a minimal print-from-plate config, flattened with its machine profile.

The experiment YAML never restates deck slots, calibrated heights, or the
validated print-release air handling -- it references the SAME
laboratory-owned machine profile family the standard printing workflow uses
(``configs/machines/ot2_print_from_plate_p20_v1.yaml``) via
``src.printing.standard.loader.load_machine_profile``. Only genuinely
experiment-owned choices (source well, droplet volume, targets, repeats,
delay, tip behavior) live in this file's YAML.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from ..config import resolve_repo_path
from ..standard.loader import ExperimentJobLoadError, load_machine_profile


class PrintFromPlateLoadError(ValueError):
    """A print-from-plate configuration file is not valid."""


REQUIRED_LABWARE_ROLES = ("plate_source", "paper", "tiprack")


def load_print_from_plate_config(reference: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and flatten one print-from-plate YAML into an executor-ready CONFIG.

    Returns ``(config, run_modes)``. ``config`` matches exactly what
    ``01b_print_from_plate.py``'s embedded ``CONFIG`` block expects.
    """
    path = resolve_repo_path(reference)
    if not path.is_file():
        raise PrintFromPlateLoadError(f"print-from-plate config not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise PrintFromPlateLoadError(f"print-from-plate config must be a mapping: {path}")
    loaded = deepcopy(loaded)

    loaded.pop("schema_version", None)
    machine_profile_ref = loaded.pop("machine_profile", None)
    if not machine_profile_ref:
        raise PrintFromPlateLoadError(
            f"{path} must reference a registered machine_profile"
        )
    try:
        machine = load_machine_profile(machine_profile_ref)
    except ExperimentJobLoadError as exc:
        raise PrintFromPlateLoadError(str(exc)) from exc

    labware_by_role = {item["role"]: item for item in machine.get("labware", [])}
    missing_roles = [role for role in REQUIRED_LABWARE_ROLES if role not in labware_by_role]
    if missing_roles:
        raise PrintFromPlateLoadError(
            f"machine profile {machine_profile_ref} has no {missing_roles} labware role(s)"
        )
    plate_source = labware_by_role["plate_source"]
    paper = labware_by_role["paper"]
    tiprack = labware_by_role["tiprack"]
    try:
        release = machine["print_release"]
        pipette = machine["pipette"]
    except KeyError as exc:
        raise PrintFromPlateLoadError(
            f"machine profile {machine_profile_ref} is missing {exc}"
        ) from exc

    protocol_label = loaded.pop("protocol_label", "print_from_plate")
    run_modes = loaded.pop("run_modes", {}) or {}
    source = loaded.pop("source", {}) or {}
    printing = loaded.pop("printing", {}) or {}
    targets = loaded.pop("targets", None)
    tips = loaded.pop("tips", {}) or {}

    if loaded:
        raise PrintFromPlateLoadError(
            f"{path} has unexpected top-level key(s): {sorted(loaded)}"
        )
    if "well" not in source:
        raise PrintFromPlateLoadError(f"{path}: source must declare a 'well'")
    if not targets:
        raise PrintFromPlateLoadError(f"{path}: targets must list at least one paper well")

    config: dict[str, Any] = {
        "protocol_label": str(protocol_label),
        "deck": {
            "source": {
                "slot": int(plate_source["slot"]),
                "load_name": plate_source["load_name"],
                "namespace": plate_source.get("namespace"),
                "version": plate_source.get("version"),
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
            "aspirate_height_mm": float(plate_source.get("aspirate_height_mm") or 0.2),
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
            "expected_source_slot": int(plate_source["slot"]),
        },
    }
    return config, run_modes
