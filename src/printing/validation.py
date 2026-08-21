"""Deterministic pre-build validation for structured printing requests."""
from __future__ import annotations

import importlib.util
import json
import math
import re
from types import ModuleType
from typing import Any

import yaml

from .config import REPO_ROOT
from .schemas import PrintingFamily, Severity, ValidationIssue, ValidationReport


_PAPER_JSON = REPO_ROOT / "labware" / "paper_print_96_flat.json"
_PROTOCOLS = REPO_ROOT / "src" / "protocols" / "printing"
_ROBOT_CONFIG = REPO_ROOT / "configs" / "robot.yaml"


def _issue(code: str, message: str, field: str | None = None) -> ValidationIssue:
    return ValidationIssue(severity=Severity.ERROR, code=code, field=field, message=message)


def _fresh_protocol_module(filename: str) -> ModuleType:
    # Opentrons 9 imports numpy.trapz, which numpy>=2 renamed to trapezoid.
    # Tests apply the same compatibility alias in conftest; production validation
    # must also work when invoked directly through the printing CLI/agent.
    import numpy as np

    if not hasattr(np, "trapz") and hasattr(np, "trapezoid"):
        np.trapz = np.trapezoid
    path = _PROTOCOLS / filename
    spec = importlib.util.spec_from_file_location(f"printing_validation_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load protocol adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _paper_wells() -> dict[str, dict[str, Any]]:
    return json.loads(_PAPER_JSON.read_text(encoding="utf-8"))["wells"]


def _well_xy(name: str) -> tuple[float, float]:
    well = _paper_wells()[str(name).upper()]
    return float(well["x"]), float(well["y"])


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in str(value).split("."))


def _api_errors(module: ModuleType) -> list[ValidationIssue]:
    requirements = getattr(module, "requirements", {})
    api_level = str(requirements.get("apiLevel", ""))
    robot = yaml.safe_load(_ROBOT_CONFIG.read_text(encoding="utf-8")) or {}
    maximum = str(robot.get("capabilities", {}).get("max_protocol_api_version", ""))
    errors: list[ValidationIssue] = []
    if requirements.get("robotType") != "OT-2" or not api_level:
        errors.append(_issue("protocol_requirements", "protocol must declare OT-2 and an API level", "requirements"))
    elif maximum and _version_tuple(api_level) > _version_tuple(maximum):
        errors.append(_issue("protocol_api_compatibility", f"protocol API {api_level} exceeds configured robot maximum {maximum}", "requirements.apiLevel"))
    return errors


def _common_errors(config: dict[str, Any], expected_slots: dict[str, int]) -> list[ValidationIssue]:
    errors: list[ValidationIssue] = []
    deck = config.get("deck")
    if not isinstance(deck, dict) or not deck:
        return [_issue("missing_deck", "deck must be a nonempty mapping", "deck")]
    slots: dict[int, str] = {}
    for role, labware in deck.items():
        if not isinstance(labware, dict) or "slot" not in labware:
            errors.append(_issue("missing_deck_slot", f"deck.{role}.slot is required", f"deck.{role}"))
            continue
        slot = labware["slot"]
        if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= 11:
            errors.append(_issue("invalid_deck_slot", f"deck.{role}.slot must be 1-11", f"deck.{role}.slot"))
        elif slot in slots:
            errors.append(_issue("duplicate_deck_slot", f"deck slot {slot} is shared by {slots[slot]} and {role}", "deck"))
        else:
            slots[slot] = role
    for role, expected in expected_slots.items():
        actual = deck.get(role, {}).get("slot")
        if actual != expected:
            errors.append(_issue("fixed_deck_slot", f"deck.{role} must be slot {expected}, got {actual}", f"deck.{role}.slot"))
    pipette = config.get("pipette", {})
    if pipette.get("name") != "p20_single_gen2":
        errors.append(_issue("fixed_pipette", "modern printing requires p20_single_gen2", "pipette.name"))
    if pipette.get("mount") != "left":
        errors.append(_issue("fixed_mount", "modern printing requires the configured left mount", "pipette.mount"))
    return errors


