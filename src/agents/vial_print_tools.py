"""
src/agents/vial_print_tools.py
==============================
LangChain tools that let an AI agent drive the flagship **vial-dilution-print**
demo (20 mL vials -> 96-well dilution series -> 8-channel paper print) entirely
from natural language.

These wrap the existing, hardened CLI pipeline rather than re-implementing it:

    build_vial_dilution_print.py  (build + embed CONFIG + simulate)
    validate_vial_print.py        (5-case run-mode matrix gate)
    verify_print_droplets.py      (host-side droplet CV, mock mode)

Design rules (see AI_AGENTS_SKILLS_OVERVIEW.md):
  * Edit the YAML, never the generated file. The agent mutates an in-memory
    working dict seeded from the committed default and writes a *user* copy under
    configs/workflows/user/. The committed default is never touched.
  * The builder's validate() and the protocol's on-robot _preflight() remain the
    enforced backstops. The soft checks here are for fast conversational feedback,
    not a substitute for them.
  * Every value the agent sets is mapped back to a documented YAML key with safe
    bounds (skills/vial-dilution-print/PARAMETERS.md).

The robot-side tools (deploy/execute) are NOT here — they are reused unchanged
from src/agents/tools.py and are lab-laptop only.
"""
from __future__ import annotations

import copy
import json
import math
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import yaml
from langchain.tools import tool

from src.utils.paths import (
    PROJECT_ROOT,
    DEFAULT_WORKFLOW_CONFIG_DIR,
    TEMPLATE_WORKFLOW_CONFIG_DIR,
    USER_WORKFLOW_CONFIG_DIR,
    GENERATED_PROTOCOL_DIR,
    SIMULATION_RECORDS_PATH,
)
from src.core.config_loader import merge_user_updates
from src.utils.hashing import hash_file

# ── Pipeline locations ────────────────────────────────────────────────────────────
WORKFLOW_NAME    = "vial_dilution_print"
DEFAULT_YAML     = DEFAULT_WORKFLOW_CONFIG_DIR / f"{WORKFLOW_NAME}.yaml"
TEMPLATE_DIR     = TEMPLATE_WORKFLOW_CONFIG_DIR / WORKFLOW_NAME
BUILD_SCRIPT     = PROJECT_ROOT / "scripts" / "build_vial_dilution_print.py"
VALIDATE_SCRIPT  = PROJECT_ROOT / "scripts" / "validate_vial_print.py"
CV_SCRIPT        = PROJECT_ROOT / "vision_tests" / "scripts" / "verify_print_droplets.py"
GENERATED_LATEST = GENERATED_PROTOCOL_DIR / f"{WORKFLOW_NAME}_latest.py"

# Physical ceiling: a 96-well plate column has 8 rows, so at most 8 dilutions /
# 8 simultaneous droplets. Hard bounds are re-checked by the builder.
MAX_DILUTIONS         = 8
MAX_DROPLET_UL        = 300.0   # p300 max; PARAMETERS.md keeps droplets small (default 15)
MAX_TOTAL_WELL_UL     = 300.0

# ── Module working state (one conversation = one working config) ──────────────────
_WORKING: Optional[dict] = None        # full YAML incl. run_modes
_DEFAULT_FOLDS: list = []              # canonical explicit fold list, captured on load
_LAST_USER_YAML: Optional[Path] = None
_LAST_SHA: Optional[str] = None
_TEMPLATE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


# ════════════════════════════════════════════════════════════════════════════════
# Pure helpers (no globals) — these carry the logic and are unit-tested directly.
# ════════════════════════════════════════════════════════════════════════════════

def _resolve_factors(fc: dict) -> list:
    """Resolve the fold list from a `dilution.factors` block.

    Mirrors the resolver in build_vial_dilution_print.py / validate_vial_print.py so
    previews and the dilution count match exactly what the protocol will produce.
    """
    mode = fc.get("mode", "explicit")
    if mode == "explicit":
        return [float(x) for x in fc.get("explicit", [])]
    count = int(fc.get("count", 8))
    start = float(fc.get("start", 1))
    if mode == "geometric":
        step = float(fc.get("step_factor", 2))
        return [round(start * (step ** i), 4) for i in range(count)]
    end = float(fc.get("end", 50))
    if count == 1:
        return [start]
    if mode == "linear":
        return [round(start + (end - start) * i / (count - 1), 4) for i in range(count)]
    lo, hi = math.log(start), math.log(end)
    return [round(math.exp(lo + (hi - lo) * i / (count - 1)), 4) for i in range(count)]


