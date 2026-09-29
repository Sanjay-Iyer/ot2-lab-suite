"""Thread-safe bridge between NiceGUI callbacks and the real DemoSession loop."""
from __future__ import annotations

import queue
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Collection

from src.agents.dye_demo import render
from src.agents.dye_demo.language import TRIGGER
from src.agents.dye_demo.session import DEMO_RESET_BANNER, DemoSession

LOOP_PROMPTS = {"you>", "confirm>", "clarify>"}      # the session's own prompts; any other prompt is a question to show
# Reset Demo is taken only at the session's main prompt: idle, or a proposal or question of its own waiting. Never while
# it works or runs, and never inside a run's yes/no safety question.
RESET_STATES = frozenset({"idle", "proposal", "clarify"})
RESET_BUTTON = "Reset Demo"
STOP_WAIT_S = 180.0                                   # how long a server shutdown waits for a stopped robot run
LOADING_MESSAGE = "Loading AI Agent NanoDrop..."
# The page shows the same run statuses whichever execution backend (simulator or real OT-2) runs the plan.
_RUN_STATUS = {"succeeded": "RUN COMPLETE", "aborted": "RUN STOPPED", "interrupted before start": "RUN NOT STARTED",
               "failed": "RUN FAILED"}
_WAITING_STATUS = {"busy": "WORKING", "operator": "ENTER YOUR NAME", "proposal": "PROPOSAL WAITING",
                   "clarify": "NEEDS CLARIFICATION", "question": "ANSWER YES OR NO"}


def print_diagnostic(text: str) -> None:
    """Default home for diagnostics: the terminal window the page was started from (flushed so a pipe shows it too)."""
    print(text, flush=True)


@dataclass(frozen=True)
class ChatMessage:
    role: str             # user, assistant, status (a quiet progress line such as the loading message) or marker
    text: str             # (Reset Demo: where the new experiment starts in the transcript)


@dataclass(frozen=True)
class GuiSnapshot:
    revision: int
    current: dict[str, Any]
    proposed: dict[str, Any] | None
    current_sections: list[render.PlanSection]
    proposed_sections: list[render.PlanSection]
    proposal_id: int | None
    proposal_attention: list[str]
    status: str
    validation: str
    live: bool
    waiting: str          # idle, busy, operator, question, proposal or clarify
    question: str         # the yes/no question waiting for an answer
    running: bool         # the build or robot runner is running
    operator: str         # display the existing session identity
    validation_ok: bool   # the existing plan validation report, not a separate check
    # physical reasons the session would refuse this run (DemoSession.physical_run_blockers: wells already full, ...)
    blockers: tuple[str, ...] = ()
    validation_errors: tuple[str, ...] = ()
    optional_clarification: bool = False
    resets: int = 0       # how often Reset Demo started the experiment over: the page redraws everything when it changes

    @property
    def reset_block_reason(self) -> str:
        """Why Reset Demo is unavailable ("" when it can be pressed). Never during a run: a reset is no way to stop
        the robot."""
        if self.status == "SESSION ENDED":
            return "The session has ended."
        if self.running:
            return "Cannot reset while a robot run is active."
        if self.waiting == "busy":
            return "The agent is still working on the last message."
        if self.waiting == "operator":
            return "Enter the operator name first."
        if self.waiting not in RESET_STATES:
            return "Answer the yes/no question first (No cancels the run)."
        return ""

    @property
    def run_block_reasons(self) -> tuple[str, ...]:
        """Every reason the Run button is unavailable, in the order to fix them (empty: it can run). The same list as
        DemoSession.run_block_reasons - a proposal or question waiting, the plan's errors, the recorded physical state
        (these fields come from the same session calls) - plus what only the page knows: a run in progress, the agent
        still busy, a yes/no question, no operator yet."""
        if self.status == "SESSION ENDED":
            return ("The session has ended.",)
        reasons: list[str] = []
        if self.running:
            reasons.append("A run is in progress: wait until it finishes (or press Stop).")
        elif self.waiting == "busy":
            reasons.append("The agent is still working on the last message.")
        if self.waiting == "operator" or not self.operator:
            reasons.append("Enter the operator name first.")
        if self.proposed is not None or self.waiting == "proposal":
            number = f" #{self.proposal_id}" if self.proposal_id is not None else ""
            reasons.append(f"Proposal{number} is waiting: apply or discard it first.")
        if self.waiting == "clarify" and not self.optional_clarification:
            reasons.append("A question is waiting: answer or cancel it first.")
        if self.waiting == "question":
            question = " ".join(self.question.split())
            reasons.append("A yes/no question is waiting: " + (question[:160] or "answer it first."))
        reasons += self.validation_errors or (() if self.validation_ok else ("The plan does not pass its checks.",))
        reasons += self.blockers
        return tuple(dict.fromkeys(reasons))

    @property
    def run_block_reason(self) -> str:
        reasons = self.run_block_reasons
        return reasons[0] if reasons else ""

    @property
    def run_ready(self) -> bool:
        """UI readiness from the shared eligibility list; the run path repeats every execution-time check."""
        return not self.run_block_reasons


