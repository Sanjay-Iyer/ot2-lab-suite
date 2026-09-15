"""Simulate conversation-generated states of the AI dye demo with the pinned opentrons simulator.

States come from real DemoSession conversations (replayed through the red-team harness with recorded model
replies), including the SOP 1 and SOP 2 lab procedure and long red-team end states. Each approved configuration is
embedded into protocol v19 exactly as scripts/build_vial_dilution_print.py does, written under the output folder
(never over the tracked src/protocols/generated/*_latest.py), and simulated locally with the pinned API 2.15
interpreter. The simulator output is then checked line by line against the plan, because a simulation run can end
"successfully" on a protocol that errored.

Local simulation only: nothing contacts a robot.
"""
from __future__ import annotations

import importlib.util
import json
import random
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from src.agents.dye_demo.model import REPO, load_config
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.harness import ConversationSpec, replay, run_conversation
from src.agents.dye_demo.redteam.scenarios import GOALS, PERSONAS
from src.agents.dye_demo.redteam.sop_paths import PATHS, START_CONFIGS
from src.agents.dye_demo.sop_check import check_runs, load_expected, session_run_configs

PINNED_SIMULATOR = REPO / ".venv" / "ot2-api-2.15-py310" / "python.exe"
BASE_PROTOCOL = REPO / "src" / "protocols" / "printing" / "13_ai_agent_dilution_print_demo.py"


def _said(text: str, *changes: dict[str, Any]) -> dict[str, Any]:
    if not changes:
        return {"text": text}
    reply = {"intent": "change", "changes": list(changes), "explanation": "scripted", "clarification": ""}
    return {"text": text, "llm": [{"kind": "interpret", "reply": json.dumps(reply)}]}


def _run() -> dict[str, Any]:
    return {"text": "run", "label": {"category": "run", "may_run": True, "may_propose": False}}


def _change(path: str, value: Any = None, evidence: str = "", **extra: Any) -> dict[str, Any]:
    data = {"path": path, "evidence": evidence, **extra}
    if "op" not in extra:
        data["value"] = value
    return data


RUN1 = ("Make three dilutions by diluting dye with water. Use dilution factors 2x, 5x, and 10x, with a final total volume "
        "of 100 uL in each dilution well. Start at row A in plate column 11. Put the paper in deck slot 5. Print one 5 uL "
        "drop of each dilution starting in paper column 1. Use one replicate and start from tip A1.")
RUN2 = ("The three dye dilutions are already made, so skip the dilution step. Each well now holds about 95 uL. Print "
        "three separate 5 uL drops stacked on each spot, starting in paper column 2, and start from tip F1.")
THREE = (_change("dilution.factors", [2, 5, 10], "2x, 5x, and 10x"), _change("dilution.total_volume_ul", "100 uL", "100 uL"))

