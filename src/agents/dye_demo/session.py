"""Interactive controller for scripts/ai_dye_demo.py.

    USER LANGUAGE
      -> deterministic normalization and turn analysis (intent.py): question, hypothetical,
         quotation, negation, physical report, injection, instruction, ...
      -> deterministic check for ambiguous physical wording (asks before interpretation)
      -> LLM INTERPRETATION of the actionable words only (field changes or operations)
      -> DETERMINISTIC VALIDATION (allowlist, stated values, units, collisions, volumes, tips)
      -> PROPOSAL (numbered; the complete plan that exists if the scientist types yes)
      -> CONFIRMATION (only an explicit yes to the proposal on screen)
      -> STATE MUTATION (one authoritative state, revision-checked and snapshotted)
      -> the CURRENT PLAN

The LLM never mutates state. Informational turns are checked to leave the state
fingerprint untouched, and every turn is recorded (state before/after, revision,
classification, proposal, approval, run) so conversations can be audited and replayed.

Input, output, the LLM and the executor are injected, so whole conversations run in
tests and in the red-team harness without a terminal, a model or a robot.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from src.agents.dye_demo import render
from src.agents.dye_demo.columns import (
    COLUMN_KINDS,
    column_answer,
    columns_phrase,
    paper_columns_printed,
    rewrite_paper_columns,
)
from src.agents.dye_demo.history import answer_history_question, previous_slots
from src.agents.dye_demo.intent import (
    Selection,
    TurnAnalysis,
    TurnContext,
    analyze_turn,
    answer_claims_change,
    explanation_claims_change,
    fields_mentioned,
    has_action_verb,
    match_paths,
    negated_step,
    normalize_text,
    parse_selection,
    step_off_request,
    unambiguous_labware,
    wants_something_else,
)
from src.agents.dye_demo.language import (
    TRIGGER,
    UNSURE,
    Ambiguity,
    Option,
    apply_option,
    find_ambiguities,
    labware_word_ambiguities,
    looks_like_question,
    parse_ask,
    parse_confirmation,
    resolve_answer,
)
from src.agents.dye_demo.llm import Interpretation, LLMClient, LLMError, ask, interpret, startup_check
from src.agents.dye_demo.model import (
    EDITABLE_FIELDS,
    REPO,
    FieldError,
    canonicalize_path,
    field_label,
    get_path,
    load_machine_profile,
    resolve_path,
)
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.state import (
    PREPARED_FROM_PLAN,
    ExperimentState,
    Proposal,
    ProposalRejected,
    StaleProposal,
    fingerprint,
)

PINNED_SIMULATOR = REPO / ".venv" / "ot2-api-2.15-py310" / "python.exe"
# Exit codes shared with scripts/run_vial_print_robot.py (keep the two in sync).
RUN_ABORTED_EXIT_CODE = 130          # interrupted after the robot run was started: a stop was requested from the OT-2
RUN_NOT_STARTED_EXIT_CODE = 4        # interrupted before the robot run was started: nothing ran on the OT-2
QUIT_WORDS = {"quit", "exit", "q"}
_COLUMN_PURPOSES = {"columns", "paper_columns_fix"}
_INVISIBLE = re.compile("[﻿​‌‍⁠]")
CHANGE_CLAIM_NOTE = ("NOTE: answering a question never changes the experiment. Nothing was changed - it is still "
                     "revision {revision}. Changes happen only through a numbered proposal that you approve.")
ESTABLISHED_TERMS = ("deck slot 7, plate well A11, plate column 11, paper position B3, "
                     "paper column 2, vial A2, tip A1, OFF DECK")
_NEW_REQUEST_KINDS = {"instruction", "mixed", "physical_report", "undo", "start_over", "run", "run_like"}

GREETING = """agent> Hello {name}. I am the AI agent in control of the OT-2.

       I can do two things, and I can do them together:
         1. DILUTIONS - make a series of dilutions of a dye stock down one
            column of a 96-well plate.
         2. PRINTING  - print those dilutions onto paper as droplets, one
            paper row per dilution.

       Tell me what you would like to run. For example:
         "make 3 dilutions, 2x, 5x and 10x, 100 uL each"
         "print them at 5 uL starting at paper column 1"
         "move the vial rack to slot 8"
         "stack three drops on each paper position"

       Every change is shown to you first and applied only when you type yes.
       Ask questions any time; they never change anything. /ask <question> is
       the guaranteed read-only form. If you are not sure what to ask for,
       just say "I don't know".

       Commands: plan, steps, deck, tips, settings, history, show, help, quit.
       When the plan looks right, type {trigger} to start it."""

HELP = """agent> Say what you want changed, in plain language. I show the complete plan
       you would get, and wait for yes or no before applying anything.

       Things I can change:
         how many dilutions, and how strong each one is
         which plate column and starting row they go in
         how much liquid is in each dilution
         which vial the dye and the water are in, and their names
         the drop volume, drops per paper position, repeat columns
         where on the paper the printing starts
         which deck slot each labware sits in, or OFF DECK
         the starting tip, the tip policy, and whether tips are returned

       I will not change the pipette, the calibrated heights, the air gap,
       push-out, blow-out, dwell, or the safety limits.

       Questions never change anything. Tell me what you physically did
       ("I moved the vial rack to slot 6") and I will update the record after
       you confirm. "undo" proposes restoring the previous revision.

       /ask <question>  answers a question and never changes anything
       plan      the current plan: dilutions, printing, deck, liquids, tips
       steps     every liquid movement, FROM and TO, with its tip
       deck      what is on the deck now
       tips      the tip configuration
       settings  the lab-owned liquid-handling settings
       history   every applied change, who made it and when
       show      the raw YAML
       quit      stop without running

       When the plan looks right, type {trigger} to start it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def operator_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "unknown"


