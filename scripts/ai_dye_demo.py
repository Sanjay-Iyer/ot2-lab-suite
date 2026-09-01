#!/usr/bin/env python3
"""Conversational single-dye dilution and paper-print demo.

    python scripts/ai_dye_demo.py --simulate  # local only
    python scripts/ai_dye_demo.py             # real robot after RUN LIVE

The LLM edits a timestamped YAML copy. Robot Python remains deterministic.
"""
from __future__ import annotations

import argparse
import json
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

DEFAULT_CONFIG = REPO / "configs/workflows/templates/vial_dilution_print/basic_single_dye.yaml"
USER_CONFIG_DIR = REPO / "configs/workflows/user"
RUN_ROOT = REPO / "runs/ai_dye_demo"
RULE = "=" * 78
ALLOWED_ROOTS = {"deck", "sources", "dilution", "printing", "tips", "camera", "cv"}

SYSTEM_PROMPT = """You edit YAML configuration for one OT-2 demo: one dye stock
vial plus one water vial, a 1-8 well dilution series in one 96-well plate
column, then paper printing with an 8-channel pipette.

Return ONLY JSON:
{"updates": {nested YAML keys to merge}, "explanation": "short summary"}

Keep unmentioned values unchanged. Allowed update roots: deck, sources,
dilution, printing, tips, camera, cv. Never return Python or shell commands.

Mappings:
- dye location -> sources.food_coloring_vial (A1-B4)
- water location -> sources.water_vial
- number of dilutions N -> dilution.factors with exactly N factors (N=1..8)
- dilution location -> dilution.destination_column; printing.source_column is
  synchronized automatically
- dilution final volume -> dilution.total_volume_ul
- paper location -> printing.paper_start_column
- number of print passes -> printing.num_replicates
- total printed drops = number of dilution factors * number of replicates
- drop volume -> printing.droplet_volume_ul
- deck location -> deck.<tuberack|plate|paper|tiprack>.slot
- plate identity -> deck.plate.load_name/namespace/version; it must be a locally
  installed 96-well plate compatible with an 8-channel pipette

Never alter pipette, safety, run_modes, protocol_version, setup tips,
print_block_column, air handling, flow rates, camera endpoint, or add
color_series. Preserve a single-dye workflow.
"""


def _resolve_factors(dilution: dict[str, Any]) -> list[float]:
    factors = dilution.get("factors", {})
    mode = factors.get("mode", "explicit")
    if mode == "explicit":
        return [float(value) for value in factors.get("explicit", [])]
    count = int(factors.get("count", 0))
    start = float(factors.get("start", 1))
    if count < 1:
        return []
    if mode == "geometric":
        step = float(factors.get("step_factor", 2))
        return [start * step ** index for index in range(count)]
    end = float(factors.get("end", start))
    if count == 1:
        return [start]
    if mode == "linear":
        return [start + (end - start) * index / (count - 1) for index in range(count)]
    if mode == "log":
        import math
        if start <= 0 or end <= 0:
            raise ValueError("log dilution factors require positive start and end")
        lo, hi = math.log(start), math.log(end)
        return [math.exp(lo + (hi - lo) * index / (count - 1)) for index in range(count)]
    raise ValueError(f"unsupported dilution factor mode: {mode!r}")


