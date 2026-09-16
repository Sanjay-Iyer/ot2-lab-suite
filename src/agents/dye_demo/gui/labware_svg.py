"""SVG labware renderers for the conversational dye demo GUI.

Generates self-contained SVG strings directly from authoritative experiment state
(dict config and Plan). Pure display logic only; does not mutate state.
"""
from __future__ import annotations

from typing import Any
from src.agents.dye_demo.model import format_slot, material_label, material_spec, slot_of
from src.agents.dye_demo.plan import build_plan

WELL_ROWS = tuple("ABCDEFGH")
WELL_COLS = tuple(range(1, 13))
VIAL_ROWS = ("A", "B")
VIAL_COLS = (1, 2, 3, 4)


def render_plate_svg(config: dict[str, Any]) -> str:
    """Generate SVG for the 96-well dilution plate."""
    plan = build_plan(config)
    slot = format_slot(slot_of(config, "plate"))

    used_wells = set()
    if plan.plate_column:
        for r in plan.rows:
            used_wells.add(f"{r}{plan.plate_column}")
    for well in plan.wells:
        used_wells.add(well.well)

    width = 256
    height = 180

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" max-width="{width}px" height="auto" xmlns="http://www.w3.org/2000/svg" style="background: white; border-radius: 6px; font-family: system-ui, sans-serif;">',
        '<!-- Title & Header -->',
        f'<text x="128" y="14" font-size="11" font-weight="bold" fill="#334155" text-anchor="middle">96-Well Plate — {slot}</text>',
        '<!-- Background Plate -->',
        '<rect x="22" y="24" width="226" height="150" rx="6" ry="6" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1.5" />',
    ]

    for c_idx, c in enumerate(WELL_COLS):
        cx = 32 + c_idx * 18
        svg_parts.append(f'<text x="{cx}" y="36" font-size="9" font-weight="bold" fill="#64748b" text-anchor="middle">{c}</text>')

    for r_idx, r in enumerate(WELL_ROWS):
        cy = 48 + r_idx * 16
        svg_parts.append(f'<text x="14" y="{cy + 3}" font-size="9" font-weight="bold" fill="#64748b" text-anchor="middle">{r}</text>')
        for c_idx, c in enumerate(WELL_COLS):
            cx = 32 + c_idx * 18
            well_name = f"{r}{c}"
            is_used = well_name in used_wells
            fill = "#22c55e" if is_used else "#e2e8f0"
            stroke = "#15803d" if is_used else "#94a3b8"
            sw = "1.5" if is_used else "1"
            title_text = f"Well {well_name} ({'Used' if is_used else 'Unused'})"
            svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="6.5" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"><title>{title_text}</title></circle>')

    svg_parts.append('</svg>')
    return "".join(svg_parts)


