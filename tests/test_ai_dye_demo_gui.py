"""Offline tests for the NiceGUI adapter; no browser, model, or robot is contacted."""
from __future__ import annotations

import inspect
import json
import threading
import time
from copy import deepcopy
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
    assert CHAT_TITLE == "AI Agent Chatbox"
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
    assert flow[1].destination.endswith("Slot 5 · columns 1 · 1 drop per position")

    changed = deepcopy(config)
    changed["dilution"].update({"factors": [2, 5, 10], "start_row": "C", "plate_column": "3"})
    changed["print"].update({"paper_start_column": 4, "replicates": 2, "droplets_per_spot": 3})
    changed["deck"]["plate"]["slot"] = 6
    changed["deck"]["paper"]["slot"] = 8
    changed_flow = experiment_flow(changed)
    assert "C3 | D3 | E3" in changed_flow[0].destination
    assert "2× | 5× | 10×" in changed_flow[0].destination
    assert "Slot 6" in changed_flow[1].source
    assert changed_flow[1].destination.endswith("Slot 8 · columns 4 | 5 · 3 drops per position")


def test_experiment_flow_omits_disabled_steps():
    config = load_config(DEFAULT_CONFIG)
    config["print"]["enabled"] = False
    assert [step.title for step in experiment_flow(config)] == ["STEP 1 — PREPARE DILUTIONS"]
    config["dilution"]["enabled"] = False
    assert experiment_flow(config) == []
