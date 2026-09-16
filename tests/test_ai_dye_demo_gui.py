"""Offline tests for the NiceGUI adapter; no browser, model, or robot is contacted."""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
import time
from copy import deepcopy
from dataclasses import replace

import pytest
from nicegui import Client, core, ui
from src.agents.dye_demo.validation import Report
from pathlib import Path
from types import SimpleNamespace

import yaml

import scripts.ai_dye_demo_gui as launcher
from src.agents.dye_demo.gui.app import (
    CHAT_HISTORY_HEIGHT_PX,
    CHAT_TITLE,
    DEFAULT_REFERENCE_IMAGES,
    _image_data_uri,
    build_page,
    experiment_flow,
)
from src.agents.dye_demo.gui.adapter import DemoGuiAdapter
from src.agents.dye_demo.llm import LLMClient
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.session import DemoSession, SessionSettings


class FakeRobotExecutor:
    """Stands in for SubprocessExecutor on the live path: records the plan it is handed, and a run that waits for Stop
    ends the way the robot runner reports a stopped OT-2 run."""

    def __init__(self, *, reachable=True, wait_for_stop=False):
        self.reachable, self.wait_for_stop = reachable, wait_for_stop
        self.calls, self.checks, self.active, self.last_status = [], 0, False, None
        self.emit = lambda line: None
        self._stop = threading.Event()

    def check_robot(self):
        self.checks += 1
        return None if self.reachable else "Could not discover and verify the configured OT-2."

    def request_stop(self):
        if not self.active:
            return False
        self._stop.set()
        return True

    def __call__(self, path, simulate, log):
        self.calls.append((yaml.safe_load(Path(path).read_text(encoding="utf-8")), simulate))
        self.active = True
        try:
            self.emit("[upload] ai_agent_dilution_print_demo_latest.py")
            self.emit("[monitor]")
            if self.wait_for_stop:
                assert self._stop.wait(5), "Stop was never pressed"
                self.emit("[stop] The OT-2 reports the run as stopped.")
                self.last_status = {"started": True, "stop_requested": True, "robot_status": "stopped",
                                    "stop_confirmed": True}
                return 130
            self.last_status = {"started": True, "robot_status": "succeeded"}
            return 0
        finally:
            self.active = False


def make_adapter(tmp_path, *, executor=None, llm=None, simulate=True, operator="Tester"):
    settings = SessionSettings(simulate=simulate, config_source=DEFAULT_CONFIG,
                               working_config=tmp_path / "working.yaml", run_dir=tmp_path / "run",
                               session_label="GUI test", operator=operator, skip_llm_startup=True,
                               raise_errors=True, run_button="Simulate" if simulate else "Run on OT-2")
    session = DemoSession(settings, load_config(DEFAULT_CONFIG), llm=llm,
                          executor=executor or (lambda path, simulate, log: 0), sleep=lambda _: None)
    adapter = DemoGuiAdapter(session)
    if hasattr(executor, "emit"):
        executor.emit = adapter.add_output         # as the launcher wires the real executor
    return adapter


def started(adapter):
    adapter.start()
    wait_for(lambda: adapter.waiting == "idle")
    return adapter


def apply_form(adapter, values):
    revision = adapter.snapshot().revision
    assert adapter.propose_form(values)
    wait_for(lambda: adapter.waiting == "proposal")
    assert adapter.submit_text("yes")
    wait_for(lambda: adapter.waiting == "idle" and adapter.snapshot().revision == revision + 1)


def chat_text(adapter):
    return "\n".join(message.text for message in adapter.messages())


class OneReplyModel:
    def invoke(self, _messages):
        return SimpleNamespace(content=json.dumps({
            "intent": "change",
            "explanation": "Proposed through the existing session.",
            "changes": [{"path": "print.droplets_per_spot", "value": 3,
                         "evidence": "three drops per position"}],
        }))


