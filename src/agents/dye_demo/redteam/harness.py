"""Red-team conversation runner: simulated users against the real DemoSession. Simulation only.

    simulated user (scenarios.Driver, a Gemini user agent, or a regression file)
        -> DemoSession: the demo's own intent analysis, interpretation, validation,
           proposals, confirmation and state
             -> interpreter: scripted (faithful or adversarial) or Gemini (API key)
             -> executor: a Python function that records the configuration it was given
    after every turn:        invariants.check_turn
    after the conversation:  invariants.check_conversation (includes the fake-OT-2 protocol run)

Nothing here can reach the OT-2. The session's executor is always the recording function
below, never SubprocessExecutor; no subprocess is started and the robot runner is not
imported. "live_logic" conversations only exercise the session's post-run bookkeeping
(prepared dilutions, used tips) with that same recording executor.
"""
from __future__ import annotations

import json
import random
import shutil
import tempfile
import time
import traceback
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from src.agents.dye_demo.llm import LLMClient
from src.agents.dye_demo.model import DEFAULT_CONFIG, canonicalize_path, load_config
from src.agents.dye_demo.redteam.interpreter import ReplayInterpreter, SimulatedInterpreter
from src.agents.dye_demo.redteam.invariants import (
    ADVERSARIAL,
    AMBIGUOUS,
    APPROVAL_ATTEMPTS,
    BUCKETS,
    CONTRADICTIONS,
    EXTERNAL_INVARIANTS,
    HYPOTHETICAL,
    INFORMATIONAL,
    Tracker,
    check_conversation,
    check_turn,
    explicit_yes,
)
from src.agents.dye_demo.redteam.scenarios import GOALS, PERSONAS, Driver, UserTurn
from src.agents.dye_demo.session import DemoSession, SessionSettings

REPORT_METRICS = (
    "simulated_users", "total_conversations", "total_turns",
    "informational_questions", "general_knowledge_questions", "experiment_modifications", "proposals_shown",
    "ambiguous_instructions", "hypotheticals", "contradictions", "approval_attempts", "adversarial_attempts",
    "physical_state_reconciliation_cases",
    "unexpected_state_changes", "unexpected_proposals", "unexpected_runs", "invalid_deck_states",
    "stale_revision_acceptance", "duplicate_operations", "skipped_dilution_cases", "reappearing_dilution_cases",
    "unresolved_ambiguity", "crashes", "potential_response_quality_issues",
)
INFORMATIONAL_QUESTIONS = {"science_question", "history_question", "detour_question", "question_while_pending",
                           "question_while_clarifying"}


@dataclass
class ConversationSpec:
    seed: int
    persona: str
    goal: str | None = None
    length: int = 10
    chaos: float = 0.0
    live_logic: bool = False
    interpreter: str = "scripted"

    @property
    def name(self) -> str:
        goal = self.goal or "free"
        return f"s{self.seed}-{self.persona}-{goal}-L{self.length}-c{self.chaos:g}{'-live' if self.live_logic else ''}"


_NEVER_AN_ANSWER = INFORMATIONAL | ADVERSARIAL


def finalize_label(turn: UserTurn, session) -> UserTurn:
    """Labels that depend on what is on screen when the message is sent.

    A yes applies only if a proposal is waiting. While a clarification question is
    waiting, a short message ("use 3", "slot 6") may legitimately answer it and lead to a
    proposal, so only questions and bypass attempts keep a strict no-proposal label.
    """
    if explicit_yes(turn.text, session.pending.id if session.pending is not None else None):
        turn.may_mutate = session.pending is not None
        turn.may_propose = None if session.clarifying is not None else False
    elif session.clarifying is not None and turn.category not in _NEVER_AN_ANSWER:
        turn.may_propose = None
    return turn


class ReplayDriver:
    """Plays back a fixed list of messages (a regression file or a minimization candidate)."""

    def __init__(self, messages: list[dict[str, Any]], *, soft_labels: bool = False):
        self.soft_labels = soft_labels
        self.messages = list(messages)
        self.index = 0
        self.history: list[UserTurn] = []
        self._gates: list[str] = []

    @property
    def done(self) -> bool:
        return self.index >= len(self.messages)

    def next_turn(self, session) -> UserTurn:
        message = self.messages[self.index]
        self.index += 1
        label = dict(message.get("label") or {"category": "unlabelled", "may_propose": None})
        turn = UserTurn(message["text"], **label)
        self._gates = list(message.get("gates", []))
        self.history.append(turn)
        return turn

    def gate_answer(self, prompt: str) -> str:
        return self._gates.pop(0) if self._gates else "no"