def _liquid_errors(config: dict[str, Any], *, design: bool) -> list[ValidationIssue]:
    section_name = "printing" if design else "print"
    volume_name = "droplet_volume_ul" if design else "volume_ul"
    section = config.get(section_name, {})
    maximum = float(config.get("safety", {}).get("p20_max_volume_ul", 20.0))
    volume = float(section.get(volume_name, 0.0) or 0.0)
    air_gap = float(section.get("air_gap_ul", 0.0) or 0.0)
    chase = float(section.get("pre_air_chase_ul", 0.0) or 0.0) if design else 0.0
    push_out = float(section.get("push_out_ul", 0.0) or 0.0)
    errors: list[ValidationIssue] = []
    if not 0 < volume <= maximum:
        errors.append(_issue("nonpositive_volume", f"{section_name}.{volume_name} must be in (0, {maximum:g}] uL", f"{section_name}.{volume_name}"))
    if air_gap < 0:
        errors.append(_issue("negative_air_gap", f"{section_name}.air_gap_ul must be >= 0", f"{section_name}.air_gap_ul"))
    if chase < 0:
        errors.append(_issue("negative_air_chase", "printing.pre_air_chase_ul must be >= 0", "printing.pre_air_chase_ul"))
    piston = volume + max(air_gap, 0.0) + max(chase, 0.0)
    if piston > maximum + 1e-9:
        errors.append(_issue("pipette_capacity", f"piston load {piston:g} uL exceeds configured P20 capacity {maximum:g} uL", f"{section_name}.{volume_name}"))
    if not 0 <= push_out <= maximum:
        errors.append(_issue("push_out_range", f"{section_name}.push_out_ul must be in [0, {maximum:g}]", f"{section_name}.push_out_ul"))
    timing = (
        ("dispense_height_mm", "air_gap_height_mm", "inter_drop_delay_s", "inter_layer_delay_s", "inter_clover_delay_s")
        if design
        else ("air_gap_height_mm", "z_mm", "post_dispense_delay_s", "rest_minutes")
    )
    for key in timing:
        if float(section.get(key, 0.0) or 0.0) < 0:
            errors.append(_issue("negative_distance_or_delay", f"{section_name}.{key} must be >= 0", f"{section_name}.{key}"))
    return errors