def _config_problems(config: dict[str, Any]) -> list[str]:
    """Fast edit-time checks; the builder and simulator remain authoritative."""
    problems: list[str] = []
    required = ("deck", "pipette", "sources", "dilution", "printing", "tips", "safety")
    missing = [name for name in required if name not in config]
    if missing:
        return ["missing top-level section(s): " + ", ".join(missing)]
    deck = config["deck"]
    try:
        slots = [deck[role]["slot"] for role in ("tuberack", "plate", "paper", "tiprack")]
    except (KeyError, TypeError):
        return ["deck must define tuberack, plate, paper, and tiprack with slots"]
    if any(isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= 11
           for slot in slots):
        problems.append(f"deck slots must be integers from 1 to 11, got {slots}")
    if len(slots) != len(set(slots)):
        problems.append(f"deck slots must be distinct, got {slots}")
    middle = {4, 5, 6, 7, 8, 9}
    for role in ("tuberack", "plate", "tiprack"):
        if deck[role]["slot"] not in middle:
            problems.append(
                f"deck.{role}.slot must be in {sorted(middle)} for P300 SINGLE-nozzle motion"
            )
    try:
        factors = _resolve_factors(config["dilution"])
    except (TypeError, ValueError) as exc:
        problems.append(str(exc))
        factors = []
    if not 1 <= len(factors) <= 8:
        problems.append(f"number of dilutions must be 1-8, got {len(factors)}")
    if any(value <= 0 for value in factors):
        problems.append("all dilution factors must be positive")
    total = float(config["dilution"].get("total_volume_ul", 0) or 0)
    if not 0 < total <= 300:
        problems.append(f"dilution.total_volume_ul must be in (0, 300], got {total:g}")
    column = str(config["dilution"].get("destination_column", ""))
    if not column.isdigit() or not 1 <= int(column) <= 12:
        problems.append(f"dilution.destination_column must be 1-12, got {column!r}")
    sources = config["sources"]
    vial_pattern = re.compile(r"^[AB][1-4]$")
    for key in ("water_vial", "food_coloring_vial"):
        if not vial_pattern.fullmatch(str(sources.get(key, "")).upper()):
            problems.append(f"sources.{key} must be A1-B4")
    if sources.get("water_vial") == sources.get("food_coloring_vial"):
        problems.append("water and dye must use different vial wells")
    printing = config["printing"]
    drop = float(printing.get("droplet_volume_ul", 0) or 0)
    if not 20 <= drop <= 300:
        problems.append(f"printing.droplet_volume_ul must be 20-300 for P300, got {drop:g}")
    reps = int(printing.get("num_replicates", 0) or 0)
    paper_start = int(printing.get("paper_start_column", 0) or 0)
    if reps < 1:
        problems.append("printing.num_replicates must be at least 1")
    if not 1 <= paper_start <= 12 or paper_start + max(reps, 1) - 1 > 12:
        problems.append("paper_start_column plus replicates must fit in columns 1-12")
    return problems


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the model did not return a JSON object")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict) or not isinstance(value.get("updates"), dict):
        raise ValueError("the model response must contain an 'updates' object")
    return value


def _sync_derived_fields(config: dict[str, Any]) -> None:
    dilution = config.setdefault("dilution", {})
    config.setdefault("printing", {})["source_column"] = str(
        dilution.get("destination_column", "9")
    )
    config.setdefault("cv", {})["expected_droplets"] = len(_resolve_factors(dilution))
    config.pop("color_series", None)