def label_dict(turn: UserTurn) -> dict[str, Any]:
    data = {"category": turn.category, "may_mutate": turn.may_mutate, "may_propose": turn.may_propose,
            "may_run": turn.may_run}
    if turn.intended is not None:
        data["intended"] = turn.intended
    if turn.goal:
        data["goal"] = turn.goal
    if turn.detour:
        data["detour"] = True
    return data


def run_conversation(spec: ConversationSpec, *, workdir: Path, driver: Any = None, interpreter: Any = None,
                     keep_transcript: bool = False, config: dict[str, Any] | None = None,
                     return_session: bool = False) -> dict[str, Any]:
    rng = random.Random(spec.seed)
    if driver is None:
        driver = Driver(PERSONAS[spec.persona], GOALS[spec.goal] if spec.goal else None, spec.length, rng)
    if interpreter is None:
        interpreter = SimulatedInterpreter(seed=spec.seed, chaos=spec.chaos)
    directory = Path(tempfile.mkdtemp(prefix="rt_", dir=workdir))
    outputs: list[str] = []
    labels: list[UserTurn] = []
    marks: list[int] = []
    executed: list[dict[str, Any]] = []
    gates: list[dict[str, Any]] = []
    turn_outputs: list[str] = []
    tracker = Tracker(simulate=not spec.live_logic, soft_labels=getattr(driver, "soft_labels", False))
    if hasattr(driver, "outputs") and driver.outputs is None:
        driver.outputs = outputs                 # a model-driven user reads what the agent printed
    box: dict[str, DemoSession] = {}

    def executor(path: Path, simulate: bool, log) -> int:
        executed.append({"simulate": simulate, "config": yaml.safe_load(Path(path).read_text(encoding="utf-8"))})
        return 0

    def check_finished_turns() -> None:
        session = box["session"]
        while len(turn_outputs) < len(session.turns):
            index = len(turn_outputs)
            record = session.turns[index]
            label = labels[index] if index < len(labels) else UserTurn(record["message"], "unlabelled")
            text = "\n".join(outputs[marks[index]:]) if index < len(marks) else ""
            turn_outputs.append(text)
            check_turn(tracker, session, label, record, text, executed)

    def read(prompt: str) -> str:
        session = box["session"]
        if session._turn is not None:           # a yes/no gate inside a turn
            answer = driver.gate_answer(prompt)
            gates.append({"turn": len(session.turns) + 1, "prompt": prompt.strip(), "answer": answer})
            return answer
        check_finished_turns()
        if driver.done:
            raise EOFError
        turn = finalize_label(driver.next_turn(session), session)
        labels.append(turn)
        marks.append(len(outputs))
        interpreter.turn = len(labels)
        if hasattr(interpreter, "set_oracle"):
            interpreter.set_oracle(turn.intended if turn.intended else None,
                                   inert=turn.may_propose is False and not turn.may_mutate)
        return turn.text

    settings = SessionSettings(simulate=not spec.live_logic, config_source=DEFAULT_CONFIG,
                               working_config=directory / "working.yaml", run_dir=directory / "run",
                               session_label=f"redteam-{spec.seed}", operator=spec.persona.replace("_", " ").title(),
                               skip_llm_startup=True, llm_description="red-team interpreter (simulation only)")
    session = DemoSession(settings, config if config is not None else load_config(DEFAULT_CONFIG),
                          llm=LLMClient(lambda: interpreter), executor=executor, input_fn=read,
                          output_fn=outputs.append, sleep=lambda seconds: None)
    box["session"] = session
    tracker.start(session)
    started = time.perf_counter()
    exit_code = None
    try:
        exit_code = session.run()
    except Exception as exc:  # noqa: BLE001 - recorded as a crash finding
        tracker.fail("no_crash", len(session.turns), f"session.run raised {type(exc).__name__}: {exc}",
                     traceback=traceback.format_exc())
    check_finished_turns()
    try:
        check_conversation(tracker, session, labels)
    except Exception as exc:  # noqa: BLE001
        tracker.fail("no_crash", len(session.turns), f"conversation check raised {type(exc).__name__}: {exc}",
                     traceback=traceback.format_exc())
    seconds = time.perf_counter() - started
    violations = [violation.as_dict() for violation in tracker.violations]
    result: dict[str, Any] = {
        "name": spec.name, "spec": asdict(spec), "soft_labels": tracker.soft_labels, "exit_code": exit_code,
        "turns": len(session.turns),
        "violations": violations, "quality": tracker.quality, "seconds": round(seconds, 3),
        "metrics": conversation_metrics(labels, session, tracker, interpreter),
        "final": {"revision": session.state.revision, "pending": session.pending.id if session.pending else None,
                  "clarifying": session.clarifying is not None, "runs": len(session.state.runs),
                  "config_fingerprint": session.state.fingerprint()},
    }
    if violations or keep_transcript:
        result["transcript"] = transcript(labels, session, turn_outputs, getattr(interpreter, "calls", []), gates)
        errors = _turn_errors(directory / "run" / "session.log")
        if errors:
            result["tracebacks"] = errors
    if return_session:
        result["session"] = session
    shutil.rmtree(directory, ignore_errors=True)
    return result