def _labware_wells(config: dict[str, Any], role: str) -> dict[str, dict[str, Any]]:
    load_name = str(config["deck"][role]["load_name"])
    path = REPO_ROOT / "labware" / f"{load_name}.json"
    if not path.is_file():
        raise ValueError(f"custom source labware definition not found: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))["wells"]


def _source_errors(
    config: dict[str, Any],
    source: dict[str, Any],
    *,
    deck_role: str,
    required_ul: float,
    label: str,
    well_key: str = "well",
) -> list[ValidationIssue]:
    errors: list[ValidationIssue] = []
    wells = _labware_wells(config, deck_role)
    name = str(source.get(well_key, "")).upper()
    if name not in wells:
        return [_issue("source_well", f"{label} source well {name} does not exist", f"{label}.{well_key}")]
    well = wells[name]
    depth = float(well["depth"])
    maximum = float(well["totalLiquidVolume"])
    aspirate_height = float(source.get("aspirate_height_mm", 0.0) or 0.0)
    loaded = float(source.get("loaded_volume_ul", 0.0) or 0.0)
    reserve = float(source.get("minimum_remaining_ul", 0.0) or 0.0)
    if not 0 < aspirate_height < depth:
        errors.append(_issue("aspirate_height", f"{label}.aspirate_height_mm must be > 0 and < {depth:g} mm", f"{label}.aspirate_height_mm"))
    if not 0 < loaded <= maximum:
        errors.append(_issue("source_overfill", f"{label}.loaded_volume_ul must be in (0, {maximum:g}]", f"{label}.loaded_volume_ul"))
    if reserve < 0:
        errors.append(_issue("negative_reserve", f"{label}.minimum_remaining_ul must be >= 0", f"{label}.minimum_remaining_ul"))
    if loaded < required_ul + max(reserve, 0.0):
        errors.append(_issue("source_volume", f"{label} needs {required_ul:g} uL plus {reserve:g} uL reserve; loaded_volume_ul is {loaded:g}", f"{label}.loaded_volume_ul"))
    diameter = well.get("diameter")
    if diameter and aspirate_height > 0:
        cover_volume = math.pi * (float(diameter) / 2.0) ** 2 * aspirate_height
        remaining = loaded - required_ul
        if remaining <= cover_volume:
            errors.append(_issue("liquid_column", f"{label} would retain {remaining:g} uL, below approximately {cover_volume:g} uL needed to cover the aspiration height", f"{label}.loaded_volume_ul"))
    return errors


def _tip_error(name: Any, field: str) -> ValidationIssue | None:
    tip = str(name).upper()
    if not re.fullmatch(r"[A-H](?:[1-9]|1[0-2])", tip):
        return _issue("tip_location", f"P20 tip {tip!r} does not exist in a 96-tip rack", field)
    return None


def validate_four_clover_config(config: dict[str, Any], *, workflow_name: str) -> ValidationReport:
    expected_source = int(config.get("safety", {}).get("expected_source_slot", 7))
    errors = _common_errors(config, {"source": expected_source, "paper": 5, "tiprack_p20": 9})
    errors.extend(_liquid_errors(config, design=True))
    calculated: dict[str, Any] = {}
    try:
        module = _fresh_protocol_module("12_four_clover_paper_print.py")
        errors.extend(_api_errors(module))
        module.CONFIG = config
        specs = module._center_specs()
        paper_names = _paper_wells()
        for spec in specs:
            if spec["reference_well"] not in paper_names:
                errors.append(_issue("reference_well", f"{spec['name']} reference well {spec['reference_well']} does not exist", "destination"))
        clovers = module._resolve_clovers(_well_xy)
        module._print_order(clovers)
        bounds = module._paper_bounds(_well_xy, list(paper_names))
        radius = float(config.get("validation", {}).get("droplet_radius_mm", 0.0) or 0.0)
        if radius < 0:
            errors.append(_issue("droplet_radius", "validation.droplet_radius_mm must be >= 0", "validation.droplet_radius_mm"))
            radius = 0.0
        errors.extend(_issue("paper_footprint", message, "destination") for message in module._boundary_violations(clovers, bounds, radius))
        maximum = float(config.get("safety", {}).get("p20_max_volume_ul", 20.0))
        errors.extend(
            _issue("pipette_capacity", message, "printing")
            for message in module._capacity_errors(clovers, float(config["printing"]["droplet_volume_ul"]), float(config["printing"].get("air_gap_ul", 0.0) or 0.0), maximum)
        )
        grid = config.get("destination", {}).get("clover_grid") or {}
        if bool(grid.get("enabled", False)):
            names = {clover["name"] for clover in clovers}
            for key in ("geometry_overrides", "layer_overrides", "pre_air_chase_overrides"):
                unknown = set((grid.get(key) or {})) - names
                if unknown:
                    errors.append(_issue("unknown_grid_override", f"destination.clover_grid.{key} has unknown clover(s): {', '.join(sorted(unknown))}", f"destination.clover_grid.{key}"))
        intra, inter = module._distance_report(clovers)
        limits = config.get("validation", {})
        minimum_intra = float(limits.get("min_intra_clover_distance_mm", 0.0) or 0.0)
        minimum_inter = float(limits.get("min_inter_clover_distance_mm", 0.0) or 0.0)
        allow_duplicates = bool(limits.get("allow_duplicate_droplet_positions", False))
        for entry in intra:
            if entry["min_distance"] <= 1e-6 and not allow_duplicates:
                errors.append(_issue("duplicate_coordinate", f"{entry['clover']} has duplicate droplet coordinates", "destination"))
            elif entry["min_distance"] < minimum_intra:
                errors.append(_issue("intra_clover_spacing", f"{entry['clover']} minimum spacing {entry['min_distance']:.2f} mm is below {minimum_intra:g} mm", "destination"))
        for entry in inter:
            if entry["min_distance"] < minimum_inter:
                errors.append(_issue("inter_clover_spacing", f"{entry['clovers'][0]} and {entry['clovers'][1]} approach to {entry['min_distance']:.2f} mm, below {minimum_inter:g} mm", "destination"))
        volume = float(config["printing"]["droplet_volume_ul"])
        deposits = sum(clover["layers"] for clover in clovers) * 4
        required = deposits * volume
        errors.extend(_source_errors(config, config["source"], deck_role="source", required_ul=required, label="source"))
        tip_error = _tip_error(config.get("tips", {}).get("p20", {}).get("print_tip"), "tips.p20.print_tip")
        if tip_error:
            errors.append(tip_error)
        calculated = {
            "clover_count": len(clovers),
            "deposit_count": deposits,
            "liquid_required_ul": required,
            "coordinates": {clover["name"]: {key: list(clover["droplets"][key]["absolute"]) for key in ("d1", "d2", "d3", "d4")} for clover in clovers},
        }
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(_issue("four_clover_config", str(exc), "parameters"))
    return ValidationReport(valid=not errors, workflow_name=workflow_name, family=PrintingFamily.DESIGN, design_name="four_clover", errors=errors, calculated=calculated)


def validate_standard_config(config: dict[str, Any], *, workflow_name: str) -> ValidationReport:
    version = int(config.get("protocol_version", 0) or 0)
    expected_slots = {
        9: {"plate": 4, "paper": 5, "tiprack_p20": 9},
        12: {"bp_source": 7, "dmmp_source": 4, "paper": 5, "tiprack_p20": 9},
    }.get(version, {"source": int(config.get("safety", {}).get("expected_source_slot", 7)), "paper": 5, "tiprack_p20": 9})
    errors = _common_errors(config, expected_slots)
    errors.extend(_liquid_errors(config, design=False))
    calculated: dict[str, Any] = {}
    try:
        volume = float(config["print"]["volume_ul"])
        paper_names = _paper_wells()
        if version == 9:
            module = _fresh_protocol_module("09_plate_well_direct_paper_print_v9.py")
            module.CONFIG = config
            _, layers = module._layer_plan()
            columns = [int(column) for column in config["print"]["replicate_columns"]]
            if not layers or not columns or len(columns) != len(set(columns)):
                errors.append(_issue("destination_completeness", "layers and unique replicate columns are required", "print"))
            if any(count < 1 for count in layers.values()):
                errors.append(_issue("layer_count", "print.layers_by_row values must be >= 1", "print.layers_by_row"))
            for row in layers:
                for column in columns:
                    if f"{row}{column}" not in paper_names:
                        errors.append(_issue("paper_location", f"paper well {row}{column} does not exist", "print"))
            deposits = sum(layers.values()) * len(columns)
            required = deposits * volume
            errors.extend(_source_errors(config, config["source"], deck_role="plate", required_ul=required, label="source"))
        elif version in {10, 11, 13, 14}:
            module = _fresh_protocol_module("10_complementary_direct_paper_print.py")
            module.CONFIG = config
            rows, columns, spots = module._spot_layers()
            if not rows or len(rows) != len(set(rows)) or not columns or len(columns) != len(set(columns)):
                errors.append(_issue("destination_completeness", "destination rows and columns must be nonempty and unique", "destination"))
            if len(spots) != len(rows) * len(columns):
                errors.append(_issue("destination_completeness", "print.layers must define every shared destination row/column", "print.layers"))
            if any(count < 1 for count in spots.values()):
                errors.append(_issue("layer_count", "every destination layer count must be >= 1", "print.layers"))
            if any(name not in paper_names for name in spots):
                errors.append(_issue("paper_location", "one or more configured paper destinations do not exist", "destination"))
            deposits = sum(spots.values())
            required = deposits * volume
            errors.extend(_source_errors(config, config["source"], deck_role="source", required_ul=required, label="source"))
        elif version == 12:
            module = _fresh_protocol_module("11_combined_bp_dmmp_paper_print.py")
            module.CONFIG = config
            rows, columns, plans = module._resolve_plans()
            if len(config.get("parts", [])) != 2:
                errors.append(_issue("parts", "parts must contain exactly two configured source profiles", "parts"))
            if not rows or len(rows) != len(set(rows)) or not columns or len(columns) != len(set(columns)):
                errors.append(_issue("destination_completeness", "destination rows and columns must be nonempty and unique", "destination"))
            deposits = 0
            assigned_tips: list[str] = []
            for index, plan in enumerate(plans):
                part = plan["part"]
                spots = plan["spots"]
                label = str(part.get("label", f"parts[{index}]"))
                if len(spots) != len(rows) * len(columns):
                    errors.append(_issue("destination_completeness", f"{label}.layers must define every destination", f"parts.{index}.layers"))
                if any(count < 1 for count in spots.values()):
                    errors.append(_issue("layer_count", f"{label} destination layers must be >= 1", f"parts.{index}.layers"))
                if any(name not in paper_names for name in spots):
                    errors.append(_issue("paper_location", f"{label} contains a paper destination that does not exist", "destination"))
                if float(part.get("rest_minutes", 0.0) or 0.0) < 0:
                    errors.append(_issue("negative_delay", f"{label}.rest_minutes must be >= 0", f"parts.{index}.rest_minutes"))
                part_deposits = sum(spots.values())
                deposits += part_deposits
                role = str(part.get("source_role", ""))
                if role not in {"bp_source", "dmmp_source"}:
                    errors.append(_issue("source_role", f"{label}.source_role is invalid: {role}", f"parts.{index}.source_role"))
                else:
                    errors.extend(_source_errors(config, part, deck_role=role, required_ul=part_deposits * volume, label=label, well_key="source_well"))
                tip = str(part.get("print_tip", "")).upper()
                assigned_tips.append(tip)
                tip_error = _tip_error(tip, f"parts.{index}.print_tip")
                if tip_error:
                    errors.append(tip_error)
            if len(assigned_tips) != len(set(assigned_tips)):
                errors.append(_issue("duplicate_tip", "combined workflow parts require distinct print tips", "parts"))
            if float(config.get("between_parts_delay_minutes", 0.0) or 0.0) < 0:
                errors.append(_issue("negative_delay", "between_parts_delay_minutes must be >= 0", "between_parts_delay_minutes"))
        else:
            raise ValueError(f"protocol_version {version} is not a modern standard workflow")
        errors.extend(_api_errors(module))
        if version != 12:
            tip_error = _tip_error(config.get("tips", {}).get("p20", {}).get("print_tip"), "tips.p20.print_tip")
            if tip_error:
                errors.append(tip_error)
        calculated = {"deposit_count": deposits, "liquid_required_ul": deposits * volume}
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(_issue("standard_config", str(exc), "parameters"))
    return ValidationReport(valid=not errors, workflow_name=workflow_name, family=PrintingFamily.STANDARD, errors=errors, calculated=calculated)