def _num_dilutions(cfg: dict) -> int:
    return len(_resolve_factors(cfg.get("dilution", {}).get("factors", {})))


def _apply_params(
    cfg: dict,
    num_dilutions: Optional[int] = None,
    droplet_volume_ul: Optional[float] = None,
    num_replicates: Optional[int] = None,
    paper_start_column: Optional[int] = None,
    orange_plate_column: Optional[int] = None,
    blue_plate_column: Optional[int] = None,
    advanced_updates: Optional[dict] = None,
    default_folds: Optional[list] = None,
) -> Tuple[dict, list]:
    """Apply the four conversational knobs to a copy of `cfg`.

    Returns (new_cfg, warnings). Pure: does not touch module globals. Keeps
    cv.expected_droplets in sync with the dilution count so the CV check's
    --expect always matches the number of wells printed.
    """
    cfg = copy.deepcopy(cfg)
    warnings: list = []
    cfg.setdefault("dilution", {}).setdefault("factors", {})
    cfg.setdefault("printing", {})
    cfg.setdefault("cv", {})

    # 1. Number of dilutions == droplets per print pass == wells in the column.
    if num_dilutions is not None:
        n = int(num_dilutions)
        if not (1 <= n <= MAX_DILUTIONS):
            warnings.append(
                f"num_dilutions {n} out of range; clamped to [1, {MAX_DILUTIONS}] "
                f"(a 96-well column has {MAX_DILUTIONS} rows).")
            n = max(1, min(n, MAX_DILUTIONS))
        fc = cfg["dilution"]["factors"]
        base = list(default_folds) if default_folds else list(fc.get("explicit", []))
        if fc.get("mode", "explicit") == "explicit" and n <= len(base):
            # Predictable: the first N canonical folds (e.g. 5 -> 1,2,5,10,20).
            fc["explicit"] = [base[i] for i in range(n)]
        else:
            # Extending past the canonical list (rare; only if a shorter explicit
            # list was set earlier) -> regenerate geometrically.
            fc["mode"] = "geometric"
            fc["count"] = n
            warnings.append(
                f"regenerated {n} folds geometrically (no explicit list of that "
                f"length); set explicit folds via advanced_updates to override.")
        cfg["cv"]["expected_droplets"] = n

    # 2. Droplet volume (per channel, per replicate).
    if droplet_volume_ul is not None:
        v = float(droplet_volume_ul)
        if not (0 < v <= MAX_DROPLET_UL):
            warnings.append(
                f"droplet_volume_ul {v} out of (0, {MAX_DROPLET_UL}]; clamped.")
            v = max(0.1, min(v, MAX_DROPLET_UL))
        if v > 50:
            warnings.append(
                f"droplet_volume_ul {v} is large; start around 30 uL for reliable "
                f"P300 GEN2 release, then increase only if the paper tolerates it.")
        cfg["printing"]["droplet_volume_ul"] = v

    # 3. Number of replicates (how many times the column is printed across paper).
    if num_replicates is not None:
        r = int(num_replicates)
        if r < 1:
            warnings.append(f"num_replicates {r} < 1; set to 1.")
            r = 1
        cfg["printing"]["num_replicates"] = r
        for series in cfg.get("color_series", []) or []:
            series["num_replicates"] = r

    # 4. Paper start column.
    if paper_start_column is not None:
        col = int(paper_start_column)
        if not (1 <= col <= 12):
            warnings.append(f"paper_start_column {col} out of range; clamped to [1, 12].")
            col = max(1, min(col, 12))
        cfg["printing"]["paper_start_column"] = col

    # 5. Per-color plate columns for the dual-color workflow.
    requested_color_columns = {
        "orange": orange_plate_column,
        "blue": blue_plate_column,
    }
    for color, requested_col in requested_color_columns.items():
        if requested_col is None:
            continue
        col = int(requested_col)
        if not (1 <= col <= 12):
            warnings.append(f"{color}_plate_column {col} out of range; clamped to [1, 12].")
            col = max(1, min(col, 12))
        matched = False
        for series in cfg.get("color_series", []) or []:
            if str(series.get("name", "")).lower() == color:
                series["destination_column"] = str(col)
                matched = True
                break
        if not matched:
            warnings.append(
                f"could not find {color!r} in color_series; plate column was not changed."
            )

    # 6. Free-form nested overrides (any documented YAML key).
    if advanced_updates:
        cfg = merge_user_updates(cfg, advanced_updates)
        # Re-sync the CV count if the caller changed the factor block directly.
        cfg.setdefault("cv", {})["expected_droplets"] = _num_dilutions(cfg)

    return cfg, warnings