def _validate_ai_update(before: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    blocked = sorted(set(updates) - ALLOWED_ROOTS)
    if blocked:
        raise ValueError("blocked top-level update(s): " + ", ".join(blocked))
    forbidden = (
        ("dilution", "setup_tip"), ("dilution", "water_setup_tip"),
        ("dilution", "single_tip_columns"), ("printing", "print_block_column"),
        ("printing", "air_gap_ul"), ("printing", "air_gap_height_mm"),
        ("printing", "dispense_z_mm"), ("camera", "robot_api_url"),
    )
    for root, key in forbidden:
        if isinstance(updates.get(root), dict) and key in updates[root]:
            raise ValueError(f"LLM changes to {root}.{key} are not allowed")
    candidate = merge_user_updates(deepcopy(before), updates)
    _sync_derived_fields(candidate)
    for fixed in ("pipette", "safety", "run_modes"):
        if candidate.get(fixed) != before.get(fixed):
            raise ValueError(f"{fixed} cannot be changed by the LLM")
    problems = _config_problems(candidate)
    if problems:
        raise ValueError("config validation failed:\n- " + "\n- ".join(problems))
    return candidate


def render_plan(config: dict[str, Any], *, simulate: bool, config_path: Path) -> str:
    deck, src = config["deck"], config["sources"]
    dil, pr = config["dilution"], config["printing"]
    factors = _resolve_factors(dil)
    total = float(dil["total_volume_ul"])
    column = str(dil["destination_column"])
    reps, drop = int(pr["num_replicates"]), float(pr["droplet_volume_ul"])
    start, end = int(pr["paper_start_column"]), int(pr["paper_start_column"]) + reps - 1
    try:
        display_path = config_path.relative_to(REPO)
    except ValueError:
        display_path = config_path
    lines = [
        RULE, "AI DYE -> DILUTION -> PAPER PRINT PLAN", RULE,
        f"Mode          : {'SIMULATION' if simulate else 'LIVE REAL ROBOT'}",
        f"Config        : {display_path}", "", "DECK",
        f"  slot {deck['tuberack']['slot']:>2}  vial rack   {deck['tuberack']['load_name']}",
        f"  slot {deck['plate']['slot']:>2}  well plate  {deck['plate']['load_name']}",
        f"  slot {deck['paper']['slot']:>2}  paper proxy {deck['paper']['load_name']}",
        f"  slot {deck['tiprack']['slot']:>2}  tip rack    {deck['tiprack']['load_name']}",
        "", "SOURCES",
        f"  water : vial {src['water_vial']} in slot {deck['tuberack']['slot']}",
        f"  dye   : vial {src['food_coloring_vial']} in slot {deck['tuberack']['slot']}",
        "", f"DILUTIONS ({len(factors)} wells in plate column {column})",
        "  well   fold     dye stock     water       final",
    ]
    for index, fold in enumerate(factors):
        stock = round(total / float(fold), 2)
        water = round(total - stock, 2)
        well = f"{chr(ord('A') + index)}{column}"
        lines.append(f"  {well:<5}  {float(fold):>5g}x  {stock:>8g} uL  {water:>8g} uL  {total:>7g} uL")
    drops = len(factors) * reps
    lines += [
        "", "PRINT", f"  source       : plate column {pr['source_column']}",
        f"  paper columns: {start if start == end else f'{start}-{end}'}",
        f"  passes       : {reps}",
        f"  per pass     : {len(factors)} simultaneous droplets",
        f"  drop volume  : {drop:g} uL per channel", f"  total drops  : {drops}",
        f"  printed fluid: {drops * drop:g} uL total",
        f"  return tips  : {config['tips']['return_tips']}", "", "EXECUTION",
        "  local build + simulation + validation + mock CV; no robot contact"
        if simulate else
        "  rebuild + validate + upload + start physical OT-2 liquid handling",
        RULE,
    ]
    return "\n".join(lines)


class SessionLog:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "session.log"

    def write(self, event: str, **fields: Any) -> None:
        record = {"timestamp_utc": _now(), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def _write_config(path: Path, config: dict[str, Any]) -> None:
    header = f"# AI-edited working copy; review before execution.\n# Last written UTC: {_now()}\n"
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


def _execute_simulation(path: Path, config: dict[str, Any], log: SessionLog) -> int:
    commands = [
        [sys.executable, "scripts/build_vial_dilution_print.py", "--config", str(path)],
        [sys.executable, "scripts/validate_vial_print.py", "--config", str(path)],
        [sys.executable, "vision_tests/scripts/verify_print_droplets.py", "--mock",
         "--expect", str(len(_resolve_factors(config["dilution"])))],
    ]
    for command in commands:
        code = _run_stream(command, log)
        if code:
            return code
    return 0


def _execute_live(path: Path, robot_ip: str, log: SessionLog) -> int:
    return _run_stream([
        sys.executable, "scripts/run_vial_print_robot.py", "--robot-ip", robot_ip,
        "--config", str(path), "--live",
    ], log)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true",
                        help="Local only; never discovers or contacts a robot.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(REPO)),
                        help="Starting YAML; a timestamped working copy is created.")
    parser.add_argument("--request", default=None, help="Optional first natural-language edit.")
    parser.add_argument("--robot-ip", default="169.254.46.57",
                        help="Real OT-2 IP, used only in live mode.")
    args = parser.parse_args(argv)

    source = Path(args.config)
    source = source if source.is_absolute() else REPO / source
    if not source.is_file():
        raise FileNotFoundError(f"starting config not found: {source}")
    config = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    _sync_derived_fields(config)
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

    print(f"\n{RULE}\nAI DYE DEMO AGENT - {mode}\n{RULE}")
    print(Config.describe_llm_auth())
    print(f"Working config : {working.relative_to(REPO)}")
    print(f"Session log    : {log.path.relative_to(REPO)}")
    print("\nDescribe changes in plain language. Commands: show, run, quit.\n")
    print(render_plan(config, simulate=args.simulate, config_path=working))

    llm = Config.get_llm(temperature=0)
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
        if low == "show":
            print(yaml.safe_dump(config, sort_keys=False))
            continue
        if low == "run":
            break
        log.write("user_request", text=request)
        try:
            response = _ask_llm(llm, config, request)
            config = _validate_ai_update(config, response["updates"])
        except Exception as exc:
            print(f"\nagent> edit rejected; config unchanged: {exc}")
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
    confirmation = input("confirm> ").strip()
    log.write("confirmation", expected=expected, received=confirmation)
    if confirmation != expected:
        print("Cancelled. No run was started.")
        return 0
    (run_dir / "executed_config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    code = (_execute_simulation(working, config, log) if args.simulate
            else _execute_live(working, args.robot_ip, log))
    log.write("session_finished", exit_code=code)
    print(f"\nCompleted with exit code {code}.")
    print(f"Working config : {working.relative_to(REPO)}")
    print(f"Session log    : {log.path.relative_to(REPO)}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
