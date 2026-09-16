"""Whole conversations with the dye demo, using a scripted LLM and a fake executor.

These pin the interaction model: READY before anything else, the operator's
name on every record, /ask never changing state, nothing applied without an
explicit yes, collisions explained instead of failing, ambiguous words confirmed
before interpretation, and live runs gated on the physical deck.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from src.agents.dye_demo.llm import LLMClient
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.session import DemoSession, SessionSettings


class FakeLLM:
    """Replies READY to the startup check, then returns queued replies in order."""

    def __init__(self, events, replies=(), fail_startup=False):
        self.events, self.replies, self.fail_startup = events, list(replies), fail_startup
        self.calls = []

    def invoke(self, messages):
        human = messages[-1][1]
        self.calls.append(messages)
        self.events.append(("llm", human[:60]))
        if "Respond READY" in human:
            if self.fail_startup:
                raise ConnectionError("proxy refused the connection")
            return SimpleNamespace(content="READY")
        if not self.replies:
            raise AssertionError(f"unexpected LLM call: {human[-200:]}")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=reply if isinstance(reply, str) else json.dumps(reply))


class Harness:
    def __init__(self, tmp_path, inputs, *, replies=(), simulate=True, fail_startup=False, offline=False,
                 exit_code=0):
        self.events, self.outputs, self.runs = [], [], []
        self.lines = list(inputs)
        self.llm = FakeLLM(self.events, replies, fail_startup)
        settings = SessionSettings(simulate=simulate, config_source=DEFAULT_CONFIG,
                                   working_config=tmp_path / "working.yaml", run_dir=tmp_path / "run",
                                   session_label="2026-09-03 Demo 1", raise_errors=True)
        self.session = DemoSession(settings, load_config(DEFAULT_CONFIG),
                                   llm=None if offline else LLMClient(lambda: self.llm),
                                   executor=self._execute, input_fn=self._input,
                                   output_fn=self.outputs.append, sleep=lambda seconds: None)
        self.exit_code = exit_code

    def _input(self, prompt):
        self.events.append(("input", prompt.strip()))
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)

    def _execute(self, path, simulate, log):
        self.runs.append({"simulate": simulate, "config": yaml.safe_load(path.read_text(encoding="utf-8"))})
        return self.exit_code

    def run(self):
        self.code = self.session.run()
        return self

    @property
    def text(self):
        return "\n".join(self.outputs)

    @property
    def state(self):
        return self.session.state


def proposal(*changes, intent="change", explanation="Proposed."):
    return {"intent": intent, "explanation": explanation,
            "changes": [{"path": p, "value": v, "evidence": e} for p, v, e in changes]}


def test_the_llm_answers_ready_before_the_operator_is_asked_anything(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "quit"]).run()
    assert harness.code == 0
    assert harness.events[0][0] == "llm" and "Respond READY" in harness.llm.calls[0][-1][1]
    assert harness.events[1] == ("input", "Who is running this experiment?")
    assert '[startup] LLM replied "READY"' in harness.text and "connection established" in harness.text


def test_an_unreachable_llm_stops_before_the_session_starts(tmp_path):
    harness = Harness(tmp_path, ["Stephen"], fail_startup=True).run()
    assert harness.code == 3
    assert not [event for event in harness.events if event[0] == "input"]
    assert "attempt 3 of 3" in harness.text and "LLM STARTUP FAILED" in harness.text


def test_the_operator_is_on_every_record_and_in_the_config(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "plan", "quit"]).run()
    assert "USER    : Stephen" in harness.text and "SESSION : 2026-09-03 Demo 1" in harness.text
    records = [json.loads(line) for line in (tmp_path / "run" / "session.log").read_text(encoding="utf-8").splitlines()]
    identified = [index for index, record in enumerate(records) if record["event"] == "operator_identified"][0]
    assert all(record["operator"] == "Stephen" for record in records[identified:])
    working = yaml.safe_load((tmp_path / "working.yaml").read_text(encoding="utf-8"))
    assert working["session"]["operator"] == "Stephen" and working["session"]["operator_id"] == "stephen"
    summary = json.loads((tmp_path / "run" / "session.json").read_text(encoding="utf-8"))
    assert summary["operator"] == "Stephen" and summary["session_label"] == "2026-09-03 Demo 1"


def test_ask_mode_never_changes_the_experiment_even_if_the_reply_looks_like_a_change(tmp_path):
    reply = '{"changes": [{"path": "print.droplet_volume_ul", "value": 10}]} Three dilutions span the range.'
    harness = Harness(tmp_path, ["Stephen", "/ask Why are we using three dilution steps?", "quit"],
                      replies=[reply]).run()
    before = load_config(DEFAULT_CONFIG)
    assert "ASK MODE — no experiment parameters changed." in harness.text and "Answer:" in harness.text
    assert harness.state.revision == 0 and harness.state.config == before
    assert "informational" in harness.llm.calls[-1][0][1]


def test_a_change_is_applied_only_after_an_explicit_yes(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "move the paper print plate to slot 8", "sure?", "yes", "quit"],
                      replies=[proposal(("deck.paper.slot", 8, "paper print plate to slot 8"))]).run()
    text = harness.text
    assert "That is not a clear yes, so nothing was applied" in text
    proposed, applied = text.split("APPLIED proposal #1. This is now the current plan (recorded for Stephen).")
    for expected in ("PROPOSED PLAN #1", "  Paper print plate     Slot 8",
                     "After you apply this, physically move the Paper print plate from Slot 5 to Slot 8."):
        assert expected in proposed
    for expected in ("CURRENT PLAN", "  Paper print plate     Slot 8",
                     "Now physically move the Paper print plate from Slot 5 to Slot 8."):
        assert expected in applied
    assert "  Paper print plate     Slot 5" not in applied and " -> " not in text
    assert harness.state.config["deck"]["paper"]["slot"] == 8 and harness.state.revision == 1
    history = (tmp_path / "run" / "parameter_history.jsonl").read_text(encoding="utf-8")
    assert '"operator": "Stephen"' in history and '"deck.paper.slot"' in history


def test_no_discards_a_proposed_change(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "move the paper print plate to slot 8", "no", "quit"],
                      replies=[proposal(("deck.paper.slot", 8, "slot 8"))]).run()
    assert "Discarded proposal #1. Nothing was changed." in harness.text
    assert harness.state.config["deck"]["paper"]["slot"] == 5 and harness.state.revision == 0


def test_an_occupied_slot_is_explained_and_nothing_changes(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "move the dilution plate from slot 4 to slot 7", "quit"],
                      replies=[proposal(("deck.plate.slot", 7, "dilution plate from slot 4 to slot 7"))]).run()
    assert "Cannot apply that deck change yet." in harness.text
    assert "Slot 7 is currently occupied by the Vial rack." in harness.text
    assert harness.state.revision == 0 and harness.session.pending is None


def test_spot_is_confirmed_as_a_slot_before_the_llm_interprets_anything(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "Move the dilution plate to spot 6.", "yes", "yes", "quit"],
                      replies=[proposal(("deck.plate.slot", 6, "dilution plate to deck slot 6"))]).run()
    assert 'You said "spot 6." Did you mean OT-2 deck SLOT 6?' in harness.text
    # the router reads the confirmed wording as the message (the conversation it also sees still shows "spot 6")
    interpreted = harness.llm.calls[1][-1][1].split("SCIENTIST'S MESSAGE:\n")[-1]
    assert "deck slot 6" in interpreted and "spot 6" not in interpreted
    assert harness.state.config["deck"]["plate"]["slot"] == 6


def test_rejecting_the_meaning_of_an_ambiguous_word_changes_nothing(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "move the dilution plate to spot 6", "no", "quit"]).run()
    assert "Nothing was changed. Please say it again using established terms" in harness.text
    assert len(harness.llm.calls) == 1        # only the startup check
    assert harness.state.revision == 0


def test_a_question_without_ask_is_answered_and_nothing_changes(tmp_path):
    # Answered conversationally by the router (no ask-mode screen, no "say it as an instruction" footer).
    harness = Harness(tmp_path, ["Stephen", "why do we mix before printing?", "quit"],
                      replies=[{"route": "experiment_question", "changes": [], "answer": "So the dye stays mixed."}]).run()
    assert "agent> So the dye stays mixed." in harness.text
    assert "ASK MODE" not in harness.text and "say it as an instruction" not in harness.text
    assert harness.state.revision == 0 and harness.session.pending is None


def test_resent_unchanged_values_are_dropped_and_the_dilution_plan_survives(tmp_path):
    three = proposal(("dilution.factors", [2, 5, 10], "2x, 5x and 10x"), ("dilution.total_volume_ul", 100, "100 uL"))
    move = proposal(("deck.paper.slot", 8, "paper print plate to slot 8"),
                    ("dilution.factors", [2, 5, 10], "2x, 5x and 10x"))
    harness = Harness(tmp_path, ["Stephen", "make 2x, 5x and 10x dilutions at 100 uL", "yes",
                                 "move the paper print plate to slot 8", "yes", "quit"], replies=[three, move]).run()
    assert harness.state.config["dilution"]["factors"] == [2, 5, 10]
    assert harness.state.history[-1]["changes"][0]["path"] == "deck.paper.slot"
    assert len(harness.state.history[-1]["changes"]) == 1


def test_an_older_plan_trying_to_come_back_is_shown_and_not_applied_without_yes(tmp_path):
    three = proposal(("dilution.factors", [2, 5, 10], "2x, 5x and 10x"), ("dilution.total_volume_ul", 100, "100 uL"))
    stale = proposal(("deck.paper.slot", 8, "paper print plate to slot 8"),
                     ("dilution.factors", [1, 2, 3, 4, 6, 8, 12, 16], ""))
    harness = Harness(tmp_path, ["Stephen", "make 2x, 5x and 10x dilutions at 100 uL", "yes",
                                 "move the paper print plate to slot 8", "no", "quit"], replies=[three, stale]).run()
    assert "CHECK THESE - I could not find them in what you typed" in harness.text
    assert harness.state.config["dilution"]["factors"] == [2, 5, 10]
    assert harness.state.config["deck"]["paper"]["slot"] == 5


def test_run_uses_the_approved_state_and_the_session_continues(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "run", "plan", "quit"]).run()
    assert len(harness.runs) == 1 and harness.runs[0]["simulate"] is True
    assert harness.runs[0]["config"]["session"] == {"operator": "Stephen", "operator_id": "stephen",
                                                    "session_label": "2026-09-03 Demo 1",
                                                    "session_id": "run", "revision": 0}
    for expected in ("STARTING SIMULATION", "  Operator              Stephen | 2026-09-03 Demo 1 | run 1", "THIS RUN",
                     "  Tips                  10   (A1-B2)", "Run 1 finished with exit code 0",
                     "Simulation only: no tips, liquid or paper were used.", "You can plan another run"):
        assert expected in harness.text
    assert (tmp_path / "run" / "executed_config_run1.yaml").exists()


def test_a_pending_change_blocks_the_run(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "move the paper print plate to slot 8", "run", "no", "quit"],
                      replies=[proposal(("deck.paper.slot", 8, "slot 8"))]).run()
    assert harness.runs == [] and "Proposal #1 is still waiting" in harness.text


def test_a_live_run_after_a_deck_change_needs_the_physical_deck_confirmed(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "move the paper print plate to slot 8", "yes", "run", "no", "quit"],
                      replies=[proposal(("deck.paper.slot", 8, "slot 8"))], simulate=False).run()
    assert "Is the physical deck arranged exactly as the ACTIVE DECK above?" in harness.text
    assert "Run cancelled. Nothing was executed." in harness.text and harness.runs == []


def test_a_live_print_only_run_asks_that_the_dilutions_already_exist(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "the dilutions are already made, skip making them", "yes", "run", "yes",
                                 "quit"],
                      replies=[proposal(("dilution.enabled", False, "dilutions are already made"))],
                      simulate=False).run()
    assert "Do plate wells A11-H11 already hold the dilutions?" in harness.text
    assert len(harness.runs) == 1 and harness.runs[0]["simulate"] is False


def test_after_a_live_run_the_next_tip_is_proposed_not_silently_changed(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "run", "yes", "quit"], simulate=False).run()
    assert "Tips A1-B2 were used by this run, so the next unused tip is C2." in harness.text
    assert "  - tip start: run 1 used tips A1-B2" in harness.text
    assert harness.state.config["tips"]["start_tip"] == "C2"
    assert harness.state.history[-1]["source"] == "post-run"
    assert harness.state.printed_positions == {f"{row}1" for row in "ABCDEFGH"}


def test_a_failed_run_changes_nothing_and_warns_about_physical_state(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "run", "quit"], simulate=False, exit_code=1).run()
    assert "the physical state may not match the plan" in harness.text
    assert harness.state.config["tips"]["start_tip"] == "A1" and harness.session.pending is None


def test_an_llm_failure_mid_session_changes_nothing(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "make 4 dilutions", "quit"],
                      replies=[ConnectionError("reset"), ConnectionError("reset again")]).run()
    assert "the LLM request failed" in harness.text and harness.state.revision == 0


def test_a_stale_connection_is_rebuilt_once_and_the_request_goes_through(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "use 2 replicates", "yes", "quit"],
                      replies=[ConnectionError("stale"), proposal(("print.replicates", 2, "2 replicates"))]).run()
    assert harness.state.config["print"]["replicates"] == 2


def test_offline_rehearsal_serves_commands_but_never_interprets(tmp_path):
    harness = Harness(tmp_path, ["Stephen", "deck", "make 4 dilutions", "quit"], offline=True).run()
    assert "LLM OFFLINE" in harness.text and "cannot interpret requests while offline" in harness.text
    assert harness.state.revision == 0