def _paper_columns(start_column, num_replicates) -> str:
    start = int(start_column)
    reps = int(num_replicates)
    end = start + reps - 1
    return str(start) if reps == 1 else f"{start}-{end}"


def _resolve_series(cfg: dict) -> list[dict]:
    """Return explicit color series, or a legacy single-series fallback."""
    pr = cfg.get("printing", {})
    series = copy.deepcopy(cfg.get("color_series") or [])
    if series:
        return series
    sources = cfg.get("sources", {})
    return [{
        "name": "single",
        "dye_vial": sources.get("food_coloring_vial", sources.get("blue_dye_vial", "A2")),
        "destination_column": pr.get("source_column", cfg.get("dilution", {}).get("destination_column", "9")),
        "setup_tip": cfg.get("dilution", {}).get("setup_tip", "H12"),
        "print_block_column": pr.get("print_block_column", 1),
        "paper_start_column": pr.get("paper_start_column", 1),
        "num_replicates": pr.get("num_replicates", 1),
    }]


def _series_summary_line(series: dict) -> str:
    return (
        f"{series.get('name')} vial {series.get('dye_vial')} -> plate column "
        f"{series.get('destination_column')} -> paper columns "
        f"{_paper_columns(series.get('paper_start_column', 1), series.get('num_replicates', 1))}; "
        f"stock tip {series.get('setup_tip')}, print block column "
        f"{series.get('print_block_column')}"
    )


def _soft_validate(cfg: dict) -> list:
    """Cheap, conversational pre-checks. NOT authoritative — the builder's
    validate() and the on-robot _preflight() are the real gates."""
    problems: list = []
    dil = cfg.get("dilution", {})
    pr  = cfg.get("printing", {})
    factors = _resolve_factors(dil.get("factors", {}))
    n = len(factors)
    if n == 0:
        problems.append("no dilution factors resolved")
    if n > MAX_DILUTIONS:
        problems.append(f"{n} dilutions exceeds the {MAX_DILUTIONS}-row column limit")
    total = float(dil.get("total_volume_ul", 0) or 0)
    if total <= 0 or total > MAX_TOTAL_WELL_UL:
        problems.append(f"dilution.total_volume_ul {total} must be in (0, {MAX_TOTAL_WELL_UL}]")
    for f in factors:
        if f <= 0:
            problems.append(f"fold {f} must be > 0")
        elif total and round(total / f, 2) > MAX_DROPLET_UL:
            problems.append(f"fold {f}: stock {round(total/f,2)} uL > pipette max {MAX_DROPLET_UL}")
    # tip feasibility: current workflow uses one setup tip for all vial transfers.
    reserved = int(pr.get("print_block_column", 1))
    setup_tip = str(dil.get("setup_tip", "")).upper()
    if setup_tip:
        setup_col_s = "".join(ch for ch in setup_tip if ch.isdigit())
        if not setup_col_s:
            problems.append(f"dilution.setup_tip {setup_tip!r} must include a column number")
        elif int(setup_col_s) == reserved:
            problems.append(f"dilution.setup_tip {setup_tip} overlaps print_block_column {reserved}")
    else:
        single = [int(c) for c in dil.get("single_tip_columns", []) if int(c) != reserved]
        n_tips = 8 * len(single)
        if factors and n_tips < 1:
            problems.append("single_tip_columns must supply at least one setup tip")
    if reserved in [int(c) for c in dil.get("single_tip_columns", [])]:
        problems.append(
            f"printing.print_block_column {reserved} overlaps single_tip_columns")
    return problems