def render_tuberack_svg(config: dict[str, Any]) -> str:
    """Generate SVG for the 8-vial rack. Returns empty string if not relevant."""
    plan = build_plan(config)
    slot = format_slot(slot_of(config, "tuberack"))

    sample_spec = material_spec(config, "sample")
    solvent_spec = material_spec(config, "solvent")
    vial_sample = str(sample_spec.get("vial", "")).upper()
    vial_solvent = str(solvent_spec.get("vial", "")).upper()
    sample_name = material_label(config, "sample")
    solvent_name = material_label(config, "solvent")

    used_vials = {}
    if plan.do_dilution:
        if vial_sample:
            used_vials[vial_sample] = sample_name
        if vial_solvent:
            used_vials[vial_solvent] = solvent_name

    width = 220
    height = 140

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" max-width="{width}px" height="auto" xmlns="http://www.w3.org/2000/svg" style="background: white; border-radius: 6px; font-family: system-ui, sans-serif;">',
        '<!-- Title & Header -->',
        f'<text x="110" y="14" font-size="11" font-weight="bold" fill="#334155" text-anchor="middle">8-Vial Rack — {slot}</text>',
        '<!-- Background Rack -->',
        '<rect x="22" y="24" width="186" height="106" rx="6" ry="6" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1.5" />',
    ]

    for c_idx, c in enumerate(VIAL_COLS):
        cx = 45 + c_idx * 42
        svg_parts.append(f'<text x="{cx}" y="38" font-size="10" font-weight="bold" fill="#64748b" text-anchor="middle">{c}</text>')

    for r_idx, r in enumerate(VIAL_ROWS):
        cy = 58 + r_idx * 42
        svg_parts.append(f'<text x="14" y="{cy + 4}" font-size="10" font-weight="bold" fill="#64748b" text-anchor="middle">{r}</text>')
        for c_idx, c in enumerate(VIAL_COLS):
            cx = 45 + c_idx * 42
            vial_name = f"{r}{c}"
            is_used = vial_name in used_vials
            fill = "#22c55e" if is_used else "#e2e8f0"
            stroke = "#15803d" if is_used else "#94a3b8"
            sw = "1.5" if is_used else "1"
            label = used_vials.get(vial_name, "")
            title_text = f"Vial {vial_name}: {label}" if is_used else f"Vial {vial_name} (Unused)"
            svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="15" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"><title>{title_text}</title></circle>')
            text_fill = "#ffffff" if is_used else "#475569"
            svg_parts.append(f'<text x="{cx}" y="{cy + 4}" font-size="10" font-weight="bold" fill="{text_fill}" text-anchor="middle">{vial_name}</text>')

    svg_parts.append('</svg>')
    return "".join(svg_parts)


def render_paper_svg(config: dict[str, Any]) -> str:
    """Generate SVG for the paper substrate."""
    plan = build_plan(config)
    slot = format_slot(slot_of(config, "paper"))

    printed_positions = set()
    if plan.do_print and plan.spots:
        for spot in plan.spots:
            col = spot["column"]
            for row in plan.rows:
                printed_positions.add((row, col))

    width = 256
    height = 196

    drops = int((config.get("print") or {}).get("droplets_per_spot", 1))
    drop_unit = "drop" if drops == 1 else "drops"
    summary_label = f"{drops} {drop_unit} / position" if (plan.do_print and printed_positions) else "No printing planned"

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" max-width="{width}px" height="auto" xmlns="http://www.w3.org/2000/svg" style="background: white; border-radius: 6px; font-family: system-ui, sans-serif;">',
        '<!-- Title & Header -->',
        f'<text x="128" y="14" font-size="11" font-weight="bold" fill="#334155" text-anchor="middle">Paper Substrate — {slot}</text>',
        '<!-- Background Paper Surface (White with clear outline) -->',
        '<rect x="22" y="24" width="226" height="146" rx="4" ry="4" fill="#ffffff" stroke="#475569" stroke-width="1.5" />',
    ]

    for c_idx, c in enumerate(WELL_COLS):
        cx = 32 + c_idx * 18
        svg_parts.append(f'<text x="{cx}" y="36" font-size="9" font-weight="bold" fill="#64748b" text-anchor="middle">{c}</text>')

    for r_idx, r in enumerate(WELL_ROWS):
        cy = 48 + r_idx * 15.5
        svg_parts.append(f'<text x="14" y="{cy + 3}" font-size="9" font-weight="bold" fill="#64748b" text-anchor="middle">{r}</text>')
        for c_idx, c in enumerate(WELL_COLS):
            cx = 32 + c_idx * 18
            pos = (r, c)
            if pos in printed_positions:
                svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="4.5" fill="#22c55e" stroke="#15803d" stroke-width="1"><title>Position {r}{c} ({summary_label})</title></circle>')

    svg_parts.append(f'<text x="135" y="186" font-size="10" font-weight="bold" fill="#1e293b" text-anchor="middle">{summary_label}</text>')
    svg_parts.append('</svg>')
    return "".join(svg_parts)