CONVERSATIONS: dict[str, list[dict[str, Any]]] = {
    "01_default_plan": [_said("What is SERS?"), _run()],
    "02_three_dilutions_rack_slot8_after_detour": [
        _said("Make dilutions of 2x, 5x and 10x at 100 µL total.", _change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"),
              _change("dilution.total_volume_ul", "100 µL", "100 µL total")),
        _said("yes"), _said("Why do we mix after making a dilution?"), _said("What is Raman scattering?"),
        _said("Move the vial rack to slot 8.", _change("deck.tuberack.slot", 8, "vial rack to slot 8")), _said("yes"),
        _run()],
    "03_print_only_vial_rack_off_deck": [
        _said("We already made all the dilutions."), _said("yes"),
        _said("Take the vial rack off the deck.", _change("deck.tuberack.slot", "OFF_DECK", "vial rack off the deck")),
        _said("yes"), _said("Print 3 drops per spot.", _change("print.droplets_per_spot", 3, "3 drops per spot")),
        _said("yes"), _run()],
    "04_four_changes_one_proposal": [
        _said("Move the dilution plate to slot 8, use four dilutions, start tips at G1, and print three drops.",
              _change("deck.plate.slot", 8, "dilution plate to slot 8"),
              _change("dilution.factors", evidence="four dilutions", op="set_count", count=4),
              _change("tips.start_tip", "G1", "tips at G1"), _change("print.droplets_per_spot", 3, "three drops")),
        _said("yes"), _run()],
    "05_new_tips_two_volumes_two_replicates": [
        _said("Use a new tip for every transfer.",
              _change("tips.policy", "new_tip_every_transfer", "new tip for every transfer")),
        _said("yes"),
        _said("Make dilutions of 2x, 4x and 8x, print 5 and 10 µL drops with 2 replicates.",
              _change("dilution.factors", [2, 4, 8], "2x, 4x and 8x"),
              _change("print.droplet_volume_ul", ["5 µL", "10 µL"], "5 and 10 µL drops"),
              _change("print.replicates", 2, "2 replicates")),
        _said("yes"), _run()],
    "06_rollback_after_two_changes": [
        _said("Move the dilution plate to slot 6.", _change("deck.plate.slot", 6, "plate to slot 6")), _said("yes"),
        _said("Make each dilution 200 µL total.", _change("dilution.total_volume_ul", "200 µL", "200 µL total")),
        _said("yes"), _said("Undo my last change."), _said("yes"), _run()],
    "07_plate_replaced_restores_dilution_step": [
        _said("We already made all the dilutions."), _said("yes"),
        _said("I replaced the dilution plate with a new empty one."), _said("yes"), _run()],
    "08_sop1_three_dilutions_paper_column1": [_said(RUN1, *THREE), _said("yes"), _run()],
    "09_sop2_print_only_three_drops_column2_tip_f1": [
        _said("Make three dilutions by diluting dye with water. Use dilution factors 2x, 5x, and 10x, with a final total "
              "volume of 100 uL in each dilution well.", *THREE),
        _said("yes"),
        _said(RUN2, _change("dilution.enabled", False, "skip the dilution step"),
              _change("dilution.prepared_volume_ul", "95 uL", "about 95 uL"),
              _change("print.droplets_per_spot", 3, "three separate 5 uL drops stacked"),
              _change("print.paper_start_column", 2, "paper column 2"), _change("tips.start_tip", "F1", "tip F1")),
        _said("yes"), _run()],
}


def sop_states(workdir: Path) -> dict[str, tuple[dict[str, Any], int]]:
    """The configuration of every run of every scripted user-test SOP path, once per distinct configuration."""
    states: dict[str, tuple[dict[str, Any], int]] = {}
    seen: set[str] = set()
    for sop, paths in PATHS.items():
        for path_name, messages in paths.items():
            result = replay(messages, workdir=workdir, return_session=True, config=load_config(START_CONFIGS[sop]))
            session = result["session"]
            configs = session_run_configs(session)
            unmet = [finding for finding in check_runs(configs, load_expected(sop)) if not finding.ok]
            if result["violations"] or unmet:
                raise RuntimeError(f"SOP {sop} {path_name}: {result['violations'] or unmet}")
            for number, (config, run) in enumerate(zip(configs, session.state.runs), start=1):
                key = json.dumps(config, sort_keys=True, default=str)
                if key not in seen:
                    seen.add(key)
                    states[f"sop{sop}_{path_name}_run{number}"] = (config, run["revision"])
    return states


