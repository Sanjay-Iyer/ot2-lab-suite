"""Thread-safe bridge between NiceGUI callbacks and the real DemoSession loop."""
from __future__ import annotations

import queue
import threading
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from src.agents.dye_demo import render
from src.agents.dye_demo.session import DemoSession


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


class DemoGuiAdapter:
    """Run ``DemoSession.run`` unchanged and feed it normal queued input.

    Callable queue items execute on the session thread before it reads the next
    line, serializing form proposals and validation with chat and simulation.
    """

    def __init__(self, session: DemoSession):
        self.session = session
        self._inputs: queue.Queue[str | Callable[[], None]] = queue.Queue()
        self._messages: queue.Queue[ChatMessage] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._status = "READY"
        session.input = self._read
        session.output = self._write

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="dye-demo-session", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            code = self.session.run()
            if self._status not in {"SIMULATION COMPLETE", "ERROR"}:
                self._status = "READY" if code == 0 else "ERROR"
        except Exception as exc:
            self._status = "ERROR"
            self._messages.put(ChatMessage("assistant", f"Session stopped: {type(exc).__name__}: {exc}"))

    def _read(self, _prompt: str) -> str:
        while True:
            item = self._inputs.get()
            if callable(item):
                try:
                    item()
                except Exception as exc:
                    self._status = "ERROR"
                    self._write(f"agent> GUI action failed: {type(exc).__name__}: {exc}")
                continue
            return item

    def _write(self, text: str = "") -> None:
        text = str(text)
        if "STARTING SIMULATION" in text:
            self._status = "SIMULATING"
        if "finished with exit code 0" in text and self.session.settings.simulate:
            self._status = "SIMULATION COMPLETE"
        elif "Something went wrong" in text or "did not finish" in text:
            self._status = "ERROR"
        display = text
        if "PROPOSED PLAN #" in text and render.APPLY_PROMPT in text:
            display = "Proposed Plan updated. Review it below, then choose Apply or Discard."
        elif "CURRENT PLAN" in text and render.RULE in text:
            display = "Current Plan refreshed."
        self._messages.put(ChatMessage("assistant", display))

    def submit_text(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        self._messages.put(ChatMessage("user", text))
        self._inputs.put(text)
        return True

    def propose_form(self, values: dict[str, Any]) -> bool:
        current = self.session.state.config
        changes, phrases = [], []
        for path, value in values.items():
            if _get(current, path) == value:
                continue
            phrase = _phrase(path, value)
            changes.append({"path": path, "value": value, "evidence": phrase})
            phrases.append(phrase)
        if not changes:
            self._messages.put(ChatMessage("assistant", "GUI controls already match the Current Plan."))
            return False
        request = "GUI controls: " + "; ".join(phrases) + "."
        self._messages.put(ChatMessage("user", request))
        self._inputs.put(lambda: self.session.propose_form(changes, request=request))
        return True

    def validate(self) -> None:
        def action() -> None:
            report = self.session.state.validate()
            self._status = "VALID" if not report.errors else "ERROR"
            self._write("VALIDATION\n" + render.render_report(report))
        self._inputs.put(action)

    def simulate(self) -> bool:
        if not self.session.settings.simulate:
            self._write("Simulation refused: this GUI is configured for simulation only.")
            return False
        return self.submit_text("run")

    def stop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._inputs.put("quit")

    def drain_messages(self) -> list[ChatMessage]:
        messages = []
        while True:
            try:
                messages.append(self._messages.get_nowait())
            except queue.Empty:
                return messages

    def snapshot(self) -> GuiSnapshot:
        current = self.session.state.config
        pending = self.session.pending
        proposed = deepcopy(pending.after) if pending else None
        prepared = self.session.state.physical.get("dilutions_prepared")
        proposed_prepared = pending.physical.get("dilutions_prepared", prepared) if pending else prepared
        report = self.session.state.validate()
        status = "PROPOSAL WAITING" if pending else "NEEDS CLARIFICATION" if self.session.clarifying else self._status
        return GuiSnapshot(
            revision=self.session.state.revision,
            current=current,
            proposed=proposed,
            current_sections=render.plan_model(current, prepared=prepared),
            proposed_sections=render.plan_model(proposed, prepared=proposed_prepared) if proposed else [],
            proposal_id=pending.id if pending else None,
            proposal_attention=render.proposal_attention_items(pending) if pending else [],
            status=status,
            validation=render.render_report(report),
        )


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
