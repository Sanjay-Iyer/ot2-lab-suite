"""
src/core/print_groups.py
========================
The unified pipette-group schema + resolver/validator for the printing workflow.

A *print group* is one homogeneous batch of droplets that share a volume, a pipette,
a source, and a destination pattern. Modelling printing as a list of groups is what
lets a single experiment mix a single-channel P20 (small volumes, one spot at a time)
and an 8-channel P300 (larger volumes, 8-up) — see docs/REFACTOR_PIPETTE_SELECTION_PLAN.md.

This module:
  * defines the Pydantic schema (`MountedPipetteConfig`, `PrintGroup`, ...),
  * parses a workflow config into that schema, including a **backward-compat layer**
    that maps legacy `pipette:` + `printing:`/`color_series:` onto one P300 8-up group
    (with a recorded migration note),
  * validates the resolved groups against the mounted pipettes and each other,
    delegating pipette choice to `src/core/pipette_selection.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.pipette_selection import (
    MountedPipette,
    PipetteChoice,
    PipetteSelectionError,
    assert_layout_supported,
    load_pipette_specs,
    parse_mounted_pipettes,
    select_pipette,
)

Layout = Literal["single_spot", "column_8up"]
_WELLS_PER_DISPENSE: dict[str, int] = {"single_spot": 1, "column_8up": 8}


class PrintConfigError(ValueError):
    """Raised when a print-group configuration is invalid (has ERROR issues)."""


# ── Schema ────────────────────────────────────────────────────────────────────

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MountedPipetteConfig(_Strict):
    name: str
    mount: Literal["left", "right"]
    single_start: Optional[str] = None


class SourceSpec(_Strict):
    plate_column: Optional[str] = None
    wells: Optional[list[str]] = None
    vial: Optional[str] = None
    aspirate_height_mm: Optional[float] = None

    @model_validator(mode="after")
    def _need_a_source(self) -> "SourceSpec":
        if not (self.plate_column or self.wells or self.vial):
            raise ValueError("source must specify one of: plate_column, wells, vial")
        return self


class Spacing(_Strict):
    x: float = 9.0
    y: float = 0.0
    z: float = 0.0


class DestinationSpec(_Strict):
    paper_start_column: int = Field(ge=1)
    spacing_mm: Spacing = Field(default_factory=Spacing)


class DispenseSpec(_Strict):
    z_mm: float = 1.0
    air_gap_ul: float = Field(default=0.0, ge=0)
    air_gap_height_mm: float = 20.0
    blow_out: bool = True
    touch_tip: bool = False
    post_dispense_delay_s: float = Field(default=0.5, ge=0)
    move_speed_mm_per_s: Optional[float] = None


class TipSpec(_Strict):
    # single-channel explicit tip well (e.g. H12) OR 8-channel full-column pickup.
    well: Optional[str] = None
    block_column: Optional[int] = None
    # v3 can keep one P20 tip per dilution/source row while printing several
    # volume groups. The concrete row -> tip mapping lives once in the workflow's
    # global tip_policy block and is referenced by name from each group.
    strategy: Optional[Literal["per_source_row"]] = None
    map_ref: Optional[str] = None
    reuse: bool = True
    return_tip: bool = Field(default=True, alias="return")
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PrintGroup(_Strict):
    name: str
    volume_ul: float = Field(gt=0)
    pipette: str = "auto"          # "auto" or a mounted pipette name
    layout: Layout = "single_spot"
    source: SourceSpec
    replicates: int = Field(default=1, ge=1)
    # Droplets stacked on the SAME paper spot. Each one is its own aspirate ->
    # dispense cycle (the tip goes back to the source well and returns), so a spot
    # receives volume_ul * droplets_per_spot in total. Used to build up loading on
    # one location instead of spreading it over more paper columns.
    droplets_per_spot: int = Field(default=1, ge=1)
    # Mix this group's source column 8-up with the P300 before printing. Default True
    # keeps the historical behaviour; set False on later groups that share a source
    # column with an earlier one so the column is only mixed once (avoids re-dipping
    # a print block that has already touched paper back into the plate).
    mix_before: bool = True
    destination: DestinationSpec
    dispense: DispenseSpec = Field(default_factory=DispenseSpec)
    tips: TipSpec = Field(default_factory=TipSpec)

    def wells_per_dispense(self) -> int:
        return _WELLS_PER_DISPENSE[self.layout]

    def volume_per_source_well_ul(self) -> float:
        """Total µL drawn from each source well by this group."""
        return self.volume_ul * self.replicates * self.droplets_per_spot

    def paper_columns(self) -> list[int]:
        """Paper columns this group occupies (one per replicate line)."""
        start = self.destination.paper_start_column
        return list(range(start, start + self.replicates))


# ── Parsed result ─────────────────────────────────────────────────────────────

@dataclass
class ParsedPrintConfig:
    pipettes: list[MountedPipetteConfig]
    groups: list[PrintGroup]
    mounted: list[MountedPipette]
    migration_notes: list[str] = field(default_factory=list)


@dataclass
class Issue:
    severity: Literal["error", "warning"]
    where: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.severity.upper()}] {self.where}: {self.message}"


# ── Parsing + backward-compat ─────────────────────────────────────────────────

def parse_print_config(config: Mapping[str, Any]) -> ParsedPrintConfig:
    """Parse a workflow config into pipettes + print groups.

    Accepts the new schema (`pipettes:` + `print_groups:`) directly. If instead the
    legacy `pipette:` + `printing:`/`color_series:` fields are present, they are
    auto-migrated to a single P300 8-up group per color series (recording notes).
    Mixing new `print_groups:` with legacy `printing:` is rejected.
    """
    has_new = bool(config.get("print_groups"))
    # A *real* legacy print block carries flat print fields. `color_series` on its own
    # is dilution metadata in the unified schema and may coexist with print_groups.
    legacy_printing = isinstance(config.get("printing"), Mapping) and any(
        k in config["printing"]
        for k in ("droplet_volume_ul", "source_column", "num_replicates", "print_block_column")
    )
    if has_new and legacy_printing:
        raise PrintConfigError(
            "Config mixes new 'print_groups' with a legacy 'printing' block. "
            "Use one or the other (remove the legacy printing block to migrate)."
        )

    notes: list[str] = []
    if has_new:
        pipette_entries = config.get("pipettes")
        if not pipette_entries:
            # allow a single legacy pipette mapping alongside new groups
            if isinstance(config.get("pipette"), Mapping):
                pipette_entries = [config["pipette"]]
            else:
                raise PrintConfigError("New schema requires a 'pipettes:' list.")
        pipettes = [MountedPipetteConfig(**e) for e in pipette_entries]
        groups = [PrintGroup(**g) for g in config["print_groups"]]
    else:
        pipettes, groups, notes = _migrate_legacy(config)

    specs = load_pipette_specs()
    mounted = parse_mounted_pipettes(
        {"pipettes": [p.model_dump(exclude_none=True) for p in pipettes]}, specs
    )
    return ParsedPrintConfig(pipettes=pipettes, groups=groups, mounted=mounted, migration_notes=notes)


def _migrate_legacy(config: Mapping[str, Any]) -> tuple[list[MountedPipetteConfig], list[PrintGroup], list[str]]:
    """Map legacy pipette + printing/color_series onto the unified schema."""
    notes: list[str] = []
    pip = config.get("pipette")
    if not isinstance(pip, Mapping):
        raise PrintConfigError("Legacy config missing a 'pipette:' mapping.")
    pipettes = [MountedPipetteConfig(
        name=pip["name"], mount=pip.get("mount", "right"), single_start=pip.get("single_start"),
    )]
    notes.append(f"migrated single pipette '{pip['name']}' -> pipettes[] (8-up P300 path).")

    printing = config.get("printing") or {}
    spacing = printing.get("replicate_spacing_mm") or {}
    dispense = DispenseSpec(
        z_mm=printing.get("dispense_z_mm", 1.0),
        air_gap_ul=printing.get("air_gap_ul", 0.0),
        air_gap_height_mm=printing.get("air_gap_height_mm", 20.0),
        blow_out=printing.get("blow_out", True),
        touch_tip=printing.get("touch_tip", False),
        post_dispense_delay_s=printing.get("post_dispense_delay_s", 0.5),
        move_speed_mm_per_s=printing.get("move_speed_mm_per_s"),
    )
    return_tips = (config.get("tips") or {}).get("return_tips", True)

    groups: list[PrintGroup] = []
    series_list = config.get("color_series")
    if series_list:
        for s in series_list:
            groups.append(PrintGroup(
                name=s["name"],
                volume_ul=printing.get("droplet_volume_ul", 20.0),
                pipette=pip["name"],
                layout="column_8up",
                source=SourceSpec(plate_column=str(s.get("destination_column", printing.get("source_column", "9")))),
                replicates=s.get("num_replicates", printing.get("num_replicates", 1)),
                destination=DestinationSpec(
                    paper_start_column=s.get("paper_start_column", 1),
                    spacing_mm=Spacing(x=spacing.get("x", 9.0), y=spacing.get("y", 0.0), z=spacing.get("z", 0.0)),
                ),
                dispense=dispense,
                tips=TipSpec.model_validate({"block_column": s.get("print_block_column", 1), "reuse": True, "return": return_tips}),
            ))
        notes.append(f"migrated {len(groups)} color_series entries -> print_groups[].")
    elif printing:
        groups.append(PrintGroup(
            name="print",
            volume_ul=printing.get("droplet_volume_ul", 20.0),
            pipette=pip["name"],
            layout="column_8up",
            source=SourceSpec(plate_column=str(printing.get("source_column", "9"))),
            replicates=printing.get("num_replicates", 1),
            destination=DestinationSpec(
                paper_start_column=printing.get("paper_start_column", 1),
                spacing_mm=Spacing(x=spacing.get("x", 9.0), y=spacing.get("y", 0.0), z=spacing.get("z", 0.0)),
            ),
            dispense=dispense,
            tips=TipSpec.model_validate({"block_column": printing.get("print_block_column", 1), "reuse": True, "return": return_tips}),
        ))
        notes.append("migrated flat 'printing' block -> one print_group.")
    return pipettes, groups, notes


# ── Validation ────────────────────────────────────────────────────────────────

def resolve_and_validate(
    parsed: ParsedPrintConfig,
    dilution_config: Optional[Mapping[str, Any]] = None,
) -> tuple[dict[str, PipetteChoice], list[Issue]]:
    """Resolve the pipette for each group and validate the whole set.

    Returns (group_name -> PipetteChoice, issues). Raises PrintConfigError if any
    issue is an error; call this and inspect `issues` for warnings.
    """
    issues: list[Issue] = []
    choices: dict[str, PipetteChoice] = {}

    if not parsed.groups:
        issues.append(Issue("error", "print_groups", "no print groups defined."))

    # Per-group: pipette selection + layout compatibility + tip assignment.
    for g in parsed.groups:
        try:
            choice = select_pipette(g.volume_ul, parsed.mounted, explicit=None if g.pipette == "auto" else g.pipette)
            choices[g.name] = choice
            for w in choice.warnings:
                issues.append(Issue("warning", g.name, w))
            try:
                assert_layout_supported(choice.spec, g.wells_per_dispense())
            except PipetteSelectionError as e:
                issues.append(Issue("error", g.name, str(e)))
        except PipetteSelectionError as e:
            issues.append(Issue("error", g.name, str(e)))

        # Tip assignment must match the layout.
        if g.layout == "single_spot" and not (
            g.tips.well
            or (g.tips.strategy == "per_source_row" and g.tips.map_ref)
        ):
            issues.append(Issue(
                "error",
                g.name,
                "single_spot layout requires tips.well or a "
                "tips.strategy=per_source_row mapping reference.",
            ))
        if g.layout == "column_8up" and g.tips.block_column is None:
            issues.append(Issue("error", g.name, "column_8up layout requires tips.block_column (full 8-tip pickup)."))

    # Cross-group: duplicate + out-of-bounds paper destination columns (a paper sheet
    # is addressed as a 96-well grid -> 12 columns).
    PAPER_COLUMNS = 12
    seen: dict[int, str] = {}
    for g in parsed.groups:
        for col in g.paper_columns():
            if not (1 <= col <= PAPER_COLUMNS):
                issues.append(Issue(
                    "error", g.name,
                    f"paper column {col} is out of bounds (1..{PAPER_COLUMNS}); "
                    f"lower destination.paper_start_column or replicates.",
                ))
            elif col in seen:
                issues.append(Issue(
                    "error", g.name,
                    f"paper column {col} already used by group '{seen[col]}' (duplicate destination).",
                ))
            else:
                seen[col] = g.name

    # Cross-group: shared source column (often intended; warn only).
    src_seen: dict[str, str] = {}
    for g in parsed.groups:
        col = g.source.plate_column
        if col is None:
            continue
        if col in src_seen and src_seen[col] != g.name:
            issues.append(Issue(
                "warning", g.name,
                f"source plate column {col} also used by group '{src_seen[col]}' "
                f"(shared source — fine if intended).",
            ))
        src_seen[col] = g.name

    # Optional: per-well transfer vs available source volume. A group draws
    # volume_ul per replicate per stacked droplet from EACH source well, so the
    # per-well demand is volume_ul * replicates * droplets_per_spot (independent of
    # how many wells/channels are used).
    if dilution_config:
        avail = dilution_config.get("total_volume_ul")
        if isinstance(avail, (int, float)):
            for g in parsed.groups:
                needed_per_well = g.volume_per_source_well_ul()
                if needed_per_well > avail:
                    issues.append(Issue(
                        "warning", g.name,
                        f"group draws ~{needed_per_well:g} µL per source well but each holds "
                        f"{avail:g} µL.",
                    ))

    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise PrintConfigError(
            "Invalid print configuration:\n  " + "\n  ".join(str(i) for i in errors)
        )
    return choices, issues
