"""Load the dye-dilution demo config, flattened with its machine profile.

Deck slots for the two sources and the paper come from the experiment YAML (this
demo deliberately places the BRAND plate in slot 1 and the paper in slot 11);
labware identity, the pipette, and the validated print-release air handling come
from the registered machine profile, exactly as the other printing workflows do.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from ..config import resolve_repo_path
from ..source_config import SOURCE_TYPES
from ..standard.loader import ExperimentJobLoadError, load_machine_profile


class DyeDemoLoadError(ValueError):
    """A dye-demo configuration file is not valid."""


def load_dye_demo_config(reference: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(config, run_modes)`` ready for the 03 executor's CONFIG block."""
    path = resolve_repo_path(reference)
    if not path.is_file():
        raise DyeDemoLoadError(f"dye demo config not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise DyeDemoLoadError(f"dye demo config must be a mapping: {path}")
    loaded = deepcopy(loaded)

    machine_profile_ref = loaded.pop("machine_profile", None)
    if not machine_profile_ref:
        raise DyeDemoLoadError(f"{path} must reference a registered machine_profile")
    try:
        machine = load_machine_profile(machine_profile_ref)
    except ExperimentJobLoadError as exc:
        raise DyeDemoLoadError(str(exc)) from exc

    labware_by_role = {item["role"]: item for item in machine.get("labware", [])}
    try:
        paper = labware_by_role["paper"]
        tiprack = labware_by_role["tiprack"]
        release = machine["print_release"]
        pipette = machine["pipette"]
    except KeyError as exc:
        raise DyeDemoLoadError(
            f"machine profile {machine_profile_ref} is missing {exc}"
        ) from exc

    protocol_label = loaded.pop("protocol_label", "dye_dilution_print_demo")
    run_modes = loaded.pop("run_modes", {}) or {}
    tip_reuse = bool(loaded.pop("pipette_tip_reuse", False))
    dye_source = loaded.pop("dye_source", {}) or {}
    water_source = loaded.pop("water_source", {}) or {}
    dilution = loaded.pop("dilution", {}) or {}
    paper_cfg = loaded.pop("paper", {}) or {}
    standard_print = loaded.pop("standard_print", {}) or {}
    clover_print = loaded.pop("clover_print", {}) or {}
    printing = loaded.pop("printing", {}) or {}
    tips = loaded.pop("tips", {}) or {}
    if loaded:
        raise DyeDemoLoadError(f"{path} has unexpected top-level key(s): {sorted(loaded)}")

    plate_spec = SOURCE_TYPES["well_plate"]
    vial_spec = SOURCE_TYPES["vial_rack"]
    for name, block in (("dye_source", dye_source), ("water_source", water_source)):
        if "well" not in block:
            raise DyeDemoLoadError(f"{path}: {name} must declare a 'well'")
    if "destination_well" not in dilution:
        raise DyeDemoLoadError(f"{path}: dilution must declare a destination_well")
    if not standard_print.get("targets"):
        raise DyeDemoLoadError(f"{path}: standard_print.targets must not be empty")

    dye_volume = float(dilution.get("dye_volume_ul", 20.0))
    water_volume = float(dilution.get("water_volume_ul", 80.0))

    config: dict[str, Any] = {
        "protocol_label": str(protocol_label),
        "deck": {
            "plate": {
                "slot": int(dye_source.get("slot", plate_spec["default_slot"])),
                "load_name": dye_source.get("labware", plate_spec["load_name"]),
                "namespace": plate_spec["namespace"],
                "version": plate_spec["version"],
            },
            "vial_rack": {
                "slot": int(water_source.get("slot", vial_spec["default_slot"])),
                "load_name": water_source.get("labware", vial_spec["load_name"]),
                "namespace": vial_spec["namespace"],
                "version": vial_spec["version"],
            },
            "paper": {
                "slot": int(paper_cfg.get("slot", paper["slot"])),
                "load_name": paper_cfg.get("labware", paper["load_name"]),
                "namespace": paper.get("namespace"),
                "version": paper.get("version"),
            },
            "tiprack_p20": {
                "slot": int(tiprack["slot"]),
                "load_name": tiprack["load_name"],
            },
        },
        "pipette": {"name": pipette["name"], "mount": pipette["mount"]},
        "dye_source": {
            "well": str(dye_source["well"]).upper(),
            "loaded_volume_ul": float(dye_source.get("loaded_volume_ul", 0.0)),
            "aspirate_height_mm": float(
                dye_source.get("aspirate_height_mm",
                               plate_spec["default_aspirate_height_mm"])
            ),
        },
        "water_source": {
            "well": str(water_source["well"]).upper(),
            "material": str(water_source.get("material", "water")),
            "aspirate_height_mm": float(
                water_source.get("aspirate_height_mm",
                                 vial_spec["default_aspirate_height_mm"])
            ),
        },
        "dilution": {
            # false -> the destination well is prepared by hand; the robot only prints
            "enabled": bool(dilution.get("enabled", True)),
            "destination_well": str(dilution["destination_well"]).upper(),
            "dye_volume_ul": dye_volume,
            "water_volume_ul": water_volume,
            "total_volume_ul": float(
                dilution.get("total_volume_ul", dye_volume + water_volume)
            ),
            "transfer_chunk_ul": float(
                dilution.get("transfer_chunk_ul", pipette["maximum_volume_ul"])
            ),
            "mix_cycles": int(dilution.get("mix_cycles", 0)),
            "mix_volume_ul": float(dilution.get("mix_volume_ul", 0.0)),
            "dispense_height_mm": float(dilution.get("dispense_height_mm", 2.0)),
        },
        "printing": {
            "droplet_volume_ul": float(printing.get("droplet_volume_ul", 5.0)),
            # Aspiration height inside the diluted well during printing. Kept low
            # because that well drains from 100 uL to ~40 uL over the run.
            "source_aspirate_height_mm": float(
                printing.get("source_aspirate_height_mm", 0.5)
            ),
            "dispense_height_mm": float(release["dispense_height_mm"]),
            "pre_air_chase_ul": float(release.get("pre_air_chase_ul", 0.0) or 0.0),
            "air_gap_ul": float(release.get("trailing_air_gap_ul", 0.0) or 0.0),
            "air_gap_height_mm": float(release.get("air_gap_height_mm", 0.0) or 0.0),
            "push_out_ul": float(release.get("push_out_ul", 0.0) or 0.0),
            "blow_out": bool(release.get("blow_out", True)),
            "inter_drop_delay_s": float(printing.get("inter_drop_delay_s", 0.0) or 0.0),
        },
        "standard_print": {
            # false -> skip the standard column print entirely (clover-only run)
            "enabled": bool(standard_print.get("enabled", True)),
            "source_well": str(
                standard_print.get("source_well", dilution["destination_well"])
            ).upper(),
            "targets": [str(t).upper() for t in standard_print["targets"]],
            "droplets_per_target": int(standard_print.get("droplets_per_target", 1)),
        },
        "clover_print": {
            "source_well": str(
                clover_print.get("source_well", dilution["destination_well"])
            ).upper(),
            "reference": str(clover_print.get("reference", "B3")).upper(),
            "clovers": int(clover_print.get("clovers", 1)),
            "half_width_mm": float(clover_print.get("half_width_mm", 2.0)),
            "half_height_mm": float(clover_print.get("half_height_mm", 2.0)),
            "x_offset_mm": float(clover_print.get("x_offset_mm", 0.0)),
            "y_offset_mm": float(clover_print.get("y_offset_mm", 0.0)),
        },
        "tips": {
            "start_tip": str(tips.get("start_tip", "A1")).upper(),
            "return_tips": bool(tips.get("return_tips", False)),
            "pipette_tip_reuse": tip_reuse,
        },
        "flow_rates": {"p20": pipette.get("flow_rates", {})},
        "safety": {
            "p20_max_volume_ul": float(pipette["maximum_volume_ul"]),
            "source_minimum_remaining_ul": float(
                dilution.get("minimum_remaining_ul", 10.0)
            ),
        },
    }
    return config, run_modes