def _summary(cfg: dict) -> str:
    dil = cfg.get("dilution", {})
    pr  = cfg.get("printing", {})
    deck = cfg.get("deck", {})
    sources = cfg.get("sources", {})
    series = _resolve_series(cfg)
    factors = _resolve_factors(dil.get("factors", {}))
    folds = ", ".join(f"{f:g}x" for f in factors)
    rm = cfg.get("run_modes", {})
    series_lines = "\n".join(f"    - {_series_summary_line(item)}" for item in series)
    reps_summary = ", ".join(
        f"{item.get('name')}={item.get('num_replicates')}" for item in series
    )
    return (
        "Vial-dilution-print working config\n"
        f"  Deck           : vial rack slot {deck.get('tuberack', {}).get('slot')}, "
        f"plate slot {deck.get('plate', {}).get('slot')}, paper slot "
        f"{deck.get('paper', {}).get('slot')}, tips slot {deck.get('tiprack', {}).get('slot')}\n"
        f"  Vials          : water {sources.get('water_vial')}, "
        f"orange {sources.get('orange_dye_vial')}, blue {sources.get('blue_dye_vial')}\n"
        f"  Dilutions      : {len(factors)} wells per color series (folds: {folds})\n"
        f"  Color series   :\n{series_lines}\n"
        f"  Total / well   : {dil.get('total_volume_ul')} uL, mix {dil.get('mix_reps')}x\n"
        f"  Droplet volume : {pr.get('droplet_volume_ul')} uL/channel\n"
        f"  Replicates     : {reps_summary} (x-spacing "
        f"{pr.get('replicate_spacing_mm', {}).get('x')} mm)\n"
        f"  Print release  : air gap {pr.get('air_gap_ul')} uL, z {pr.get('dispense_z_mm')} mm, "
        f"blow_out={pr.get('blow_out')}, dwell={pr.get('post_dispense_delay_s')} s\n"
        f"  Droplets/print : {len(factors)}  (CV expects {cfg.get('cv', {}).get('expected_droplets')})\n"
        f"  Return tips    : {cfg.get('tips', {}).get('return_tips')}\n"
        f"  Run modes      : dry_run={rm.get('dry_run')} do_dilution={rm.get('do_dilution')} "
        f"do_print={rm.get('do_print')}"
    )


def _write_user_yaml(cfg: dict) -> Path:
    """Write the working config to a timestamped user YAML (committed default untouched)."""
    USER_WORKFLOW_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = USER_WORKFLOW_CONFIG_DIR / f"{WORKFLOW_NAME}_{ts}.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, sort_keys=False)
    return path


def _template_slug(name: str) -> str:
    slug = _TEMPLATE_NAME_RE.sub("_", (name or "").strip()).strip("._-").lower()
    if not slug:
        raise ValueError("template name must include at least one letter or number")
    if not slug.endswith((".yaml", ".yml")):
        slug += ".yaml"
    return slug


def _template_path(name: str) -> Path:
    slug = _template_slug(name)
    path = (TEMPLATE_DIR / slug).resolve()
    root = TEMPLATE_DIR.resolve()
    if root not in path.parents and path != root:
        raise ValueError(f"template path escaped template directory: {name!r}")
    return path


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _template_brief(path: Path) -> str:
    try:
        cfg = _load_yaml(path)
    except Exception as exc:
        return f"- {path.name}: unreadable ({exc})"
    series = "; ".join(_series_summary_line(item) for item in _resolve_series(cfg))
    factors = _resolve_factors(cfg.get("dilution", {}).get("factors", {}))
    pr = cfg.get("printing", {})
    return (
        f"- {path.name}: {len(factors)} dilution(s), "
        f"{pr.get('droplet_volume_ul')} uL droplets, {series}"
    )