def _turn_errors(path: Path) -> list[str]:
    if not path.exists():
        return []
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("event") == "turn_error":
            found.append(record.get("traceback", record.get("error", "")))
    return found


def transcript(labels: list[UserTurn], session, outputs: list[str], calls: list[dict[str, Any]],
               gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for index, record in enumerate(session.turns):
        number = record["turn"]
        label = labels[index] if index < len(labels) else UserTurn(record["message"], "unlabelled")
        rows.append({
            "turn": number, "text": record["message"], "label": label_dict(label),
            "classification": record["classification"], "flags": record["flags"],
            "normalized": record.get("normalized"), "revision_before": record["revision_before"],
            "revision_after": record["revision_after"], "state_before": record["state_before"][:12],
            "state_after": record["state_after"][:12], "pending_before": record["pending_before"],
            "pending_after": record["pending_after"], "events": record["events"],
            "output": outputs[index] if index < len(outputs) else "",
            "llm": [{key: call[key] for key in ("kind", "reply", "chaos") if key in call}
                    for call in calls if call.get("turn") == number],
            "gates": [gate["answer"] for gate in gates if gate["turn"] == number],
        })
    return rows


def conversation_metrics(labels: list[UserTurn], session, tracker: Tracker, interpreter: Any) -> dict[str, int]:
    counter: Counter[str] = Counter()
    config = session.state.snapshots[0]["config"]
    for label, record in zip(labels, session.turns):
        category = label.category
        counter["total_turns"] += 1
        if category in INFORMATIONAL_QUESTIONS:
            counter["informational_questions"] += 1
        if category in {"general_question", "chat"}:
            counter["general_knowledge_questions"] += 1
        if category in AMBIGUOUS:
            counter["ambiguous_instructions"] += 1
        if category in HYPOTHETICAL:
            counter["hypotheticals"] += 1
        if category in CONTRADICTIONS:
            counter["contradictions"] += 1
        if category in APPROVAL_ATTEMPTS or explicit_yes(record["message"]):
            counter["approval_attempts"] += 1
        if category in ADVERSARIAL or {"injection", "authority"} & set(record["flags"]):
            counter["adversarial_attempts"] += 1
        if category == "physical_report" or record["classification"] == "physical_report":
            counter["physical_state_reconciliation_cases"] += 1
        shown = []
        for event in record["events"]:
            kind = event["type"]
            if kind == "proposal":
                counter["proposals_shown"] += 1
                shown.append(set(event["paths"]))
            elif kind == "applied":
                counter["experiment_modifications"] += 1
            elif kind == "clarification":
                counter["clarifications_asked"] += 1
            elif kind == "refusal":
                counter["refusals"] += 1
            elif kind == "run":
                counter["runs"] += 1
            elif kind in {"rejected", "conflict"}:
                counter["validation_rejections"] += 1
            elif kind == "noop" and event.get("reason") in {"clarification rounds exhausted",
                                                            "clarification did not resolve"}:
                counter["unresolved_ambiguity"] += 1
        if label.intended and category not in {"clarification_answer", "approve"}:
            counter["actionable_requests"] += 1
            wanted = {canonicalize_path(config, item["path"]) for item in label.intended}
            if any(wanted <= paths for paths in shown):
                counter["proposed_as_intended"] += 1
    if session.clarifying is not None:
        counter["unresolved_ambiguity"] += 1
    for violation in tracker.violations:
        counter[violation.bucket] += 1
    counter["potential_response_quality_issues"] += len(tracker.quality)
    counter["chaos_replies"] += sum(1 for call in getattr(interpreter, "calls", []) if call.get("chaos"))
    counter["model_calls"] += len(getattr(interpreter, "calls", []))
    return dict(counter)


# ── campaigns ────────────────────────────────────────────────────────────────

def campaign_specs(*, conversations: int, seed: int, personas: list[str], goals: list[str | None],
                   lengths: list[int], chaos_levels: list[float], live_share: float) -> list[ConversationSpec]:
    rng = random.Random(seed)
    pairs = [(persona, goal) for persona in personas for goal in goals]
    rng.shuffle(pairs)
    specs = []
    for index in range(conversations):
        persona, goal = pairs[index % len(pairs)]
        specs.append(ConversationSpec(
            seed=seed * 1_000_000 + index, persona=persona, goal=goal, length=lengths[index % len(lengths)],
            chaos=chaos_levels[(index // len(lengths)) % len(chaos_levels)], live_logic=rng.random() < live_share))
    return specs


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    by_invariant: Counter[str] = Counter()
    for result in results:
        totals.update(result["metrics"])
        by_invariant.update(violation["invariant"] for violation in result["violations"])
    personas = sorted({result["spec"]["persona"] for result in results})
    goals = sorted({result["spec"]["goal"] for result in results if result["spec"]["goal"]})
    summary = {metric: totals.get(metric, 0) for metric in REPORT_METRICS}
    summary.update(simulated_users=len(personas), total_conversations=len(results))
    failing = [result for result in results if demo_failures(result)]
    return {
        "metrics": summary,
        "other": {key: value for key, value in sorted(totals.items()) if key not in summary},
        "violations_by_invariant": dict(by_invariant.most_common()),
        "personas": personas, "attack_goals": goals,
        "lengths": dict(Counter(result["spec"]["length"] for result in results)),
        "chaos_levels": dict(Counter(str(result["spec"]["chaos"]) for result in results)),
        "live_logic_conversations": sum(1 for result in results if result["spec"]["live_logic"]),
        "failing_conversations": [
            {"name": result["name"], "seed": result["spec"]["seed"], "persona": result["spec"]["persona"],
             "goal": result["spec"]["goal"], "invariants": sorted({v["invariant"] for v in demo_failures(result)})}
            for result in failing],
        "conversations_with_external_model_errors": sum(
            1 for result in results if any(v["invariant"] in EXTERNAL_INVARIANTS for v in result["violations"])),
    }


def demo_failures(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Violations that are the demo's fault (a quota or rate-limit refusal from the model service is not)."""
    return [violation for violation in result["violations"] if violation["invariant"] not in EXTERNAL_INVARIANTS]


# ── replay, minimization and regression files ───────────────────────────────

def messages_from_transcript(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"text": row["text"], "label": row["label"],
             "llm": [{"kind": call["kind"], "reply": call["reply"], **({"chaos": call["chaos"]} if call.get("chaos")
                                                                       else {})} for call in row["llm"]],
             **({"gates": row["gates"]} if row["gates"] else {})} for row in rows]


def replay(messages: list[dict[str, Any]], *, workdir: Path, live_logic: bool = False, seed: int = 0,
           keep_transcript: bool = False, config: dict[str, Any] | None = None,
           soft_labels: bool = False, return_session: bool = False) -> dict[str, Any]:
    script = {number: [dict(item) for item in message.get("llm", [])]
              for number, message in enumerate(messages, start=1)}
    interpreter = ReplayInterpreter(seed=seed, script=script)
    spec = ConversationSpec(seed=seed, persona="replay", length=len(messages), live_logic=live_logic,
                            interpreter="replay")
    return run_conversation(spec, workdir=workdir, driver=ReplayDriver(messages, soft_labels=soft_labels),
                            interpreter=interpreter, keep_transcript=keep_transcript, config=config,
                            return_session=return_session)


def minimize(messages: list[dict[str, Any]], invariant: str, *, workdir: Path, live_logic: bool = False,
             budget: int = 300, soft_labels: bool = False) -> list[dict[str, Any]]:
    """Greedy one-message-at-a-time removal while the same invariant still fails."""
    current = list(messages)
    attempts = 0
    progress = True
    while progress and attempts < budget:
        progress = False
        for index in range(len(current) - 1, -1, -1):
            if len(current) == 1 or attempts >= budget:
                break
            candidate = current[:index] + current[index + 1:]
            attempts += 1
            result = replay(candidate, workdir=workdir, live_logic=live_logic, soft_labels=soft_labels)
            if any(violation["invariant"] == invariant for violation in result["violations"]):
                current = candidate
                progress = True
    return current


def regression_document(result: dict[str, Any], violation: dict[str, Any], messages: list[dict[str, Any]],
                        name: str) -> dict[str, Any]:
    spec = result["spec"]
    return {
        "name": name,
        "description": violation["message"],
        "found_by": {key: spec[key] for key in ("seed", "persona", "goal", "length", "chaos", "live_logic",
                                                "interpreter")},
        "invariant": violation["invariant"],
        "settings": {"live_logic": bool(spec["live_logic"]), "soft_labels": bool(result.get("soft_labels"))},
        "messages": messages,
        "expected": {"violations": []},
    }


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")


def render_failure_markdown(result: dict[str, Any]) -> str:
    lines = [f"# {result['name']}", "", f"seed: `{result['spec']['seed']}`  persona: `{result['spec']['persona']}`  "
             f"goal: `{result['spec']['goal']}`  chaos: `{result['spec']['chaos']}`  live_logic: "
             f"`{result['spec']['live_logic']}`", "", "## Violations", ""]
    lines += [f"- turn {v['turn']}: **{v['invariant']}** - {v['message']}" for v in result["violations"]]
    if result.get("quality"):
        lines += ["", "## Potential response-quality issues", ""]
        lines += [f"- turn {item['turn']}: {item['issue']}" for item in result["quality"]]
    lines += ["", "## Transcript", ""]
    for row in result.get("transcript", []):
        lines += [f"### turn {row['turn']} - {row['label']['category']} -> {row['classification']}",
                  f"revision {row['revision_before']} -> {row['revision_after']}, pending {row['pending_before']} -> "
                  f"{row['pending_after']}", "", "```text", f"you> {row['text']}", "", row["output"], "```"]
        if row["llm"]:
            lines += ["model replies:", "```json"] + [json.dumps(call) for call in row["llm"]] + ["```"]
        lines.append("")
    for trace in result.get("tracebacks", []):
        lines += ["```text", trace, "```"]
    return "\n".join(lines)


def render_report_markdown(summary: dict[str, Any], *, title: str, settings: dict[str, Any]) -> str:
    metrics = summary["metrics"]
    lines = [f"# {title}", "", "Simulation only: scripted or model-driven users against the demo's own session logic, "
             "with a recording executor. No robot, no subprocess, no protocol execution on hardware.", "",
             "## Settings", "", "```json", json.dumps(settings, indent=2), "```", "", "## Metrics", "",
             "| metric | count |", "|---|---:|"]
    lines += [f"| {metric.replace('_', ' ')} | {metrics[metric]} |" for metric in REPORT_METRICS]
    lines += ["", "## Other counters", "", "| counter | count |", "|---|---:|"]
    lines += [f"| {key.replace('_', ' ')} | {value} |" for key, value in summary["other"].items()]
    lines += ["", "## Violations by invariant", ""]
    if summary["violations_by_invariant"]:
        lines += [f"- {name} ({BUCKETS.get(name, 'other')}): {count}"
                  for name, count in summary["violations_by_invariant"].items()]
    else:
        lines.append("None.")
    lines += ["", f"Personas: {', '.join(summary['personas'])}", "",
              f"Attack goals: {', '.join(summary['attack_goals'])}", "",
              f"Conversation lengths: {summary['lengths']}", "", f"Chaos levels: {summary['chaos_levels']}", "",
              f"Live-logic conversations (recording executor): {summary['live_logic_conversations']}", "",
              "Conversations with external model errors (quota or rate limit; reported, not demo failures): "
              f"{summary.get('conversations_with_external_model_errors', 0)}", "",
              "## Failing conversations", ""]
    if summary["failing_conversations"]:
        lines += [f"- `{item['name']}` (seed {item['seed']}): {', '.join(item['invariants'])}"
                  for item in summary["failing_conversations"]]
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def write_reports(results: list[dict[str, Any]], out_dir: Path, *, title: str, settings: dict[str, Any],
                  seconds: float, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = aggregate(results)
    summary["seconds"] = round(seconds, 1)
    if extra:
        summary.update(extra)
    (out_dir / "report.json").write_text(json.dumps({"settings": settings, **summary}, indent=2, default=str),
                                         encoding="utf-8")
    markdown = render_report_markdown(summary, title=title, settings=settings)
    if extra and extra.get("judge"):
        markdown += "\n## Judge observations and follow-up attacks\n\n"
        for item in extra["judge"]:
            markdown += f"### after `{item['after']}`\n\n" + "".join(f"- {obs}\n" for obs in item["observations"])
            markdown += "".join(f"- attack: {attack['goal']} -> `{attack['result']}`\n" for attack in item["attacks"])
            markdown += "\n"
    (out_dir / "report.md").write_text(markdown, encoding="utf-8")
    with (out_dir / "conversations.jsonl").open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps({key: result[key] for key in ("name", "spec", "turns", "violations", "quality",
                                                                  "metrics", "final", "seconds")}, default=str) + "\n")
    return summary


def run_campaign(specs: list[ConversationSpec], *, out_dir: Path, minimize_failures: bool = True,
                 progress: Callable[[str], None] = print, title: str = "Conversational robustness report",
                 settings: dict[str, Any] | None = None,
                 runner: Callable[[ConversationSpec, Path], dict[str, Any]] | None = None,
                 stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="redteam_"))
    runner = runner or (lambda spec, directory: run_conversation(spec, workdir=directory))
    results = []
    started = time.perf_counter()
    try:
        for index, spec in enumerate(specs, start=1):
            if stop is not None and stop():
                progress(f"  stopping after {index - 1} conversations (model budget or quota exhausted)")
                break
            result = runner(spec, workdir)
            results.append(result)
            if demo_failures(result):
                save_failure(result, out_dir, workdir, minimize_failures)
            if index % 25 == 0 or index == len(specs):
                failing = sum(1 for item in results if demo_failures(item))
                progress(f"  {index}/{len(specs)} conversations, {sum(r['turns'] for r in results)} turns, "
                         f"{failing} with violations ({time.perf_counter() - started:.0f} s)")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return write_reports(results, out_dir, title=title, settings=settings or {},
                         seconds=time.perf_counter() - started)


def save_failure(result: dict[str, Any], out_dir: Path, workdir: Path, minimize_failures: bool) -> None:
    """Save the failing transcript and a (minimized) regression candidate per violated invariant."""
    failures = out_dir / "failures"
    failures.mkdir(parents=True, exist_ok=True)
    (failures / f"{result['name']}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    (failures / f"{result['name']}.md").write_text(render_failure_markdown(result), encoding="utf-8")
    messages = messages_from_transcript(result.get("transcript", []))
    live_logic, soft = bool(result["spec"]["live_logic"]), bool(result.get("soft_labels"))
    seen = set()
    for violation in demo_failures(result):
        if violation["invariant"] in seen:
            continue
        seen.add(violation["invariant"])
        reduced = messages[:violation["turn"]] if violation["turn"] else messages
        reproduced = replay(reduced, workdir=workdir, live_logic=live_logic, soft_labels=soft)
        reproducible = any(v["invariant"] == violation["invariant"] for v in reproduced["violations"])
        if not reproducible:
            reduced = messages
        elif minimize_failures:
            reduced = minimize(reduced, violation["invariant"], workdir=workdir, live_logic=live_logic,
                               soft_labels=soft)
        name = f"{violation['invariant']}__{result['spec']['seed']}"
        document = regression_document(result, violation, reduced, name)
        document["reproduced_by_replay"] = reproducible
        write_yaml(out_dir / "regression_candidates" / f"{name}.yaml", document)