class DemoGuiAdapter:
    """Run ``DemoSession.run`` unchanged and feed it normal queued input.

    Input is handed to the session only while it is waiting for input, and the run button and form proposals only while
    it is idle at its main prompt, so a double click can never queue a second run. Callable queue items execute on the
    session thread before it reads the next line.
    """

    def __init__(self, session: DemoSession, *, diagnostics: Callable[[str], None] = print_diagnostic):
        self.session = session
        self._diagnostics_sink = diagnostics
        self._inputs: queue.Queue[str | Callable[[], None]] = queue.Queue()
        self._lock = threading.Lock()
        self._messages: list[ChatMessage] = []
        self._output: list[str] = []
        self._waiting: str | None = None      # the prompt the session is blocked on; None while it works
        self._question = ""
        self._last_said = ""
        self._ended = False
        self._thread: threading.Thread | None = None
        self._diagnostics: list[str] = []
        self._run_confirmation_requested = False
        self._run_confirmation_count = 0      # every chat run request; each open page answers each one once
        session.request_run_confirmation = self._request_run_confirmation
        session.input = self._read
        session.output = self._write
        session.diagnostic = self._diagnostic

    @property
    def run_label(self) -> str:
        return self.session.settings.run_button or "Run"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._add("status", LOADING_MESSAGE)
        self._thread = threading.Thread(target=self._run, name="dye-demo-session", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.session.run()
        except Exception as exc:
            self._add("assistant", f"Session stopped: {type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self._ended, self._waiting = True, None

    # ── the session's input and output ──────────────────────────────────────

    def _read(self, prompt: str) -> str:
        prompt = prompt.strip()
        if prompt not in LOOP_PROMPTS:
            self._add("assistant", prompt)       # a question asked through the prompt itself: who is running this
        # the session's main prompt, not a yes/no question asked in the middle of a turn ("confirm>" with no proposal)
        main = prompt in LOOP_PROMPTS and (prompt != "confirm>" or self.session.pending is not None)
        while True:
            with self._lock:
                if main:      # an action handled below (Reset Demo) can clear the proposal or question it was for
                    prompt = self.session.main_prompt().strip()
                self._waiting = prompt
                self._question = self._last_said if prompt == "confirm>" and self.session.pending is None else ""
            item = self._inputs.get()
            if not callable(item):
                return item
            try:
                item()
            except Exception as exc:  # noqa: BLE001 - a failed GUI action must not end the session
                self._write(f"agent> GUI action failed: {type(exc).__name__}: {exc}")

    def _write(self, text: str = "") -> None:
        text = str(text)
        if text.strip() == DEMO_RESET_BANNER:            # drawn as a divider: "DEMO RESET · New experiment ..."
            marker = " · ".join(line for line in DEMO_RESET_BANNER.splitlines() if line.strip("-"))
            with self._lock:
                self._last_said = ""
                self._messages.append(ChatMessage("marker", marker))
            return
        display = text.strip("\n").strip()
        if display.startswith("agent> "):
            display = display[7:].lstrip()
        elif display.startswith("agent>"):
            display = display[6:].lstrip()
        if "PROPOSED PLAN #" in text and render.APPLY_PROMPT in text:
            display = "Proposed Plan updated. Review it below, then choose Apply or Discard."
        elif "CURRENT PLAN" in text and render.RULE in text:
            display = "Current Plan refreshed."
        elif self.session.settings.run_button:
            display = display.replace(f"type {TRIGGER} to start it", f"press {self.run_label} to start it")
        with self._lock:
            self._last_said = text.strip()
            if display.strip():
                self._messages.append(ChatMessage("assistant", display))

    def _diagnostic(self, text: str = "") -> None:
        """Startup banner, LLM setup and file paths: the terminal window and session log keep them; the chat does not."""
        text = str(text).strip("\n")
        with self._lock:
            self._diagnostics.append(text)
        self._diagnostics_sink(text)

    def diagnostics(self, start: int = 0) -> list[str]:
        with self._lock:
            return self._diagnostics[start:]

    def _add(self, role: str, text: str) -> None:
        if role == "assistant":
            text = text.strip()
            if text.startswith("agent> "):
                text = text[7:].lstrip()
            elif text.startswith("agent>"):
                text = text[6:].lstrip()
        with self._lock:
            self._messages.append(ChatMessage(role, text))

    def _state(self, waiting: str | None) -> str:
        if waiting is None:
            return "busy"
        if waiting not in LOOP_PROMPTS:
            return "operator"
        if self.session.pending is not None:
            return "proposal"
        if self.session.clarifying is not None:
            return "clarify"
        return "question" if waiting == "confirm>" else "idle"

    def _offer(self, item: str | Callable[[], None], *, shown: str, states: Collection[str] | None = None) -> bool:
        """Hand one input to the session if it is waiting for one (in one of `states`, when given)."""
        with self._lock:
            if self._waiting is None or (states is not None and self._state(self._waiting) not in states):
                return False
            self._waiting = None
            self._messages.append(ChatMessage("user", shown))
            self._inputs.put(item)
        return True

    # ── what the page calls ──────────────────────────────────────────────────

    def submit_text(self, text: str) -> bool:
        """Chat text, or an answer (yes/no, Apply/Discard), while the session waits for input."""
        text = text.strip()
        return bool(text) and self._offer(text, shown=text)

    def _request_run_confirmation(self) -> None:
        with self._lock:
            self._run_confirmation_requested = True
            self._run_confirmation_count += 1

    @property
    def run_confirmation_count(self) -> int:
        """How many run confirmations the chat has requested. A page opens its dialog for each new one, so the dialog
        appears on the page the scientist is looking at even with the page open in two tabs."""
        with self._lock:
            return self._run_confirmation_count

    def take_run_confirmation_request(self) -> bool:
        with self._lock:
            if not self._run_confirmation_requested:
                return False
            self._run_confirmation_requested = False
            return True

    def run(self) -> bool:
        """The run button: the session's own run command, only while the session is idle at its main prompt."""
        if self.session.settings.llm_first and self.waiting == "clarify" \
                and not self.session.clarification_blocks_run():
            def run_current_plan() -> None:
                self.session.clarifying = None
                self.session.run_from_button()
            return self._offer(run_current_plan, shown=self.run_label)
        return self._offer(self.session.run_from_button, shown=self.run_label, states=("idle",))

    def propose_form(self, values: dict[str, Any]) -> bool:
        if self.waiting != "idle":
            return False
        current = self.session.state.config
        changes, phrases = [], []
        for path, value in values.items():
            if _get(current, path) == value:
                continue
            phrase = _phrase(path, value)
            changes.append({"path": path, "value": value, "evidence": phrase})
            phrases.append(phrase)
        if not changes:
            self._add("assistant", "GUI controls already match the Current Plan.")
            return False
        request = "GUI controls: " + "; ".join(phrases) + "."
        return self._offer(lambda: self.session.propose_form(changes, request=request), shown=request,
                           states=("idle",))

    def reset_demo(self, *, physical_reset_confirmed: bool = False) -> bool:
        """Reset Demo, after the page's confirmation: DemoSession.reset_to_defaults on the session thread, taken only at
        the session's main prompt (RESET_STATES). The session owns what a reset means and refuses it itself while a run
        is active or, live, without the physical confirmation; here only a stale run request of the old experiment is
        dropped. False when the session could not take it now."""
        def reset() -> None:
            if self.session.reset_to_defaults(physical_reset_confirmed=physical_reset_confirmed):
                with self._lock:
                    self._run_confirmation_requested = False

        return not self.running and self._offer(reset, shown=RESET_BUTTON, states=RESET_STATES)

    def physical_record(self) -> list[str]:
        """What the session's physical record holds now (the live Reset Demo dialog lists it)."""
        return self.session.physical_record_lines()

    def request_stop(self) -> bool:
        """The Stop button: the robot runner stops the OT-2 run exactly as it does on Ctrl-C in the terminal."""
        stop = getattr(self.session.executor, "request_stop", None)
        if stop is None or not stop():
            return False
        self._add("assistant", "Stop requested. The robot runner is asking the OT-2 to stop this run; wait until it "
                               "reports back.")
        return True

    def add_output(self, line: str) -> None:
        with self._lock:
            self._output.append(str(line))

    def messages(self, start: int = 0) -> list[ChatMessage]:
        with self._lock:
            return self._messages[start:]

    def output(self, start: int = 0) -> list[str]:
        with self._lock:
            return self._output[start:]

    @property
    def running(self) -> bool:
        return bool(getattr(self.session.executor, "active", False))

    @property
    def waiting(self) -> str:
        with self._lock:
            return self._state(self._waiting)

    def stop(self) -> None:
        """Server shutdown: a running robot run is asked to stop and given time to report back, then the session quits."""
        if self.running and self.request_stop():
            deadline = time.monotonic() + STOP_WAIT_S
            while self.running and time.monotonic() < deadline:
                time.sleep(0.2)
        if self._thread is not None and self._thread.is_alive():
            self._inputs.put("quit")

    def snapshot(self) -> GuiSnapshot:
        session = self.session
        current = session.state.config
        pending = session.pending
        proposed = deepcopy(pending.after) if pending else None
        prepared = session.state.physical.get("dilutions_prepared")
        proposed_prepared = pending.physical.get("dilutions_prepared", prepared) if pending else prepared
        report = session.state.validate()
        blockers = tuple(session.physical_run_blockers())
        with self._lock:
            waiting, question, ended = self._state(self._waiting), self._question, self._ended
        running = self.running
        validation = render.render_report(report)
        if blockers:
            validation += "\n" + "\n".join(f"Run blocked: {reason}" for reason in blockers)
        return GuiSnapshot(
            revision=session.state.revision,
            current=current,
            proposed=proposed,
            current_sections=render.plan_model(current, prepared=prepared),
            proposed_sections=render.plan_model(proposed, prepared=proposed_prepared) if proposed else [],
            proposal_id=pending.id if pending else None,
            proposal_attention=render.proposal_attention_items(pending) if pending else [],
            status=self._status(waiting, running, ended),
            validation=validation,
            live=not session.settings.simulate,
            waiting=waiting,
            question=question if waiting == "question" else "",
            running=running,
            operator=session.operator,
            validation_ok=report.ok,
            blockers=blockers,
            validation_errors=tuple(report.error_messages()),
            optional_clarification=session.settings.llm_first and not session.clarification_blocks_run(),
            resets=session.resets,
        )

    def _status(self, waiting: str, running: bool, ended: bool) -> str:
        if ended:
            return "SESSION ENDED"
        if running:
            return "RUNNING ON OT-2"
        if waiting in _WAITING_STATUS:
            return _WAITING_STATUS[waiting]
        runs = self.session.state.runs
        if runs and runs[-1]["revision"] == self.session.state.revision:     # a later change makes the result stale
            status = runs[-1]["status"]
            return _RUN_STATUS.get(status, f"RUN {status}".upper())
        return "READY"


def _get(config: dict[str, Any], path: str) -> Any:
    node: Any = config
    for part in path.split("."):
        node = node[part]
    return deepcopy(node)


def _phrase(path: str, value: Any) -> str:
    if path == "dilution.factors":
        return "dilution factors " + ", ".join(f"{item}x" for item in value)
    if path == "dilution.enabled":
        return "make the dilutions in this run" if value else "disable making dilutions; the dilutions are already made"
    if path == "print.enabled":
        return "enable printing in this run" if value else "disable printing in this run"
    if path == "print.paper_start_column":
        return f"start at paper column {value}"
    if path == "print.replicates":
        return f"use {value} total replicates"
    if path == "print.droplets_per_spot":
        return f"use {value} drops per paper position"
    if path == "mixing.enabled":
        return "mix the dilutions after they are made" if value else "do not mix the dilutions"
    if path == "liquid_handling.air_gap_ul":
        return f"use a {value:g} uL air gap" if value else "no air gap"
    if path == "liquid_handling.blow_out":
        return "blow out after each plate dispense" if value else "no blow-out after plate dispenses"
    if path == "liquid_handling.well_plate_shake.enabled":
        return "shake after dispensing into the plate" if value else "turn off the well-plate shake"
    deck = {
        "deck.plate.slot": "96-well dilution plate",
        "deck.paper.slot": "paper print plate",
        "deck.tuberack.slot": "vial rack",
        "deck.tiprack.slot": "P20 tip rack",
    }
    if path in deck:
        return f"move the {deck[path]} to slot {value}"
    return f"set {path} to {value}"