class ScriptedRouter:
    """Stands in for the model behind the conversational router: `reply(message)` is the JSON a capable model would
    return for the scientist's message. Validation, proposals and approval are all the real session's."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def invoke(self, messages):
        self.calls.append(messages)
        message = messages[-1][1].split("SCIENTIST'S MESSAGE:\n")[-1].strip()
        return SimpleNamespace(content=json.dumps(self.reply(message)))


def skip_dilution_reply(message):
    return {"route": "experiment_change", "explanation": "Proposes printing without making dilutions.",
            "changes": [{"path": "dilution.enabled", "value": False, "evidence": message}]}


def answer_reply(message):
    return {"route": "experiment_question", "answer": "That would print from the plate as it is; nothing is changed "
                                                      "until you ask for it."}


def router(reply):
    return LLMClient(lambda: ScriptedRouter(reply))


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for GUI adapter")


def test_form_change_is_a_validated_proposal_until_apply(tmp_path):
    adapter = started(make_adapter(tmp_path))
    before = adapter.snapshot().current
    assert adapter.propose_form({"print.droplets_per_spot": 3})
    wait_for(lambda: adapter.snapshot().proposal_id == 1 and adapter.waiting == "proposal")
    waiting = adapter.snapshot()
    assert waiting.status == "PROPOSAL WAITING"
    assert waiting.current["print"]["droplets_per_spot"] == before["print"]["droplets_per_spot"] == 1
    assert waiting.proposed["print"]["droplets_per_spot"] == 3
    adapter.submit_text("yes")
    wait_for(lambda: adapter.snapshot().revision == 1)
    assert adapter.snapshot().proposal_id is None
    assert adapter.snapshot().current["print"]["droplets_per_spot"] == 3
    adapter.stop()


def test_chat_text_reaches_the_real_session_interpreter(tmp_path):
    adapter = started(make_adapter(tmp_path, llm=LLMClient(lambda: OneReplyModel())))
    assert adapter.submit_text("Use three drops per position.")
    wait_for(lambda: adapter.snapshot().proposal_id == 1)
    assert adapter.snapshot().proposed["print"]["droplets_per_spot"] == 3
    assert adapter.snapshot().current["print"]["droplets_per_spot"] == 1
    messages = adapter.messages()
    assert any(message.text.startswith("Proposed Plan updated") for message in messages)
    assert not any("Apply proposal #1?" in message.text for message in messages)
    adapter.stop()


def test_discard_leaves_authoritative_state_unchanged(tmp_path):
    adapter = started(make_adapter(tmp_path))
    adapter.propose_form({"print.paper_start_column": 3})
    wait_for(lambda: adapter.snapshot().proposal_id == 1)
    adapter.submit_text("no")
    wait_for(lambda: adapter.snapshot().proposal_id is None)
    assert adapter.snapshot().revision == 0
    assert adapter.snapshot().current["print"]["paper_start_column"] == 1
    adapter.stop()


def test_typed_run_is_refused_and_only_the_run_button_starts_a_run(tmp_path):
    calls = []
    adapter = started(make_adapter(tmp_path, executor=lambda path, simulate, log: calls.append(simulate) or 0))
    assert "press Simulate to start it" in chat_text(adapter) and "type run to start it" not in chat_text(adapter)
    assert adapter.submit_text("run")
    wait_for(lambda: "check the plan and press Simulate" in chat_text(adapter) and adapter.waiting == "idle")
    assert calls == []
    assert adapter.run()
    wait_for(lambda: calls == [True] and adapter.waiting == "idle")
    assert adapter.snapshot().status == "SIMULATION COMPLETE"
    apply_form(adapter, {"print.droplets_per_spot": 2})
    assert adapter.snapshot().status == "READY"                # a later change makes the result stale
    adapter.stop()


def test_live_run_checks_the_robot_first_and_an_unreachable_robot_changes_nothing(tmp_path):
    executor = FakeRobotExecutor(reachable=False)
    adapter = started(make_adapter(tmp_path, executor=executor, simulate=False))
    apply_form(adapter, {"print.paper_start_column": 3})
    before = deepcopy(adapter.snapshot().current)
    assert adapter.run()
    wait_for(lambda: executor.checks == 1 and adapter.waiting == "idle")
    text = chat_text(adapter)
    assert "Cannot reach the OT-2, so nothing was run. The plan is unchanged." in text
    assert "Could not discover and verify the configured OT-2." in text
    assert executor.calls == [] and adapter.session.state.runs == []
    after = adapter.snapshot()
    assert after.current == before and after.revision == 1 and after.status == "READY"
    # once the robot answers, the same prepared plan runs
    executor.reachable = True
    assert adapter.run()
    wait_for(lambda: executor.calls and adapter.waiting == "proposal")   # the next starting tip is proposed after a run
    [(plan, simulate)] = executor.calls
    assert simulate is False and plan["print"]["paper_start_column"] == 3 and executor.checks == 2
    assert adapter.session.state.runs[-1]["status"] == "succeeded"
    adapter.stop()


def test_live_run_gets_the_applied_plan_shows_output_and_stop_reaches_the_runner(tmp_path):
    executor = FakeRobotExecutor(wait_for_stop=True)
    adapter = started(make_adapter(tmp_path, executor=executor, simulate=False))
    assert adapter.propose_form({"print.droplets_per_spot": 3})
    wait_for(lambda: adapter.waiting == "proposal")
    assert adapter.run() is False                                    # never while a proposal waits
    assert adapter.submit_text("yes")
    wait_for(lambda: adapter.waiting == "idle" and adapter.snapshot().revision == 1)
    assert adapter.request_stop() is False                           # nothing is running yet
    assert adapter.run()
    wait_for(lambda: adapter.running)
    running = adapter.snapshot()
    assert running.status == "RUNNING ON OT-2" and running.live and running.running
    assert adapter.run() is False and adapter.submit_text("yes") is False   # a second press queues nothing
    [(plan, simulate)] = executor.calls
    assert simulate is False and plan["print"]["droplets_per_spot"] == 3 and plan["session"]["operator"] == "Tester"
    wait_for(lambda: "[monitor]" in adapter.output())
    assert adapter.request_stop()
    wait_for(lambda: not adapter.running and adapter.waiting == "idle")
    text = chat_text(adapter)
    assert "press Stop at the top of the page" in text and "Stop requested." in text
    assert "The OT-2 reported the run as stopped." in text
    assert adapter.output()[-1] == "[stop] The OT-2 reports the run as stopped."
    assert adapter.snapshot().status == "RUN STOPPED" and adapter.session.state.runs[-1]["status"] == "aborted"
    assert len(executor.calls) == 1
    adapter.stop()


def test_live_run_safety_questions_are_answered_from_the_page(tmp_path):
    executor = FakeRobotExecutor()
    adapter = started(make_adapter(tmp_path, executor=executor, simulate=False))
    apply_form(adapter, {"deck.paper.slot": 8})
    assert adapter.run()
    wait_for(lambda: adapter.waiting == "question")
    asking = adapter.snapshot()
    assert "Is the physical deck arranged exactly as the ACTIVE DECK above?" in asking.question
    assert asking.status == "ANSWER YES OR NO" and executor.calls == [] and adapter.run() is False
    assert adapter.submit_text("no")
    wait_for(lambda: adapter.waiting == "idle")
    assert "Run cancelled. Nothing was executed." in chat_text(adapter) and executor.calls == []
    assert adapter.run()
    wait_for(lambda: adapter.waiting == "question")
    assert adapter.submit_text("yes")
    wait_for(lambda: executor.calls and adapter.waiting == "proposal")
    assert executor.calls[0][0]["deck"]["paper"]["slot"] == 8
    adapter.stop()


def test_operator_is_asked_in_the_chat_before_anything_can_run(tmp_path):
    adapter = make_adapter(tmp_path, operator=None)
    adapter.start()
    wait_for(lambda: adapter.waiting == "operator")
    assert "Who is running this experiment?" in chat_text(adapter)
    assert adapter.snapshot().status == "ENTER YOUR NAME"
    assert adapter.run() is False and adapter.propose_form({"print.droplets_per_spot": 2}) is False
    assert adapter.submit_text("Ada Lovelace")
    wait_for(lambda: adapter.waiting == "idle")
    assert adapter.session.operator == "Ada Lovelace"
    adapter.stop()


def test_shared_plan_model_drives_both_snapshots(tmp_path):
    adapter = started(make_adapter(tmp_path))
    current = adapter.snapshot()
    assert [section.title for section in current.current_sections] == [
        "DILUTIONS", "PRINTING", "DECK", "LIQUIDS", "PIPETTING", "LAB-OWNED PARAMETERS"]
    adapter.propose_form({"print.droplets_per_spot": 2})
    wait_for(lambda: adapter.snapshot().proposal_id == 1)
    proposed = adapter.snapshot()
    printing = next(section for section in proposed.proposed_sections if section.title == "PRINTING")
    assert printing.value("Drops per position").startswith("2")
    adapter.stop()


def test_the_launcher_refuses_unsafe_live_starts_before_serving_a_page(monkeypatch, capsys):
    def serve(*args, **kwargs):
        raise AssertionError("a page was served")

    monkeypatch.setattr(launcher.ui, "run", serve)
    assert launcher.main(["--offline"]) == 2
    assert launcher.main(["--host", "0.0.0.0"]) == 2
    monkeypatch.setattr(launcher.Config, "live_robot_llm_auth_error", lambda: "REFUSED: live runs need Vertex AI")
    assert launcher.main([]) == 2
    assert "REFUSED: live runs need Vertex AI" in capsys.readouterr().err


def test_repository_reference_images_are_the_defaults_and_load():
    assert set(DEFAULT_REFERENCE_IMAGES) == {
        "96-WELL PLATE", "PAPER SUBSTRATE / HOLDER", "20 ML VIAL RACK"}
    for path in DEFAULT_REFERENCE_IMAGES.values():
        assert path.is_file()
        assert path.suffix.lower() == ".jpg"
        assert path.read_bytes()[:2] == b"\xff\xd8"


def test_chat_is_named_tall_scrollable_and_full_width_without_a_splitter():
    assert CHAT_TITLE == "Chat"
    assert CHAT_HISTORY_HEIGHT_PX >= 500
    source = inspect.getsource(build_page)
    assert "ui.splitter" not in source
    assert 'section-chat w-full' in source
    assert 'chat-scroll overflow-y-auto' in source
    assert source.index('section-chat w-full') < source.index('section-plan plan-card w-full')


def test_uploaded_reference_bytes_can_replace_the_default_for_the_session():
    assert _image_data_uri("image/png", b"png") == "data:image/png;base64,cG5n"


def test_experiment_flow_comes_from_the_authoritative_plan():
    config = load_config(DEFAULT_CONFIG)
    flow = experiment_flow(config)
    assert [step.title for step in flow] == ["STEP 1 — PREPARE DILUTIONS", "STEP 2 — PRINT"]
    assert "vial A2" in flow[0].source and "vial A1" in flow[0].source and "Slot 7" in flow[0].source
    assert "Slot 4" in flow[0].destination and "A11 | B11" in flow[0].destination and "16×" in flow[0].destination
    assert "Slot 4" in flow[1].source and "A11 | B11" in flow[1].source
    assert flow[1].destination.endswith("Slot 5 · columns 1 · rows A–H · 1 drop per position")

    changed = deepcopy(config)
    changed["dilution"].update({"factors": [2, 5, 10], "start_row": "C", "plate_column": "3"})
    changed["print"].update({"paper_start_column": 4, "replicates": 2, "droplets_per_spot": 3})
    changed["deck"]["plate"]["slot"] = 6
    changed["deck"]["paper"]["slot"] = 8
    changed_flow = experiment_flow(changed)
    assert "C3 | D3 | E3" in changed_flow[0].destination
    assert "2× | 5× | 10×" in changed_flow[0].destination
    assert "Slot 6" in changed_flow[1].source
    assert changed_flow[1].destination.endswith("Slot 8 · columns 4-5 · rows C–E · 3 drops per position")


def test_experiment_flow_omits_disabled_steps():
    config = load_config(DEFAULT_CONFIG)
    config["print"]["enabled"] = False
    assert [step.title for step in experiment_flow(config)] == ["STEP 1 — PREPARE DILUTIONS"]
    config["dilution"]["enabled"] = False
    assert experiment_flow(config) == []


@pytest.mark.parametrize("introduction,name", [
    ("My name is Sni", "Sni"), ("I'm Ada Lovelace.", "Ada Lovelace"),
    ("I am Sni", "Sni"), ("Call me Sni", "Sni"), ("Ada Lovelace", "Ada Lovelace"),
])
def test_chat_introduction_uses_the_session_operator(tmp_path, introduction, name):
    adapter = make_adapter(tmp_path, operator=None)
    try:
        adapter.start()
        wait_for(lambda: adapter.waiting == "operator")
        assert adapter.snapshot().operator == "" and not adapter.snapshot().run_ready
        assert adapter.submit_text(introduction)
        wait_for(lambda: adapter.waiting == "idle")
        assert adapter.snapshot().operator == adapter.session.operator == name
        assert adapter.session.log.context["operator"] == name
        assert adapter.snapshot().run_ready
    finally:
        adapter.stop()


def test_run_readiness_follows_existing_session_and_validation(tmp_path, monkeypatch):
    adapter = started(make_adapter(tmp_path))
    try:
        ready = adapter.snapshot()
        assert ready.run_ready
        for waiting in ("busy", "operator", "question", "proposal", "clarify"):
            assert not replace(ready, waiting=waiting).run_ready
        for changes in ({"running": True}, {"operator": ""}, {"validation_ok": False},
                        {"proposed": ready.current}, {"status": "SESSION ENDED"}):
            assert not replace(ready, **changes).run_ready
        assert adapter.propose_form({"print.droplets_per_spot": 3})
        wait_for(lambda: adapter.waiting == "proposal")
        assert not adapter.snapshot().run_ready
        adapter.submit_text("yes")
        wait_for(lambda: adapter.waiting == "idle")
        assert adapter.snapshot().run_ready
        report = Report()
        report.error("test", "Missing required experiment information")
        monkeypatch.setattr(adapter.session.state, "validate", lambda: report)
        assert not adapter.snapshot().validation_ok
        assert not adapter.snapshot().run_ready
    finally:
        adapter.stop()


def test_page_identity_readiness_and_shared_live_confirmation(tmp_path, monkeypatch):
    asyncio.run(_check_page_identity(tmp_path, monkeypatch))


async def _check_page_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
    # Construct real NiceGUI elements, but use only a fake executor and manually tick the refresh timer.
    executor = FakeRobotExecutor()
    adapter = make_adapter(tmp_path, executor=executor, simulate=False, operator=None)
    ticks = []
    monkeypatch.setattr(ui, "timer", lambda interval, callback: ticks.append(callback))
    monkeypatch.setattr(ui, "run_javascript", lambda *args, **kwargs: None)
    client = Client(ui.page("/nanodrop-test"))
    try:
        with client:
            build_page(adapter)
            wait_for(lambda: adapter.waiting == "operator")
            ticks[0]()
            elements = list(client.elements.values())
            texts = [getattr(e, "text", "") for e in elements]
            assert "Agent NanoDrop" in texts and "Chat" in texts and "EXPERIMENT PROCEDURE" in texts
            assert "CURRENT PLAN" not in texts
            assert not any("Simulation-only session ·" in text or "Describe the experiment" in text for text in texts)
            user_badge = next(e for e in elements if getattr(e, "text", "").startswith("Current User ="))
            assert user_badge.text == "Current User = waiting for chat input"
            assert "current-user" in user_badge.classes
            assert not any(e.visible for e in elements if getattr(e, "text", "") == "ENTER YOUR NAME")
            bubbles = [e for e in elements if isinstance(e, ui.chat_message)]
            assert bubbles and all(e.props["name"] == "Agent NanoDrop" for e in bubbles)
            steps = [e for e in elements if "flow-step-title" in e.classes]
            assert len(steps) == 2
            buttons = [e for e in elements if isinstance(e, ui.button) and e.text == "Run on OT-2"]
            assert len(buttons) == 2
            assert all(not b.enabled and b.props["color"] == "warning" for b in buttons)
            callbacks = [next(v.handler for v in b._event_listeners.values() if v.type == "click") for b in buttons]
            assert callbacks[0] is callbacks[1]
            assert adapter.submit_text("My name is Sni")
            wait_for(lambda: adapter.waiting == "idle")
            ticks[0]()
            assert user_badge.text == "Current User = Sni"
            assert all(b.enabled and b.props["color"] == "positive" for b in buttons)
            assert adapter.submit_text("run")
            wait_for(lambda: adapter.waiting == "idle" and "check the plan and press Run on OT-2" in chat_text(adapter))
            assert executor.calls == [] and executor.checks == 0
            dialog = next(e for e in elements if isinstance(e, ui.dialog))
            for callback in callbacks:
                callback()
                assert dialog.value is True
                assert executor.calls == [] and executor.checks == 0
                dialog.close()
            apply_form(adapter, {"print.droplets_per_spot": 2})
            adapter.propose_form({"print.droplets_per_spot": 3})
            wait_for(lambda: adapter.waiting == "proposal")
            ticks[0]()
            assert all(not b.enabled and b.props["color"] == "warning" for b in buttons)
            adapter.submit_text("no")
            wait_for(lambda: adapter.waiting == "idle")
            ticks[0]()
            assert all(b.enabled and b.props["color"] == "positive" for b in buttons)
            starts = []
            monkeypatch.setattr(adapter, "run", lambda: starts.append("existing adapter.run") or True)
            start_button = next(e for e in elements if isinstance(e, ui.button) and e.text == "Start run")
            start_callback = next(v.handler for v in start_button._event_listeners.values() if v.type == "click")
            callbacks[0]()
            start_callback()
            assert starts == ["existing adapter.run"] and not dialog.value
    finally:
        adapter.stop()
        client.delete()


@pytest.mark.parametrize("utterance", [
    "don't do the dilutions", "skip dilution", "just print", "the samples are already made",
    "Don't prepare the dilutions this run", "no dilution", "print only", "don't dilute", "skip the dilution step",
])
def test_informal_print_only_requests_propose_without_extra_questions(tmp_path, utterance):
    # read by the router (a report like "the samples are already made" is recorded without it)
    adapter = started(make_adapter(tmp_path, llm=router(skip_dilution_reply)))
    try:
        before = adapter.snapshot().current
        assert adapter.submit_text(utterance)
        wait_for(lambda: adapter.waiting != "busy")
        assert adapter.waiting == "proposal", chat_text(adapter)
        proposed = adapter.snapshot().proposed
        assert proposed["dilution"]["enabled"] is False
        assert proposed["dilution"]["factors"] == before["dilution"]["factors"]
        assert proposed["dilution"]["plate_column"] == before["dilution"]["plate_column"]
        assert adapter.snapshot().current == before
        assert adapter.session.state.physical["dilutions_prepared"] is None
        assert adapter.run() is False
        assert adapter.submit_text("yes")
        wait_for(lambda: adapter.waiting == "idle")
        assert not adapter.snapshot().current["dilution"]["enabled"]
        assert adapter.session.state.physical["dilutions_prepared"] is not None
        assert adapter.session.state.runs == []
    finally:
        adapter.stop()


def selection_reply(message):
    """What the router returns for "just print columns 1 2 and 3, rows a b c, 3 drops each" and its variants."""
    columns = [int(number) for number in re.findall(r"\d+", message.split("rows")[0])]
    rows = re.findall(r"\b([a-h])\b", message.split("rows")[1].split(",")[0])
    drops = int(re.search(r"(\d+)\s+drops", message).group(1))
    return {"route": "experiment_change", "explanation": "Proposes a print-only run.", "changes": [
        {"path": "dilution.enabled", "value": False, "evidence": "just print"},
        {"path": "paper_columns", "value": columns, "evidence": message.split(",")[0]},
        {"path": "rows", "value": [row.upper() for row in rows], "evidence": "rows " + " ".join(rows)},
        {"path": "print.droplets_per_spot", "value": drops, "evidence": f"{drops} drops each"}]}


def test_informal_print_selection_preserves_source_wells_and_needs_approval(tmp_path):
    adapter = started(make_adapter(tmp_path, llm=router(selection_reply)))
    try:
        before = adapter.snapshot().current
        assert adapter.submit_text("just print columns 1 2 and 3, rows a b c, 3 drops each")
        wait_for(lambda: adapter.waiting != "busy")
        assert adapter.waiting == "proposal", chat_text(adapter)
        from src.agents.dye_demo.plan import build_plan
        from src.agents.dye_demo.columns import paper_columns_printed
        proposed = adapter.snapshot().proposed
        plan = build_plan(proposed)
        assert not plan.do_dilution and plan.do_print
        assert plan.rows == ["A", "B", "C"] and plan.total_drops == 27
        assert paper_columns_printed(proposed) == [1, 2, 3]
        assert proposed["dilution"]["factors"] == before["dilution"]["factors"][:3]
        assert proposed["dilution"]["plate_column"] == before["dilution"]["plate_column"]
        assert proposed["print"]["droplet_volume_ul"] == before["print"]["droplet_volume_ul"]
        assert "I interpreted rows A, B, C" in " ".join(adapter.session.pending.notes)
        assert adapter.snapshot().current == before
        assert adapter.submit_text("run")
        wait_for(lambda: adapter.waiting == "proposal")
        assert adapter.session.state.runs == []
    finally:
        adapter.stop()


@pytest.mark.parametrize("utterance", [
    "What if we skip dilution?", "Should we just print?", 'Explain "just print".',
    "Don't move the dilution plate", "Don't change the print volume",
])
def test_informal_interpretation_keeps_questions_quotes_and_settings_negations_safe(tmp_path, utterance):
    adapter = started(make_adapter(tmp_path, llm=router(answer_reply)))
    try:
        before = adapter.snapshot().current
        assert adapter.submit_text(utterance)
        wait_for(lambda: adapter.waiting != "busy")
        assert adapter.snapshot().proposed is None
        assert adapter.snapshot().current == before
        assert adapter.session.state.physical["dilutions_prepared"] is None
        assert adapter.session.state.runs == []
    finally:
        adapter.stop()


def test_print_only_does_not_overwrite_conflicting_prepared_samples(tmp_path):
    adapter = started(make_adapter(tmp_path, llm=router(skip_dilution_reply)))
    try:
        record = {"wells": ["A11"], "factors": [100], "total_volume_ul": 150, "source": "earlier run"}
        adapter.session.state.physical["dilutions_prepared"] = record
        assert adapter.submit_text("just print")
        wait_for(lambda: adapter.waiting != "busy")
        assert adapter.snapshot().proposed is None
        assert adapter.session.state.physical["dilutions_prepared"] == record
        assert adapter.snapshot().current["dilution"]["enabled"]
    finally:
        adapter.stop()


def test_short_followup_uses_the_single_recent_setting(tmp_path):
    class FollowupModel:
        def __init__(self):
            self.calls = []

        def invoke(self, messages):
            self.calls.append(messages)
            count = 2 if len(self.calls) == 1 else 3
            return SimpleNamespace(content=json.dumps({"intent": "change", "changes": [
                {"path": "print.droplets_per_spot", "value": count, "evidence": str(count)}]}))
    model = FollowupModel()
    adapter = started(make_adapter(tmp_path, llm=LLMClient(lambda: model)))
    try:
        adapter.submit_text("Print 2 drops per spot")
        wait_for(lambda: adapter.waiting == "proposal")
        adapter.submit_text("yes")
        wait_for(lambda: adapter.waiting == "idle")
        adapter.submit_text("make it 3")
        wait_for(lambda: adapter.waiting != "busy")
        assert adapter.waiting == "proposal", chat_text(adapter)
        assert adapter.snapshot().proposed["print"]["droplets_per_spot"] == 3
        assert adapter.snapshot().current["print"]["droplets_per_spot"] == 2
        # the router resolves "it" from the conversation it is shown (the message itself is passed on unchanged)
        prompt = model.calls[-1][-1][1]
        conversation = prompt.split("RECENT CONVERSATION")[1].split("PYTHON NOTES")[0]
        assert "Scientist: Print 2 drops per spot" in conversation
        assert prompt.split("SCIENTIST'S MESSAGE:\n")[-1].strip() == "make it 3"
    finally:
        adapter.stop()


def test_row_subset_keeps_the_factors_at_those_source_wells(tmp_path):
    adapter = started(make_adapter(tmp_path, llm=router(selection_reply)))
    try:
        apply_form(adapter, {"dilution.factors": [2, 7, 11]})
        adapter.submit_text("just print columns 2 3, rows b c, 3 drops each")
        wait_for(lambda: adapter.waiting != "busy")
        assert adapter.waiting == "proposal", chat_text(adapter)
        proposed = adapter.snapshot().proposed
        assert proposed["dilution"]["start_row"] == "B"
        assert proposed["dilution"]["factors"] == [7, 11]
        assert adapter.session.pending.physical["dilutions_prepared"]["wells"] == ["B11", "C11"]
    finally:
        adapter.stop()


def test_top_proposal_buttons_visibility_and_actions(tmp_path, monkeypatch):
    asyncio.run(_check_top_proposal_buttons(tmp_path, monkeypatch))


async def _check_top_proposal_buttons(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
    adapter = started(make_adapter(tmp_path))
    ticks = []
    monkeypatch.setattr(ui, "timer", lambda interval, callback: ticks.append(callback))
    monkeypatch.setattr(ui, "run_javascript", lambda *args, **kwargs: None)
    client = Client(ui.page("/top-buttons-test"))
    try:
        with client:
            build_page(adapter)
            wait_for(lambda: adapter.waiting == "idle")
            ticks[0]()
            elements = list(client.elements.values())

            # 1. Top proposal action buttons exist in top header row
            top_apply = next(e for e in elements if isinstance(e, ui.button) and e.text == "Apply")
            top_discard = next(e for e in elements if isinstance(e, ui.button) and e.text == "Discard")
            top_actions = top_apply.parent_slot.parent

            # 2. Hidden when idle / no pending proposal
            assert not top_actions.visible

            # 3. Create a proposal
            assert adapter.propose_form({"print.droplets_per_spot": 3})
            wait_for(lambda: adapter.waiting == "proposal")
            ticks[0]()

            # 4. Visible when proposal is pending
            assert top_actions.visible

            # 5. Executing top_apply callback applies the proposal
            top_apply_cb = next(v.handler for v in top_apply._event_listeners.values() if v.type == "click")
            top_apply_cb(None)
            wait_for(lambda: adapter.waiting == "idle")

            # 6. Create another proposal and verify top_discard callback discards it
            assert adapter.propose_form({"print.droplets_per_spot": 2})
            wait_for(lambda: adapter.waiting == "proposal")
            ticks[0]()
            top_discard_cb = next(v.handler for v in top_discard._event_listeners.values() if v.type == "click")
            top_discard_cb(None)
            wait_for(lambda: adapter.waiting == "idle")
    finally:
        adapter.stop()



