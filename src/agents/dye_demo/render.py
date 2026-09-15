"""Plain-text screens for the dye demo terminal.

One plan layout answers "what exactly will the experiment do?": the CURRENT PLAN (what a run does now) and every
proposal (the plan that exists if the scientist types yes) are rendered by the same section builders - DILUTIONS,
PRINTING, DECK, LIQUIDS, PIPETTING and LAB-OWNED PARAMETERS - with resulting values only, never old -> new.
Anything that blocks execution or must be checked before yes (unverified changes, validation warnings, conflicts,
physical moves still to make) goes in a separate ATTENTION block, never inside the plan.

Detail views stay available as commands: `steps` (every liquid movement FROM and TO with its tip), `tips`,
`settings` and `history`.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from src.agents.dye_demo.columns import format_columns, paper_columns_printed
from src.agents.dye_demo.model import (
    DECK_SLOTS,
    LABWARE_NAMES,
    LABWARE_ROLES,
    TIP_POLICIES,
    circle_area_mm2,
    field_label,
    fmt_factor,
    fmt_num,
    fmt_ul,
    format_slot,
    get_path,
    is_off_deck,
    material_label,
    material_spec,
    occupancy,
    off_deck_roles,
    slot_of,
    well_geometry,
)
from src.agents.dye_demo.plan import Operation, Plan, build_plan, paper_layout
from src.agents.dye_demo.validation import DeckConflict, Report, free_slots, off_deck_blocker

WIDTH = 72
RULE = "=" * WIDTH
THIN = "-" * WIDTH
LABEL = 20                                  # plan rows: two spaces, a 20-character label, two spaces, the value
VALUE_COLUMN = 2 + LABEL + 2
DETAILED_PRINT_STEPS = 12
OFF_DECK_MEANING = '"OFF DECK" means the labware has been physically removed from the OT-2 deck.'
APPLY_PROMPT = "Apply proposal #"           # every proposal screen ends with this prompt
BLOCKED = "Execution is blocked until this is resolved."
COLUMN_MISMATCH = "Requested paper columns do not match the executable plan."
_DECK_LABELS = {"plate": "Dilution plate", "paper": "Paper print plate", "tuberack": "Vial rack",
                "tiprack": "P20 tip rack"}


def _where(config: dict[str, Any], role: str) -> str:
    return f"{LABWARE_NAMES[role]}, {format_slot(slot_of(config, role))}"


def _range(names: Sequence[str]) -> str:
    if not names:
        return "none"
    return names[0] if len(names) == 1 else f"{names[0]}-{names[-1]}"


# ── layout primitives ───────────────────────────────────────────────────────────

def _banner(title: str, subtitle: str = "") -> list[str]:
    lines = [RULE, title.center(WIDTH).rstrip()]
    if subtitle:
        lines.append(subtitle.center(WIDTH).rstrip())
    return lines + [RULE]


def _heading(title: str, status: str = "") -> str:
    return f"{title}{status:>{WIDTH - len(title)}}" if status else title


def _wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False) or [""]


def _wrap_value(value: str, width: int) -> list[str]:
    """A value that fits the value column: `a | b | c` lists break after a `|`, and a trailing `   (note)` moves to
    its own line whole rather than being split."""
    main, separator, note = value.partition("   (")
    items = main.split(" | ")
    lines: list[str] = []
    current = ""
    for index, item in enumerate(items):
        candidate = f"{current} | {item}" if current else item
        room = width if index == len(items) - 1 else width - 2
        if current and len(candidate) > room:
            lines.append(current + " |")
            current = item
        else:
            current = candidate
    lines += _wrap(current, width)
    if separator:
        note = "(" + note
        if len(lines[-1]) + 3 + len(note) <= width:
            lines[-1] += "   " + note
        else:
            lines += _wrap(note, width)
    return lines


def _row(label: str, value: Any) -> list[str]:
    """`  Label                 value`, long values wrapped under the value column."""
    parts = _wrap_value(str(value), WIDTH - VALUE_COLUMN)
    if len(label) > LABEL:
        return [f"  {label}"] + [" " * VALUE_COLUMN + part for part in parts]
    return [f"  {label:<{LABEL}}  {parts[0]}".rstrip()] + [" " * VALUE_COLUMN + part for part in parts[1:]]


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def attention(*lines: str) -> str:
    """A short block for what blocks execution or must be checked before yes. Lines keep their exact wording."""
    title = " ATTENTION "
    left = (WIDTH - len(title)) // 2
    body = []
    for line in lines:
        for part in str(line).split("\n"):
            body.append(f"  {part}".rstrip() if part.strip() else "")
    return "\n".join(["!" * left + title + "!" * (WIDTH - left - len(title)), *body, "!" * WIDTH])


def pipes(values: Iterable[Any]) -> str:
    return " | ".join(str(value) for value in values)


# ── plan sections ───────────────────────────────────────────────────────────────
# The sections are data (PlanSection) so the terminal and the GUI show the same values; the terminal formats them
# as text rows.

@dataclass
class Grid:
    """The dilution wells: one row per property (Well, Factor, Dye µL, Water µL), one cell per well."""
    rows: list[tuple[str, list[str]]]


@dataclass
class PlanSection:
    title: str
    status: str = ""
    items: list[Any] = field(default_factory=list)          # (label, value) rows, or a Grid

    def add(self, label: str, value: Any) -> None:
        self.items.append((label, str(value)))

    @property
    def rows(self) -> list[tuple[str, str]]:
        return [item for item in self.items if not isinstance(item, Grid)]

    def value(self, label: str) -> str | None:
        return next((value for name, value in self.rows if name == label), None)

    def lines(self) -> list[str]:
        lines = [_heading(self.title, self.status)]
        for item in self.items:
            if isinstance(item, Grid):
                lines += ["  " + f"{name:<12}" + "".join(f"{cell:>7}" for cell in cells) for name, cells in item.rows]
            else:
                lines += _row(*item)
        return lines


LAB_OWNED_TITLE = "LAB-OWNED PARAMETERS"


def _well_grid(plan: Plan, *, volumes: bool) -> Grid:
    rows = [("Well", [well.well for well in plan.wells]), ("Factor", [fmt_factor(well.factor) for well in plan.wells])]
    if volumes:
        rows += [("Dye µL", [fmt_num(well.sample_ul) for well in plan.wells]),
                 ("Water µL", [fmt_num(well.solvent_ul) for well in plan.wells])]
    return Grid(rows)


def _dilutions(config: dict[str, Any], plan: Plan, prepared: dict[str, Any] | None, record: bool) -> PlanSection:
    section = PlanSection("DILUTIONS", "made in this run" if plan.do_dilution else "SKIPPED - already in the plate")
    if not plan.wells:
        section.add("Dilutions", "none")
        return section
    first, last = plan.wells[0], plan.wells[-1]
    rows = f"rows {first.row}-{last.row}" if len(plan.wells) > 1 else f"row {first.row}"
    section.add("Dilutions", f"{len(plan.wells)} in plate column {plan.plate_column} ({rows})")
    section.items.append(_well_grid(plan, volumes=plan.do_dilution))
    if plan.do_dilution:
        section.add("Final volume", f"{fmt_ul(plan.total_volume_ul)} in each well")
        counts = {role: sum(op.kind == "transfer" and op.role == role for op in plan.operations)
                  for role in ("solvent", "sample")}
        section.add("Transfers", f"{counts['solvent']} {material_label(config, 'solvent')} | "
                                 f"{counts['sample']} {material_label(config, 'sample')}")
    else:
        section.add("Volume in each well", fmt_ul(plan.source_volume_ul))
        if record:
            section.add("Recorded", prepared.get("source", "reported") if prepared else "no record in this session")
    return section


def _drop_volumes(config: dict[str, Any]) -> str:
    by_volume: dict[float, list[int]] = {}
    for spot in paper_layout(config, include_overflow=True):
        by_volume.setdefault(float(spot["volume_ul"]), []).append(int(spot["column"]))
    if len(by_volume) <= 1:
        return fmt_ul(next(iter(by_volume))) if by_volume else "none"
    return pipes(f"{fmt_ul(volume)} (column{'s' if len(columns) > 1 else ''} {format_columns(columns)})"
                 for volume, columns in by_volume.items())


def _printing(config: dict[str, Any], plan: Plan) -> PlanSection:
    if not plan.do_print:
        return PlanSection("PRINTING", "SKIPPED - this run does not print")
    printing = config["print"]
    columns = paper_columns_printed(config)
    droplets = int(printing.get("droplets_per_spot", 1))
    replicates = int(printing.get("replicates", 1))
    positions = sum(op.kind == "print" for op in plan.operations)
    left = max(plan.source_volume_ul - plan.print_draw_per_well_ul, 0.0)
    section = PlanSection("PRINTING", "in this run")
    section.add("Paper columns", pipes(columns) if columns else "none")
    section.add("Paper rows", f"{pipes(well.row for well in plan.wells)}   (one row per dilution)")
    section.add("Drop volume", _drop_volumes(config))
    section.add("Drops per position", f"{droplets}" + ("  (stacked)" if droplets > 1 else ""))
    section.add("Replicates", f"{_plural(replicates, 'side-by-side column')} per drop volume")
    section.add("Print positions", f"{positions}   ({_plural(len(plan.wells), 'row')} × "
                                   f"{_plural(len(columns), 'column')})")
    section.add("Total drops", plan.total_drops)
    section.add("Printed volume", f"{fmt_ul(plan.printed_fluid_ul)}   ({fmt_ul(plan.print_draw_per_well_ul)} per "
                                  f"well, {fmt_ul(left)} left in each)")
    return section


def _deck_items(config: dict[str, Any]) -> list[tuple[str, str]]:
    taken = occupancy(config)
    items = []
    for slot, roles in taken.items():
        for role in roles:
            items.append((_DECK_LABELS[role], format_slot(slot) + ("   <-- COLLISION" if len(roles) > 1 else "")))
    for role in LABWARE_ROLES:
        slot = slot_of(config, role)
        if is_off_deck(slot):
            items.append((_DECK_LABELS[role], "OFF DECK (removed from the robot)"))
        elif not isinstance(slot, int) or isinstance(slot, bool):
            items.append((_DECK_LABELS[role], f"{slot!r} (not a deck slot)"))
    empty = [slot for slot in DECK_SLOTS if slot not in taken]
    items.append(("Empty slots", pipes(empty) if empty else "none"))
    return items


def _deck_rows(config: dict[str, Any]) -> list[str]:
    return [line for label, value in _deck_items(config) for line in _row(label, value)]


def _liquids(config: dict[str, Any], plan: Plan) -> PlanSection:
    geometry = well_geometry(str(config["deck"]["tuberack"].get("load_name", "")))
    section = PlanSection("LIQUIDS")
    for role, noun in (("sample", "dye"), ("solvent", "water")):
        spec = material_spec(config, role)
        label = material_label(config, role)
        name = label[:1].upper() + label[1:] + ("" if label.strip().lower() == noun else f" ({noun})")
        value = f"vial {spec.get('vial')}"
        if plan.do_dilution:
            draw = plan.vial_use_ul.get(role, 0.0)
            value += f" | uses {fmt_ul(draw)}"
            if geometry:
                cover = circle_area_mm2(geometry[0]) * float(spec.get("aspirate_height_mm", 4.0))
                value += f" | load at least {(draw + cover) / 1000:.2f} mL"
        else:
            value += " | not used in this run"
        section.add(name, value)
    return section


def _pipetting(config: dict[str, Any], plan: Plan) -> PlanSection:
    mixing, tips = config["mixing"], config["tips"]
    section = PlanSection("PIPETTING")
    if plan.do_print:
        section.add("Mixing", f"{int(mixing['reps'])} × {fmt_ul(mixing['volume_ul'])} before each print step")
    else:
        section.add("Mixing", "none (a well is mixed only before it is printed)")
    section.add("Tip start", plan.start_tip or tips.get("start_tip"))
    if plan.tips_short:
        section.add("Tips required", f"{plan.tips_needed}   (only {plan.tips_available} left from {plan.start_tip})")
    else:
        section.add("Tips required", f"{plan.tips_needed}" + (f"   ({_range([tip.tip for tip in plan.tips])})"
                                                             if plan.tips else ""))
    section.add("Tips left after", f"{max(0, plan.tips_available - plan.tips_needed)}"
                + (f"   (next unused {plan.next_tip})" if plan.next_tip else "   (the rack would be empty)"))
    section.add("Tip use", format_value("tips.policy", plan.policy))
    section.add("Used tips", "returned to the rack (do not reuse them)" if tips.get("return_tips")
                else "dropped in the trash")
    return section


def _lab_owned(config: dict[str, Any]) -> PlanSection:
    printing, dilution, mixing, pipette = config["print"], config["dilution"], config["mixing"], config["pipette"]
    water = material_spec(config, "solvent").get("aspirate_height_mm", 4.0)
    dye = material_spec(config, "sample").get("aspirate_height_mm", 4.0)
    on = {True: "on", False: "off"}
    rates = config.get("flow_rates") or {}
    section = PlanSection(LAB_OWNED_TITLE, "never changed in conversation")
    section.add("Pipette", f"{pipette.get('name')} ({pipette.get('mount')} mount)")
    if rates:
        section.add("Flow rates", pipes(f"{name} {fmt_num(rates[name])} µL/s" for name in ("aspirate", "dispense")
                                        if name in rates))
    section.add("Print height", f"{fmt_num(printing.get('z_mm', 0))} mm above the paper")
    section.add("Drop release", f"{fmt_ul(printing.get('push_out_ul', 0))} push-out | "
                                f"blow-out {on[bool(printing.get('blow_out'))]} | "
                                f"{fmt_num(printing.get('post_dispense_delay_s', 0))} s dwell")
    section.add("Air gap", f"{fmt_ul(printing.get('air_gap_ul', 0))}, taken "
                           f"{fmt_num(printing.get('air_gap_height_mm', 0))} mm above the well top")
    section.add("Print aspirate", f"{fmt_num(printing.get('aspirate_height_mm', 0))} mm above the well bottom")
    section.add("Mixing height", f"{fmt_num(mixing.get('height_mm', 2.0))} mm above the well bottom")
    section.add("Dilution dispense",
                f"water {fmt_num(-float(dilution.get('solvent_dispense_from_top_mm', -2.0)))} mm | "
                f"dye {fmt_num(-float(dilution.get('sample_dispense_from_top_mm', -1.0)))} mm below the well top")
    section.add("Dilution transfers", f"at most {fmt_ul(dilution.get('max_transfer_ul', 20))} each | blow-out "
                                      f"{on[bool(dilution.get('blow_out_after_dispense', True))]}")
    section.add("Vial aspirate", f"{fmt_num(water)} mm above the vial bottom" if float(water) == float(dye)
                else f"water {fmt_num(water)} mm | dye {fmt_num(dye)} mm above the vial bottom")
    return section


def plan_model(config: dict[str, Any], *, prepared: dict[str, Any] | None = None,
               record: bool = True) -> list[PlanSection]:
    """The plan a run of this configuration carries out, grouped by what the robot does: DILUTIONS, PRINTING, DECK,
    LIQUIDS, PIPETTING, LAB-OWNED PARAMETERS. `prepared` is the session's record of dilutions already in the plate;
    `record=False` leaves that row out where no session record exists."""
    plan = build_plan(config)
    return [_dilutions(config, plan, prepared, record), _printing(config, plan),
            PlanSection("DECK", items=list(_deck_items(config))), _liquids(config, plan), _pipetting(config, plan),
            _lab_owned(config)]


def plan_sections(config: dict[str, Any], *, prepared: dict[str, Any] | None = None, record: bool = True) -> list[str]:
    """The plan as terminal lines: sections separated by a blank line, the lab-owned parameters between rules."""
    lines: list[str] = []
    for section in plan_model(config, prepared=prepared, record=record):
        if section.title == LAB_OWNED_TITLE:
            lines += ["", THIN] + section.lines() + [THIN]
        else:
            lines += ([""] if lines else []) + section.lines()
    return lines


def render_plan(config: dict[str, Any], *, title: str, subtitle: str = "", prepared: dict[str, Any] | None = None,
                record: bool = True) -> str:
    return "\n".join(_banner(title, subtitle) + [""] + plan_sections(config, prepared=prepared, record=record))


def _report_items(report: Report) -> list[str]:
    items = []
    if report.errors:
        items.append("This plan cannot run. Execution is blocked until these are fixed:")
        items += [f"  - {issue.message}" for issue in report.errors]
    return items + [f"Warning: {issue.message}" for issue in report.warnings]


def render_report(report: Report) -> str:
    """Validation results: an ATTENTION block when anything failed or warns."""
    items = _report_items(report)
    return attention(*items) if items else "All plan checks passed."


def render_column_conflict(reason: str, *, blocked: bool) -> str:
    """Paper columns named in a request that the plan would not print. `blocked`: a run is refused until the question
    that follows is answered or cancelled."""
    return attention(COLUMN_MISMATCH, reason, *([BLOCKED] if blocked else []))


def render_current_plan(config: dict[str, Any], report: Report, *, simulate: bool, operator: str = "",
                        trigger: str = "run", prepared: dict[str, Any] | None = None,
                        notices: Sequence[str] = ()) -> str:
    """The CURRENT PLAN: what typing `run` carries out now, then how to run it. `notices` (physical moves still to
    make, for example) join the validation problems in the ATTENTION block right above the run instruction."""
    mode = "SIMULATION - nothing contacts the robot" if simulate else "LIVE - the real OT-2 will move"
    lines = _banner("CURRENT PLAN", mode + (f"   |   {operator}" if operator else "")) + [""]
    items = _report_items(report) + list(notices)
    lines += plan_sections(config, prepared=prepared) + ["", attention(*items) if items else "All plan checks passed.", ""]
    if report.errors:
        lines.append("Fix the problems above before running.")
    elif simulate:
        lines += [f">>> TO RUN THE SIMULATION NOW, TYPE:  {trigger}",
                  "    No robot is contacted. Or keep talking to change the plan first."]
    else:
        lines += [f">>> TO START THE REAL ROBOT NOW, TYPE:  {trigger}",
                  "    The OT-2 starts moving as soon as you do. Or keep talking to change the plan first."]
    return "\n".join(lines)


def render_run_banner(config: dict[str, Any], *, simulate: bool, operator: str, session_label: str,
                      run_number: int) -> str:
    plan = build_plan(config)
    columns = paper_columns_printed(config)
    if simulate:
        lines = _banner("STARTING SIMULATION", "building, then simulating every movement locally - no robot is contacted")
    else:
        lines = _banner("STARTING THE REAL OT-2", "the robot is about to move")
    lines += _row("Operator", f"{operator} | {session_label} | run {run_number}")
    lines += ["", "DECK"] + _deck_rows(config) + ["", "THIS RUN"]
    wells = _range([well.well for well in plan.wells])
    lines += _row("Dilutions made", f"{len(plan.wells)}   (plate wells {wells})" if plan.do_dilution
                  else f"none   (plate wells {wells} already hold them)")
    if plan.do_print:
        lines += _row("Paper columns", pipes(columns))
        lines += _row("Print positions", sum(op.kind == "print" for op in plan.operations))
        lines += _row("Total drops", plan.total_drops)
    else:
        lines += _row("Printing", "none")
    lines += _row("Tips", f"{plan.tips_needed}   ({_range([tip.tip for tip in plan.tips])})" if plan.tips
                  else "none")
    return "\n".join(lines)


# ── proposals ───────────────────────────────────────────────────────────────────

_CHANGE_NAMES = {
    "deck.plate.slot": "dilution plate slot", "deck.paper.slot": "paper print plate slot",
    "deck.tuberack.slot": "vial rack slot", "deck.tiprack.slot": "tip rack slot",
    "materials.sample.vial": "dye vial", "materials.solvent.vial": "water vial",
    "materials.sample.label": "dye name", "materials.solvent.label": "water name",
    "dilution.factors": "dilution factors", "dilution.plate_column": "plate column", "dilution.start_row": "start row",
    "dilution.total_volume_ul": "final volume", "dilution.prepared_volume_ul": "volume in each prepared well",
    "mixing.reps": "mixes", "mixing.volume_ul": "mixing volume", "print.droplet_volume_ul": "drop volume",
    "print.droplets_per_spot": "drops per position", "print.replicates": "replicates",
    "print.paper_start_column": "first paper column", "tips.start_tip": "tip start", "tips.return_tips": "used tips",
    "tips.policy": "tip use",
}


def _change_name(change: Any) -> str:
    if change.path == "dilution.enabled":
        return "dilution step on" if change.after else "dilution step off"
    if change.path == "print.enabled":
        return "printing on" if change.after else "printing off"
    return _CHANGE_NAMES.get(change.path, field_label(change.path).lower())


def format_value(path: str, value: Any) -> str:
    if value is None:
        return "(not set)"
    if path.endswith(".slot"):
        return format_slot(value)
    if path == "dilution.factors":
        return pipes(fmt_factor(item) for item in value)
    if path == "tips.policy":
        return "one tip per liquid" if value == "per_liquid" else "new tip every transfer"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if path.endswith("_ul"):
        return pipes(fmt_ul(item) for item in value) if isinstance(value, list) else fmt_ul(value)
    return str(value)


def render_numbered_changes(changes: Sequence[Any]) -> str:
    """The changes of a waiting proposal, numbered, each with the value it would have."""
    return "\n".join(f"  {number}) {field_label(change.path)}: {format_value(change.path, change.after)}"
                     for number, change in enumerate(changes, start=1))


def _proposal_attention(proposal: Any) -> list[str]:
    items: list[str] = []
    unverified = [change for change in proposal.changes if not change.verified]
    if unverified:
        items.append("CHECK THESE - I could not find them in what you typed:")
        items += [f"  - {field_label(change.path)}: {format_value(change.path, change.after)}" for change in unverified]
    warned = {issue.code for issue in proposal.report.warnings}
    for change in proposal.changes:
        if change.path == "dilution.enabled" and change.after is False and "print.assumes_prepared" not in warned:
            items.append("Saying yes SKIPS making the dilutions in the next run.")
        elif change.path == "print.enabled" and change.after is False:
            items.append("Saying yes SKIPS printing in the next run.")
    record = (proposal.physical or {}).get("dilutions_prepared", ...)
    if record is None:
        items.append("Saying yes records that the plate wells no longer hold dilutions.")
    elif record is not ...:
        wells = [str(well) for well in record.get("wells", [])]
        items.append(f"Saying yes records plate wells {_range(wells)} as already holding the dilutions "
                     f"({pipes(fmt_factor(factor) for factor in record.get('factors', []))}; made at "
                     f"{fmt_ul(record.get('total_volume_ul', 0))} each; {record.get('source', 'reported')}).")
    reported = reported_roles(proposal.changes)
    if reported:
        names = ", ".join(f"the {LABWARE_NAMES[role]}" for role in reported)
        items.append(f"For {names} this only updates the record to match what is physically on the robot. "
                     "The robot does not move.")
    moves = moves_to_make(proposal.changes, proposal.before, proposal.after)
    if moves:
        items.append("After you apply this, physically " + "; ".join(moves) + ".")
    if any(change.path.startswith("deck.") and is_off_deck(change.after) for change in proposal.changes):
        items.append(OFF_DECK_MEANING)
    return items + _report_items(proposal.report)


def proposal_attention_items(proposal: Any) -> list[str]:
    """Warnings and required checks shown beside a proposal in any UI."""
    return _proposal_attention(proposal)


def render_proposal(proposal: Any, *, prepared: dict[str, Any] | None = None) -> str:
    """The complete plan that exists if the scientist types yes. `prepared` is the prepared-dilutions record it keeps."""
    subtitle = ("record update only - the robot does not move - applied only after yes"
                if proposal.title == "PHYSICAL STATE RECONCILIATION"
                else "not applied yet - nothing changes until you type yes")
    if proposal.replaces is not None:
        subtitle = f"replaces #{proposal.replaces} - {subtitle}"
    lines = _banner(f"{proposal.title} #{proposal.id}", subtitle)
    names = [_change_name(change) for change in proposal.changes]
    if len(names) > 1:
        # numbered, so "1 3" keeps only those changes (the partial approval parser counts in this order)
        names = [f"{number}) {name}" for number, name in enumerate(names, start=1)]
    if proposal.physical:
        names.append("physical record")
    if names:
        lines += _row("Changes", pipes(names))
    lines += [""] + plan_sections(proposal.after, prepared=prepared)
    notes = list(proposal.notes) + [f"{_change_name(change)}: {change.why}" for change in proposal.changes
                                    if change.kind == "dependent" and change.why]
    if notes:
        lines += ["", "NOTES"] + [f"  - {note}" for note in notes]
    items = _proposal_attention(proposal)
    if items:
        lines += ["", attention(*items)]
    lines += ["", f"{APPLY_PROMPT}{proposal.id}?  Type yes to apply this plan, or no to discard it."]
    return "\n".join(lines)


# ── deck ────────────────────────────────────────────────────────────────────────

def render_deck(title: str, config: dict[str, Any]) -> str:
    return "\n".join([title] + _deck_rows(config))


def physical_moves(before: dict[str, Any], after: dict[str, Any], *, skip: Iterable[str] = ()) -> list[str]:
    moves = []
    skipped = set(skip)
    for role in LABWARE_ROLES:
        old, new = slot_of(before, role), slot_of(after, role)
        if old == new or role in skipped:
            continue
        if is_off_deck(new):
            moves.append(f"remove the {LABWARE_NAMES[role]} from {format_slot(old)} (it goes OFF DECK)")
        elif is_off_deck(old):
            moves.append(f"place the {LABWARE_NAMES[role]} in {format_slot(new)}")
        else:
            moves.append(f"move the {LABWARE_NAMES[role]} from {format_slot(old)} to {format_slot(new)}")
    return moves


# A deck change built from the scientist's report of where labware already is ("I moved the vial rack to slot 6
# myself") only updates the record; any other deck change is a move the scientist still has to make.
REPORTED_WHY = "you reported that it is physically there already"


def reported_roles(changes: Sequence[Any]) -> list[str]:
    return [change.path.split(".")[1] for change in changes
            if change.path.startswith("deck.") and getattr(change, "why", "") == REPORTED_WHY]


def moves_to_make(changes: Sequence[Any], before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return physical_moves(before, after, skip=reported_roles(changes))


def render_conflict(before: dict[str, Any], after: dict[str, Any], conflicts: list[DeckConflict]) -> str:
    lines = ["Cannot apply that deck change yet. Nothing was changed.", "", "Requested:"]
    for role in LABWARE_ROLES:
        if slot_of(before, role) != slot_of(after, role):
            lines.append(f"  {LABWARE_NAMES[role]} to {format_slot(slot_of(after, role))} "
                         f"(now in {format_slot(slot_of(before, role))})")
    vacated = [slot_of(before, role) for role in LABWARE_ROLES
               if slot_of(before, role) != slot_of(after, role) and isinstance(slot_of(before, role), int)]
    for conflict in conflicts:
        lines += ["", "Conflict:"]
        movers = " and the ".join(LABWARE_NAMES[role] for role in conflict.movers)
        if conflict.occupants:
            occupant = LABWARE_NAMES[conflict.occupants[0]]
            lines += [f"  Slot {conflict.slot} is currently occupied by the {occupant}.",
                      f"  I need a new location for the {occupant} before the {movers} can move to "
                      f"Slot {conflict.slot}."]
        else:
            lines += [f"  The {movers} cannot all go to Slot {conflict.slot}.",
                      "  I need a different location for one of them."]
    open_slots = [slot for slot in free_slots(before) if slot not in {c.slot for c in conflicts}]
    lines += ["", "Valid locations:",
              "  Slots 1-11 that are currently unoccupied: "
              + (", ".join(str(slot) for slot in open_slots) if open_slots else "none")]
    if vacated:
        lines.append("  Slot(s) " + ", ".join(str(slot) for slot in vacated)
                     + " also become free if the requested move goes ahead")
    # OFF DECK is a valid location only for labware that no step of the requested plan needs. The labware that needs
    # a new location is the occupant of the slot, or, when several labware are moved into one empty slot, the movers.
    needing: list[str] = []
    for conflict in conflicts:
        for role in (conflict.occupants[:1] if conflict.occupants else conflict.movers):
            if role not in needing:
                needing.append(role)
    reasons = {role: off_deck_blocker(after, role) for role in needing}
    required = [role for role in needing if reasons[role]]
    removable = [role for role in needing if not reasons[role]]
    if len(needing) == 1 and required:
        lines.append(f"  Not OFF DECK: {reasons[required[0]]}.")
    elif required:
        lines.append("  Not OFF DECK: " + "; ".join(f"the {LABWARE_NAMES[role]} is needed ({reasons[role]})"
                                                    for role in required) + ".")
    if removable:
        names = " or the ".join(LABWARE_NAMES[role] for role in removable)
        lines += ["  or OFF DECK" + (f" (only the {names})" if required else ""),
                  f'  "OFF DECK" means the {names} has been physically removed from the OT-2 deck.']
    lines += ["", "Say where it should go, in one request, for example:",
              f'  "{_conflict_example(conflicts[0], open_slots + vacated, removable)}"']
    return attention(*lines)


_SHORT_NAMES = {"plate": "dilution plate", "paper": "paper print plate", "tuberack": "vial rack", "tiprack": "tip rack"}


def _conflict_example(conflict: DeckConflict, spare_slots: list[int], removable: Sequence[str] = ()) -> str:
    """A request that resolves this conflict, naming the labware actually involved (any free slot would do).
    OFF DECK is suggested only for labware the plan can run without."""
    spare = next((slot for slot in spare_slots if slot != conflict.slot), None)

    def elsewhere(role: str) -> str:
        if spare is not None:
            return f"slot {spare}"
        return "OFF DECK" if role in removable else "a free slot"

    if conflict.occupants:
        occupant = conflict.occupants[0]
        example = f"move the {_SHORT_NAMES[occupant]} to {elsewhere(occupant)}"
        if conflict.movers:
            example += f" and the {_SHORT_NAMES[conflict.movers[0]]} to slot {conflict.slot}"
        return example
    first, second = conflict.movers[0], conflict.movers[-1]
    return f"move the {_SHORT_NAMES[first]} to slot {conflict.slot} and the {_SHORT_NAMES[second]} to {elsewhere(second)}"


# ── steps: every liquid movement FROM and TO (the `steps` command) ──────────────

def dilution_name(config: dict[str, Any], factor: float) -> str:
    sample, solvent = material_label(config, "sample"), material_label(config, "solvent")
    if float(factor) == 1.0:
        return f"neat {sample} (1×, undiluted)"
    return f"{fmt_factor(factor)} {sample} dilution in {solvent}"


def _tips_for(plan: Plan, predicate) -> list[str]:
    tips = []
    for assignment in plan.tips:
        ops = plan.operations[assignment.first_operation:assignment.first_operation + assignment.operation_count]
        if any(predicate(op) for op in ops):
            tips.append(assignment.tip)
    return tips


def render_dilution_step(config: dict[str, Any], plan: Plan) -> str:
    total = plan.total_volume_ul
    header = (f"STEP 1 - DILUTIONS   {len(plan.wells)} well(s) in plate column {plan.plate_column}, "
              f"{fmt_ul(total)} each")
    if not plan.do_dilution:
        lines = [header + "   [SKIPPED: this run does not make dilutions]",
                 f"  These plate wells are assumed to ALREADY hold the dilutions "
                 f"({fmt_ul(plan.source_volume_ul)} each):"]
        lines += [f"    {well.well:<5} {dilution_name(config, well.factor)}" for well in plan.wells]
        return "\n".join(lines)
    sample, solvent = material_spec(config, "sample"), material_spec(config, "solvent")
    lines = [header, "  well     fold          dye         water"]
    for well in plan.wells:
        lines.append(f"  {well.well:<6} {fmt_factor(well.factor):>6}  {well.sample_ul:>9.2f} µL  {well.solvent_ul:>9.2f} µL")
    lines.append("  FROM -> TO")
    for role, spec in (("solvent", solvent), ("sample", sample)):
        ops = [op for op in plan.operations if op.kind == "transfer" and op.role == role]
        if not ops:
            continue
        wells = list(dict.fromkeys(op.destination for op in ops))
        tips = _tips_for(plan, lambda op, role=role: op.kind == "transfer" and op.role == role)
        name = material_label(config, role)
        lines += [
            f"    {name} ({role})",
            f"      FROM : {_where(config, 'tuberack')}, vial {spec.get('vial')}",
            f"      TO   : {_where(config, 'plate')}, well(s) {', '.join(wells)}",
            f"      {fmt_ul(sum(op.volume_ul for op in ops))} in {len(ops)} transfer(s); "
            f"tip(s) {_range(tips) if len(tips) > 2 else ', '.join(tips)}",
        ]
    dilution = config["dilution"]
    blow = "then blow out" if dilution.get("blow_out_after_dispense", True) else "no blow-out"
    lines.append(
        f"  Each transfer: aspirate {fmt_num(solvent.get('aspirate_height_mm', 4.0))} mm above the vial "
        f"bottom, dispense {fmt_num(-float(dilution.get('solvent_dispense_from_top_mm', -2.0)))} mm (water) / "
        f"{fmt_num(-float(dilution.get('sample_dispense_from_top_mm', -1.0)))} mm (dye) below the well top, {blow}."
    )
    return "\n".join(lines)


def _tip_of_operation(plan: Plan, index: int) -> str:
    for assignment in plan.tips:
        if assignment.first_operation <= index < assignment.first_operation + assignment.operation_count:
            return assignment.tip
    return "?"


def render_print_step(config: dict[str, Any], plan: Plan) -> str:
    header = "STEP 2 - PRINTING   each dilution prints on its own paper row"
    if not plan.do_print:
        return header + "   [SKIPPED: this run does not print]"
    steps = [(index, op) for index, op in enumerate(plan.operations) if op.kind == "print"]
    columns = paper_columns_printed(config)
    lines = [header, f"  Paper columns printed: {format_columns(columns)}   (first paper column "
                     f"{config['print'].get('paper_start_column', 1)}; one paper column per drop volume x replicate)"]
    if len(steps) <= DETAILED_PRINT_STEPS:
        for number, (index, op) in enumerate(steps, start=1):
            lines += [
                f"  PRINT STEP {number} of {len(steps)}",
                f"    FROM : {_where(config, 'plate')}, well {op.source}",
                f"           {dilution_name(config, op.factor)}",
                f"    TO   : {_where(config, 'paper')}, position {op.destination}",
                f"    Volume per drop: {fmt_ul(op.volume_ul)}   Drops: {op.droplets}   "
                f"Total: {fmt_ul(op.volume_ul * op.droplets)}   Tip: {_tip_of_operation(plan, index)}",
            ]
    else:
        lines += [f"  FROM: {_where(config, 'plate')}        TO: {_where(config, 'paper')}",
                  "     #  from well  dilution                        to position  drop      drops  total     tip"]
        for number, (index, op) in enumerate(steps, start=1):
            lines.append(
                f"  {number:>4}  {op.source:<9}  {dilution_name(config, op.factor)[:30]:<30}  -> {op.destination:<9} "
                f"{fmt_ul(op.volume_ul):<9} {op.droplets:<6} {fmt_ul(op.volume_ul * op.droplets):<9} "
                f"{_tip_of_operation(plan, index)}")
    mixing, printing = config["mixing"], config["print"]
    lines += [
        f"  Totals: {len(steps)} print step(s), {plan.total_drops} drop(s), {fmt_ul(plan.printed_fluid_ul)} printed.",
        f"  Every print step first mixes its source well {int(mixing['reps'])}× with {fmt_ul(mixing['volume_ul'])}; "
        f"each drop is released {fmt_num(printing['z_mm'])} mm above the paper with a "
        f"{fmt_ul(printing.get('air_gap_ul', 0))} air gap, {fmt_ul(printing.get('push_out_ul', 0))} push-out"
        f"{', blow-out' if printing.get('blow_out') else ''} and a {fmt_num(printing.get('post_dispense_delay_s', 0))} s dwell.",
    ]
    return "\n".join(lines)


def render_steps(config: dict[str, Any]) -> str:
    plan = build_plan(config)
    return render_dilution_step(config, plan) + "\n\n" + render_print_step(config, plan)


def _group_label(config: dict[str, Any], plan: Plan, op: Operation, count: int) -> str:
    if op.kind == "transfer":
        name = material_label(config, op.role)
        if plan.policy == "per_liquid":
            wells = len({o.destination for o in plan.operations if o.kind == "transfer" and o.role == op.role})
            return f"{name} transfers into {wells} well(s)"
        return f"{name} -> {op.destination}"
    if plan.policy == "per_liquid":
        return f"printing from {op.source} ({count} position(s))"
    return f"printing {op.source} -> paper {op.destination}"


def render_tip_configuration(config: dict[str, Any], plan: Plan) -> str:
    tips = config["tips"]
    spec = config["deck"]["tiprack"]
    reuse = "Yes" if plan.policy == "per_liquid" else "No"
    lines = [
        "TIP CONFIGURATION",
        f"  Tip rack               : {_where(config, 'tiprack')} ({spec.get('load_name')})",
        f"  Starting tip           : {plan.start_tip or tips.get('start_tip')}   (the first tip this run picks up)",
        f"  Tip reuse              : {reuse} - {TIP_POLICIES.get(plan.policy, plan.policy)}",
        f"  Estimated tips required: {plan.tips_needed}"
        + (f"   ({_range([t.tip for t in plan.tips])})" if plan.tips else ""),
        f"  Available from start   : {plan.tips_available}",
        f"  Next unused tip after  : {plan.next_tip or 'none - the rack would be empty'}",
        f"  Estimated remaining    : {max(0, plan.tips_available - plan.tips_needed)} after this run",
        f"  Used tips              : {'returned to the rack (do not reuse them)' if tips.get('return_tips') else 'dropped in the trash'}",
    ]
    if plan.tips_short:
        lines.append(f"  WARNING: this plan needs {plan.tips_needed} tips but only {plan.tips_available} "
                     f"remain from {plan.start_tip}.")
    rows: list[tuple[list[str], str]] = []
    for assignment in plan.tips:
        op = plan.operations[assignment.first_operation]
        label = _group_label(config, plan, op, assignment.operation_count)
        if rows and plan.policy != "per_liquid" and rows[-1][1].split(" -> ")[0] == label.split(" -> ")[0] and op.kind == "transfer":
            rows[-1][0].append(assignment.tip)
            continue
        rows.append(([assignment.tip], label))
    if rows:
        lines.append("  Tip use:")
        for names, label in rows[:24]:
            shown = _range(names) + (f" ({len(names)} tips)" if len(names) > 1 else "")
            lines.append(f"    {shown:<18} {label.split(' -> ')[0] + ' transfers' if len(names) > 1 else label}")
        if len(rows) > 24:
            lines.append(f"    ... {len(rows) - 24} more")
    return "\n".join(lines)


# ── lab-owned settings, history, ask mode ───────────────────────────────────────

_PROFILE_FIELDS = (
    ("Drop release height above paper (mm)", "print.z_mm", ("print_release", "dispense_height_mm")),
    ("Trailing air gap (µL)", "print.air_gap_ul", ("print_release", "trailing_air_gap_ul")),
    ("Air-gap height above the well (mm)", "print.air_gap_height_mm", ("print_release", "air_gap_height_mm")),
    ("Push-out (µL)", "print.push_out_ul", ("print_release", "push_out_ul")),
    ("Blow-out after each drop", "print.blow_out", ("print_release", "blow_out")),
    ("Dwell after each drop (s)", "print.post_dispense_delay_s", ("print_release", "post_dispense_delay_s")),
    ("Aspirate height in dilution well (mm)", "print.aspirate_height_mm", ("labware:plate", "aspirate_height_mm")),
    ("Water dispense, offset from well top (mm)", "dilution.solvent_dispense_from_top_mm", ("labware:plate", "dispense_height_mm")),
    ("Dye dispense, offset from well top (mm)", "dilution.sample_dispense_from_top_mm", ("labware:plate", "dispense_height_mm")),
    ("Aspirate flow rate (µL/s)", "flow_rates.aspirate", ("pipette", "flow_rates", "aspirate_ul_s")),
    ("Dispense flow rate (µL/s)", "flow_rates.dispense", ("pipette", "flow_rates", "dispense_ul_s")),
)
DOCUMENTED_DIFFERENCES = {
    "print.aspirate_height_mm": "inherited from the working v6 dilution/print protocol",
    "dilution.sample_dispense_from_top_mm": "inherited from the working v6 dilution/print protocol",
}


def _profile_value(profile: dict[str, Any], keys: tuple[str, ...]) -> Any:
    machine = profile.get("machine") or {}
    if keys[0].startswith("labware:"):
        role = keys[0].split(":", 1)[1]
        entry = next((item for item in machine.get("labware", []) if item.get("role") == role), {})
        return entry.get(keys[1])
    node: Any = machine
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def profile_comparison(config: dict[str, Any], profile: dict[str, Any]) -> list[tuple[str, Any, Any, str]]:
    rows = []
    for label, path, keys in _PROFILE_FIELDS:
        demo, reference = get_path(config, path), _profile_value(profile, keys)
        if reference is None:
            status = "no profile value"
        elif demo == reference or (isinstance(demo, (int, float)) and isinstance(reference, (int, float))
                                   and not isinstance(demo, bool) and abs(float(demo) - float(reference)) < 1e-9):
            status = "match"
        elif path in DOCUMENTED_DIFFERENCES:
            status = f"differs ({DOCUMENTED_DIFFERENCES[path]})"
        else:
            status = "MISMATCH"
        rows.append((label, demo, reference, status))
    vial = _profile_value(profile, ("labware:vial_rack", "aspirate_height_mm"))
    for role in ("solvent", "sample"):
        demo = material_spec(config, role).get("aspirate_height_mm")
        status = "match" if vial is not None and demo is not None and float(demo) == float(vial) else "MISMATCH"
        rows.append((f"{material_label(config, role)} vial aspirate height (mm)", demo, vial, status))
    return rows


def render_settings(config: dict[str, Any], profile: dict[str, Any]) -> str:
    dilution, mixing = config["dilution"], config["mixing"]
    lines = ["LAB-OWNED LIQUID HANDLING (never changed in conversation)",
             "  setting                                   demo      machine profile   status"]
    for label, demo, reference, status in profile_comparison(config, profile):
        lines.append(f"  {label:<41} {str(demo):<9} {str(reference):<17} {status}")
    lines += [
        f"  Largest single dilution transfer          {fmt_ul(dilution.get('max_transfer_ul', 20))}",
        f"  Blow-out after each dilution dispense     {'yes' if dilution.get('blow_out_after_dispense', True) else 'no'}"
        "   (the P20's default push-out is 0 µL, so this is what empties the tip)",
        f"  Mixing before each print step             {mixing.get('reps')}× {fmt_ul(mixing.get('volume_ul'))} "
        f"at {fmt_num(mixing.get('height_mm', 2.0))} mm above the well bottom",
        "  Release height history: 0.5 mm was physically confirmed (four_clover_spacing_v13);",
        "  1.1 mm was requested on 2026-08-31 and its physical revalidation is still pending.",
        "  See docs/ai_dye_demo/liquid_handling_parameters.md.",
    ]
    return "\n".join(lines)


def render_history(history: Sequence[dict[str, Any]]) -> str:
    """The `history` command: how the plan changed, revision by revision (the plan screens never show this)."""
    if not history:
        return "PARAMETER HISTORY\n  No changes have been applied in this session."
    lines = ["PARAMETER HISTORY"]
    for record in history:
        lines.append(f"  revision {record['revision']}  {record['timestamp_utc']}  by {record['operator']}"
                     f"  ({record.get('source', 'conversation')})")
        if record.get("request"):
            lines.append(f"    request: {record['request']}")
        for change in record["changes"]:
            lines.append(f"    {field_label(change['path'])}: {format_value(change['path'], change['before'])} -> "
                         f"{format_value(change['path'], change['after'])}")
    return "\n".join(lines)


def render_ask(answer: str) -> str:
    body = "\n".join(textwrap.fill(paragraph, width=76) if paragraph.strip() else ""
                     for paragraph in answer.strip().splitlines())
    return f"ASK MODE — no experiment parameters changed.\n\nAnswer:\n{body}"
