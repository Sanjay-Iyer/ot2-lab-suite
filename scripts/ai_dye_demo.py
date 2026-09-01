#!/usr/bin/env python3
"""Conversational OT-2 demo: dilute a dye series, then print it onto paper.

    python scripts/ai_dye_demo.py --simulate  # local only, no robot contact
    python scripts/ai_dye_demo.py             # real robot after RUN LIVE

The agent introduces itself, asks what you want, and edits a timestamped YAML copy
from plain language. Robot Python stays deterministic: the LLM never writes code,
never touches the pipette, the calibrated print-release geometry or the safety
limits, and every edit is validated before it is accepted.

The workflow itself is protocol v19
(src/protocols/printing/13_ai_agent_dilution_print_demo.py) — the P20-only dilute
-> mix -> print path from v6, with the validated release cycle from the recent
printing scripts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.core.config import Config  # noqa: E402
from src.core.config_loader import merge_user_updates  # noqa: E402

DEFAULT_CONFIG = REPO / "configs/workflows/defaults/ai_agent_dilution_print_demo.yaml"
USER_CONFIG_DIR = REPO / "configs/workflows/user"
RUN_ROOT = REPO / "runs/ai_dye_demo"
# API 2.15 needs an interpreter that actually has opentrons 7.0.2 installed.
PINNED_SIMULATOR = REPO / ".venv" / "ot2-api-2.15-py310" / "python.exe"

RULE = "=" * 78
THIN = "-" * 78
ROWS = tuple("ABCDEFGH")

# Sections the agent may rewrite. Everything else — the pipette, the calibrated
# release geometry, flow rates, safety limits, run modes — is laboratory-owned.
ALLOWED_ROOTS = {"deck", "materials", "dilution", "mixing", "print", "tips"}
LOCKED_KEYS = (
    ("print", "z_mm"),
    ("print", "aspirate_height_mm"),
    ("print", "air_gap_ul"),
    ("print", "air_gap_height_mm"),
    ("print", "push_out_ul"),
    ("print", "blow_out"),
    ("print", "post_dispense_delay_s"),
    ("print", "paper_columns"),
    ("dilution", "max_transfer_ul"),
)
IMMUTABLE_ROOTS = ("pipette", "safety", "flow_rates", "run_modes", "protocol_version")

# "I have no idea what to ask for" — show the standard example and explain it.
UNSURE = re.compile(
    r"\b(i (don'?t|do not) know|not sure|no idea|whatever|anything|default|"
    r"example|standard|typical|suggest|recommend|surprise me|you (pick|choose|decide))\b",
    re.I,
)

SYSTEM_PROMPT = """You edit the YAML configuration for one OT-2 demo: a dye is
diluted in water across a fold series down one column of a 96-well plate, then
every one of those dilutions is printed onto paper as droplets. A single-channel
P20 does all of it.

Return ONLY JSON:
{"updates": {nested YAML keys to merge}, "explanation": "one short sentence"}

Keep unmentioned values unchanged. Allowed update roots: deck, materials,
dilution, mixing, print, tips. Never return Python or shell commands.

Mappings:
- how many dilutions -> dilution.factors, a list of that many fold factors
  (1 to 8 of them, each >= 1; 1 means undiluted stock)
- which plate column -> dilution.plate_column ("1" to "12")
- which plate row the series starts on -> dilution.start_row (A-H); the series
  runs down from there, so start_row plus the number of dilutions must not pass H
- how much liquid per dilution -> dilution.total_volume_ul (up to 340)
- dye vial / water vial -> materials.dye.vial / materials.water.vial (A1-B4)
- drop volume -> print.droplet_volume_ul, 1-18.5 uL; a list prints the same
  dilutions at several volumes, one paper column each
- stacked drops on one spot -> print.droplets_per_spot
- repeat columns of the same volume -> print.replicates
- where printing starts on the paper -> print.paper_start_column (1-12)
- deck position of anything -> deck.<tuberack|plate|paper|tiprack>.slot (1-11)
- first tip to use -> tips.start_tip; keep used tips -> tips.return_tips
- mixing before each print aspiration -> mixing.reps / mixing.volume_ul
- "only dilute, do not print" -> print.enabled false
- "the dilutions are already made" -> dilution.enabled false

