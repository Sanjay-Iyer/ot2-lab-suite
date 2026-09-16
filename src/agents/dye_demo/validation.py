"""Deterministic checks for the dye demo.

The LLM proposes; this module decides whether a plan is physically possible.
Errors block a proposal or a run. Warnings are shown but do not block.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from src.agents.dye_demo.model import (
    DECK_SLOTS,
    LABWARE_NAMES,
    LABWARE_ROLES,
    ROWS,
    TIP_ORDER,
    TIP_POLICIES,
    VIAL_NAMES,
    fmt_factor,
    fmt_num,
    fmt_ul,
    is_off_deck,
    occupancy,
    required_roles,
    slot_of,
    well_geometry,
)
from src.agents.dye_demo.plan import (
    build_plan,
    droplet_volumes,
    factors_of,
    paper_layout,
    steps_enabled,
    well_area_mm2,
)

REQUIRED_SECTIONS = ("deck", "pipette", "materials", "dilution", "mixing", "print", "tips", "safety")
_WHY_REQUIRED = {
    "plate": "the dilutions are made in it and printing draws from it",
    "paper": "printing needs the paper",
    "tuberack": "making dilutions draws dye and water from its vials",
    "tiprack": "every step needs P20 tips",
}


@dataclass(frozen=True)
class Issue:
    code: str
    message: str


@dataclass
class Report:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, code: str, message: str) -> None:
        self.errors.append(Issue(code, message))

    def warn(self, code: str, message: str) -> None:
        self.warnings.append(Issue(code, message))

    def error_messages(self) -> list[str]:
        return [issue.message for issue in self.errors]


@dataclass(frozen=True)
class DeckConflict:
    """Two or more labware ending up in one slot.

    movers are labware whose slot this change sets to `slot`; occupants were
    already there and are not being moved.
    """

    slot: int
    movers: tuple[str, ...]
    occupants: tuple[str, ...]


def deck_conflicts(before: dict[str, Any], after: dict[str, Any]) -> list[DeckConflict]:
    conflicts = []
    for slot, roles in occupancy(after).items():
        if len(roles) < 2:
            continue
        movers = tuple(role for role in roles if slot_of(before, role) != slot)
        occupants = tuple(role for role in roles if slot_of(before, role) == slot)
        conflicts.append(DeckConflict(slot, movers, occupants))
    return conflicts


def free_slots(config: dict[str, Any]) -> list[int]:
    taken = occupancy(config)
    return [slot for slot in DECK_SLOTS if slot not in taken]


def off_deck_blocker(config: dict[str, Any], role: str) -> str | None:
    """Why `role` cannot go OFF DECK with this plan (a step that runs needs it), or None when it can."""
    try:
        do_dilution, do_print = steps_enabled(config)
    except (TypeError, ValueError):
        return None
    return _WHY_REQUIRED[role] if role in required_roles(do_dilution, do_print) else None


def validate(config: dict[str, Any], *, printed_positions: Iterable[str] = ()) -> Report:
    report = Report()
    missing = [name for name in REQUIRED_SECTIONS if not isinstance(config.get(name), dict)]
    if missing:
        report.error("config.sections", "missing section(s): " + ", ".join(missing))
        return report
    safety = config["safety"]
    p20_max = float(safety.get("p20_max_volume_ul", 20.0))
    p20_min = float(safety.get("p20_min_volume_ul", 1.0))
    max_fill = float(safety.get("max_well_fill_ul", 340.0))

    _check_deck(config, report)
    _check_materials(config, report)
    _check_dilution(config, report, p20_min, max_fill)
    _check_mixing(config, report, p20_max)
    _check_print(config, report, p20_min, p20_max)
    _check_tip_fields(config, report)
    if report.errors:
        return report       # the plan-level checks below need a well-formed config

    plan = build_plan(config)
    if not plan.do_dilution and not plan.do_print:
        report.error("steps.none", "nothing to run: both the dilution step and the print step are off")
    if plan.tips_short:
        report.error(
            "tips.insufficient",
            f"this plan needs {plan.tips_needed} tips but only {plan.tips_available} remain from "
            f"{plan.start_tip}; start from an earlier tip or load a fresh rack",
        )
    elif plan.next_tip is not None and 0 < len(TIP_ORDER) - TIP_ORDER.index(plan.next_tip) < 8:
        remaining = len(TIP_ORDER) - TIP_ORDER.index(plan.next_tip)
        report.warn("tips.low", f"only {remaining} unused tips will remain after this run")
    if config["tips"].get("return_tips"):
        report.warn("tips.returned", "used tips go back into the rack; they are contaminated, "
                                     "so a later run must start after them")
    _check_print_liquid(config, plan, report)
    _check_dispense_clearance(config, plan, report)
    positions = [op.destination for op in plan.operations if op.kind == "print"]
    if len(positions) != len(set(positions)):
        report.error("print.duplicate_positions", "two print steps target the same paper position")
    if plan.do_dilution and not plan.do_print:
        report.warn("dilution.not_mixed", "a dilute-only run does not mix the wells; they are mixed "
                                          "only right before each print step")
    if plan.do_print and not plan.do_dilution and plan.wells:
        report.warn(
            "print.assumes_prepared",
            f"this run does not make dilutions: it assumes plate wells {plan.wells[0].well}-"
            f"{plan.wells[-1].well} already hold them ({fmt_ul(plan.source_volume_ul)} each)",
        )
    if plan.do_dilution and (config["dilution"].get("prepared_volume_ul") not in (None, "")):
        report.warn("dilution.prepared_ignored",
                    "the 'volume now in each prepared well' is ignored because this run makes the dilutions")
    reused = sorted({op.destination for op in plan.operations if op.kind == "print"}
                    & {str(position) for position in printed_positions})
    if reused:
        report.warn("print.positions_reused",
                    "paper position(s) already printed earlier in this session: " + ", ".join(reused))
    return report


# ── individual checks ───────────────────────────────────────────────────────────

def _check_deck(config: dict[str, Any], report: Report) -> None:
    deck = config["deck"]
    for role in LABWARE_ROLES:
        spec = deck.get(role)
        if not isinstance(spec, dict) or "slot" not in spec:
            report.error("deck.missing", f"the deck must define the {LABWARE_NAMES[role]} with a slot")
            continue
        slot = spec["slot"]
        if is_off_deck(slot):
            continue
        if isinstance(slot, bool) or not isinstance(slot, int) or slot not in DECK_SLOTS:
            report.error("deck.slot", f"the {LABWARE_NAMES[role]} must be in slot 1-11 or OFF DECK "
                                      f"(12 is the trash), got {slot!r}")
    for slot, roles in occupancy(config).items():
        if len(roles) > 1:
            names = " and the ".join(LABWARE_NAMES[role] for role in roles)
            report.error("deck.collision", f"slot {slot} cannot hold both the {names}")
    try:
        do_dilution, do_print = steps_enabled(config)
    except (TypeError, ValueError):
        return
    for role in sorted(required_roles(do_dilution, do_print), key=LABWARE_ROLES.index):
        if is_off_deck(slot_of(config, role)):
            report.error("deck.required_off_deck",
                         f"the {LABWARE_NAMES[role]} is OFF DECK, but {_WHY_REQUIRED[role]}")


def _check_materials(config: dict[str, Any], report: Report) -> None:
    materials = config["materials"]
    for role in ("solvent", "sample"):
        matches = [name for name, spec in materials.items()
                   if isinstance(spec, dict) and spec.get("role") == role]
        if len(matches) != 1:
            report.error("materials.role", f"exactly one material must have role {role!r}, got {matches}")
    vials = []
    for name, spec in materials.items():
        vial = str((spec or {}).get("vial", "")).upper()
        vials.append(vial)
        if vial not in VIAL_NAMES:
            report.error("materials.vial", f"materials.{name}.vial must be A1-B4 on the 8-vial rack")
    if len(set(vials)) != len(vials):
        report.error("materials.same_vial", "the dye and the water must be in different vials")


def _check_dilution(config: dict[str, Any], report: Report, p20_min: float, max_fill: float) -> None:
    dilution = config["dilution"]
    raw = dilution.get("factors")
    factors = factors_of(config)
    if not isinstance(raw, list) or len(factors) != len(raw):
        report.error("dilution.factors", "dilution factors must be a list of numbers")
        return
    if not 1 <= len(factors) <= len(ROWS):
        report.error("dilution.count", f"1 to 8 dilutions are possible, got {len(factors)}")
    if any(factor < 1 for factor in factors):
        report.error("dilution.factor_below_1", "every dilution factor must be 1× or greater (1× is neat stock)")
    explicit_rows = dilution.get("rows")
    if isinstance(explicit_rows, (list, tuple)) and explicit_rows:
        rows = [str(r).strip().upper() for r in explicit_rows]
        if any(r not in ROWS for r in rows):
            report.error("dilution.rows", f"all dilution rows must be A-H, got {explicit_rows!r}")
        elif len(rows) != len(factors):
            report.error("dilution.rows_count", f"number of selected rows ({len(rows)}) must match dilution factors count ({len(factors)})")
    else:
        start_row = str(dilution.get("start_row", "A")).upper()
        if start_row not in ROWS:
            report.error("dilution.start_row", f"the dilution start row must be A-H, got {start_row!r}")
        elif ROWS.index(start_row) + len(factors) > len(ROWS):
            report.error("dilution.past_row_h",
                         f"{len(factors)} dilutions starting at row {start_row} run past row H")
    column = str(dilution.get("plate_column", ""))
    if not column.isdigit() or not 1 <= int(column) <= 12:
        report.error("dilution.column", f"the dilution plate column must be 1-12, got {column!r}")
    try:
        total = float(dilution.get("total_volume_ul", 0) or 0)
    except (TypeError, ValueError):
        total = 0.0
    if not 0 < total <= max_fill:
        report.error("dilution.total_volume",
                     f"total_volume_ul must be in (0, {fmt_num(max_fill)}] µL, got {dilution.get('total_volume_ul')!r}")
        return
    prepared = dilution.get("prepared_volume_ul")
    if prepared not in (None, ""):
        try:
            prepared_ul = float(prepared)
        except (TypeError, ValueError):
            prepared_ul = -1.0
        if not 0 < prepared_ul <= max_fill:
            report.error("dilution.prepared_volume",
                         f"the volume now in each prepared well must be in (0, {fmt_num(max_fill)}] µL")
    if not dilution.get("enabled", True):
        return
    for factor in factors:
        if factor < 1:
            continue
        dye, water = total / factor, total - total / factor
        if dye < p20_min:
            report.error("dilution.dye_below_min",
                         f"{fmt_factor(factor)} would need {dye:.2f} µL of dye, under the P20's "
                         f"{fmt_num(p20_min)} µL minimum; use a smaller fold factor or more total volume")
        if 0.01 < water < p20_min:
            report.error("dilution.water_below_min",
                         f"{fmt_factor(factor)} would need {water:.2f} µL of water, under the P20's "
                         f"{fmt_num(p20_min)} µL minimum")


def _check_mixing(config: dict[str, Any], report: Report, p20_max: float) -> None:
    mixing = config["mixing"]
    try:
        volume = float(mixing.get("volume_ul", 0) or 0)
        reps = int(mixing.get("reps", 0) or 0)
    except (TypeError, ValueError):
        report.error("mixing.values", "mixing volume and repetitions must be numbers")
        return
    if not 0 < volume <= p20_max:
        report.error("mixing.volume", f"the mixing volume must be in (0, {fmt_num(p20_max)}] µL")
    if reps < 1:
        report.error("mixing.reps", "mixing must repeat at least once")


def _check_print(config: dict[str, Any], report: Report, p20_min: float, p20_max: float) -> None:
    printing = config["print"]
    air_gap = float(printing.get("air_gap_ul", 0.0) or 0.0)
    volumes = droplet_volumes(config)
    if not volumes:
        report.error("print.volume", "the drop volume must be a number or a list of numbers")
    for volume in volumes:
        if volume < p20_min:
            report.error("print.volume_below_min",
                         f"a {fmt_ul(volume)} drop is under the P20's {fmt_num(p20_min)} µL minimum")
        elif volume + air_gap > p20_max:
            report.error("print.volume_over_max",
                         f"a {fmt_ul(volume)} drop plus the {fmt_ul(air_gap)} air gap is "
                         f"{fmt_ul(volume + air_gap)}, over the P20's {fmt_num(p20_max)} µL")
    for key, label in (("replicates", "replicates"), ("droplets_per_spot", "drops per position")):
        try:
            if int(printing.get(key, 1)) < 1:
                report.error(f"print.{key}", f"{label} must be at least 1")
        except (TypeError, ValueError):
            report.error(f"print.{key}", f"{label} must be a whole number")
    try:
        start = int(printing.get("paper_start_column", 1))
        width = int(printing.get("paper_columns", 12))
    except (TypeError, ValueError):
        report.error("print.paper_column", "the first paper column must be a whole number")
        return
    if start < 1:
        report.error("print.paper_column", "the first paper column must be 1-12")
        return
    try:
        columns = [spot["column"] for spot in paper_layout(config, include_overflow=True)]
    except (TypeError, ValueError):
        return                  # malformed counts are already reported above
    if columns and max(columns) > width:
        report.error("print.past_paper",
                     f"the print plan needs paper column {max(columns)}, past the paper's {width} "
                     f"columns; start further left or use fewer volumes/replicates")


def _check_tip_fields(config: dict[str, Any], report: Report) -> None:
    tips = config["tips"]
    start = str(tips.get("start_tip", "A1")).upper()
    if start not in TIP_ORDER:
        report.error("tips.start_tip", f"the starting tip {tips.get('start_tip')!r} is not a rack position (A1-H12)")
    if str(tips.get("policy", "per_liquid")) not in TIP_POLICIES:
        report.error("tips.policy", f"the tip policy must be one of {sorted(TIP_POLICIES)}")


def _check_print_liquid(config: dict[str, Any], plan, report: Report) -> None:
    """Keep the tip submerged while mixing and aspirating inside each dilution well."""
    if not plan.do_print or not plan.spots:
        return
    area = well_area_mm2(config, "plate")
    if not area:
        report.warn("print.geometry_unknown", "the plate's well geometry is unknown, so liquid depth was not checked")
        return
    mixing, printing = config["mixing"], config["print"]
    mix_ul, mix_mm = float(mixing["volume_ul"]), float(mixing.get("height_mm", 2.0))
    aspirate_mm = float(printing.get("aspirate_height_mm", 1.0))
    volume = plan.source_volume_ul
    wells = f"{plan.wells[0].well}-{plan.wells[-1].well}" if len(plan.wells) > 1 else plan.wells[0].well
    for spot in plan.spots:
        need = mix_ul + mix_mm * area
        if volume + 1e-6 < need:
            report.error(
                "print.mix_draws_air",
                f"mixing {fmt_ul(mix_ul)} at {fmt_num(mix_mm)} mm needs at least {need:.0f} µL in each "
                f"dilution well ({wells}), but a well would hold {volume:.0f} µL before printing paper "
                f"column {spot['column']}; the tip would draw air. Use more volume per dilution or print less",
            )
            return
        for _ in range(int(spot["droplets"])):
            need = float(spot["volume_ul"]) + aspirate_mm * area
            if volume + 1e-6 < need:
                report.error(
                    "print.aspirate_draws_air",
                    f"printing a {fmt_ul(spot['volume_ul'])} drop needs at least {need:.0f} µL in each "
                    f"dilution well ({wells}) to keep the tip ({fmt_num(aspirate_mm)} mm) submerged, but a "
                    f"well would hold {volume:.0f} µL at paper column {spot['column']}; use more volume "
                    f"per dilution or print less",
                )
                return
            volume -= float(spot["volume_ul"])


def _check_dispense_clearance(config: dict[str, Any], plan, report: Report) -> None:
    """Dilution dispenses happen above the liquid so a shared tip never touches it."""
    if not plan.do_dilution or not plan.wells:
        return
    geometry = well_geometry(str(config["deck"]["plate"].get("load_name", "")))
    if not geometry:
        return
    area = 3.141592653589793 * (geometry[0] / 2.0) ** 2
    depth = geometry[1]
    dilution = config["dilution"]
    water_tip = depth + float(dilution.get("solvent_dispense_from_top_mm", -2.0))
    dye_tip = depth + float(dilution.get("sample_dispense_from_top_mm", -1.0))
    water_level = max(well.solvent_ul for well in plan.wells) / area
    final_level = plan.total_volume_ul / area
    if water_level > water_tip - 1.0 or final_level > dye_tip - 1.0:
        report.warn(
            "dilution.tip_near_liquid",
            f"at {fmt_ul(plan.total_volume_ul)} per well the liquid rises to within 1 mm of the "
            f"dispense height, so the shared water/dye tip may touch the liquid and carry it back "
            f"into the vials; a smaller total volume avoids this",
        )
