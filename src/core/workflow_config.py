"""
src/core/workflow_config.py
===========================
The single authoritative build-time entry point that turns a workflow YAML into the
internal CONFIG the flagship protocol executes, running ALL shared validators along
the way. Used by scripts/build_vial_dilution_print.py so the build script does not
reimplement any validation rule (Part 4: "one authoritative implementation per rule").

It accepts two shapes:
  * NEW schema — visually-distinct sections: deck / pipettes / labware / materials /
    dilution_plan / mixing_plan / print_groups / imaging / tip_policy.
  * LEGACY schema — the historical flat dict (deck / pipette / sources / dilution /
    color_series / printing / camera / tips). Passed through unchanged so old configs
    and the embedded default keep working; print_groups are still resolved/validated.

Validators reused (never duplicated here):
  * src/core/pipette_selection.py  — volume->pipette resolution.
  * src/core/print_groups.py       — group schema + layout/destination validation.
  * src/core/materials.py          — material/vial + conservative volume accounting.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from src.core.materials import (
    build_labware_index,
    parse_materials,
    validate_materials,
)
from src.core.pipette_selection import load_pipette_specs
from src.core.print_groups import (
    Issue,
    PrintConfigError,
    parse_print_config,
    resolve_and_validate,
)

DEFAULT_OVERAGE = 1.10  # 10% conservative overage on every vial aspiration

# Pre-flight geometry/safety expectations the protocol's _preflight() reads. New-schema
# configs inherit these defaults (matching the active custom rack) unless overridden.
DEFAULT_SAFETY = {
    "expected_tuberack_load_name": "tuberack_3dprint_20ml_8vials_v2",
    "expected_well_count": 8,
    "expected_diameter_mm": 28.0,
    "expected_depth_mm": 55.0,
    "expected_row_spacing_mm": 34.0,
    "expected_col_spacing_mm": 31.0,
    "geometry_tolerance_mm": 0.5,
    "pipette_min_accurate_ul": 20.0,
    "expected_plate_well_count": 96,
    "tiprack_rows_per_column": 8,
    "pipette_max_volume_ul": 300.0,
}


class WorkflowConfigError(ValueError):
    """Raised when a workflow config fails validation (has ERROR issues)."""


@dataclass
class NormalizedWorkflow:
    config: dict                      # internal CONFIG the protocol consumes
    issues: list[Issue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    resolved_pipettes: dict = field(default_factory=dict)  # group name -> pipette name


# ── Factor resolution (local mirror; avoids importing the opentrons protocol) ──

def resolve_factors(dil: Mapping[str, Any]) -> list[float]:
    fc = dil["factors"]
    mode = fc.get("mode", "explicit")
    if mode == "explicit":
        return [float(x) for x in fc["explicit"]]
    count = int(fc.get("count", 8))
    start = float(fc.get("start", 1))
    if mode == "geometric":
        step = float(fc.get("step_factor", 2))
        return [round(start * (step ** i), 4) for i in range(count)]
    end = float(fc.get("end", 50))
    if mode == "linear":
        return [start] if count == 1 else [round(start + (end - start) * i / (count - 1), 4) for i in range(count)]
    if mode == "log":
        lo, hi = math.log(start), math.log(end)
        return [start] if count == 1 else [round(math.exp(lo + (hi - lo) * i / (count - 1)), 4) for i in range(count)]
    raise WorkflowConfigError(f"Unknown dilution factors mode: {mode!r}")


def _is_new_schema(raw: Mapping[str, Any]) -> bool:
    return any(k in raw for k in ("materials", "dilution_plan", "labware", "tip_policy", "imaging"))


# ── New-schema -> internal CONFIG ─────────────────────────────────────────────

def _normalize_new(raw: Mapping[str, Any], materials: dict) -> tuple[dict, list[str]]:
    notes: list[str] = []
    deck_in = raw.get("deck", {})
    lw_in = raw.get("labware", {})

    def _deckrole(role: str, default_slot=None):
        slot = deck_in.get(role, {}).get("slot", default_slot)
        body = dict(lw_in.get(role, {}))
        if slot is not None:
            body["slot"] = slot
        return body

    deck = {
        "tuberack": _deckrole("tuberack"),
        "plate": _deckrole("plate"),
        "paper": _deckrole("paper"),
        "tiprack": _deckrole("tiprack_p300") or _deckrole("tiprack"),
    }
    p20_rack = _deckrole("tiprack_p20")
    if p20_rack.get("load_name"):
        deck["tiprack_p20"] = p20_rack

    pipettes = list(raw.get("pipettes", []))
    primary = next((p for p in pipettes if "multi" in p["name"]), pipettes[0] if pipettes else None)
    if primary is None:
        raise WorkflowConfigError("config.pipettes must list at least one pipette.")

    # Dilution + mixing plans -> internal dilution + color_series + sources.
    dplan = raw.get("dilution_plan", {})
    mplan = raw.get("mixing_plan", {})
    water_mat_name = dplan.get("water_material", "water")
    water_mat = materials.get(water_mat_name)
    sources = {
        "water_vial": water_mat.vial if water_mat else dplan.get("water_vial", "A1"),
        "vial_aspirate_height_mm": (water_mat.aspirate_height_mm if water_mat and water_mat.aspirate_height_mm
                                    else dplan.get("vial_aspirate_height_mm", 4.0)),
    }
    color_series = []
    for s in dplan.get("series", []):
        dye_mat = materials.get(s.get("material"))
        color_series.append({
            "name": s["name"],
            "dye_vial": dye_mat.vial if dye_mat else s.get("dye_vial"),
            "destination_column": str(s["destination_column"]),
            "setup_tip": s.get("setup_tip"),
            # print_block_column / paper info come from the matching print group (below).
            "print_block_column": s.get("print_block_column", 1),
            "paper_start_column": s.get("paper_start_column", 1),
            "num_replicates": s.get("num_replicates", 1),
        })
        if dye_mat:
            sources[f"{s['name']}_dye_vial"] = dye_mat.vial

    # Give each dilution series the print metadata of its matching column_8up group,
    # so the protocol's series-based pre-flight validates real block columns / paper
    # positions. Distinct block columns keep the "tips must be unique" check happy.
    pgs = raw.get("print_groups", [])
    for i, s in enumerate(color_series):
        match = next((g for g in pgs
                      if g.get("layout") == "column_8up"
                      and str(g.get("source", {}).get("plate_column")) == s["destination_column"]), None)
        if match:
            s["print_block_column"] = match.get("tips", {}).get("block_column", i + 1)
            s["paper_start_column"] = match.get("destination", {}).get("paper_start_column", 1)
            s["num_replicates"] = match.get("replicates", 1)
        else:
            s["print_block_column"] = i + 1
            s["paper_start_column"] = 1
            s["num_replicates"] = 1

    dilution = {
        "enabled": dplan.get("enabled", True),
        "destination_column": str(dplan.get("destination_column", color_series[0]["destination_column"] if color_series else "9")),
        "total_volume_ul": float(dplan.get("total_volume_ul", 200.0)),
        "factors": dplan["factors"],
        "mix_reps": int(mplan.get("mix_reps", 0)),
        "mix_volume_ul": float(mplan.get("mix_volume_ul", 0.0)),
        "water_setup_tip": dplan.get("water_setup_tip", "H12"),
        "setup_tip": dplan.get("setup_tip", dplan.get("water_setup_tip", "H12")),
        "single_tip_columns": dplan.get("single_tip_columns", [12]),
    }

    imaging = raw.get("imaging", {})
    camera = {
        "enabled": imaging.get("enabled", True),
        "capture_before": imaging.get("capture_before", True),
        "capture_after": imaging.get("capture_after", True),
        "capture_mid_rows": imaging.get("capture_mid_rows", []),
        "robot_image_dir": imaging.get("robot_image_dir", "/data/vision/vial_dilution_print"),
        "robot_api_url": imaging.get("robot_api_url", "http://localhost:31950/camera/picture"),
        "capture_timeout_s": imaging.get("capture_timeout_s", 5),
    }

    config = {
        "deck": deck,
        "pipette": primary,
        "pipettes": pipettes,
        "sources": sources,
        "dilution": dilution,
        "color_series": color_series,
        "print_groups": [dict(g) for g in raw.get("print_groups", [])],
        "printing": {"enabled": bool(raw.get("print_groups"))},
        "tips": {"return_tips": raw.get("tip_policy", {}).get("return_tips", True)},
        "camera": camera,
        "materials": {n: m.model_dump(exclude_none=True) for n, m in materials.items()},
        "flow_rates": raw.get("flow_rates", {"aspirate": 20.0, "dispense": 80.0, "mix": None}),
        "safety": {**DEFAULT_SAFETY,
                   "expected_tuberack_load_name": deck["tuberack"].get(
                       "load_name", DEFAULT_SAFETY["expected_tuberack_load_name"]),
                   **raw.get("safety", {})},
    }
    notes.append("normalized new-schema sections -> internal CONFIG.")
    return config, notes


# ── Consumption accounting (vial materials) ───────────────────────────────────

def _material_consumption(config: Mapping[str, Any]) -> dict[str, float]:
    """Conservative uL drawn from each *vial material* (dilution phase only; printing
    aspirates from the plate, not the vials). Includes a 10% overage."""
    consumed: dict[str, float] = {}
    materials = config.get("materials", {})
    # Map vial -> material name for reverse lookup.
    vial_to_mat = {(m["labware"], m["vial"]): name for name, m in materials.items()}
    dil = config.get("dilution", {})
    if not dil.get("enabled"):
        return consumed
    tuberack_lw = config.get("deck", {}).get("tuberack", {}).get("load_name")
    factors = resolve_factors(dil)
    total = float(dil.get("total_volume_ul", 0.0))
    sources = config.get("sources", {})

    def _add(vial: Optional[str], ul: float):
        if not vial or not tuberack_lw:
            return
        name = vial_to_mat.get((tuberack_lw, vial))
        if name:
            consumed[name] = consumed.get(name, 0.0) + ul * DEFAULT_OVERAGE

    n_series = max(1, len(config.get("color_series", []) or [1]))
    # Water: (total - stock) per well, summed over factors, per series.
    water_per_series = sum(max(0.0, total - (total / f if f else 0.0)) for f in factors)
    _add(sources.get("water_vial"), water_per_series * n_series)
    # Dye stock per series: total/f summed over factors.
    for s in config.get("color_series", []):
        stock = sum((total / f if f else 0.0) for f in factors)
        _add(s.get("dye_vial"), stock)
    return consumed


# ── Public entry point ────────────────────────────────────────────────────────

def normalize_and_validate(raw: Mapping[str, Any], *, strict: bool = True) -> NormalizedWorkflow:
    """Normalize a workflow YAML to internal CONFIG and run every shared validator.

    Raises WorkflowConfigError (with section/field/value/correction detail) if any
    ERROR-level issue is found and strict=True; warnings are always returned.
    """
    issues: list[Issue] = []
    notes: list[str] = []

    materials = parse_materials(raw)
    if _is_new_schema(raw):
        config, nnotes = _normalize_new(raw, materials)
        notes += nnotes
    else:
        config = dict(raw)
        notes.append("legacy schema: passed through unchanged.")

    # Pipette resolution + print-group validation (authoritative: print_groups.py).
    resolved: dict[str, str] = {}
    try:
        parsed = parse_print_config(config)
        notes += parsed.migration_notes
        choices, group_issues = resolve_and_validate(parsed, dilution_config=config.get("dilution"))
        issues += group_issues
        # Bake the resolved concrete pipette name into each embedded group.
        by_name = {g.name: g for g in parsed.groups}
        for g in config.get("print_groups", []):
            if g.get("name") in choices:
                g["pipette"] = choices[g["name"]].name
                resolved[g["name"]] = choices[g["name"]].name
        # If groups were synthesised from legacy fields, embed them.
        if not config.get("print_groups") and parsed.groups:
            config["print_groups"] = [
                {**g.model_dump(by_alias=True), "pipette": choices[g.name].name}
                for g in parsed.groups
            ]
            resolved = {g.name: choices[g.name].name for g in parsed.groups}
    except PrintConfigError as e:
        issues.append(Issue("error", "print_groups", str(e)))

    # Materials validation with conservative consumption (authoritative: materials.py).
    if materials:
        referenced = _referenced_materials(raw)
        consumed = _material_consumption(config)
        issues += validate_materials(materials, consumed_ul=consumed, referenced_names=referenced,
                                     labware_index=build_labware_index())

    errors = [i for i in issues if i.severity == "error"]
    if errors and strict:
        raise WorkflowConfigError(
            "Workflow configuration is invalid:\n  " + "\n  ".join(str(i) for i in errors))
    return NormalizedWorkflow(config=config, issues=issues, notes=notes, resolved_pipettes=resolved)


def _referenced_materials(raw: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    dplan = raw.get("dilution_plan", {})
    if dplan.get("water_material"):
        refs.add(dplan["water_material"])
    for s in dplan.get("series", []):
        if s.get("material"):
            refs.add(s["material"])
    return refs