Never alter the pipette, safety limits, flow rates, run modes, protocol_version,
or anything under print that describes release geometry (z_mm,
aspirate_height_mm, air_gap_ul, air_gap_height_mm, push_out_ul, blow_out,
post_dispense_delay_s, paper_columns) — those are calibrated on the instrument.
"""

GREETING = """agent> Hello. I am the AI agent in control of the OT-2.

       I can do two things, and I can do them together:
         1. DILUTIONS - make a series of dilutions of a dye stock down one
            column of a 96-well plate.
         2. PRINTING  - print those dilutions onto paper as droplets, one
            paper row per dilution.

       Tell me what you would like to run. For example:
         "make 8 dilutions in column 11 and print them at 5 uL"
         "4 dilutions, 10 uL drops, start printing at paper column 3"
         "move the plate to slot 6 and use dye vial A3"
         "three drops stacked on each spot"

       If you are not sure what to ask for, just say "I don't know" and I will
       start from a standard example that you can then adjust.

       Commands: plan (show the plan), show (raw YAML), run, help, quit."""

HELP = """agent> Say what you want changed, in plain language. Things I can change:
         how many dilutions, and how strong each one is
         which plate column and starting row they go in
         how much liquid is in each dilution
         which vial the dye and the water are in
         the drop volume, how many drops per spot, how many repeat columns
         where on the paper the printing starts
         which deck slot each piece of labware sits in
         which tip to start from, and whether tips are returned or binned

       I will not change the pipette, the calibrated print heights and air
       handling, or the safety limits - those are set on the instrument.

       Commands: plan, show, run, help, quit."""


# ── plan arithmetic ───────────────────────────────────────────────────────────────

def _factors(config: dict[str, Any]) -> list[float]:
    return [float(value) for value in config["dilution"]["factors"]]


def _dilution_rows(config: dict[str, Any]) -> list[str]:
    """The plate rows the series occupies, one per factor, from start_row down."""
    start = str(config["dilution"].get("start_row", "A")).upper()
    if start not in ROWS:
        return []
    first = ROWS.index(start)
    return list(ROWS[first : first + len(_factors(config))])


def _droplet_volumes(config: dict[str, Any]) -> list[float]:
    """print.droplet_volume_ul as a list, whether it was written as one or not."""
    raw = config["print"]["droplet_volume_ul"]
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    return [float(value) for value in values]


def _paper_columns(config: dict[str, Any]) -> list[dict[str, Any]]:
    """One paper column per droplet volume x replicate, left to right."""
    printing = config["print"]
    start = int(printing.get("paper_start_column", 1))
    replicates = int(printing.get("replicates", 1))
    droplets = int(printing.get("droplets_per_spot", 1))
    spots: list[dict[str, Any]] = []
    column = start
    for volume in _droplet_volumes(config):
        for replicate in range(1, replicates + 1):
            spots.append({"column": column, "volume_ul": volume,
                          "droplets": droplets, "replicate": replicate})
            column += 1
    return spots


def _tip_names(config: dict[str, Any], count: int) -> list[str]:
    order = [f"{row}{column}" for column in range(1, 13) for row in ROWS]
    start = str(config["tips"].get("start_tip", "A1")).upper()
    if start not in order:
        return []
    return order[order.index(start) : order.index(start) + count]


def _material(config: dict[str, Any], role: str) -> tuple[str, dict[str, Any]]:
    for name, spec in config.get("materials", {}).items():
        if spec.get("role") == role:
            return name, spec
    return "", {}


# ── validation (fast edit-time mirror of the protocol's own pre-flight) ───────────

def _config_problems(config: dict[str, Any]) -> list[str]:
    """Reject an edit before it is written. The protocol pre-flight stays the
    authority; this exists so the agent can explain the refusal in the chat."""
    problems: list[str] = []
    required = ("deck", "pipette", "materials", "dilution", "mixing", "print",
                "tips", "safety")
    missing = [name for name in required if name not in config]
    if missing:
        return ["missing top-level section(s): " + ", ".join(missing)]

    deck = config["deck"]
    try:
        slots = {role: int(deck[role]["slot"]) for role in
                 ("tuberack", "plate", "paper", "tiprack")}
    except (KeyError, TypeError, ValueError):
        return ["deck must define tuberack, plate, paper and tiprack, each with a slot"]
    for role, slot in slots.items():
        if not 1 <= slot <= 11:
            problems.append(f"deck.{role}.slot must be 1-11 (12 is the trash), got {slot}")
    if len(set(slots.values())) != len(slots):
        problems.append(f"two things cannot share a deck slot: {slots}")

    safety = config["safety"]
    p20_max = float(safety.get("p20_max_volume_ul", 20.0))
    p20_min = float(safety.get("p20_min_volume_ul", 1.0))
    max_fill = float(safety.get("max_well_fill_ul", 340.0))

    materials = config["materials"]
    vial_pattern = re.compile(r"^[AB][1-4]$")
    for role in ("solvent", "sample"):
        matches = [name for name, spec in materials.items() if spec.get("role") == role]
        if len(matches) != 1:
            problems.append(f"exactly one material must have role {role!r}, got {matches}")
    vials = [str(spec.get("vial", "")).upper() for spec in materials.values()]
    for name, spec in materials.items():
        if not vial_pattern.fullmatch(str(spec.get("vial", "")).upper()):
            problems.append(f"materials.{name}.vial must be A1-B4 on the 8-vial rack")
    if len(set(vials)) != len(vials):
        problems.append("the dye and the water must be in different vials")

    dilution = config["dilution"]
    try:
        factors = _factors(config)
    except (TypeError, ValueError):
        problems.append("dilution.factors must be a list of numbers")
        factors = []
    if not 1 <= len(factors) <= 8:
        problems.append(f"1 to 8 dilutions are possible, got {len(factors)}")
    if any(factor < 1 for factor in factors):
        problems.append("every dilution factor must be 1x or greater (1x is neat stock)")
    start_row = str(dilution.get("start_row", "A")).upper()
    if start_row not in ROWS:
        problems.append(f"dilution.start_row must be A-H, got {start_row!r}")
    elif ROWS.index(start_row) + len(factors) > len(ROWS):
        problems.append(
            f"{len(factors)} dilutions starting at row {start_row} run past row H"
        )
    column = str(dilution.get("plate_column", ""))
    if not column.isdigit() or not 1 <= int(column) <= 12:
        problems.append(f"dilution.plate_column must be 1-12, got {column!r}")
    total = float(dilution.get("total_volume_ul", 0) or 0)
    if not 0 < total <= max_fill:
        problems.append(f"dilution.total_volume_ul must be in (0, {max_fill:g}], got {total:g}")
    for factor in factors:
        if factor >= 1 and total > 0 and total / factor < p20_min:
            problems.append(
                f"{factor:g}x would need {total / factor:.2f} uL of dye, under the "
                f"P20's {p20_min:g} uL minimum; use a smaller fold factor or more "
                f"total volume"
            )

    mixing = config["mixing"]
    if not 0 < float(mixing.get("volume_ul", 0) or 0) <= p20_max:
        problems.append(f"mixing.volume_ul must be in (0, {p20_max:g}]")
    if int(mixing.get("reps", 0) or 0) < 1:
        problems.append("mixing.reps must be at least 1")

    printing = config["print"]
    air_gap = float(printing.get("air_gap_ul", 0.0) or 0.0)
    try:
        volumes = _droplet_volumes(config)
    except (TypeError, ValueError):
        problems.append("print.droplet_volume_ul must be a number or a list of numbers")
        volumes = []
    if not volumes:
        problems.append("print.droplet_volume_ul must name at least one volume")
    for volume in volumes:
        if volume < p20_min:
            problems.append(
                f"a {volume:g} uL drop is under the P20's {p20_min:g} uL minimum"
            )
        elif volume + air_gap > p20_max:
            problems.append(
                f"a {volume:g} uL drop plus the {air_gap:g} uL air gap is "
                f"{volume + air_gap:g} uL, over the P20's {p20_max:g} uL"
            )
    if int(printing.get("replicates", 1) or 0) < 1:
        problems.append("print.replicates must be at least 1")
    if int(printing.get("droplets_per_spot", 1) or 0) < 1:
        problems.append("print.droplets_per_spot must be at least 1")
    paper_width = int(printing.get("paper_columns", 12))
    for spot in _paper_columns(config):
        if not 1 <= spot["column"] <= paper_width:
            problems.append(
                f"the print plan needs paper column {spot['column']}, past the "
                f"paper's {paper_width} columns; start further left or use fewer "
                f"volumes/replicates"
            )
            break

    needed = 2 + len(factors)
    tips = _tip_names(config, needed)
    if not tips:
        problems.append(f"tips.start_tip {config['tips'].get('start_tip')!r} is not a rack position")
    elif len(tips) < needed:
        problems.append(
            f"this plan needs {needed} tips but only {len(tips)} remain from "
            f"{config['tips'].get('start_tip')}; start from an earlier tip"
        )
    return problems


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the model did not return a JSON object")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict) or not isinstance(value.get("updates"), dict):
        raise ValueError("the model response must contain an 'updates' object")
    return value


def _validate_ai_update(before: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    blocked = sorted(set(updates) - ALLOWED_ROOTS)
    if blocked:
        raise ValueError("blocked top-level update(s): " + ", ".join(blocked))
    for root, key in LOCKED_KEYS:
        if isinstance(updates.get(root), dict) and key in updates[root]:
            raise ValueError(
                f"{root}.{key} is calibrated on the instrument and cannot be changed here"
            )
    candidate = merge_user_updates(deepcopy(before), updates)
    for fixed in IMMUTABLE_ROOTS:
        if candidate.get(fixed) != before.get(fixed):
            raise ValueError(f"{fixed} cannot be changed by the agent")
    problems = _config_problems(candidate)
    if problems:
        raise ValueError("that would not be a valid run:\n- " + "\n- ".join(problems))
    return candidate


# ── the plan the human reads before anything happens ──────────────────────────────

def render_plan(config: dict[str, Any], *, simulate: bool, config_path: Path) -> str:
    deck, dilution, printing = config["deck"], config["dilution"], config["print"]
    solvent_name, solvent = _material(config, "solvent")
    sample_name, sample = _material(config, "sample")
    factors = _factors(config)
    rows = _dilution_rows(config)
    spots = _paper_columns(config)
    total = float(dilution["total_volume_ul"])
    column = str(dilution["plate_column"])
    do_dilution = bool(dilution.get("enabled", True))
    do_print = bool(printing.get("enabled", True))
    try:
        display_path = config_path.relative_to(REPO)
    except ValueError:
        display_path = config_path

    lines = [
        THIN, "THE PLAN", THIN,
        f"Mode        : {'SIMULATION - nothing contacts the robot' if simulate else 'LIVE - the real OT-2 will move'}",
        f"Config      : {display_path}",
        f"Instrument  : {config['pipette']['name']} on the {config['pipette']['mount']} mount",
        "", "DECK",
        f"  slot {deck['tuberack']['slot']:>2}   vial rack    {deck['tuberack']['load_name']}",
        f"  slot {deck['plate']['slot']:>2}   well plate   {deck['plate']['load_name']}",
        f"  slot {deck['paper']['slot']:>2}   paper        {deck['paper']['load_name']}",
        f"  slot {deck['tiprack']['slot']:>2}   tip rack     {deck['tiprack']['load_name']}",
        "", "LIQUIDS",
        f"  sample  : {sample_name:<8} in vial {sample.get('vial', '?')}",
        f"  solvent : {solvent_name:<8} in vial {solvent.get('vial', '?')}",
        "",
    ]

    header = f"STEP 1 - DILUTIONS   {len(factors)} wells in plate column {column}, {total:g} uL each"
    lines.append(header if do_dilution else header + "   [SKIPPED: already prepared]")
    lines.append("  well     fold        dye       water")
    for row, factor in zip(rows, factors):
        dye_ul = total / factor
        water_ul = total - dye_ul
        lines.append(
            f"  {row + column:<6}  {factor:>5g}x  {dye_ul:>8.2f} uL  {water_ul:>8.2f} uL"
        )

    lines.append("")
    drops = len(rows) * sum(spot["droplets"] for spot in spots)
    fluid = len(rows) * sum(spot["droplets"] * spot["volume_ul"] for spot in spots)
    header = "STEP 2 - PRINTING    each dilution prints on its own paper row"
    lines.append(header if do_print else header + "   [SKIPPED: dilutions only]")
    row_span = f"rows {rows[0]}-{rows[-1]}" if len(rows) > 1 else f"row {rows[0]}" if rows else "no rows"
    for spot in spots:
        stack = f"{spot['droplets']} drops stacked" if spot["droplets"] > 1 else "1 drop"
        lines.append(
            f"  paper column {spot['column']:<3} {spot['volume_ul']:>5g} uL   "
            f"{stack}   on {row_span}"
        )
    lines += [
        f"  total drops  : {drops}",
        f"  printed fluid: {fluid:g} uL",
        f"  mixed {int(config['mixing']['reps'])}x before every aspiration; "
        f"{float(printing['post_dispense_delay_s']):g} s dwell after every drop",
        "",
        f"TIPS         {2 + len(rows)} tips from {config['tips'].get('start_tip', 'A1')} "
        f"(1 water, 1 dye, 1 per printed dilution), "
        f"{'returned to the rack' if config['tips'].get('return_tips') else 'dropped in the trash'}",
        "", "ON CONFIRMATION",
        "  build this YAML into the protocol, simulate every movement locally,"
        if simulate else
        "  build this YAML, simulate it, then upload and play it on the OT-2,",
        "  and report the result. No robot is contacted."
        if simulate else
        "  watching the run until it finishes.",
        THIN,
    ]
    return "\n".join(lines)


# ── session plumbing ──────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SessionLog:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "session.log"

    def write(self, event: str, **fields: Any) -> None:
        record = {"timestamp_utc": _now(), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def _write_config(path: Path, config: dict[str, Any]) -> None:
    header = (
        "# Agent-edited working copy; review before execution.\n"
        f"# Last written UTC: {_now()}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _message_text(reply: Any) -> str:
    content = getattr(reply, "content", reply)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)


def _ask_llm(llm: Any, config: dict[str, Any], request: str) -> dict[str, Any]:
    current = yaml.safe_dump(config, sort_keys=False)
    reply = llm.invoke([
        ("system", SYSTEM_PROMPT),
        ("human", f"Current YAML:\n{current}\n\nScientist request:\n{request}"),
    ])
    return _extract_json(_message_text(reply))


def _simulator_python() -> str | None:
    """An interpreter that can actually simulate an API 2.15 protocol."""
    from_env = os.environ.get("OT2_API_2_15_PYTHON")
    if from_env:
        return from_env
    if PINNED_SIMULATOR.exists():
        return str(PINNED_SIMULATOR)
    return None


def _run_stream(command: list[str], log: SessionLog) -> int:
    log.write("command_started", command=command)
    process = subprocess.Popen(
        command, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        log.write("command_output", line=line.rstrip("\r\n"))
    code = process.wait()
    log.write("command_finished", exit_code=code)
    return code


def _execute_simulation(path: Path, log: SessionLog) -> int:
    command = [sys.executable, "scripts/build_vial_dilution_print.py",
               "--config", str(path)]
    simulator = _simulator_python()
    if simulator:
        command += ["--simulator-python", simulator]
    return _run_stream(command, log)


def _execute_live(path: Path, log: SessionLog, robot_host: str | None) -> int:
    command = [sys.executable, "scripts/run_vial_print_robot.py",
               "--config", str(path), "--live"]
    simulator = _simulator_python()
    if simulator:
        command += ["--simulator-python", simulator]
    if robot_host:
        command += ["--robot-host", robot_host]
    return _run_stream(command, log)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true",
                        help="Local only; never discovers or contacts a robot.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(REPO)),
                        help="Starting YAML; a timestamped working copy is created.")
    parser.add_argument("--request", default=None,
                        help="Optional first natural-language request.")
    parser.add_argument("--robot-host", default=None,
                        help="Override the OT-2 host resolved from configs/robot.yaml.")
    args = parser.parse_args(argv)

    source = Path(args.config)
    source = source if source.is_absolute() else REPO / source
    if not source.is_file():
        raise FileNotFoundError(f"starting config not found: {source}")
    config = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    problems = _config_problems(config)
    if problems:
        raise ValueError("starting config is invalid:\n- " + "\n- ".join(problems))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RUN_ROOT / stamp
    working = USER_CONFIG_DIR / f"ai_dye_demo_{stamp}.yaml"
    log = SessionLog(run_dir)
    _write_config(working, config)
    (run_dir / "starting_config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    mode = "SIMULATION" if args.simulate else "LIVE REAL ROBOT"
    log.write("session_started", mode=mode, source_config=str(source),
              working_config=str(working), llm_auth=Config.describe_llm_auth())
    if not args.simulate:
        auth_error = Config.live_robot_llm_auth_error()
        if auth_error:
            print(auth_error, file=sys.stderr)
            log.write("refused_live_auth", reason=auth_error)
            return 2

    print(f"\n{RULE}\nOT-2 AI AGENT - DILUTIONS AND PRINTING - {mode}\n{RULE}")
    print(Config.describe_llm_auth())
    print(f"Working config : {working.relative_to(REPO)}")
    print(f"Session log    : {log.path.relative_to(REPO)}\n")
    print(GREETING)

    # The model is built on first use, so plan/show/help/run and the worked example
    # still work on a laptop without Vertex credentials.
    llm: Any = None
    pending = args.request
    while True:
        try:
            request = pending if pending is not None else input("\nyou> ").strip()
            pending = None
        except (EOFError, KeyboardInterrupt):
            print("\nStopped. Nothing was executed.")
            log.write("session_stopped")
            return 0
        if not request:
            continue
        low = request.lower()
        if low in {"quit", "exit", "q"}:
            print("Stopped. Nothing was executed.")
            log.write("session_stopped")
            return 0
        if low in {"help", "?"}:
            print(HELP)
            continue
        if low in {"show", "yaml"}:
            print(yaml.safe_dump(config, sort_keys=False))
            continue
        if low in {"plan", "p"}:
            print(render_plan(config, simulate=args.simulate, config_path=working))
            continue
        if low in {"run", "go"}:
            break
        if UNSURE.search(low):
            log.write("user_request", text=request, handled_as="default_example")
            print(
                "\nagent> No problem. Here is the standard example I start from:\n"
                f"       {len(_factors(config))} dilutions of dye down plate column "
                f"{config['dilution']['plate_column']}, then one "
                f"{_droplet_volumes(config)[0]:g} uL drop of each onto paper.\n"
                "       Change any part of it by telling me - the number of\n"
                "       dilutions, the drop volume, the columns, the deck slots -\n"
                "       or type run to go ahead with it as it stands."
            )
            print(render_plan(config, simulate=args.simulate, config_path=working))
            continue

        log.write("user_request", text=request)
        try:
            if llm is None:
                llm = Config.get_llm(temperature=0)
            response = _ask_llm(llm, config, request)
            config = _validate_ai_update(config, response["updates"])
        except Exception as exc:
            print(f"\nagent> I did not change anything, because: {exc}")
            log.write("edit_rejected", error=str(exc))
            continue
        _write_config(working, config)
        explanation = str(response.get("explanation", "Config updated."))
        print(f"\nagent> {explanation}")
        print(render_plan(config, simulate=args.simulate, config_path=working))
        log.write("config_updated", explanation=explanation, updates=response["updates"])

    print("\nFINAL REVIEW\n")
    print(render_plan(config, simulate=args.simulate, config_path=working))
    expected = "RUN SIMULATION" if args.simulate else "RUN LIVE"
    print(f"\nType exactly {expected} to execute, or anything else to cancel.")
    try:
        confirmation = input("confirm> ").strip()
    except (EOFError, KeyboardInterrupt):
        confirmation = ""
    log.write("confirmation", expected=expected, received=confirmation)
    if confirmation != expected:
        print("Cancelled. No run was started.")
        return 0
    (run_dir / "executed_config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    code = (_execute_simulation(working, log) if args.simulate
            else _execute_live(working, log, args.robot_host))
    log.write("session_finished", exit_code=code)
    print(f"\nCompleted with exit code {code}.")
    print(f"Working config : {working.relative_to(REPO)}")
    print(f"Session log    : {log.path.relative_to(REPO)}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
