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


def render_plate_svg(config: dict[str, Any], role: str = "all") -> str:
    """Generate SVG for the 96-well dilution plate. `role` picks the wells shown in use: "dilution" the wells the
    dilution step fills, "print" the wells the print step draws from, "all" both."""
    plan = build_plan(config)
    slot = format_slot(slot_of(config, "plate"))

    filled = {well.well for well in plan.wells} if plan.do_dilution or role == "all" else set()
    printed_from = {source.well for source in plan.print_sources}      # the wells the print step draws from
    if plan.plate_column and not plan.mapped and (plan.do_print or role == "all"):
        printed_from |= {f"{r}{plan.plate_column}" for r in plan.rows}
    used_wells = filled if role == "dilution" else printed_from if role == "print" else filled | printed_from
    in_use = {"dilution": "filled in this step", "print": "printed from"}.get(role, "Used")

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
            title_text = f"Well {well_name} ({in_use if is_used else 'Unused'})"
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
    # A unique condition is a source well at one drop volume. Its first paper
    # position is the original; every later position is a repeat, wherever the
    # canonical allocator put it.
    copy_index: dict[tuple[str, float], int] = {}
    repeats: set[tuple[str, int]] = set()
    for operation in plan.operations:
        if operation.kind != "print":
            continue
        key = (operation.source, operation.volume_ul)
        copy_index[key] = copy_index.get(key, 0) + 1
        if copy_index[key] > 1:
            repeats.add((operation.destination[0], int(operation.destination[1:])))
    unique_drops = len(copy_index)
    total_spots = len(plan.print_positions)
    total_replicates = int((config.get("print") or {}).get("replicates", 1))
    applied = set()
    if baseline is not None:
        applied_plan = build_plan(baseline)
        applied = {(position[0], int(position[1:])) for position in applied_plan.print_positions} \
            if applied_plan.do_print else set()
    added = printed_positions - applied if baseline is not None else set()

    width = 256
    height = 200

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
            if pos in printed_positions:
                repeat = pos in repeats
                fill = "#3b82f6" if repeat else "#22c55e"
                stroke = "#d97706" if pos in added else "#1d4ed8" if repeat else "#15803d"
                sw = "2.5" if pos in added else "1"
                label = "repeat" if repeat else "original"
                pending = ", new in this proposal" if pos in added else ""
                svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="5" fill="{fill}" stroke="{stroke}" '
                                 f'stroke-width="{sw}"><title>Position {r}{c} ({summary_label}, {label}{pending})'
                                 '</title></circle>')

    if added:
        summary_label += f" · {len(added)} new"
    svg_parts.append(f'<text x="135" y="181" font-size="9" font-weight="bold" fill="#1e293b" text-anchor="middle">'
                     f'{unique_drops} unique · {total_replicates} replicate{"" if total_replicates == 1 else "s"} · '
                     f'{total_spots} spot{"" if total_spots == 1 else "s"}</text>')
    svg_parts.append(f'<text x="135" y="192" font-size="9" fill="#475569" text-anchor="middle">{summary_label}</text>')
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

    width = 256
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

    svg_parts.append('</svg>')
    return "".join(svg_parts)


def paper_summary(config: dict[str, Any]) -> tuple[int, int, int]:
    """Unique source/volume conditions, total copies requested, actual paper spots."""
    plan = build_plan(config)
    prints = [operation for operation in plan.operations if operation.kind == "print"]
    return (len({(operation.source, operation.volume_ul) for operation in prints}),
            int((config.get("print") or {}).get("replicates", 1)), len(prints))


def render_deck_svg(config: dict[str, Any]) -> str:
    """A standalone, readable OT-2 deck map; position comes only from config."""
    roles_in = occupancy(config)
    parts = ['<svg viewBox="0 0 390 310" xmlns="http://www.w3.org/2000/svg" '
             'style="width:100%; max-width:650px; height:auto; background:white; font-family:system-ui,sans-serif;">',
             '<text x="195" y="19" font-size="14" font-weight="bold" fill="#1e293b" '
             'text-anchor="middle">OT-2 Deck Layout</text>']
    for row_index, slots in enumerate(DECK_LAYOUT):
        for column_index, deck_slot in enumerate(slots):
            x, y = 22 + column_index * 124, 33 + row_index * 67
            roles = roles_in.get(deck_slot, [])
            label = "Trash" if deck_slot == 12 else " + ".join(_DECK_LABELS[role] for role in roles) or "Empty"
            fill = "#f1f5f9" if deck_slot == 12 else "#ecfdf5" if roles else "#ffffff"
            stroke = "#15803d" if roles and "tiprack" not in roles else "#111827" if roles else "#94a3b8"
            parts.append(f'<rect x="{x}" y="{y}" width="110" height="55" rx="6" fill="{fill}" '
                         f'stroke="{stroke}" stroke-width="2"><title>Slot {deck_slot}: {label}</title></rect>')
            parts.append(f'<text x="{x + 9}" y="{y + 17}" font-size="11" font-weight="bold" fill="#475569">'
                         f'{deck_slot}</text>')
            parts.append(f'<text x="{x + 55}" y="{y + 38}" font-size="12" font-weight="bold" '
                         f'fill="#1e293b" text-anchor="middle">{label}</text>')
    parts.append('</svg>')
    return "".join(parts)