def _record_pass(protocol_path: Path, sha256: str, output: str) -> None:
    """Write a PASS record to simulations.json (same schema/path that
    execute_protocol_on_robot in src/agents/tools.py reads from), so the robot
    execution hash-gate is satisfied for this generated artifact."""
    records: dict = {}
    if SIMULATION_RECORDS_PATH.exists():
        try:
            records = json.loads(SIMULATION_RECORDS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            records = {}
    records[sha256] = {
        "path": str(protocol_path),
        "timestamp": datetime.now().isoformat(),
        "status": "PASS",
        "result": output[:2000],
    }
    SIMULATION_RECORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIMULATION_RECORDS_PATH.write_text(json.dumps(records, indent=2), encoding="utf-8")


def _run(cmd: list) -> subprocess.CompletedProcess:
    """Run a pipeline script in the current interpreter from the repo root."""
    return subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


# ════════════════════════════════════════════════════════════════════════════════
# LangChain tools (thin wrappers over the helpers + module working state).
# ════════════════════════════════════════════════════════════════════════════════

@tool
def load_vial_print_defaults() -> str:
    """Load the default vial-dilution-print configuration into the working session.

    Call this FIRST. Reads configs/workflows/defaults/vial_dilution_print.yaml into
    an in-memory working copy and returns a human-readable summary of the current
    dilutions, droplet volume, and replicates. The committed default file is never
    modified.
    """
    global _WORKING, _DEFAULT_FOLDS, _LAST_USER_YAML, _LAST_SHA
    try:
        cfg = yaml.safe_load(DEFAULT_YAML.read_text(encoding="utf-8")) or {}
    except OSError as e:
        return f"Error loading default config: {e}"
    _WORKING = cfg
    _DEFAULT_FOLDS = _resolve_factors(cfg.get("dilution", {}).get("factors", {}))
    _LAST_USER_YAML = None
    _LAST_SHA = None
    return "Defaults loaded.\n\n" + _summary(cfg)


@tool
def list_vial_print_templates() -> str:
    """List reusable vial-dilution-print YAML templates.

    Templates live under configs/workflows/templates/vial_dilution_print/.
    Unlike timestamped run YAMLs in configs/workflows/user/, templates are meant
    to be named, reusable, and optionally committed to Git.
    """
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    templates = sorted(TEMPLATE_DIR.glob("*.y*ml"))
    if not templates:
        return (
            "No reusable vial-print templates found.\n"
            f"Template directory: {TEMPLATE_DIR.relative_to(PROJECT_ROOT)}\n"
            "Use save_vial_print_template after loading or editing a plan."
        )
    lines = [
        "Reusable vial-print templates:",
        f"Directory: {TEMPLATE_DIR.relative_to(PROJECT_ROOT)}",
    ]
    lines.extend(_template_brief(path) for path in templates)
    return "\n".join(lines)


@tool
def save_vial_print_template(name: str, overwrite: bool = False) -> str:
    """Save the current working vial-print config as a reusable named YAML template.

    Args:
        name: Human-friendly template name. It is converted to a safe .yaml file
            name, e.g. "orange12_blue9" -> orange12_blue9.yaml.
        overwrite: Set true to replace an existing template of the same name.

    This saves the current in-memory working config. It does not edit the
    committed default YAML and does not run the robot.
    """
    if _WORKING is None:
        return "Error: no config loaded. Call load_vial_print_defaults or load_vial_print_template first."
    try:
        path = _template_path(name)
    except ValueError as exc:
        return f"Error: {exc}"
    if path.exists() and not overwrite:
        return (
            f"Template already exists: {path.relative_to(PROJECT_ROOT)}\n"
            "Call save_vial_print_template with overwrite=true or choose a new name."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(_WORKING, sort_keys=False), encoding="utf-8")
    return (
        f"Template saved: {path.relative_to(PROJECT_ROOT)}\n\n"
        + _summary(_WORKING)
    )


@tool
def load_vial_print_template(name: str) -> str:
    """Load a reusable named vial-print YAML template into the working session.

    Args:
        name: Template file name or stem from
            configs/workflows/templates/vial_dilution_print/.

    The loaded template becomes the current working config for later edits,
    build, simulation, validation, and mock CV.
    """
    global _WORKING, _DEFAULT_FOLDS, _LAST_USER_YAML, _LAST_SHA
    try:
        path = _template_path(name)
    except ValueError as exc:
        return f"Error: {exc}"
    if not path.exists():
        return (
            f"Template not found: {path.relative_to(PROJECT_ROOT)}\n\n"
            + list_vial_print_templates.invoke({})
        )
    try:
        cfg = _load_yaml(path)
    except OSError as exc:
        return f"Error loading template {path.relative_to(PROJECT_ROOT)}: {exc}"
    _WORKING = cfg
    _DEFAULT_FOLDS = _resolve_factors(cfg.get("dilution", {}).get("factors", {}))
    _LAST_USER_YAML = None
    _LAST_SHA = None
    return (
        f"Template loaded: {path.relative_to(PROJECT_ROOT)}\n\n"
        + _summary(cfg)
    )


@tool
def update_vial_print_params(
    num_dilutions: Optional[int] = None,
    droplet_volume_ul: Optional[float] = None,
    num_replicates: Optional[int] = None,
    paper_start_column: Optional[int] = None,
    orange_plate_column: Optional[int] = None,
    blue_plate_column: Optional[int] = None,
    advanced_updates: Optional[dict] = None,
) -> str:
    """Adjust the demo's parameters from natural language.

    Args:
        num_dilutions: How many dilution wells / simultaneous droplets per print
            (1-8). Sets the first N canonical fold factors and keeps the CV
            expected-droplet count in sync.
        droplet_volume_ul: Volume dispensed per channel per replicate (uL).
        num_replicates: How many times the column is printed across the paper (>=1).
        paper_start_column: Leftmost paper column for the first replicate (1-12).
        orange_plate_column: 96-well plate column for the orange dilution series (1-12).
        blue_plate_column: 96-well plate column for the blue dilution series (1-12).
        advanced_updates: Optional nested dict for any other documented YAML key,
            e.g. {"dilution": {"total_volume_ul": 180}} or
            {"dilution": {"factors": {"explicit": [1, 3, 9, 27]}}}.

    Returns the updated summary plus any soft-bound warnings. Bounds are
    re-enforced authoritatively by the builder at build time.
    """
    global _WORKING
    if _WORKING is None:
        return "Error: no config loaded. Call load_vial_print_defaults first."
    if all(x is None for x in (
        num_dilutions,
        droplet_volume_ul,
        num_replicates,
        paper_start_column,
        orange_plate_column,
        blue_plate_column,
        advanced_updates,
    )):
        return "No changes requested. Current config:\n\n" + _summary(_WORKING)
    cfg, warnings = _apply_params(
        _WORKING, num_dilutions, droplet_volume_ul, num_replicates,
        paper_start_column, orange_plate_column, blue_plate_column,
        advanced_updates, default_folds=_DEFAULT_FOLDS)
    _WORKING = cfg
    out = ["Config updated.\n", _summary(cfg)]
    soft = _soft_validate(cfg)
    if warnings:
        out.append("\nNotes:\n" + "\n".join(f"  - {w}" for w in warnings))
    if soft:
        out.append("\nSoft-check warnings (the builder will enforce these):\n"
                    + "\n".join(f"  - {p}" for p in soft))
    return "\n".join(out)


@tool
def preview_dilution_plan() -> str:
    """Show the resolved dilution series: per-well fold, stock uL, and water uL.

    Lets the user sanity-check the numbers before building. Does not run anything
    on the robot.
    """
    if _WORKING is None:
        return "Error: no config loaded. Call load_vial_print_defaults first."
    dil = _WORKING.get("dilution", {})
    total = float(dil.get("total_volume_ul", 0) or 0)
    factors = _resolve_factors(dil.get("factors", {}))
    series = _resolve_series(_WORKING)
    water_vial = _WORKING.get("sources", {}).get("water_vial")
    lines = [
        f"Dilution plan ({total:g} uL/well, water vial {water_vial}; folds shared by each color):"
    ]
    for item in series:
        col = item.get("destination_column", dil.get("destination_column", "1"))
        lines.append(
            f"  {item.get('name')} series: dye vial {item.get('dye_vial')} -> "
            f"plate column {col}; paper columns "
            f"{_paper_columns(item.get('paper_start_column', 1), item.get('num_replicates', 1))}"
        )
        for i, f in enumerate(factors):
            row = chr(ord("A") + i)
            stock = round(total / f, 2) if f else 0.0
            water = round(total - stock, 2)
            flag = "  (below ~20 uL accurate min)" if 0 < stock < 20 else ""
            lines.append(
                f"    {row}{col}: {f:g}x  {item.get('name')} stock={stock:g} uL  "
                f"water={water:g} uL{flag}"
            )
    return "\n".join(lines)


@tool
def show_vial_print_config() -> str:
    """Dump the full current working configuration as YAML (for review/debug)."""
    if _WORKING is None:
        return "Error: no config loaded. Call load_vial_print_defaults first."
    return yaml.dump(_WORKING, sort_keys=False)


@tool
def build_vial_print_protocol() -> str:
    """Build a robot-ready protocol from the current working config and simulate it.

    Writes the working config to a timestamped user YAML, runs
    scripts/build_vial_dilution_print.py (which embeds the YAML as CONFIG and runs
    the Opentrons simulator), and reports SIMULATION OK / FAILED. On success it
    records a PASS in simulations.json for the generated artifact so a later robot
    run is permitted. Returns the generated protocol path and SHA256.
    """
    global _WORKING, _LAST_USER_YAML, _LAST_SHA
    if _WORKING is None:
        return "Error: no config loaded. Call load_vial_print_defaults first."

    user_yaml = _write_user_yaml(_WORKING)
    _LAST_USER_YAML = user_yaml
    proc = _run([sys.executable, str(BUILD_SCRIPT), "--config", str(user_yaml)])
    output = proc.stdout + "\n" + proc.stderr

    if "CONFIG VALIDATION FAILED" in output:
        fails = "\n".join(l for l in output.splitlines() if l.strip().startswith("-"))
        return (f"BUILD FAILED — config rejected by validate():\n{fails}\n\n"
                f"User YAML: {user_yaml.relative_to(PROJECT_ROOT)}")
    if "SIMULATION OK" not in output:
        return (f"BUILD/SIMULATION FAILED.\nUser YAML: "
                f"{user_yaml.relative_to(PROJECT_ROOT)}\n\n{output[-1500:]}")

    sha = hash_file(GENERATED_LATEST)
    _LAST_SHA = sha
    _record_pass(GENERATED_LATEST, sha, output)
    key = "\n".join(l for l in output.splitlines()
                    if any(k in l for k in ("Series:", "Printing", "Returned", "WARNING")))
    return (
        "SIMULATION OK.\n"
        f"  User YAML : {user_yaml.relative_to(PROJECT_ROOT)}\n"
        f"  Protocol  : {GENERATED_LATEST.relative_to(PROJECT_ROOT)}\n"
        f"  SHA256    : {sha}\n\n{key}"
    )


@tool
def validate_vial_print_matrix() -> str:
    """Run the 5-case run-mode matrix gate on the generated protocol.

    Runs scripts/validate_vial_print.py against the last-built user config so the
    full_run / dry_run / dilution_only / print_only / wrong_labware cases assert
    against the current parameters. Returns ALL CASES PASSED or the per-case
    failures. Call build_vial_print_protocol first.
    """
    if _LAST_USER_YAML is None:
        return "Error: build a protocol first (build_vial_print_protocol)."
    cmd = [sys.executable, str(VALIDATE_SCRIPT), "--config", str(_LAST_USER_YAML)]
    proc = _run(cmd)
    output = proc.stdout + "\n" + proc.stderr
    verdict = [l for l in output.splitlines() if l.startswith("[PASS]") or l.startswith("[FAIL]")]
    tail = "\n".join(verdict)
    if "ALL CASES PASSED" in output:
        return f"ALL CASES PASSED.\n{tail}"
    return f"VALIDATION FAILED.\n{tail}\n\n{output[-1200:]}"


@tool
def verify_print_droplets_mock() -> str:
    """Run host-side droplet computer-vision QC in mock mode.

    Synthesizes an N-droplet gradient image (N = current dilution count) and
    verifies the detector finds exactly N round droplets — a no-camera sanity check
    of the print stage. Returns PASS/FAIL. Call build_vial_print_protocol first so
    N is known.
    """
    if _WORKING is None:
        return "Error: no config loaded. Call load_vial_print_defaults first."
    n = _num_dilutions(_WORKING)
    cmd = [sys.executable, str(CV_SCRIPT), "--mock", "--expect", str(n)]
    proc = _run(cmd)
    output = proc.stdout + "\n" + proc.stderr
    ok = proc.returncode == 0   # verify_print_droplets.py exits 0 only on overall PASS
    tail = "\n".join(output.splitlines()[-12:])
    return f"CV {'PASS' if ok else 'FAIL'} (expected {n} droplets).\n\n{tail}"


# Exposed for tests / the agent — read-only accessors into working state.
def get_working_config() -> Optional[dict]:
    return copy.deepcopy(_WORKING) if _WORKING is not None else None


def get_last_user_yaml() -> Optional[Path]:
    return _LAST_USER_YAML