def conversation_states(workdir: Path, *, redteam_states: int = 3) -> dict[str, tuple[dict[str, Any], int]]:
    """Approved configurations (as the session writes them) and revisions, from replayed conversations."""
    states: dict[str, tuple[dict[str, Any], int]] = {}
    for name, messages in CONVERSATIONS.items():
        result = replay(messages, workdir=workdir, keep_transcript=True, return_session=True)
        if result["violations"]:
            raise RuntimeError(f"{name}: {result['violations']}")
        states[name] = (result["session"]._file_config(), result["session"].state.revision)
    states.update(sop_states(workdir))
    rng = random.Random(7)
    candidates = []
    for index in range(120):
        spec = ConversationSpec(seed=777000 + index, persona=rng.choice(list(PERSONAS)),
                                goal=rng.choice(list(GOALS) + [None]), length=35, chaos=0.35)
        result = run_conversation(spec, workdir=workdir, return_session=True)
        session = result["session"]
        if result["violations"] or session.state.validate().errors:
            continue
        candidates.append((session.state.revision, spec.name, session._file_config()))
    seen: set[str] = set()
    for revision, name, config in sorted(candidates, key=lambda item: -item[0]):
        key = json.dumps({k: config[k] for k in ("deck", "dilution", "print", "tips")}, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        states[f"redteam_{name}"] = (config, revision)
        if len(seen) >= redteam_states:
            break
    return states


def _builder() -> Any:
    spec = importlib.util.spec_from_file_location("build_vial_dilution_print", REPO / "scripts" / "build_vial_dilution_print.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_simulation(config: dict[str, Any], output: str, error_pattern: re.Pattern) -> dict[str, Any]:
    plan = build_plan(config)
    lines = output.splitlines()
    pickups = [line for line in lines if "Picking up tip" in line]
    from_rack = [line for line in lines if line.strip().startswith("Aspirating")
                 and re.search(r"of .*(?:[Vv]ial|[Tt]ube|tuberack|20ml)", line)]
    paper = [line for line in lines if line.strip().startswith("Dispensing") and re.search(r"[Pp]aper", line)]
    transfers = sum(op.kind == "transfer" for op in plan.operations)
    drops = sum(op.droplets for op in plan.operations if op.kind == "print")
    problems = []
    if len(pickups) != len(plan.tips):
        problems.append(f"{len(pickups)} tip pick-ups, plan says {len(plan.tips)}")
    if plan.tips and pickups and f"from {plan.tips[0].tip} of" not in pickups[0]:
        problems.append(f"first pick-up is not {plan.tips[0].tip}")
    if len(from_rack) != transfers:
        problems.append(f"{len(from_rack)} vial-rack aspirations, plan has {transfers} transfers")
    if len(paper) != drops:
        problems.append(f"{len(paper)} paper dispenses, plan has {drops} drops")
    errors = [line.strip() for line in lines if error_pattern.search(line)]
    if errors:
        problems.append("errors: " + "; ".join(errors[:3]))
    return {"tips": len(pickups), "vial_aspirations": len(from_rack), "paper_drops": len(paper),
            "dilutions": len(plan.wells) if plan.do_dilution else 0, "problems": problems}


def simulate_representative_states(out_dir: Path, *, python: Path = PINNED_SIMULATOR,
                                   progress: Callable[[str], None] = print) -> list[dict[str, Any]]:
    if not Path(python).exists():
        raise FileNotFoundError(f"pinned simulator not found: {python}")
    builder = _builder()
    out_dir.mkdir(parents=True, exist_ok=True)
    base_text = BASE_PROTOCOL.read_text(encoding="utf-8")
    summary = []
    for name, (full, revision) in conversation_states(Path(tempfile.mkdtemp(prefix="redteam_states_"))).items():
        full = deepcopy(full)
        run_modes = full.pop("run_modes", {})
        run_modes["dry_run"] = False
        config = dict(full)
        config["protocol_version"] = int(config.get("protocol_version", 19))
        path = out_dir / f"{name}.py"
        path.write_text(builder.build_source(base_text, config, run_modes), encoding="utf-8")
        ok, output = builder.simulate(path, str(python))
        (out_dir / f"{name}.simulation.txt").write_text(output, encoding="utf-8")
        checked = check_simulation(config, output, builder._ERROR_RE)
        verdict = "OK" if ok and not checked["problems"] else "FAILED"
        summary.append({"state": name, "revision": revision, "verdict": verdict, **checked,
                        "deck": {role: spec["slot"] for role, spec in config["deck"].items()}})
        progress(f"  {verdict:6s} {name}: tips {checked['tips']}, vial aspirations {checked['vial_aspirations']}, "
                 f"paper drops {checked['paper_drops']} {checked['problems'] or ''}")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
