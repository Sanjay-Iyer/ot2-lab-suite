#!/usr/bin/env python3
"""Conversational red-team harness for the AI dye demo. SIMULATION ONLY.

Simulated users (13 personas, targeted attack goals, 5 to 35+ turns) talk to the demo's own
conversation logic (src/agents/dye_demo/session.py). After every turn deterministic
invariants check the authoritative experiment state; nothing here asks a model whether
the demo behaved correctly. The session is given a recording function as its executor, so
no subprocess, protocol execution or robot connection can happen.

    python scripts/ai_dye_demo_redteam.py                                    # 300 scripted conversations
    python scripts/ai_dye_demo_redteam.py --conversations 1000 --chaos 0,0.35,0.7
    python scripts/ai_dye_demo_redteam.py --interpreter gemini --conversations 20 --max-model-calls 400
    python scripts/ai_dye_demo_redteam.py --user-agent gemini --judge --conversations 4
    python scripts/ai_dye_demo_redteam.py --replay tests/conversation_regressions/<case>.yaml
    python scripts/ai_dye_demo_redteam.py --simulate-states     # pinned local opentrons simulator, SOP and red-team states

--chaos is the probability that the scripted interpreter corrupts a reply the way a real
model could (leaking hypothetical text, unrelated extra changes, wrong units, wrong labware,
invented slots, claimed approvals, malformed JSON). Gemini modes use src/core/config.py
(GOOGLE_API_KEY on the simulation laptop) and stop at --max-model-calls.

Reports go to runs/ai_dye_demo_redteam/<timestamp>/: report.md, report.json,
conversations.jsonl, failures/ (transcripts with seeds) and regression_candidates/
(minimized conversations to review and copy into tests/conversation_regressions/).
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from src.agents.dye_demo.redteam.harness import (  # noqa: E402
    ConversationSpec,
    campaign_specs,
    demo_failures,
    render_failure_markdown,
    replay,
    run_campaign,
    run_conversation,
    save_failure,
    write_reports,
)
from src.agents.dye_demo.redteam.interpreter import CallBudget, RecordingLLM, SimulatedInterpreter  # noqa: E402
from src.agents.dye_demo.redteam.scenarios import GOALS, PERSONAS  # noqa: E402

RUN_ROOT = REPO / "runs" / "ai_dye_demo_redteam"


def _list(value: str, choices: list[str], *, allow_none: bool = False) -> list[str | None]:
    if value == "all":
        return list(choices) + ([None] if allow_none else [])
    if allow_none and value == "none":
        return [None]
    items = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in items if item not in choices and not (allow_none and item == "none")]
    if unknown:
        raise SystemExit(f"unknown: {', '.join(unknown)} (choose from {', '.join(choices)})")
    return [None if item == "none" else item for item in items]


def _model(temperature: float):
    from src.core.config import Config

    print(Config.describe_llm_auth())
    return Config.get_llm(temperature=temperature)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--conversations", type=int, default=300)
    parser.add_argument("--seed", type=int, default=1, help="Campaign seed; every conversation seed is recorded.")
    parser.add_argument("--personas", default="all", help=f"Comma list or all: {', '.join(PERSONAS)}")
    parser.add_argument("--goals", default="all", help="Comma list, all (includes free conversation) or none.")
    parser.add_argument("--lengths", default="5,10,20,35", help="Conversation lengths in user turns.")
    parser.add_argument("--chaos", default="0,0.35,0.7", help="Interpreter corruption probabilities to cycle.")
    parser.add_argument("--live-share", type=float, default=0.2,
                        help="Share of conversations that exercise live-mode bookkeeping with the recording "
                             "executor (nothing is contacted).")
    parser.add_argument("--interpreter", choices=["scripted", "gemini"], default="scripted")
    parser.add_argument("--user-agent", choices=["none", "gemini"], default="none")
    parser.add_argument("--judge", action="store_true", help="Gemini judge reviews model-user transcripts and its "
                                                             "five follow-up attacks are run.")
    parser.add_argument("--max-model-calls", type=int, default=300)
    parser.add_argument("--model-rpm", type=float, default=12.0,
                        help="Model calls per minute (the free Gemini API tier allows 15); rate limits are waited out.")
    parser.add_argument("--no-minimize", action="store_true")
    parser.add_argument("--keep-transcripts", action="store_true",
                        help="Save every transcript (not only failing ones) under transcripts/ for review.")
    parser.add_argument("--out", default=None)
    parser.add_argument("--replay", default=None, help="Replay one regression YAML and print its transcript.")
    parser.add_argument("--simulate-states", action="store_true",
                        help="Simulate representative conversation-generated states (SOP 1/2, red-team end states) "
                             "with the pinned local opentrons simulator and check the motion against each plan.")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    print("AI DYE DEMO RED-TEAM - SIMULATION ONLY (no robot connection; conversations use a recording executor)")

    if args.replay:
        return _replay(Path(args.replay))
    if args.simulate_states:
        from src.agents.dye_demo.redteam.simulate_states import simulate_representative_states

        target = Path(args.out) if args.out else RUN_ROOT / "simulated_states"
        results = simulate_representative_states(target)
        failed = [item["state"] for item in results if item["verdict"] != "OK"]
        print(f"\n{len(results)} states simulated, {len(failed)} failed{': ' + ', '.join(failed) if failed else ''}")
        print(f"protocols and simulator logs: {target}")
        return 1 if failed else 0

    out_dir = Path(args.out) if args.out else RUN_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    personas = _list(args.personas, list(PERSONAS))
    goals = _list(args.goals, list(GOALS), allow_none=True)
    lengths = [int(item) for item in args.lengths.split(",")]
    chaos = [float(item) for item in args.chaos.split(",")]
    settings = {key: value for key, value in vars(args).items() if key not in {"out", "replay"}}
    budget = CallBudget(args.max_model_calls)

    if args.user_agent == "gemini":
        summary = _model_user_campaign(args, personas, goals, budget, out_dir, settings)
    else:
        specs = campaign_specs(conversations=args.conversations, seed=args.seed, personas=personas, goals=goals,
                               lengths=lengths, chaos_levels=chaos if args.interpreter == "scripted" else [0.0],
                               live_share=args.live_share)
        runner = None
        base = _model(0) if args.interpreter == "gemini" else None

        def keep(result: dict) -> dict:
            if args.keep_transcripts and "transcript" in result:
                folder = out_dir / "transcripts"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{result['name'][:120]}.md").write_text(render_failure_markdown(result), encoding="utf-8")
            return result

        if base is not None or args.keep_transcripts:
            def runner(spec: ConversationSpec, workdir: Path):
                if base is None:
                    return keep(run_conversation(spec, workdir=workdir, keep_transcript=True))
                spec.interpreter = "gemini"
                return keep(run_conversation(spec, workdir=workdir, keep_transcript=args.keep_transcripts,
                                             interpreter=RecordingLLM(base, [], budget, rpm=args.model_rpm)))

        title = "Conversational robustness report" + (" (Gemini interpreter)" if base is not None else "")
        summary = run_campaign(specs, out_dir=out_dir, minimize_failures=not args.no_minimize, settings=settings,
                               title=title, runner=runner, stop=(lambda: budget.exhausted) if base is not None else None)
    metrics = summary["metrics"]
    print(f"\n{metrics['total_conversations']} conversations, {metrics['total_turns']} turns, "
          f"{len(summary['failing_conversations'])} with invariant violations")
    for name, count in summary["violations_by_invariant"].items():
        print(f"  {name}: {count}")
    if budget.used:
        print(f"model calls used: {budget.used} of {budget.limit}; rate-limited responses waited out: "
              f"{budget.rate_limited}" + (f"; stopped: {budget.unavailable[:160]}" if budget.unavailable else ""))
    if summary.get("conversations_with_external_model_errors"):
        print(f"conversations with external model errors (not demo failures): "
              f"{summary['conversations_with_external_model_errors']}")
    print(f"report: {out_dir / 'report.md'}")
    return 1 if summary["failing_conversations"] else 0


def _model_user_campaign(args, personas, goals, budget: CallBudget, out_dir: Path, settings: dict) -> dict:
    from src.agents.dye_demo.redteam.llm_agents import GeminiUserAgent, ScriptedAttack, judge

    rpm = args.model_rpm
    user_model = RecordingLLM(_model(0.9), [], budget, rpm=rpm)
    judge_model = RecordingLLM(_model(0.2), [], budget, rpm=rpm) if args.judge else None
    interpreter_model = _model(0) if args.interpreter == "gemini" else None
    rng = random.Random(args.seed)
    workdir = Path(tempfile.mkdtemp(prefix="redteam_llm_"))
    results, judged = [], []
    started = time.perf_counter()

    def interpreter_for(seed: int):
        if interpreter_model is not None:
            return RecordingLLM(interpreter_model, [], budget, rpm=rpm)
        return SimulatedInterpreter(seed=seed, chaos=0.0)

    def record(result: dict) -> None:
        results.append(result)
        if demo_failures(result):
            save_failure(result, out_dir, workdir, not args.no_minimize)

    try:
        for index in range(args.conversations):
            if budget.exhausted:
                print("model call budget or quota used up; stopping")
                break
            persona = PERSONAS[personas[index % len(personas)]]
            goal_name = rng.choice(goals)
            goal = GOALS[goal_name] if goal_name else None
            seed = args.seed * 1_000_000 + index
            length = [10, 20][index % 2]
            spec = ConversationSpec(seed=seed, persona=persona.name, goal=goal_name, length=length,
                                    interpreter=f"{args.interpreter}+gemini-user")
            driver = GeminiUserAgent(user_model, persona, goal, length, budget)
            result = run_conversation(spec, workdir=workdir, driver=driver, interpreter=interpreter_for(seed),
                                      keep_transcript=True)
            result["user_agent_errors"] = driver.errors
            record(result)
            print(f"  model user {index + 1}/{args.conversations}: {result['name']} - {result['turns']} turns, "
                  f"{len(result['violations'])} violations (model calls {budget.used})")
            if judge_model is None or budget.exhausted:
                continue
            try:
                review = judge(judge_model, result["transcript"], budget)
            except Exception as exc:  # noqa: BLE001 - a failed review does not stop the campaign
                print(f"  judge failed: {type(exc).__name__}: {exc}")
                continue
            item = {"after": result["name"], "observations": review["observations"], "attacks": []}
            for number, attack in enumerate(review["follow_up_attacks"], start=1):
                attack_spec = ConversationSpec(seed=seed * 10 + number, persona=persona.name,
                                               goal=f"judge: {attack['goal'][:60]}", length=len(attack["messages"]),
                                               interpreter=f"{args.interpreter}+judge-attack")
                attack_result = run_conversation(attack_spec, workdir=workdir,
                                                 driver=ScriptedAttack(attack["messages"], attack["goal"]),
                                                 interpreter=interpreter_for(attack_spec.seed), keep_transcript=True)
                record(attack_result)
                item["attacks"].append({"goal": attack["goal"], "messages": attack["messages"],
                                        "result": f"{len(attack_result['violations'])} violations"})
            judged.append(item)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    (out_dir / "transcripts").mkdir(parents=True, exist_ok=True)
    for result in results:
        if "transcript" in result:
            (out_dir / "transcripts" / f"{result['name'][:120].replace(':', '_')}.json").write_text(
                json.dumps(result["transcript"], indent=2, default=str), encoding="utf-8")
    return write_reports(results, out_dir, title="Conversational robustness report (Gemini user agent)",
                         settings=settings, seconds=time.perf_counter() - started,
                         extra={"judge": judged, "model_calls": budget.used})


def _replay(path: Path) -> int:
    case = yaml.safe_load(path.read_text(encoding="utf-8"))
    settings = case.get("settings") or {}
    workdir = Path(tempfile.mkdtemp(prefix="redteam_replay_"))
    try:
        result = replay(case["messages"], workdir=workdir, live_logic=bool(settings.get("live_logic")),
                        soft_labels=bool(settings.get("soft_labels")), keep_transcript=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    for row in result["transcript"]:
        print(f"\n--- turn {row['turn']} ({row['label']['category']} -> {row['classification']}), revision "
              f"{row['revision_before']} -> {row['revision_after']}")
        print(f"you> {row['text']}")
        print(row["output"])
    print("\nviolations:", json.dumps(result["violations"], indent=2) if result["violations"] else "none")
    return 1 if result["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
