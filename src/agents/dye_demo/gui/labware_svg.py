"""SVG labware renderers for the conversational dye demo GUI.

Generates self-contained SVG strings directly from authoritative experiment state
(dict config and Plan). Pure display logic only; does not mutate state.
"""
from __future__ import annotations

from typing import Any
from src.agents.dye_demo.model import TIP_ORDER, format_slot, material_label, material_spec, occupancy, slot_of
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.render import format_value

WELL_ROWS = tuple("ABCDEFGH")
WELL_COLS = tuple(range(1, 13))
VIAL_ROWS = ("A", "B")
VIAL_COLS = (1, 2, 3, 4)
# The OT-2 deck as the scientist faces it: slot 12 (back right) is the fixed trash.
DECK_LAYOUT = ((10, 11, 12), (7, 8, 9), (4, 5, 6), (1, 2, 3))
_DECK_LABELS = {"plate": "Plate", "paper": "Paper", "tuberack": "Vials", "tiprack": "Tips"}


def render_plate_svg(config: dict[str, Any]) -> str:
    """Generate SVG for the 96-well dilution plate."""
    plan = build_plan(config)
    slot = format_slot(slot_of(config, "plate"))

    used_wells = {well.well for well in plan.wells}
    used_wells |= {source.well for source in plan.print_sources}      # the wells the print step draws from
    if plan.plate_column and not plan.mapped:
        for r in plan.rows:
            used_wells.add(f"{r}{plan.plate_column}")

    width = 256
    height = 180

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" max-width="{width}px" height="auto" xmlns="http://www.w3.org/2000/svg" style="width: {width}px; max-width: 100%; height: auto; background: white; border-radius: 6px; font-family: system-ui, sans-serif;">',
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
        f'<svg viewBox="0 0 {width} {height}" width="100%" max-width="{width}px" height="auto" xmlns="http://www.w3.org/2000/svg" style="width: {width}px; max-width: 100%; height: auto; background: white; border-radius: 6px; font-family: system-ui, sans-serif;">',
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