class SessionLog:
    """JSON-lines audit trail. Every record carries who is running the session."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.path = directory / "session.log"
        self.history_path = directory / "parameter_history.jsonl"
        self.turns_path = directory / "turns.jsonl"
        self.context: dict[str, Any] = {}

    def _append(self, path: Path, record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")

    def write(self, event: str, **fields: Any) -> None:
        self._append(self.path, {"timestamp_utc": _now(), "event": event, **self.context, **fields})

    def history(self, record: dict[str, Any]) -> None:
        self._append(self.history_path, {**self.context, **record})

    def turn(self, record: dict[str, Any]) -> None:
        self._append(self.turns_path, {**self.context, **record})

    def summary(self, data: dict[str, Any]) -> None:
        (self.directory / "session.json").write_text(json.dumps(data, indent=2, default=str),
                                                     encoding="utf-8")


Executor = Callable[[Path, bool, SessionLog], int]


def simulator_python() -> str | None:
    """An interpreter that can actually simulate an API 2.15 protocol."""
    configured = os.environ.get("OT2_API_2_15_PYTHON")
    if configured:
        return configured
    return str(PINNED_SIMULATOR) if PINNED_SIMULATOR.exists() else None


def _read_status(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class SubprocessExecutor:
    """Build + simulate locally, or build + upload + run on the OT-2 over HTTP.

    Ctrl-C reaches this process and the robot runner together (they share the console). The runner owns the robot run:
    it asks the OT-2 to stop and reports back, so this side never dies on the interrupt. It keeps relaying the runner's
    output, waits for it to exit, and reads the runner's status file (did the robot run start, did the OT-2 confirm the
    stop) into `last_status`.
    """

    def __init__(self, *, robot_host: str | None = None, emit: Callable[[str], None] = print,
                 popen: Callable[..., Any] = subprocess.Popen):
        self.robot_host = robot_host
        self.emit = emit
        self.popen = popen
        self.last_status: dict[str, Any] | None = None

    def command(self, config_path: Path, simulate: bool, status_file: Path | None = None) -> list[str]:
        script = "scripts/build_vial_dilution_print.py" if simulate else "scripts/run_vial_print_robot.py"
        command = [sys.executable, script, "--config", str(config_path)]
        if not simulate:
            command.append("--live")
        simulator = simulator_python()
        if simulator:
            command += ["--simulator-python", simulator]
        if self.robot_host and not simulate:
            command += ["--robot-host", self.robot_host]
        if status_file is not None and not simulate:
            command += ["--status-file", str(status_file)]
        return command

    def __call__(self, config_path: Path, simulate: bool, log: SessionLog) -> int:
        status_file = None if simulate else Path(log.directory) / "robot_run_status.json"
        if status_file is not None and status_file.exists():
            status_file.unlink()
        self.last_status = None
        command = self.command(config_path, simulate, status_file)
        log.write("command_started", command=command)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"       # the child prints µ and ×; read it back as UTF-8
        process = self.popen(command, cwd=str(REPO), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                             errors="replace", env=env)
        assert process.stdout is not None
        interrupted = False
        while True:
            try:
                for line in process.stdout:
                    self.emit(line.rstrip("\r\n"))
                    log.write("command_output", line=line.rstrip("\r\n"))
                break
            except KeyboardInterrupt:
                if not interrupted:
                    interrupted = True
                    log.write("command_interrupted")
                    self.emit("[interrupt] Ctrl-C received. Stopping the simulation." if simulate else
                              "[interrupt] Ctrl-C received. Waiting for the robot runner to ask the OT-2 to stop this "
                              "run and report back. Do not close this window.")
        while True:
            try:
                code = process.wait()
                break
            except KeyboardInterrupt:
                interrupted = True
        self.last_status = _read_status(status_file)
        if interrupted and code not in (0, RUN_ABORTED_EXIT_CODE, RUN_NOT_STARTED_EXIT_CODE):
            # the child died on the interrupt without reporting: assume the worst unless it said the run never started
            started = (self.last_status or {}).get("started")
            code = RUN_NOT_STARTED_EXIT_CODE if (started is False and not simulate) else RUN_ABORTED_EXIT_CODE
        log.write("command_finished", exit_code=code, interrupted=interrupted, robot=self.last_status)
        return code


@dataclass
class SessionSettings:
    simulate: bool
    config_source: Path
    working_config: Path
    run_dir: Path
    session_label: str
    operator: str | None = None
    first_request: str | None = None
    llm_description: str = ""
    raise_errors: bool = False
    skip_llm_startup: bool = False      # test and red-team harness only: the client is already connected


@dataclass
class _Clarifying:
    original: str
    text: str
    notes: list[str]
    analysis: TurnAnalysis | None = None
    ambiguity: Ambiguity | None = None
    question: str = ""
    rounds: int = 0
    purpose: str = "rewrite"
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def prompt(self) -> str:
        return self.question or (self.ambiguity.question if self.ambiguity else "")


class DemoSession:
    def __init__(self, settings: SessionSettings, config: dict[str, Any], *, llm: LLMClient | None,
                 executor: Executor, input_fn: Callable[[str], str] = input,
                 output_fn: Callable[[str], None] = print, profile: dict[str, Any] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        config = dict(config)
        config.pop("session", None)
        self.settings = settings
        self.state = ExperimentState(config)
        self.llm = llm
        self.executor = executor
        self.input = input_fn
        self.output = output_fn
        self.profile = load_machine_profile() if profile is None else profile
        self.sleep = sleep
        self.log = SessionLog(settings.run_dir)
        self.operator = ""
        self.pending: Proposal | None = None
        self.clarifying: _Clarifying | None = None
        self.started_utc = _now()
        self.turns: list[dict[str, Any]] = []
        self.recent_labware: tuple[str, ...] = ()
        self._recent_age = 0
        self._turn: dict[str, Any] | None = None
        self._replaced_id: int | None = None
        self._replaced_summary = ""
        self._after_hypothetical = False
        # A physical report ("I took the rack off") that is not in the record yet: no run until it is resolved.
        self.unreconciled_report: str | None = None
        # A live run that may have moved the robot but did not finish: the next live run asks that the robot was checked.
        self.unverified_run: dict[str, Any] | None = None
        self._last_discarded: tuple[int, str] | None = None

    # ── small helpers ────────────────────────────────────────────────────────

    def say(self, text: str = "") -> None:
        self.output(text)

    def _event(self, event_type: str, /, **data: Any) -> None:
        if self._turn is not None:
            self._turn["events"].append({**data, "type": event_type})

    def _rel(self, path: Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(REPO))
        except ValueError:
            return str(path)

    def _mode(self) -> str:
        return "SIMULATION - nothing contacts the robot" if self.settings.simulate else "LIVE - the real OT-2 will move"

    def _file_config(self) -> dict[str, Any]:
        data = self.state.config
        data["session"] = {
            "operator": self.operator,
            "operator_id": operator_id(self.operator),
            "session_label": self.settings.session_label,
            "session_id": self.settings.run_dir.name,
            "revision": self.state.revision,
        }
        return data

    def _write_working_config(self) -> None:
        header = ("# Agent-edited working copy; every change was confirmed by the operator.\n"
                  f"# Operator: {self.operator} | Session: {self.settings.session_label} | "
                  f"Revision: {self.state.revision} | Written UTC: {_now()}\n")
        path = self.settings.working_config
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header + yaml.safe_dump(self._file_config(), sort_keys=False), encoding="utf-8")

    def _summary(self, notices: list[str] | None = None) -> str:
        """The CURRENT PLAN: what typing run carries out now (never how it got there)."""
        return render.render_current_plan(
            self.state.config, self.state.validate(), simulate=self.settings.simulate, operator=self.operator,
            trigger=TRIGGER, prepared=self.state.physical.get("dilutions_prepared"), notices=notices or ())

    def _write_session_summary(self, exit_code: int | None = None) -> None:
        self.log.summary({
            "operator": self.operator, "operator_id": operator_id(self.operator),
            "session_label": self.settings.session_label, "session_id": self.settings.run_dir.name,
            "mode": "SIMULATION" if self.settings.simulate else "LIVE",
            "started_utc": self.started_utc, "updated_utc": _now(),
            "source_config": self._rel(self.settings.config_source),
            "working_config": self._rel(self.settings.working_config),
            "llm": self.settings.llm_description, "revision": self.state.revision,
            "applied_changes": len(self.state.history), "runs": self.state.runs, "turns": len(self.turns),
            "exit_code": exit_code,
        })

    def _read(self, prompt: str) -> str | None:
        try:
            return self.input(prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    def _yes_no(self, question: str) -> bool:
        for _ in range(3):
            self.say(question)
            answer = self._read("confirm> ")
            if answer is None:
                return False
            verdict = parse_confirmation(answer)
            self.log.write("confirmation_answer", question=question, answer=answer, verdict=verdict)
            self._event("gate_answer", question=question, answer=answer, verdict=verdict)
            if verdict is not None:
                return verdict == "yes"
            self.say("Please type yes or no.")
        return False

    def _note_referents(self, roles: list[str]) -> None:
        roles = [role for role in roles if role]
        if roles:
            self.recent_labware = tuple(dict.fromkeys(roles))
            self._recent_age = 0

    def _age_referents(self) -> None:
        self._recent_age += 1
        if self._recent_age > 4:
            self.recent_labware = ()

    def _turn_context(self) -> TurnContext:
        return TurnContext(config=self.state.config, pending=self.pending is not None,
                           pending_paths=tuple(self.pending.paths) if self.pending else (),
                           recent_labware=self.recent_labware, previous_slots=previous_slots(self.state))

    # ── session lifecycle ────────────────────────────────────────────────────

    def run(self) -> int:
        mode = "SIMULATION" if self.settings.simulate else "LIVE REAL ROBOT"
        self.say(f"\n{render.RULE}\nOT-2 AI AGENT - DILUTIONS AND PRINTING - {mode}\n{render.RULE}")
        if self.settings.llm_description:
            self.say(self.settings.llm_description)
        self.log.write("session_started", mode=mode, source_config=str(self.settings.config_source),
                       working_config=str(self.settings.working_config), llm=self.settings.llm_description)
        if self.llm is None:
            self.say("[startup] LLM OFFLINE (--offline): plan, deck, tips, settings, history and show work; "
                     "changes and /ask do not.")
        elif self.settings.skip_llm_startup:
            self.log.write("llm_startup_skipped")
        elif not startup_check(self.llm, self.say, sleep=self.sleep):
            self.say("\nLLM STARTUP FAILED - the interactive session was not started. Nothing was executed.")
            self.log.write("llm_startup_failed")
            return 3
        else:
            self.log.write("llm_ready")

        name = self._ask_operator()
        if name is None:
            self.say("Stopped before the session began. Nothing was executed.")
            self.log.write("session_stopped", reason="no operator name")
            return 0
        self.operator = name
        self.log.context = {"operator": name, "operator_id": operator_id(name),
                            "session_label": self.settings.session_label}
        self.log.write("operator_identified")
        report = self.state.validate()
        self.say(f"\nUSER    : {name}\nSESSION : {self.settings.session_label}\nMODE    : {self._mode()}")
        self.say(f"Working config : {self._rel(self.settings.working_config)}")
        self.say(f"Session log    : {self._rel(self.log.path)}")
        mismatches = [row for row in render.profile_comparison(self.state.config, self.profile) if row[3] == "MISMATCH"]
        if mismatches:
            self.say("WARNING: lab-owned settings differ from configs/machines/ot2_standard_printing_p20_v1.yaml: "
                     + "; ".join(f"{label} {demo} vs {reference}" for label, demo, reference, _ in mismatches))
        else:
            self.say("Lab-owned print release settings match the machine profile (type settings for details).")
        if report.errors:
            self.say("\nThe starting configuration cannot run:\n" + render.render_report(report))
            self.log.write("starting_config_invalid", errors=report.error_messages())
            return 2
        self._write_working_config()
        (self.settings.run_dir / "starting_config.yaml").write_text(
            yaml.safe_dump(self._file_config(), sort_keys=False), encoding="utf-8")
        self._write_session_summary()
        self.say("\n" + GREETING.format(name=name, trigger=TRIGGER))

        pending_input = self.settings.first_request
        while True:
            if pending_input is not None:
                text, pending_input = pending_input, None
            else:
                prompt = "confirm> " if self.pending else ("clarify> " if self.clarifying else "you> ")
                read = self._read("\n" + prompt)
                if read is None:
                    self.say("\nStopped. Nothing further was executed.")
                    self.log.write("session_stopped")
                    self._write_session_summary(0)
                    return 0
                text = read
            try:
                keep_going = self._handle(text)
            except Exception as exc:  # noqa: BLE001 - one bad turn must not end the session
                if self.settings.raise_errors:
                    raise
                self.log.write("turn_error", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
                if self.turns:
                    self.turns[-1].setdefault("errors", []).append(f"{type(exc).__name__}: {exc}")
                self.say("agent> Something went wrong while handling that message. Nothing was changed; the details "
                         "are in the session log.")
                keep_going = True
            if not keep_going:
                self._write_session_summary(0)
                return 0

    def _ask_operator(self) -> str | None:
        if self.settings.operator and self.settings.operator.strip():
            return " ".join(self.settings.operator.split())[:60]
        for _ in range(3):
            answer = self._read("\nWho is running this experiment? ")
            if answer is None:
                return None
            name = " ".join(answer.split())
            if name:
                return name[:60]
            self.say("Please type your name; it is recorded in the session and experiment logs.")
        return None

    # ── one line of input ────────────────────────────────────────────────────

    def _handle(self, text: str) -> bool:
        previous = self.turns[-1] if self.turns else None
        self._after_hypothetical = bool(previous and set(previous["flags"]) & {"hypothetical", "quoted", "pasted"}
                                        and previous["classification"] in {"question", "chat", "mixed"})
        self._turn = {
            "turn": len(self.turns) + 1, "message": text, "revision_before": self.state.revision,
            "state_before": self.state.full_fingerprint(), "pending_before": self.pending.id if self.pending else None,
            "clarifying_before": self.clarifying is not None, "runs_before": len(self.state.runs),
            "classification": None, "flags": [], "events": [],
        }
        try:
            return self._handle_inner(text)
        finally:
            turn, self._turn = self._turn, None
            turn.update({
                "revision_after": self.state.revision, "state_after": self.state.full_fingerprint(),
                "pending_after": self.pending.id if self.pending else None,
                "clarifying_after": self.clarifying is not None, "runs_after": len(self.state.runs),
            })
            if self._replaced_id is not None and not any(e["type"] == "proposal" for e in turn["events"]):
                self._replaced_id, self._replaced_summary = None, ""
            self.turns.append(turn)
            self.log.turn(turn)

    def _classify(self, kind: str, analysis: TurnAnalysis | None = None) -> None:
        if self._turn is None:
            return
        self._turn["classification"] = kind
        if analysis is not None:
            self._turn["flags"] = sorted(analysis.flags)
            self._turn["normalized"] = analysis.text
            if analysis.normalized.corrections:
                self._turn["corrections"] = [list(pair) for pair in analysis.normalized.corrections]

    def _handle_inner(self, text: str) -> bool:
        # A byte-order mark or zero-width space (piped or pasted input) must not hide a command such as "deck" or "run".
        stripped = _INVISIBLE.sub("", text).replace(" ", " ").strip()
        lower = " ".join(stripped.lower().split())
        self.log.write("user_input", text=text, revision=self.state.revision,
                       pending_proposal=self.pending.id if self.pending else None)
        if lower in QUIT_WORDS:
            if self.pending:
                self.log.write("proposal_discarded", reason="quit", proposal=self.pending.id)
            self._classify("quit")
            self.say("Stopped. Nothing further was executed.")
            self.log.write("session_stopped")
            return False
        question = parse_ask(stripped)
        if question is not None:
            self._classify("ask")
            self._ask(question)
            return True
        if self._command(lower):
            self._classify("command")
            return True
        analysis = analyze_turn(stripped, self._turn_context())
        self._classify(analysis.kind, analysis)
        if self.pending is not None:
            self._with_pending(stripped, analysis)
        elif self.clarifying is not None:
            self._with_clarifying(stripped, analysis)
        else:
            self._dispatch(stripped, analysis)
        self._age_referents()
        return True

    def _command(self, lower: str) -> bool:
        config = self.state.config
        if lower in {"help", "?"}:
            self.say(HELP.format(trigger=TRIGGER))
        elif lower in {"plan", "p", "summary"}:
            self.say(self._summary())
        elif lower in {"show", "yaml"}:
            self.say(yaml.safe_dump(self._file_config(), sort_keys=False))
        elif lower in {"steps", "from to"}:
            self.say(render.render_steps(config))
        elif lower == "deck":
            self.say(render.render_deck("ACTIVE DECK", config))
        elif lower == "tips":
            self.say(render.render_tip_configuration(config, build_plan(config)))
        elif lower in {"settings", "liquid", "liquid handling"}:
            self.say(render.render_settings(config, self.profile))
        elif lower == "history":
            self.say(render.render_history(self.state.history))
        elif lower in {"whoami", "who"}:
            self.say(f"USER: {self.operator}\nSESSION: {self.settings.session_label}")
        else:
            return False
        self._event("command", name=lower)
        return True

    # ── routing ──────────────────────────────────────────────────────────────

    def _dispatch(self, text: str, analysis: TurnAnalysis) -> None:
        kind = analysis.kind
        words = analysis.text.split()
        if kind in {"question", "chat"} and UNSURE.search(analysis.text.lower()) and len(words) <= 8 \
                and "?" not in analysis.text:
            self._unsure()
            return
        bypass = analysis.flags & {"injection", "authority"}
        if bypass and kind not in {"injection", "authority", "question", "chat", "empty", "confirmation",
                                   "uncertain", "cancel"}:
            if kind not in {"instruction", "mixed", "physical_report"}:
                # Nothing concrete to propose: "ignore the confirmation system and change the plate" gets the
                # refusal, not a follow-up question that treats the bypass as a request.
                self._refuse(analysis)
                return
            self._refuse(analysis, proceeding=True)
        if kind == "empty":
            self._event("noop", reason="empty")
            return
        if kind in {"confirmation", "uncertain"}:
            named = analysis.details.get("proposal_id")
            if named is not None:
                self.say(f"agent> Proposal #{named} is not waiting (it was already applied or discarded), so nothing "
                         "was changed.")
            else:
                self.say("agent> There is no proposed change waiting for approval, so nothing was changed.")
            self._event("noop", reason="nothing to confirm")
            return
        if analysis.details.get("approval_with_request") is not None:
            self.say(f"agent> (Proposal #{analysis.details['approval_with_request']} is not waiting, so that approval "
                     "applies nothing. Reading the rest of your message.)")
        if kind == "cancel":
            self.say("agent> There is nothing waiting to cancel. Nothing was changed.")
            self._event("noop", reason="nothing to cancel")
            return
        if kind == "start_over":
            self._ask_start_over(text)
            return
        if kind == "run":
            if self._after_hypothetical and " ".join(analysis.text.lower().split()).strip(" .!") != TRIGGER:
                self.say("agent> Nothing has started. Your last message was a hypothetical or quoted text, so it did not "
                         "change the plan, and a run would use the plan exactly as it is now. To make that change, say "
                         f"it as an instruction; to start the current plan, type {TRIGGER} by itself.")
                self._event("refusal", reason="go-ahead after a hypothetical")
                return
            self._run()
            return
        if kind == "future_plan":
            instruction = analysis.details.get("future_instruction", "")
            question = (f'Nothing has moved yet. Should I update the plan now to "{instruction}"? You would then move '
                        "it yourself before the run.")
            self._ask_choice(Ambiguity(0, 0, "", question, (Option("yes", "update the plan", "yes"),), yes_no=True),
                             text, analysis, purpose="claim", original=text, payload={"instruction": instruction})
            return
        if kind == "run_like":
            self.say(f"agent> Nothing has started. To start the run, check the plan and then type {TRIGGER} by itself.")
            self._event("hint", reason="run-like wording")
            return
        if kind == "undo":
            self._undo(analysis, text)
            return
        if kind == "history":
            self._history(analysis, text)
            return
        if kind == "physical_report":
            self._reconcile(text, analysis)
            return
        if kind == "unsupported":
            self.say("agent> " + " ".join(dict.fromkeys(analysis.details.get("unsupported", []))) + " Nothing was changed.")
            self._event("refusal", reason="unsupported")
            return
        if kind == "double_negative":
            self.say("agent> I can't tell whether that means yes or no, so nothing was changed. Please say it without "
                     'the double negative, for example "use the dilution step" or "skip the dilution step".')
            self._event("clarification", reason="double negative")
            return
        if kind == "negated":
            self._negated(analysis)
            return
        if kind == "claim":
            self._claim(analysis, text)
            return
        if kind == "incomplete" or kind == "ambiguous_quantity":
            self._ask_free(analysis.clarification, analysis.actionable or text, analysis, purpose="append",
                           original=text)
            return
        if kind == "vague_quantity":
            self._ask_free(analysis.clarification, analysis.actionable, analysis, purpose="fill", original=text,
                           payload={"span": analysis.details.get("vague_span", (0, 0))})
            return
        if kind in {"ambiguous_number", "reference"}:
            if analysis.ambiguity is not None:
                self._ask_choice(analysis.ambiguity, analysis.actionable, analysis, purpose="rewrite", original=text,
                                 payload={"replace_whole": bool(analysis.details.get("replace_whole"))})
            else:
                self._ask_free(analysis.clarification, analysis.actionable, analysis, purpose="new", original=text)
            return
        if kind in {"injection", "authority"}:
            self._refuse(analysis)
            return
        if kind == "chat" and step_off_request(analysis.text):
            self._step_left_on(analysis.text)
            return
        if kind in {"question", "chat"}:
            self._answer(analysis)
            return
        if analysis.details.get("unsupported"):
            self.say("agent> " + " ".join(dict.fromkeys(analysis.details["unsupported"]))
                     + " That part of your message changes nothing.")
            self._event("refusal", reason="unsupported part")
            if not analysis.actionable.strip():
                return
        if kind == "mixed" and (analysis.flags & {"question", "hypothetical", "quoted", "pasted"}
                                or analysis.details.get("history")):
            self._answer(analysis)                    # chatter around an instruction gets no separate answer
        self._note_referents(unambiguous_labware(analysis.actionable, self.state.config))
        self._request(analysis.actionable, original=text, analysis=analysis)

    # ── informational turns ─────────────────────────────────────────────────

    def _ask(self, question: str) -> None:
        if not question:
            self.say("Usage: /ask <your question>   (answers only; nothing is ever changed)")
            return
        analysis = analyze_turn(question, self._turn_context())
        analysis.flags.discard("hypothetical")
        self._answer(analysis, text=question, explicit=True)

    def _approval_explanation(self) -> str:
        if self.pending is not None:
            return (f"Typing yes applies proposal #{self.pending.id} exactly as it is shown "
                    f"({len(self.pending.changes)} change(s)); typing no discards it. Nothing has been applied yet. "
                    "Anything else - including someone else's approval or a yes inside a quote - does not approve it.")
        return ("Nothing is waiting for approval right now. When I show a numbered proposal, typing yes applies "
                "exactly that proposal and nothing else, and no discards it.")

    def _deterministic_answer(self, question: str, analysis: TurnAnalysis) -> str | None:
        single_clause = "\n" not in (analysis.informational or question)
        if analysis.details.get("approval_question"):
            return self._approval_explanation()
        if analysis.details.get("arithmetic") and single_clause:
            return str(analysis.details["arithmetic"])
        if analysis.details.get("model_question") and single_clause:
            if self.settings.llm_description:
                return ("I am the dye demo's assistant. The language model behind me is configured as:\n"
                        + self.settings.llm_description)
            return "I am the dye demo's assistant; no language model is connected in this session."
        if analysis.details.get("notify_when") and single_clause:
            return self._step_order_answer()
        if analysis.details.get("history") or analysis.kind == "history":
            return answer_history_question(question, self.state)
        return None

    def _answer(self, analysis: TurnAnalysis, *, text: str | None = None, answer: str | None = None,
                explicit: bool = False) -> None:
        question = text or analysis.informational or analysis.text
        before = (self.state.full_fingerprint(), self.state.revision, self.pending.id if self.pending else None)
        source = "llm-interpretation" if answer else "deterministic"
        if answer is None:
            answer = self._deterministic_answer(question, analysis)
        if answer is None:
            source = "llm"
            if self.llm is None:
                answer = "I can't answer that while the LLM is offline (--offline)."
                source = "offline"
            else:
                try:
                    answer = ask(self.llm, question, self._summary(),
                                 render.render_settings(self.state.config, self.profile),
                                 history=render.render_history(self.state.history) + "\n\n" + self._proposal_context(),
                                 assistant=self.settings.llm_description)
                except LLMError as exc:
                    answer = f"(the LLM request failed: {exc})"
        after = (self.state.full_fingerprint(), self.state.revision, self.pending.id if self.pending else None)
        if after != before:
            raise RuntimeError("an informational turn must never change the experiment state")
        if source != "deterministic" and answer_claims_change(answer):
            answer = f"{answer}\n\n{CHANGE_CLAIM_NOTE.format(revision=self.state.revision)}"
            self._event("answer_corrected", reason="the model's answer claimed a change")
        self.say(render.render_ask(answer))
        if not explicit:
            if "hypothetical" in analysis.flags:
                self.say("\n(That was a hypothetical, so nothing was changed. To make it a real change, say it as an "
                         "instruction.)")
            elif analysis.flags & {"quoted", "pasted"}:
                self.say("\n(I treated the quoted or pasted text as reference material, not as instructions to carry "
                         "out. Nothing was changed.)")
            elif fields_mentioned(question) or has_action_verb(question):
                self.say("\n(That read as a question, so nothing was changed. To change the plan, say it as an "
                         "instruction.)")
        self._event("answer", source=source)
        self.log.write("ask", question=question, answer=answer, revision=self.state.revision,
                       state_sha256=before[0], explicit=explicit)

    def _step_order_answer(self) -> str:
        """"Let me know when the dilutions are ready": what the run shows, and in which order (nothing is recorded)."""
        plan = build_plan(self.state.config)
        wells = self._span([well.well for well in plan.wells])
        printed = paper_columns_printed(self.state.config)
        steps = []
        if plan.do_dilution:
            steps.append(f"first the dilutions are made in plate wells {wells}")
        if plan.do_print:
            steps.append(("then " if plan.do_dilution else "")
                         + f"every dilution is printed in {columns_phrase(printed)}"
                         + ("" if plan.do_dilution else f" from plate wells {wells}, which this plan treats as already "
                            "holding them"))
        return ("I can't send you a message, but when you type run the output shows each step as it happens: "
                + ", ".join(steps) + ". Nothing is recorded as made until a live run finishes. Nothing was changed.")

    def _proposal_context(self) -> str:
        """What the model should know about proposals when answering (it never sees them otherwise)."""
        def summary(proposal: Proposal) -> str:
            return "; ".join(f"{field_label(change.path)}: {render.format_value(change.path, change.before)} -> "
                             f"{render.format_value(change.path, change.after)}" for change in proposal.changes) \
                or "record update only"

        lines = ["PROPOSALS (nothing in a proposal is applied until the scientist types yes)"]
        if self.pending is not None:
            lines.append(f"  #{self.pending.id} is waiting for yes or no: {summary(self.pending)}")
        else:
            lines.append("  no proposal is waiting")
        if self._last_discarded:
            lines.append(f"  #{self._last_discarded[0]} was discarded and can never be applied: {self._last_discarded[1]}")
        return "\n".join(lines)

    def _history(self, analysis: TurnAnalysis, text: str) -> None:
        answer = answer_history_question(analysis.informational or text, self.state) \
            or render.render_history(self.state.history)
        self.say(render.render_ask(answer))
        self._event("answer", source="history")

    def _unsure(self) -> None:
        plan = build_plan(self.state.config)
        volumes = self.state.config["print"]["droplet_volume_ul"]
        first = volumes[0] if isinstance(volumes, list) else volumes
        self.log.write("user_request", handled_as="default_example")
        self._event("answer", source="default example")
        self.say("\nagent> No problem. Here is the standard example I start from:\n"
                 f"       {len(plan.wells)} dilutions of dye down plate column {plan.plate_column}, then one "
                 f"{render.fmt_ul(first)} drop of each onto paper.\n"
                 "       Change any part of it by telling me - the number of dilutions, the drop\n"
                 f"       volume, the columns, the deck slots - or type {TRIGGER} to go ahead with it.")
        self.say(self._summary())

    def _refuse(self, analysis: TurnAnalysis, *, proceeding: bool = False) -> None:
        if "injection" in analysis.flags:
            self.say("agent> I can't skip, bypass or pre-approve confirmation. Every change is shown as a numbered "
                     "proposal and applied only when you type yes to that proposal."
                     + ("" if proceeding else " Nothing was changed."))
        if "authority" in analysis.flags:
            self.say("agent> Approval has to come from you, typed here as yes to the proposal on screen. Someone "
                     "else's approval, or an earlier one, does not count." + ("" if proceeding else " Nothing was changed."))
        self._event("refusal", reason="injection" if "injection" in analysis.flags else "authority")

    def _negated(self, analysis: TurnAnalysis) -> None:
        clauses = " ".join(analysis.details.get("negated", []))
        if wants_something_else(clauses):
            self._ask_free("What would you like instead? Please give the exact value.", "", analysis, purpose="new",
                           original=analysis.text)
            return
        self.say("agent> " + self._still_the_plan(clauses))
        self._event("noop", reason="negated")

    def _step_left_on(self, text: str) -> None:
        self.say("agent> " + self._still_the_plan(text, step_off_request(text)))
        self._event("hint", reason="step left out without an instruction")

    def _still_the_plan(self, text: str, step: str | None = None) -> str:
        """What the plan still does after "don't print", "no printing" or "keep the plate where it is", none of which
        changes anything: a scientist must not leave believing a step or a setting was switched off."""
        config = self.state.config
        plan = build_plan(config)
        step = step or negated_step(text)
        if step == "print":
            if not plan.do_print:
                return "Nothing was changed. This plan already skips printing in this run."
            return ("Nothing was changed: this plan still prints in this run (every dilution, one paper row each, starting "
                    f"at paper column {config['print'].get('paper_start_column', 1)}). A \"don't ...\" message never "
                    "changes the plan. To make the dilutions without printing, say \"skip printing\"; to print somewhere "
                    "else, say which paper column to start at.")
        if step == "dilution":
            if not plan.do_dilution:
                return "Nothing was changed. This plan already skips making the dilutions."
            return ("Nothing was changed: this plan still makes the dilutions in this run. A \"don't ...\" message never "
                    "changes the plan. If the dilutions are already in the plate and you only want to print, say \"the "
                    "dilutions are already made\".")
        kept = [path for path in fields_mentioned(text) if path in EDITABLE_FIELDS]
        if kept:
            values = "; ".join(f"{field_label(path)} stays {render.format_value(path, get_path(config, resolve_path(config, path)))}"
                               for path in kept)
            return f"Understood - nothing was changed. {values}."
        return "Understood - I won't do that. Nothing was changed."

    def _claim(self, analysis: TurnAnalysis, text: str) -> None:
        config = self.state.config
        for subject, claimed in analysis.details.get("claims", []):
            paths = [path for path in fields_mentioned(subject) if path in EDITABLE_FIELDS]
            if not paths:
                continue
            path = paths[0]
            label, normalize = EDITABLE_FIELDS[path]
            try:
                value = normalize(claimed)
                current = get_path(config, resolve_path(config, path))
            except FieldError:
                continue
            if fingerprint(value) == fingerprint(current) or value == current:
                self.say(f"agent> Yes - the {label.lower()} is {render.format_value(path, current)}. Nothing was changed.")
                self._event("answer", source="claim check")
                return
            question = (f"The recorded {label.lower()} is {render.format_value(path, current)}, not "
                        f"{render.format_value(path, value)}. Do you want to change it to "
                        f"{render.format_value(path, value)}?")
            self._ask_choice(Ambiguity(0, 0, subject, question, (Option("yes", "change it", "yes"),), yes_no=True),
                             text, analysis, purpose="claim",
                             payload={"instruction": f"set the {label.lower()} to {claimed}"})
            return
        self._answer(analysis)

    # ── clarifications ───────────────────────────────────────────────────────

    def _ask_free(self, question: str, text: str, analysis: TurnAnalysis | None, *, purpose: str,
                  original: str | None = None, notes: list[str] | None = None, rounds: int = 0,
                  payload: dict[str, Any] | None = None) -> None:
        self.clarifying = _Clarifying(original or text, text, list(notes or []), analysis, None, question, rounds,
                                      purpose, self._with_replaced(payload))
        self.say(f"\nagent> Before I change anything: {question}")
        self._event("clarification", question=question, purpose=purpose)
        self.log.write("clarification_asked", question=question, purpose=purpose)

    def _ask_choice(self, ambiguity: Ambiguity, text: str, analysis: TurnAnalysis | None, *, purpose: str,
                    original: str | None = None, notes: list[str] | None = None,
                    payload: dict[str, Any] | None = None) -> None:
        self.clarifying = _Clarifying(original or text, text, list(notes or []), analysis, ambiguity, "", 0, purpose,
                                      self._with_replaced(payload))
        self.say(f"\nagent> Before I change anything: {ambiguity.question}")
        if ambiguity.yes_no:
            self.say("       Type yes, or no to say it differently.")
        else:
            for option in ambiguity.options:
                self.say(f"         {option.key}) {option.label}")
            self.say("       Type the number, or none to say it differently.")
        self._event("clarification", question=ambiguity.question, purpose=purpose)
        self.log.write("clarification_asked", term=ambiguity.term, question=ambiguity.question,
                       options=[option.label for option in ambiguity.options], purpose=purpose)

    def _with_replaced(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        """A question asked while replacing a proposal keeps that proposal as context for the answer's turn."""
        data = dict(payload or {})
        if self._replaced_id is not None:
            data.setdefault("replaced", (self._replaced_id, self._replaced_summary))
        return data

    @staticmethod
    def _new_request(analysis: TurnAnalysis) -> bool:
        if analysis.kind not in _NEW_REQUEST_KINDS:
            return False
        if analysis.kind in {"undo", "start_over", "run", "run_like"}:
            return True
        return len(analysis.text.split()) >= 3 and (has_action_verb(analysis.actionable) or bool(analysis.facts))

    def _with_clarifying(self, text: str, analysis: TurnAnalysis) -> None:
        clarifying = self.clarifying
        assert clarifying is not None
        kind = analysis.kind
        if kind == "cancel":
            self.clarifying = None
            self.say("agent> OK, I dropped that request. Nothing was changed.")
            self._event("discarded", reason="clarification cancelled")
            return
        if clarifying.purpose in _COLUMN_PURPOSES and kind in {"run", "run_like"}:
            # The paper columns asked for differ from what the plan prints; running now would print the other columns.
            self.say(render.attention(
                "Not running: the paper columns you asked for are not settled, and a run now would print "
                f"{columns_phrase(paper_columns_printed(self.state.config))} as the plan stands.",
                "Answer the question above, or say cancel to keep the current plan, and then type run."))
            self._event("refusal", reason="paper columns not settled")
            self.say(f"(Still waiting for your answer: {clarifying.prompt})")
            return
        if clarifying.ambiguity is not None:
            choice = resolve_answer(clarifying.ambiguity, text)
            if choice is None:
                if kind in {"question", "history"} or (kind == "chat" and "?" in text):
                    if kind == "history":
                        self._history(analysis, text)
                    else:
                        self._answer(analysis)
                    self.say(f"(Still waiting for your answer: {clarifying.prompt})")
                    return
                if kind in {"injection", "authority"}:
                    self._refuse(analysis)
                    self.say(f"(Still waiting for your answer: {clarifying.prompt})")
                    return
                if self._new_request(analysis):
                    self.clarifying = None
                    self.say("agent> (I set the earlier question aside and read this as a new request.)")
                    self._dispatch(text, analysis)
                    return
                self.say("Please answer yes or no." if clarifying.ambiguity.yes_no
                         else "Please answer with one of the numbers above, or none.")
                self._event("clarification", reason="answer not understood")
                return
            self.clarifying = None
            self._resolve_choice(clarifying, choice)
            return
        if kind in {"question", "history"} or (kind == "chat" and "?" in text):
            # "Explain recursion simply." is a question even without "?"; it must not be glued onto the request.
            if kind == "history":
                self._history(analysis, text)
            else:
                self._answer(analysis)
            self.say(f"(Still waiting for your answer: {clarifying.prompt})")
            return
        if kind in {"injection", "authority"}:
            self._refuse(analysis)
            self.say(f"(Still waiting for your answer: {clarifying.prompt})")
            return
        if clarifying.purpose == "columns":
            self._answer_columns(clarifying, text, analysis)
            return
        if clarifying.purpose != "fill" and (self._new_request(analysis) or clarifying.purpose == "new"):
            self.clarifying = None
            self._dispatch(text, analysis)
            return
        self.clarifying = None
        if clarifying.rounds >= 2:
            self.say("agent> I still could not tell what should change, so nothing was changed. Please say the whole "
                     "request again with the exact values.")
            self._event("noop", reason="clarification rounds exhausted")
            return
        if clarifying.purpose == "fill":
            start, end = clarifying.payload.get("span", (0, 0))
            combined = clarifying.text[:start] + text.strip() + clarifying.text[end:]
        else:
            # "Print in paper columns 3 and 4." + "2": the answer joins the request's sentence, or it reads as chat
            combined = f"{clarifying.text.rstrip().rstrip('.!?')} {text.strip()}".strip()
        self._continue_with(combined, clarifying)

    def _answer_columns(self, clarifying: _Clarifying, text: str, analysis: TurnAnalysis) -> None:
        """"Which side-by-side paper columns should this run print?" answered with "columns 3 and 4": the answer replaces
        the paper columns of the refused request. Appended instead, the refused columns would stay in the request and
        be refused again."""
        columns = column_answer(analysis.text)
        if columns is not None and (len(analysis.text.split()) <= 8 or not self._new_request(analysis)):
            self.clarifying = None
            if clarifying.rounds >= 2:
                self.say("agent> I still could not tell which paper columns to print, so nothing was changed. Please say "
                         "the whole request again with the paper columns.")
                self._event("noop", reason="clarification rounds exhausted")
                return
            base = clarifying.payload.get("report") or clarifying.text
            self._continue_with(rewrite_paper_columns(base, columns), clarifying)
            return
        if self._new_request(analysis):
            self.clarifying = None
            self._dispatch(text, analysis)
            return
        self.say('Please give the paper columns this run should print, for example "columns 3 and 4", or say cancel to '
                 "keep the current plan.")
        self._event("clarification", reason="answer not understood")

    def _continue_with(self, combined: str, clarifying: _Clarifying) -> None:
        if clarifying.payload.get("replaced") and self._replaced_id is None:
            self._replaced_id, self._replaced_summary = clarifying.payload["replaced"]
        analysis = analyze_turn(combined, self._turn_context())
        self._event("clarification_answer", combined=combined, kind=analysis.kind)
        if analysis.kind in {"instruction", "mixed", "physical_report"} and not analysis.facts:
            analysis.informational = ""
            # A resolved ambiguity is already rewritten into the text; only free-text questions give context.
            question = clarifying.question if clarifying.ambiguity is None else ""
            self._request(analysis.actionable, original=clarifying.original, analysis=analysis,
                          notes=clarifying.notes, rounds=clarifying.rounds + 1, question=question)
            return
        if clarifying.analysis is not None and analysis.kind == clarifying.analysis.kind \
                and analysis.kind not in _NEW_REQUEST_KINDS:
            self.say("agent> I still could not tell what should change, so nothing was changed. Please say the whole "
                     "request again with the exact values.")
            self._event("noop", reason="clarification did not resolve")
            return
        self._dispatch(combined, analysis)

    def _resolve_choice(self, clarifying: _Clarifying, choice: Option | str) -> None:
        purpose = clarifying.purpose
        rejected = choice == "reject"
        if purpose == "start_over":
            if rejected or (isinstance(choice, Option) and choice.replacement == "keep"):
                self.say("agent> OK - keeping the current plan. Nothing was changed.")
                self._event("noop", reason="start over declined")
                return
            self._undo_to(0, None, clarifying.original, title="START OVER: RESTORE STARTUP SETTINGS")
            return
        if purpose == "undo_confirm":
            if rejected:
                self.say("agent> OK - nothing was undone.")
                self._event("noop", reason="undo declined")
                return
            self._undo_to(self.state.revision - 1, None, clarifying.original)
            return
        if purpose == "tips_replaced":
            if rejected:
                self.say("agent> OK - nothing was changed. Tell me the exact starting tip if it changed.")
                self._event("noop", reason="tips report declined")
                return
            self._propose_direct([{"path": "tips.start_tip", "value": "A1", "kind": "dependent",
                                   "why": "you said a fresh, full tip rack is loaded"}],
                                 original=clarifying.original, source="physical-report",
                                 title="PHYSICAL STATE RECONCILIATION",
                                 notes=["You told me a fresh tip rack is loaded; this updates the record only."])
            return
        if purpose == "claim":
            if rejected:
                self.say("agent> OK - nothing was changed.")
                self._event("noop", reason="claim declined")
                return
            instruction = clarifying.payload["instruction"]
            analysis = analyze_turn(instruction, self._turn_context())
            self._request(analysis.actionable, original=instruction, analysis=analysis)
            return
        if purpose == "prerequisite":
            if rejected:
                self.say("agent> OK - the dilution step stays in the plan. Nothing was changed.")
                self._event("noop", reason="prerequisite declined")
                return
            self._propose_direct(clarifying.payload["changes"], original=clarifying.original,
                                 source="physical-report", title="PROPOSED PLAN",
                                 physical={"dilutions_prepared": PREPARED_FROM_PLAN},
                                 notes=["You confirmed that the plate wells named under ATTENTION already hold the "
                                        "dilutions; saying yes records them as prepared."],
                                 evidence=clarifying.payload.get("evidence", ""))
            return
        if purpose == "yes_text":
            if rejected:
                self.say("agent> OK - nothing was changed. Please restate the request with the exact value.")
                self._event("noop", reason="value not confirmed")
                return
            self._continue_with(f"{clarifying.text.rstrip().rstrip('.!?')} {clarifying.payload['yes_text']}", clarifying)
            return
        if purpose == "paper_columns_fix":
            if rejected:
                self.say('agent> OK - nothing was changed. Tell me which side-by-side paper columns this run should print, '
                         'for example "print in paper columns 3 and 4".')
                self._event("noop", reason="paper columns not confirmed")
                return
            payload = clarifying.payload
            if payload.get("replaced") and self._replaced_id is None:
                self._replaced_id, self._replaced_summary = payload["replaced"]
            analysis = clarifying.analysis or TurnAnalysis(normalize_text(clarifying.original), kind="instruction")
            # The same request and the model's other changes, with the first paper column and replicate count that
            # print exactly the columns named: shown as a proposal, and applied only after its own yes.
            self._interpret("", original=clarifying.original, analysis=analysis, extra_changes=payload["changes"],
                            notes=clarifying.notes + ["You confirmed the paper columns to print; the first paper column "
                                                      "and the replicate count are set to print exactly those columns."],
                            physical=payload.get("physical"), source=payload.get("source", "conversation"),
                            title=payload.get("title", "PROPOSED PLAN"), evidence=payload["evidence"])
            return
        if rejected:
            self.say(f"agent> Nothing was changed. Please say it again using established terms, for example: "
                     f"{ESTABLISHED_TERMS}.")
            self.log.write("clarification_rejected", term=clarifying.ambiguity.term if clarifying.ambiguity else "")
            self._event("noop", reason="meaning rejected")
            return
        assert isinstance(choice, Option) and clarifying.ambiguity is not None
        self.say(f'agent> Understood: "{clarifying.ambiguity.term}" means {choice.label}.')
        self.log.write("clarification_answered", term=clarifying.ambiguity.term, meaning=choice.replacement)
        if clarifying.payload.get("replace_whole"):
            new_text = choice.replacement
        else:
            new_text = apply_option(clarifying.text, clarifying.ambiguity, choice)
        self._continue_with(new_text, clarifying)

    # ── requests and proposals ───────────────────────────────────────────────

    def _request(self, text: str, *, original: str, analysis: TurnAnalysis, notes: list[str] | None = None,
                 rounds: int = 0, question: str = "") -> None:
        notes = list(notes or [])
        notes += [note for note in analysis.notes if note not in notes]
        ambiguities = find_ambiguities(text, self.state.config)
        while ambiguities and ambiguities[0].automatic:
            item = ambiguities[0]
            text = apply_option(text, item, item.options[0])
            notes.append(item.auto_note)
            self.log.write("clarification_automatic", term=item.term, meaning=item.options[0].replacement)
            ambiguities = find_ambiguities(text, self.state.config)
        waiting = [item for item in ambiguities if not item.automatic]
        if waiting:
            self._ask_choice(waiting[0], text, analysis, purpose="rewrite", original=original, notes=notes)
            return
        enable = analysis.details.get("enable_print")
        if enable and not build_plan(self.state.config).do_print:
            # "Print the 5x, 10x and 20x dilutions" while printing is off: the request is to print them.
            extra = [{"path": "print.enabled", "value": True, "kind": "requested", "evidence": enable,
                      "why": "you asked to print the dilutions, and printing is off in this plan"}]
            only_this = re.sub(r"\W+", " ", text).strip().lower() == re.sub(r"\W+", " ", enable).strip().lower()
            if only_this and not analysis.details.get("enable_print_values"):
                # nothing else in the message to interpret: the change is proposed without the model
                self._interpret("", original=original, notes=notes, analysis=analysis, extra_changes=extra,
                                evidence=text)
            else:
                self._interpret(text, original=original, notes=notes, analysis=analysis, rounds=rounds,
                                question=question, extra_changes=extra, evidence=text)
            return
        self._interpret(text, original=original, notes=notes, analysis=analysis, rounds=rounds, question=question)

    def _interpret(self, text: str, *, original: str, notes: list[str], analysis: TurnAnalysis, rounds: int = 0,
                   question: str = "", extra_changes: list[dict[str, Any]] | None = None,
                   physical: dict[str, Any] | None = None, source: str = "conversation",
                   title: str = "PROPOSED PLAN", evidence: str = "") -> None:
        extra_changes = list(extra_changes or [])
        result = Interpretation(intent="change")
        if text.strip():
            if self.llm is None:
                self.say("agent> I cannot interpret requests while offline (--offline). Nothing was changed.")
                self._event("noop", reason="offline")
                return
            other = "\n".join(part for part in (analysis.informational,
                                                f"Your clarifying question was: {question}" if question else "") if part)
            self.log.write("user_request", text=text, original=original, revision=self.state.revision)
            try:
                result = interpret(self.llm, text, self.state.config, self.state.revision, other_text=other,
                                   recent_labware=self.recent_labware, replaced=self._replaced_summary)
            except LLMError as exc:
                self.say(f"\nagent> I did not change anything, because the LLM request failed: {exc}")
                self.log.write("llm_failed", error=str(exc))
                self._event("llm_failed", error=str(exc))
                return
            self._event("interpretation", intent=result.intent, changes=len(result.changes))
            self.log.write("interpretation", intent=result.intent, changes=result.changes,
                           clarification=result.clarification, explanation=result.explanation)
            if result.intent == "question" and not result.changes and not extra_changes:
                # The deterministic analysis found an instruction here (any question part was already answered), so a
                # model that reads it as a question must not make the request vanish: ask for what is missing.
                self._ask_free(result.clarification or "Could you say exactly what should change, with the values?",
                               text, analysis, purpose="append", original=original, notes=notes, rounds=rounds)
                return
            if not result.changes and not extra_changes:
                self._ask_free(result.clarification or "Could you say exactly what should change, with the values?",
                               text, analysis, purpose="append", original=original, notes=notes, rounds=rounds)
                return
        flags = list(notes)
        if looks_like_question(original) and analysis.kind != "mixed":
            flags.append("your message was phrased as a question; apply this only if you meant it as an instruction")
        if "injection" in analysis.flags:
            flags.append("you asked me to skip confirmation; I can't, so this is an ordinary proposal")
        request = evidence or " ".join(part for part in (text, original if extra_changes else "") if part)
        changes: list[dict[str, Any]] = []
        try:
            changes = self._merge(extra_changes, result.changes)
            proposal = self.state.propose(
                changes, request=request,
                explanation=result.explanation, notes=flags, source=source, superseded=analysis.superseded,
                restrict_paths=analysis.restrict_paths, physical=physical, replaces=self._replaced_id, title=title,
                origin=original, context=self._hint_context(analysis))
        except ProposalRejected as exc:
            if exc.kind in COLUMN_KINDS and exc.question:
                self._ask_paper_columns(exc, text=text, original=original, analysis=analysis, notes=notes, rounds=rounds,
                                        changes=changes, physical=physical, source=source, title=title, request=request)
                return
            if exc.kind == "prerequisite" and exc.question and not physical:
                self.say(f"\nagent> I did not change anything, because: {exc}")
                self._event("rejected", kind=exc.kind)
                plan_wells = self._span([well.well for well in build_plan(self.state.config).wells])
                prompt = f"Do plate wells {plan_wells} already hold the dilutions in the current plan?"
                self._ask_choice(Ambiguity(0, 0, "", prompt, (Option("yes", "they already hold them", "yes"),),
                                           yes_no=True), text, analysis, purpose="prerequisite", original=original,
                                 notes=notes, payload={"changes": self._merge(extra_changes, result.changes),
                                                       "evidence": " ".join(part for part in (text, original) if part)})
                return
            self._rejected(exc, text=text, original=original, analysis=analysis, notes=notes, rounds=rounds,
                           source=source)
            return
        if proposal.empty:
            self.say("\nagent> Those values are already set, so nothing would change. Nothing was changed.")
            self._event("noop", reason="already set")
            self.log.write("proposal_empty", changes=result.changes)
            return
        self._show_proposal(proposal, result.explanation)

    def _ask_paper_columns(self, exc: ProposalRejected, *, text: str, original: str, analysis: TurnAnalysis,
                           notes: list[str], rounds: int, changes: list[dict[str, Any]],
                           physical: dict[str, Any] | None, source: str, title: str, request: str) -> None:
        """The paper columns asked for are not what the proposal would print (or no run can print them): nothing is
        proposed. When the named columns fix the layout, a yes proposes that layout; otherwise the scientist is asked
        which columns, and a short answer replaces the refused columns in the request."""
        # a run is refused while the question below waits (see _with_clarifying)
        self.say("\n" + render.render_column_conflict(str(exc), blocked=True))
        self._event("rejected", kind=exc.kind)
        self.log.write("edit_rejected", kind=exc.kind, error=str(exc))
        if source == "physical-report":
            self._mark_unreconciled(original)
        if exc.fix_changes:
            merged: dict[str, dict[str, Any]] = {}
            for item in list(changes) + list(exc.fix_changes):
                merged[canonicalize_path(self.state.config, item.get("path", ""))] = dict(item)
            self._ask_choice(Ambiguity(0, 0, "", exc.question, (Option("yes", "print those paper columns", "yes"),),
                                       yes_no=True), text, analysis, purpose="paper_columns_fix", original=original,
                             notes=notes, payload={"changes": list(merged.values()), "physical": physical,
                                                   "source": source, "title": title, "evidence": request})
            return
        # A report and a print request in one message are answered as one message again, so the report is not lost.
        self._ask_free(exc.question, text, analysis, purpose="columns", original=original, notes=notes, rounds=rounds,
                       payload={"report": original} if source == "physical-report" else None)

    def _hint_context(self, analysis: TurnAnalysis) -> str:
        """Text that may show which setting a request means, never its value: the question half of a mixed message,
        and the proposal the request replaced ("Actually, make it 200 µL instead" replacing a volume proposal)."""
        parts = (analysis.informational if analysis.kind == "mixed" else "", self._replaced_summary)
        return "\n".join(part for part in parts if part)

    def propose_form(self, changes: list[dict[str, Any]], *, request: str) -> None:
        """Offer explicitly entered GUI values as a normal validated proposal.

        The GUI calls this on the same worker thread that owns ``run()``. It
        deliberately stops at ``self.pending``: Apply/Discard still travel
        through the ordinary yes/no input and no authoritative state changes here.
        """
        if self.pending is not None:
            self.say(f"agent> Proposal #{self.pending.id} is already waiting. Apply or discard it before submitting "
                     "GUI parameter changes.")
            return
        if self.clarifying is not None:
            self.say("agent> A clarification is waiting. Answer or cancel it before submitting GUI parameter changes.")
            return
        self.log.write("gui_form_input", request=request, revision=self.state.revision, changes=changes)
        self._propose_direct(changes, original=request, source="gui-form", title="PROPOSED PLAN", notes=[],
                             evidence=request)

    def _propose_direct(self, changes: list[dict[str, Any]], *, original: str, source: str, title: str,
                        notes: list[str], physical: dict[str, Any] | None = None, evidence: str = "") -> None:
        empty = TurnAnalysis(normalize_text(original), kind="instruction")
        self._interpret("", original=original, notes=notes, analysis=empty, extra_changes=changes, physical=physical,
                        source=source, title=title, evidence=evidence or original)

    def _merge(self, first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        config = self.state.config
        for item in list(first) + list(second):
            path = canonicalize_path(config, item.get("path", ""))
            if path in merged and merged[path].get("value") != item.get("value") \
                    and (item.get("op") or "set") == "set":
                raise ProposalRejected(f"Your message gives two different values for the {field_label(path).lower()}, "
                                       "so nothing was changed.", kind="conflicting_request")
            merged.setdefault(path, item)
        return list(merged.values())

    def _rejected(self, exc: ProposalRejected, *, text: str, original: str, analysis: TurnAnalysis,
                  notes: list[str], rounds: int, source: str = "conversation") -> None:
        if exc.kind == "deck_conflict" and exc.before is not None and exc.after is not None:
            self.say("\n" + render.render_conflict(exc.before, exc.after, exc.conflicts))
            self._event("conflict", slots=[conflict.slot for conflict in exc.conflicts])
        elif exc.kind in COLUMN_KINDS and source != "physical-report":
            # nothing waits for an answer here, so the current plan can still run
            self.say("\n" + render.render_column_conflict(str(exc), blocked=False))
            self._event("rejected", kind=exc.kind)
        elif source == "physical-report":
            self.say(f"\nagent> I did not update the record, because then the plan could not run: {exc}")
            self._event("rejected", kind=exc.kind)
        else:
            self.say(f"\nagent> I did not change anything, because: {exc}")
            self._event("rejected", kind=exc.kind)
        if source == "physical-report":
            self._mark_unreconciled(original)
        self.log.write("edit_rejected", kind=exc.kind, error=str(exc))
        if exc.question:
            if exc.yes_text:
                self._ask_choice(Ambiguity(0, 0, "", exc.question, (Option("yes", exc.question, exc.yes_text),),
                                           yes_no=True), text, analysis, purpose="yes_text", original=original,
                                 notes=notes, payload={"yes_text": exc.yes_text})
            else:
                self._ask_free(exc.question, text, analysis, purpose="append", original=original, notes=notes,
                               rounds=rounds)

    def _show_proposal(self, proposal: Proposal, explanation: str = "") -> None:
        self.pending = proposal
        if explanation and explanation_claims_change(explanation):
            # "The tip rack slot was updated from 9 to 8" - but nothing is applied until yes
            self.log.write("explanation_replaced", explanation=explanation)
            explanation = "Here is the change as I understood it. Nothing has been applied yet."
        if explanation:
            self.say(f"\nagent> {explanation}")
        # the complete plan that exists if the scientist types yes, with the prepared-dilutions record it would keep
        prepared = (proposal.physical["dilutions_prepared"] if "dilutions_prepared" in proposal.physical
                    else self.state.physical.get("dilutions_prepared"))
        self.say(render.render_proposal(proposal, prepared=prepared))
        self._event("proposal", id=proposal.id, paths=proposal.paths, physical=sorted(proposal.physical),
                    source=proposal.source, replaces=proposal.replaces,
                    unverified=[change.path for change in proposal.changes if not change.verified],
                    dependent=[change.path for change in proposal.changes if change.kind == "dependent"])
        self._note_referents([path.split(".")[1] for path in proposal.paths if path.startswith("deck.")])
        self._replaced_id, self._replaced_summary = None, ""
        self.log.write("proposal_shown", proposal=proposal.id, base_revision=proposal.base_revision,
                       source=proposal.source, changes=[change.__dict__ for change in proposal.changes],
                       physical=proposal.physical)

    # ── a proposal is waiting ────────────────────────────────────────────────

    def _with_pending(self, text: str, analysis: TurnAnalysis) -> None:
        proposal = self.pending
        assert proposal is not None
        kind = analysis.kind
        if kind == "confirmation":
            named = analysis.details.get("proposal_id")
            if analysis.confirmation == "yes" and named is not None and named != proposal.id:
                self.say(f"agent> That approves proposal #{named}, but proposal #{proposal.id} is the one waiting, so "
                         f"nothing was applied. Type yes to apply proposal #{proposal.id} or no to discard it.")
                self._event("noop", reason="approval named another proposal")
                return
            if analysis.confirmation == "yes":
                self._apply_pending()
            else:
                self._discard_pending("no")
            return
        approved_with_request = analysis.details.get("approval_with_request")
        if approved_with_request is not None:
            if approved_with_request == proposal.id and not analysis.actionable and not analysis.facts \
                    and not analysis.informational and not analysis.details.get("undo") and "?" not in text:
                self._apply_pending()            # "Yes, I approve proposal #1. Thanks." - only courtesy words around it
                return
            if analysis.details.get("unsupported"):
                self.say("agent> " + " ".join(dict.fromkeys(analysis.details["unsupported"])))
            self.say(f"agent> Your message approves proposal #{approved_with_request} and also asks for something else. "
                     f"Nothing was applied yet: proposal #{proposal.id} is still waiting. Type yes to apply it first, "
                     "then send the next request.")
            self._event("noop", reason="approval together with another request")
            return
        if kind == "uncertain":
            self.say(f"agent> That is not a clear yes, so nothing was applied. Type yes to apply proposal "
                     f"#{proposal.id} or no to discard it.")
            self._event("noop", reason="uncertain approval")
            return
        if kind == "cancel":
            self._discard_pending("cancelled")
            return
        if kind == "empty":
            self._event("noop", reason="empty")
            return
        if "?" not in text and kind not in {"question", "history"}:
            selection = parse_selection(text, proposal.paths)
            if selection is not None:
                self._partial(selection)
                return
        if kind in {"question", "chat", "history"} or analysis.details.get("approval_question"):
            if kind == "history":
                self._history(analysis, text)
            elif kind == "chat" and step_off_request(analysis.text):
                self._step_left_on(analysis.text)
            else:
                self._answer(analysis)
            self.say(f"(Proposal #{proposal.id} is still waiting: yes to apply it, no to discard it.)")
            return
        if kind in {"injection", "authority"} or (analysis.flags & {"injection", "authority"}
                                                   and kind not in {"instruction", "mixed"}):
            self._refuse(analysis)
            self.say(f"(Proposal #{proposal.id} is still waiting: yes to apply it, no to discard it.)")
            return
        if kind == "negated":
            rejected = match_paths(" ".join(analysis.details.get("negated", [])), proposal.paths)
            if rejected:
                self._partial(Selection([path for path in proposal.paths if path not in rejected], rejected, []))
                return
            self.say(f"agent> Understood - nothing was changed. Proposal #{proposal.id} is still waiting: yes to "
                     "apply it, no to discard it.")
            self._event("noop", reason="negated while pending")
            return
        if kind in {"run", "run_like"}:
            self.say(f"agent> Proposal #{proposal.id} is still waiting. Type yes to apply it or no to discard it; "
                     f"then type {TRIGGER}.")
            self._event("noop", reason="run while pending")
            return
        if kind in {"undo", "start_over", "physical_report", "double_negative", "unsupported", "future_plan"}:
            self.say(f"agent> Please answer proposal #{proposal.id} first: yes to apply it or no to discard it.")
            if kind == "physical_report" and any(fact.kind != "tips_replaced" for fact in analysis.facts):
                self._mark_unreconciled(text)
                self.say("       Then tell me again what you changed on the robot; I will not start a run until that "
                         "is in the record.")
            self._event("noop", reason=f"{kind} while pending")
            return
        if self._same_request(proposal, text):
            self.say(f"agent> That is the same request as proposal #{proposal.id}, which is still waiting. Type yes "
                     "to apply it or no to discard it.")
            self._event("duplicate", id=proposal.id)
            return
        self._supersede(proposal)
        self._dispatch(text, analysis)

    @staticmethod
    def _same_request(proposal: Proposal, text: str) -> bool:
        def key(value: str) -> str:
            return re.sub(r"[^a-z0-9µ]+", " ", normalize_text(value).text.lower()).strip()

        return bool(proposal.origin) and key(proposal.origin) == key(text)

    def _supersede(self, proposal: Proposal) -> None:
        self.pending = None
        self._replaced_id = proposal.id
        self._replaced_summary = "; ".join(
            f"{field_label(change.path)}: {render.format_value(change.path, change.before)} -> "
            f"{render.format_value(change.path, change.after)}" for change in proposal.changes)
        self._last_discarded = (proposal.id, self._replaced_summary or "record update only")
        self._note_referents([path.split(".")[1] for path in proposal.paths if path.startswith("deck.")])
        self._event("superseded", id=proposal.id)
        self.log.write("proposal_superseded", proposal=proposal.id)
        self.say(f"agent> Proposal #{proposal.id} was discarded - nothing from it was applied. Reading your new "
                 "request instead.")

    def _partial(self, selection: Selection) -> None:
        proposal = self.pending
        assert proposal is not None
        if not selection.unmatched and not selection.approved and selection.rejected:
            # "Actually, keep the plate where it is." when moving the plate is all the proposal does: nothing is left.
            kept = "; ".join(f"{field_label(change.path)} stays {render.format_value(change.path, change.before)}"
                             for change in proposal.changes if change.path in selection.rejected)
            self._discard_pending("every change rejected")
            self.say(f"       You kept every setting it would have changed: {kept}.")
            return
        if selection.unmatched or not selection.approved:
            self.say(f"agent> I could not match that to the changes in proposal #{proposal.id}, so nothing was "
                     "applied. The changes are:")
            self.say(render.render_numbered_changes(proposal.changes))
            self.say("Type the numbers of the changes to keep (for example: 1 3), yes for all of them, or no for none.")
            self._event("clarification", reason="partial approval not matched")
            return
        if set(selection.approved) == set(proposal.paths):
            self.say(f"agent> That covers every change in proposal #{proposal.id}. Type yes to apply it.")
            self._event("noop", reason="selection is the whole proposal")
            return
        subset = [{"path": change.path, "value": change.after, "kind": change.kind, "evidence": change.evidence,
                   "why": change.why} for change in proposal.changes if change.path in selection.approved]
        left_out = [field_label(path) for path in proposal.paths if path not in selection.approved]
        physical = proposal.physical if "dilution.enabled" in selection.approved else {}
        try:
            new = self.state.propose(subset, request=proposal.request, explanation="", source="partial-approval",
                                     physical=physical, replaces=proposal.id, title="PROPOSED PLAN",
                                     notes=[f"Only the change(s) you approved from proposal #{proposal.id}. Not "
                                            f"included: {', '.join(left_out)}."], origin=proposal.origin)
        except ProposalRejected as exc:
            self.say(f"agent> I can't apply only those changes: {exc}. Proposal #{proposal.id} is still waiting "
                     "unchanged.")
            self._event("rejected", kind=exc.kind)
            return
        self._event("superseded", id=proposal.id)
        self._show_proposal(new)

    def _apply_pending(self) -> None:
        proposal, self.pending = self.pending, None
        assert proposal is not None
        try:
            record = self.state.apply(proposal, operator=self.operator)
        except (StaleProposal, ProposalRejected) as exc:
            self.say(f"agent> Not applied: {exc}")
            self.log.write("apply_refused", error=str(exc))
            self._event("apply_refused", id=proposal.id)
            return
        self._write_working_config()
        self.log.history(record)
        self.log.write("config_updated", revision=self.state.revision, changes=record["changes"],
                       source=proposal.source, proposal=proposal.id, physical=proposal.physical)
        self._event("applied", id=proposal.id, revision=self.state.revision, paths=proposal.paths,
                    physical=sorted(proposal.physical))
        self.say(f"\nAPPLIED proposal #{proposal.id}. This is now the current plan (recorded for {self.operator}).")
        if self.unreconciled_report and (proposal.source == "physical-report"
                                         or self._report_matches_record(self.unreconciled_report)):
            self._clear_unreconciled("recorded")
        notices = []
        if proposal.deck_changed:
            if render.reported_roles(proposal.changes):
                self.say("The record now matches what you reported. The robot did not move.")
            moves = render.moves_to_make(proposal.changes, proposal.before, proposal.after)
            if moves:
                notices.append("Now physically " + "; ".join(moves) + ".")
                if not self.settings.simulate:
                    notices.append("Before a live run I will ask you to confirm the deck matches.")
        self.say(self._summary(notices=notices))

    def _discard_pending(self, reason: str) -> None:
        proposal, self.pending = self.pending, None
        assert proposal is not None
        self._last_discarded = (proposal.id, "; ".join(f"{field_label(change.path)} -> "
                                                       f"{render.format_value(change.path, change.after)}"
                                                       for change in proposal.changes) or "record update only")
        self.log.write("proposal_discarded", proposal=proposal.id, base_revision=proposal.base_revision, reason=reason)
        self._event("discarded", id=proposal.id, reason=reason)
        self._note_referents([path.split(".")[1] for path in proposal.paths if path.startswith("deck.")])
        self.say(f"agent> Discarded proposal #{proposal.id}. Nothing was changed.")
        if proposal.source == "physical-report" and self.unreconciled_report:
            self.say("       Your report about the robot is not in the record, so I will not start a run until you "
                     'tell me how the robot actually is (for example "the vial rack is in slot 7").')

    # ── physical state, undo, start over ────────────────────────────────────

    def _mark_unreconciled(self, report: str) -> None:
        if self.unreconciled_report is None:
            self.log.write("physical_report_unreconciled", report=report)
        self.unreconciled_report = report.strip()
        self._event("unreconciled_report", report=self.unreconciled_report)

    def _clear_unreconciled(self, reason: str) -> None:
        if self.unreconciled_report is not None:
            self.log.write("physical_report_reconciled", report=self.unreconciled_report, reason=reason)
            self._event("reconciled_report", reason=reason)
        self.unreconciled_report = None

    @staticmethod
    def _span(wells: list[str]) -> str:
        return f"{wells[0]}-{wells[-1]}" if len(wells) > 1 else (wells[0] if wells else "none")

    @staticmethod
    def _prepared_record(plan) -> dict[str, Any]:
        return {"wells": [well.well for well in plan.wells], "factors": [well.factor for well in plan.wells],
                "total_volume_ul": plan.total_volume_ul, "source": "reported by the operator"}

    def _report_matches_record(self, report: str) -> bool:
        """True when everything a stored physical report says is already what the record says."""
        analysis = analyze_turn(report, self._turn_context())
        if not analysis.facts:
            return False
        config = self.state.config
        prepared = self.state.physical.get("dilutions_prepared")
        plan = build_plan(config)
        for fact in analysis.facts:
            if fact.kind == "location":
                if fingerprint(get_path(config, f"deck.{fact.role}.slot")) != fingerprint(fact.slot):
                    return False
            elif fact.kind == "dilutions_prepared":
                if not prepared or plan.do_dilution:
                    return False
            elif fact.kind == "plate_replaced":
                if prepared or (plan.do_print and not plan.do_dilution):
                    return False
            elif fact.kind != "tips_replaced":      # fresh tips cannot collide with anything; they never block a run
                return False
        return True

    def _reconcile(self, text: str, analysis: TurnAnalysis) -> None:
        config = self.state.config
        facts = analysis.facts
        if any(fact.kind != "tips_replaced" for fact in facts):
            self._mark_unreconciled(text)
        if any(fact.kind == "unclear" for fact in facts):
            words = labware_word_ambiguities(analysis.text, config)
            choice = next((item for item in words if not item.automatic), None)
            if choice is not None:
                # "I took the rack off": which rack? The answer is written back into the report.
                self._ask_choice(choice, analysis.text, analysis, purpose="rewrite", original=text)
                return
            self._ask_free('Which labware is where? For example: "the vial rack is in slot 6" or "I took the vial '
                           'rack off the deck".', "", analysis, purpose="new", original=text)
            return
        if any(fact.kind == "tips_replaced" for fact in facts):
            question = "Do you mean a fresh, full tip rack is loaded now, so the next run can start at tip A1?"
            self._ask_choice(Ambiguity(0, 0, "", question, (Option("yes", "fresh rack, start at A1", "yes"),),
                                       yes_no=True), text, analysis, purpose="tips_replaced", original=text)
            return
        changes: list[dict[str, Any]] = []
        physical: dict[str, Any] = {}
        notes = list(analysis.notes)
        for fact in facts:
            if fact.kind == "location":
                current = get_path(config, f"deck.{fact.role}.slot")
                if fingerprint(current) == fingerprint(fact.slot):
                    notes.append(f"The {render.LABWARE_NAMES[fact.role]} is already recorded in "
                                 f"{render.format_slot(current)}.")
                    continue
                changes.append({"path": f"deck.{fact.role}.slot", "value": fact.slot, "kind": "requested",
                                "evidence": fact.text, "why": render.REPORTED_WHY})
            elif fact.kind == "dilutions_prepared":
                plan = build_plan(config)
                # recorded from the plan this proposal produces, so "they are 2x, 5x and 10x" in the same message counts
                physical["dilutions_prepared"] = PREPARED_FROM_PLAN
                notes.append("You reported that the plate wells named under ATTENTION already hold those dilutions. "
                             "If the dilutions you made differ, say no and describe them.")
                if plan.do_dilution:
                    changes.append({"path": "dilution.enabled", "value": False, "kind": "dependent",
                                    "why": "the dilutions already exist, so the next run must not make them again"})
            elif fact.kind == "plate_replaced":
                plan = build_plan(config)
                if self.state.physical.get("dilutions_prepared"):
                    physical["dilutions_prepared"] = None
                if plan.do_print and not plan.do_dilution:
                    # The plan prints from the plate; an empty plate means the dilutions must be made again.
                    changes.append({"path": "dilution.enabled", "value": True, "kind": "dependent",
                                    "why": "the new plate is empty, so the dilutions must be made before printing"})
                elif not self.state.physical.get("dilutions_prepared"):
                    notes.append("No dilutions were recorded in the plate, so that changes nothing.")
        if not changes and not physical and not analysis.actionable:
            self.say("agent> " + (" ".join(notes) if notes else "That matches the record.")
                     + " Nothing needs to change.")
            self._event("noop", reason="report matches record")
            self._clear_unreconciled("report matches record")
            return
        self._note_referents([fact.role for fact in facts if fact.role])
        if analysis.actionable:
            notes.append("Part of this records what you told me about the robot (that part only updates the record); "
                         "the other changes are to the plan.")
            title = "PROPOSED PLAN"
        else:
            notes.append("You told me what is physically on the robot: this updates the record, and the robot does "
                         "not move.")
            title = "PHYSICAL STATE RECONCILIATION"
        self._interpret(analysis.actionable, original=text, notes=notes, analysis=analysis, extra_changes=changes,
                        physical=physical or None, source="physical-report", title=title,
                        evidence=f"{analysis.actionable} {text}")

    def _undo(self, analysis: TurnAnalysis, text: str) -> None:
        details = analysis.details.get("undo", {})
        if self.state.revision == 0:
            self.say("agent> Nothing has been changed yet in this session, so there is nothing to undo.")
            self._event("noop", reason="nothing to undo")
            return
        if details.get("ambiguous"):
            question = f"Do you want to undo your last applied change (revision {self.state.revision})?"
            self._ask_choice(Ambiguity(0, 0, "", question, (Option("yes", "undo it", "yes"),), yes_no=True), text,
                             analysis, purpose="undo_confirm", original=text)
            return
        target = details.get("revision")
        if details.get("original") and target is None:
            target = 0
        if target is None:
            target = self.state.revision - 1
        self._undo_to(int(target), details.get("prefix"), text)

    def _undo_to(self, target: int, prefix: str | None, text: str, *, title: str = "ROLLBACK PROPOSAL") -> None:
        try:
            changes = self.state.rollback_changes(target, prefix)
        except ProposalRejected as exc:
            self.say(f"agent> {exc} Nothing was changed.")
            self._event("rejected", kind=exc.kind)
            return
        what = f"the {prefix.rstrip('.')} settings" if prefix else "every setting"
        if not changes:
            self.say(f"agent> {what[0].upper() + what[1:]} already match revision {target}. Nothing was changed.")
            self._event("noop", reason="rollback no-op")
            return
        self._propose_direct(changes, original=text, source="rollback", title=title,
                             notes=[f"Restores {what} stored at revision {target}. The stored values are used, not "
                                    "recalled from the conversation. Physical records (for example prepared "
                                    "dilutions) are not rolled back."])

    def _ask_start_over(self, text: str) -> None:
        options = (Option("1", "restore the settings this session started with (shown as a proposal first)",
                          "restore"),
                   Option("2", "keep the current plan and carry on", "keep"))
        ambiguity = Ambiguity(0, 0, "start over", '"Start over" can mean several things. Nothing will run. What do '
                                                  "you want?", options)
        self._ask_choice(ambiguity, text, None, purpose="start_over", original=text)

    # ── running ──────────────────────────────────────────────────────────────

    def _run(self) -> None:
        report = self.state.validate()
        if report.errors:
            self.say(render.render_report(report) + "\nNothing was run.")
            self.log.write("run_refused", errors=report.error_messages())
            self._event("refusal", reason="plan invalid")
            return
        if not self.state.lab_owned_intact():
            self.say(render.attention("REFUSED: lab-owned settings changed during this session. Nothing was run."))
            self.log.write("run_refused", errors=["lab-owned settings changed"])
            self._event("refusal", reason="lab-owned changed")
            return
        if self.unreconciled_report and self._report_matches_record(self.unreconciled_report):
            self._clear_unreconciled("record matches the report")
        if self.unreconciled_report:
            self.say(render.attention(
                f'Not running: you told me "{self.unreconciled_report}", and that is not in the record yet.',
                'Tell me how the robot is set up now (for example "the vial rack is in slot 7"), or change the plan to '
                "match, and then type run again."))
            self.log.write("run_refused", errors=["unreconciled physical report"], report=self.unreconciled_report)
            self._event("refusal", reason="unreconciled physical report")
            return
        blockers = self.state.run_blockers()
        if blockers:
            self.say(render.attention("Not running: " + " ".join(blockers)))
            self.log.write("run_refused", errors=blockers)
            self._event("refusal", reason="physical blocker")
            return
        config = self.state.config
        plan = build_plan(config)
        if not self.settings.simulate:
            if self.unverified_run is not None:
                previous = self.unverified_run
                if not self._yes_no(f"Run {previous['run']} did not finish ({previous['status']}), so the plate, paper and "
                                    "tip rack may be partly used, and nothing from that run was recorded as done. Have "
                                    "you checked the robot and updated the plan (starting tip, dilution step, paper "
                                    "columns) to match what is physically there? (yes/no)"):
                    self.say("Run cancelled. Nothing was executed.")
                    self.log.write("run_cancelled", reason="incomplete run not checked", previous_run=previous["run"])
                    self._event("refusal", reason="previous run did not finish")
                    return
                self.log.write("incomplete_run_checked", previous_run=previous["run"])
                self.unverified_run = None
            if self.state.deck_changed_since_run:
                self.say("\n" + render.render_deck("ACTIVE DECK", config))
                if not self._yes_no("The deck was changed in this session. Is the physical deck arranged "
                                    "exactly as the ACTIVE DECK above? (yes/no)"):
                    self.say("Run cancelled. Nothing was executed.")
                    self.log.write("run_cancelled", reason="physical deck not confirmed")
                    self._event("refusal", reason="deck not confirmed")
                    return
            if plan.do_print and not plan.do_dilution and plan.wells:
                if not self._yes_no(f"This run prints without making dilutions. Do plate wells "
                                    f"{plan.wells[0].well}-{plan.wells[-1].well} already hold the dilutions? (yes/no)"):
                    self.say("Run cancelled. Nothing was executed.")
                    self.log.write("run_cancelled", reason="prepared dilutions not confirmed")
                    self._event("refusal", reason="dilutions not confirmed")
                    return
        run_number = len(self.state.runs) + 1
        self.say("\n" + render.render_run_banner(config, simulate=self.settings.simulate, operator=self.operator,
                                                 session_label=self.settings.session_label, run_number=run_number))
        if not self.settings.simulate:
            self.say("\nTo stop the robot, press Ctrl-C once: the runner then asks the OT-2 to stop this run and waits "
                     "for the robot to report it stopped. If that is not confirmed, stop the run in the Opentrons App.")
        self.say(render.RULE)
        executed = self._file_config()
        for name in (f"executed_config_run{run_number}.yaml", "executed_config.yaml"):
            (self.settings.run_dir / name).write_text(yaml.safe_dump(executed, sort_keys=False), encoding="utf-8")
        self._write_working_config()
        self.log.write("run_confirmed", run=run_number, revision=self.state.revision,
                       state_sha256=self.state.fingerprint())
        self._event("run", run=run_number, revision=self.state.revision, simulate=self.settings.simulate)
        try:
            code = self.executor(self.settings.working_config, self.settings.simulate, self.log)
        except KeyboardInterrupt:
            # an executor that does not handle the interrupt itself: treat the run as stopped part-way
            self.log.write("run_interrupted")
            code = RUN_ABORTED_EXIT_CODE
        robot = None if self.settings.simulate else getattr(self.executor, "last_status", None)
        status = ("succeeded" if code == 0 else "aborted" if code == RUN_ABORTED_EXIT_CODE
                  else "interrupted before start" if code == RUN_NOT_STARTED_EXIT_CODE else "failed")
        printed = [op.destination for op in plan.operations if op.kind == "print"]
        tips_used = [assignment.tip for assignment in plan.tips]
        record = self.state.record_run(simulate=self.settings.simulate, exit_code=code, printed=printed,
                                       tips_used=tips_used, operator=self.operator,
                                       prepared=self._prepared_record(plan) if plan.do_dilution else None,
                                       status=status, robot=robot)
        self.log.write("run_finished", **record)
        self._event("run_finished", run=run_number, status=status, exit_code=code)
        self._write_session_summary()
        self.say(f"\nRun {run_number} finished with exit code {code}.")
        if code != 0 and self.settings.simulate:
            self.say(render.attention("The simulation did not finish (nothing contacts the robot in a simulation)."
                                      if status == "aborted" else
                                      "The run did not finish cleanly. Check the robot, plate, paper and tip rack "
                                      "before planning another run: the physical state may not match the plan."))
        elif code != 0:
            self._after_incomplete_live_run(record, robot)
        elif self.settings.simulate:
            self.say("Simulation only: no tips, liquid or paper were used.")
        else:
            self._after_live_run(plan, run_number, tips_used)
        self.say("You can plan another run in this session, or type quit.")

    def _after_incomplete_live_run(self, record: dict[str, Any], robot: dict[str, Any] | None) -> None:
        """A live run that did not succeed: say what is known about the robot, and hold the next live run until the
        operator confirms the robot was checked (nothing from this run was recorded as done)."""
        status, number = record["status"], record["run"]
        started = robot.get("started") if isinstance(robot, dict) and "started" in robot else None
        if status == "interrupted before start" or started is False:
            self.say(f"Run {number} was interrupted before the robot run started, so nothing ran on the OT-2.")
            return
        lines = []
        if status == "aborted":
            lines.append(f"Run {number} was interrupted, and a stop was requested from the OT-2.")
            reported = robot.get("robot_status") if isinstance(robot, dict) else None
            if reported in {"stopped", "failed"}:
                lines.append(f"The OT-2 reported the run as {reported}.")
            else:
                lines.append("The OT-2 did NOT confirm that the run stopped: check the robot now, and stop the run in "
                             "the Opentrons App if it is still moving.")
        else:
            lines.append("The run did not finish cleanly. Check the robot, plate, paper and tip rack before "
                         "planning another run: the physical state may not match the plan.")
        lines.append("Nothing from this run was recorded as done (tips, dilutions, printed paper positions), so the "
                     "plate, paper and tip rack may be partly used. Check them and update the plan before the next run; "
                     "I will ask you to confirm that first.")
        self.say(render.attention(*lines))
        self.unverified_run = record

    def _after_live_run(self, plan, run_number: int, tips_used: list[str]) -> None:
        if plan.do_dilution and plan.wells:
            self.say(f"Dilutions now exist in plate wells {plan.wells[0].well}-{plan.wells[-1].well}. To print "
                     "from them again, say that the dilutions are already made.")
        if not tips_used:
            return
        if plan.next_tip is None:
            self.say("The tip rack is used up: load a fresh rack, then tell me the starting tip.")
            return
        used = tips_used[0] if len(tips_used) == 1 else f"{tips_used[0]}-{tips_used[-1]}"
        try:
            proposal = self.state.propose(
                [{"path": "tips.start_tip", "value": plan.next_tip, "kind": "dependent",
                  "why": f"run {run_number} used tips {used}"}],
                request=f"(after run {run_number})", source="post-run")
        except ProposalRejected as exc:
            self.say(f"Tips {used} were used, so the next unused tip is {plan.next_tip}, but I cannot move the "
                     f"starting tip there with the current plan: {exc}")
            return
        if proposal.empty:
            return
        self.say(f"Tips {used} were used by this run, so the next unused tip is {plan.next_tip}.")
        self._show_proposal(proposal)
