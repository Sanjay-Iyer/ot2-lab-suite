"""Thread-safe bridge between NiceGUI callbacks and the real DemoSession loop."""
from __future__ import annotations

import queue
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from src.agents.dye_demo import render
from src.agents.dye_demo.language import TRIGGER
from src.agents.dye_demo.session import DemoSession

LOOP_PROMPTS = {"you>", "confirm>", "clarify>"}      # the session's own prompts; any other prompt is a question to show
STOP_WAIT_S = 180.0                                   # how long a server shutdown waits for a stopped robot run
_RUN_STATUS = {
    ("LIVE", "succeeded"): "RUN COMPLETE",
    ("LIVE", "aborted"): "RUN STOPPED",
    ("LIVE", "interrupted before start"): "RUN NOT STARTED",
    ("LIVE", "failed"): "RUN FAILED",
    ("SIMULATION", "succeeded"): "SIMULATION COMPLETE",
}
_WAITING_STATUS = {"busy": "WORKING", "operator": "ENTER YOUR NAME", "proposal": "PROPOSAL WAITING",
                   "clarify": "NEEDS CLARIFICATION", "question": "ANSWER YES OR NO"}


@dataclass(frozen=True)
class ChatMessage:
    role: str
    text: str


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

    @property
    def run_ready(self) -> bool:
        """UI readiness; the existing run path still performs all execution-time checks."""
        return (self.waiting == "idle" and not self.running and self.proposed is None
                and bool(self.operator) and self.validation_ok and self.status != "SESSION ENDED")


class DemoGuiAdapter:
    """Run ``DemoSession.run`` unchanged and feed it normal queued input.

    Input is handed to the session only while it is waiting for input, and the run button and form proposals only while
    it is idle at its main prompt, so a double click can never queue a second run. Callable queue items execute on the
    session thread before it reads the next line.
    """

    def __init__(self, session: DemoSession):
        self.session = session
        self._inputs: queue.Queue[str | Callable[[], None]] = queue.Queue()
        self._lock = threading.Lock()
        self._messages: list[ChatMessage] = []
        self._output: list[str] = []
        self._waiting: str | None = None      # the prompt the session is blocked on; None while it works
        self._question = ""
        self._last_said = ""
        self._ended = False
        self._thread: threading.Thread | None = None
        session.input = self._read
        session.output = self._write

    @property
    def run_label(self) -> str:
        return self.session.settings.run_button or "Run"

    def start(self) -> None:
        if self._thread is not None:
            return
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
        while True:
            with self._lock:
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

    def _offer(self, item: str | Callable[[], None], *, shown: str, idle: bool = False) -> bool:
        """Hand one input to the session if it is waiting for one (at its main prompt, when idle=True)."""
        with self._lock:
            if self._waiting is None or (idle and self._state(self._waiting) != "idle"):
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

    def run(self) -> bool:
        """The run button: the session's own run command, only while the session is idle at its main prompt."""
        return self._offer(self.session.run_from_button, shown=self.run_label, idle=True)

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
        return self._offer(lambda: self.session.propose_form(changes, request=request), shown=request, idle=True)

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
        with self._lock:
            waiting, question, ended = self._state(self._waiting), self._question, self._ended
        running = self.running
        return GuiSnapshot(
            revision=session.state.revision,
            current=current,
            proposed=proposed,
            current_sections=render.plan_model(current, prepared=prepared),
            proposed_sections=render.plan_model(proposed, prepared=proposed_prepared) if proposed else [],
            proposal_id=pending.id if pending else None,
            proposal_attention=render.proposal_attention_items(pending) if pending else [],
            status=self._status(waiting, running, ended),
            validation=render.render_report(report),
            live=not session.settings.simulate,
            waiting=waiting,
            question=question if waiting == "question" else "",
            running=running,
            operator=session.operator,
            validation_ok=report.ok,
        )

    def _status(self, waiting: str, running: bool, ended: bool) -> str:
        if ended:
            return "SESSION ENDED"
        if running:
            return "SIMULATING" if self.session.settings.simulate else "RUNNING ON OT-2"
        if waiting in _WAITING_STATUS:
            return _WAITING_STATUS[waiting]
        runs = self.session.state.runs
        if runs and runs[-1]["revision"] == self.session.state.revision:     # a later change makes the result stale
            last = runs[-1]
            return _RUN_STATUS.get((last["mode"], last["status"]), f"{last['mode']} {last['status']}".upper())
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
        return f"use {value} replicate paper columns"
    if path == "print.droplets_per_spot":
        return f"use {value} drops per paper position"
    deck = {
        "deck.plate.slot": "96-well dilution plate",
        "deck.paper.slot": "paper print plate",
        "deck.tuberack.slot": "vial rack",
        "deck.tiprack.slot": "P20 tip rack",
    }
    if path in deck:
        return f"move the {deck[path]} to slot {value}"
    return f"set {path} to {value}"