def render_paper_svg(config: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    """Generate SVG for the paper substrate. `baseline` is the applied plan when `config` is a proposal: positions the
    proposal adds get an amber ring."""
    plan = build_plan(config)
    slot = format_slot(slot_of(config, "paper"))

    # exactly the positions the print operations reach (a print map, or each dilution row across its columns)
    printed_positions = {(position[0], int(position[1:])) for position in plan.print_positions} if plan.do_print \
        else set()
    applied = set()
    if baseline is not None:
        applied_plan = build_plan(baseline)
        applied = {(position[0], int(position[1:])) for position in applied_plan.print_positions} \
            if applied_plan.do_print else set()
    added = printed_positions - applied if baseline is not None else set()

    width = 256
    height = 196

    drops = int((config.get("print") or {}).get("droplets_per_spot", 1))
    drop_unit = "drop" if drops == 1 else "drops"
    summary_label = f"{drops} {drop_unit} / position" if (plan.do_print and printed_positions) else "No printing planned"

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" max-width="{width}px" height="auto" xmlns="http://www.w3.org/2000/svg" style="width: {width}px; max-width: 100%; height: auto; background: white; border-radius: 6px; font-family: system-ui, sans-serif;">',
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
            if pos in added:
                svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="5" fill="#22c55e" stroke="#d97706" stroke-width="2"><title>Position {r}{c} ({summary_label}, new in this proposal)</title></circle>')
            elif pos in printed_positions:
                svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="4.5" fill="#22c55e" stroke="#15803d" stroke-width="1"><title>Position {r}{c} ({summary_label})</title></circle>')

    if added:
        summary_label += f" · {len(added)} new"
    svg_parts.append(f'<text x="135" y="186" font-size="10" font-weight="bold" fill="#1e293b" text-anchor="middle">{summary_label}</text>')
    svg_parts.append('</svg>')
    return "".join(svg_parts)


def tip_settings(config: dict[str, Any]) -> list[tuple[str, str]]:
    """The tip settings shown above the tip rack, in the scientist's words (not the YAML values)."""
    tips = config.get("tips") or {}
    policy = format_value("tips.policy", tips.get("policy", "per_liquid"))
    return [
        ("Start tip", str(tips.get("start_tip", "A1")).upper()),
        ("Return tips", "Yes" if tips.get("return_tips") else "No"),
        ("Tip policy", policy[:1].upper() + policy[1:]),
    ]


def render_tiprack_svg(config: dict[str, Any]) -> str:
    """Generate SVG for the P20 tip rack: the tips this run picks up, and the deck slot the rack sits in."""
    plan = build_plan(config)
    rack_slot = slot_of(config, "tiprack")
    slot = format_slot(rack_slot)

    picked = [assignment.tip for assignment in plan.tips]            # in pick-up order, from the start tip
    order = {tip: index for index, tip in enumerate(picked, start=1)}
    first = TIP_ORDER.index(plan.start_tip) if plan.start_tip in TIP_ORDER else 0
    before_start = set(TIP_ORDER[:first])                             # skipped: the run starts after them

    width = 384
    height = 196

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" max-width="{width}px" height="auto" xmlns="http://www.w3.org/2000/svg" style="width: {width}px; max-width: 100%; height: auto; background: white; border-radius: 6px; font-family: system-ui, sans-serif;">',
        '<!-- Title & Header -->',
        f'<text x="135" y="14" font-size="11" font-weight="bold" fill="#334155" text-anchor="middle">P20 Tip Rack — {slot}</text>',
        '<!-- Background Rack -->',
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
            tip = f"{r}{c}"
            if tip in order:
                start = order[tip] == 1
                stroke, sw = ("#14532d", "2.5") if start else ("#15803d", "1.5")
                title_text = f"Tip {tip}: {'start tip, ' if start else ''}pick-up {order[tip]} of {len(picked)} in this run"
                svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="6.5" fill="#22c55e" stroke="{stroke}" stroke-width="{sw}"><title>{title_text}</title></circle>')
            elif tip in before_start:
                svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="6.5" fill="#ffffff" stroke="#cbd5e1" stroke-width="1" stroke-dasharray="2 1.5"><title>Tip {tip}: before the start tip (not used)</title></circle>')
            else:
                svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="6.5" fill="#e2e8f0" stroke="#94a3b8" stroke-width="1"><title>Tip {tip}: left in the rack</title></circle>')

    if not isinstance(rack_slot, int):
        summary, colour = "The tip rack is OFF DECK", "#b45309"
    elif plan.tips_short:
        summary, colour = (f"Needs {plan.tips_needed} tips, only {plan.tips_available} left from {plan.start_tip}",
                           "#b91c1c")
    elif not picked:
        summary, colour = "No tips picked up", "#475569"
    else:
        tips_range = picked[0] if len(picked) == 1 else f"{picked[0]}-{picked[-1]}"
        summary, colour = f"{len(picked)} tip{'' if len(picked) == 1 else 's'} this run: {tips_range}", "#1e293b"
    svg_parts.append(f'<text x="135" y="188" font-size="10" font-weight="bold" fill="{colour}" text-anchor="middle">{summary}</text>')

    # Where the rack is: the deck map, the tip rack's slot in green
    svg_parts.append('<!-- Deck map -->')
    svg_parts.append('<text x="319" y="36" font-size="9" font-weight="bold" fill="#64748b" text-anchor="middle">Deck</text>')
    roles_in = occupancy(config)
    for row_idx, slots in enumerate(DECK_LAYOUT):
        y = 44 + row_idx * 32
        for col_idx, deck_slot in enumerate(slots):
            x = 264 + col_idx * 38
            roles = roles_in.get(deck_slot, [])
            if "tiprack" in roles:
                fill, stroke, sw, text_fill = "#22c55e", "#15803d", "2", "#ffffff"
                title_text = f"Slot {deck_slot}: P20 tip rack"
            elif deck_slot == 12:
                fill, stroke, sw, text_fill = "#f1f5f9", "#cbd5e1", "1", "#94a3b8"
                title_text = "Slot 12: fixed trash"
            elif roles:
                fill, stroke, sw, text_fill = "#e2e8f0", "#94a3b8", "1", "#475569"
                title_text = f"Slot {deck_slot}: " + ", ".join(_DECK_LABELS[role].lower() for role in roles)
            else:
                fill, stroke, sw, text_fill = "#ffffff", "#cbd5e1", "1", "#94a3b8"
                title_text = f"Slot {deck_slot}: empty"
            label = "Trash" if deck_slot == 12 else "+".join(_DECK_LABELS[role] for role in roles)
            svg_parts.append(f'<rect x="{x}" y="{y}" width="34" height="28" rx="3" ry="3" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"><title>{title_text}</title></rect>')
            svg_parts.append(f'<text x="{x + 3}" y="{y + 9}" font-size="7.5" fill="{text_fill}">{deck_slot}</text>')
            if label:
                svg_parts.append(f'<text x="{x + 17}" y="{y + 21}" font-size="8" font-weight="bold" fill="{text_fill}" text-anchor="middle">{label}</text>')

    svg_parts.append('</svg>')
    return "".join(svg_parts)
