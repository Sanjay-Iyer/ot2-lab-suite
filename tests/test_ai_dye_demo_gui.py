"""Offline tests for the NiceGUI adapter; no browser, model, or robot is contacted."""
from __future__ import annotations

import time
import json
from types import SimpleNamespace

from src.agents.dye_demo.gui.adapter import DemoGuiAdapter
from src.agents.dye_demo.llm import LLMClient
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.session import DemoSession, SessionSettings


def make_adapter(tmp_path, *, executor=None, llm=None):
    settings = SessionSettings(simulate=True, config_source=DEFAULT_CONFIG,
                               working_config=tmp_path / "working.yaml", run_dir=tmp_path / "run",
                               session_label="GUI test", operator="Tester", skip_llm_startup=True,
                               raise_errors=True)
    session = DemoSession(settings, load_config(DEFAULT_CONFIG), llm=llm,
                          executor=executor or (lambda path, simulate, log: 0), sleep=lambda _: None)
    return DemoGuiAdapter(session)


class OneReplyModel:
    def invoke(self, _messages):
        return SimpleNamespace(content=json.dumps({
            "intent": "change",
            "explanation": "Proposed through the existing session.",
            "changes": [{"path": "print.droplets_per_spot", "value": 3,
                         "evidence": "three drops per position"}],
        }))


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for GUI adapter")


def test_form_change_is_a_validated_proposal_until_apply(tmp_path):
    adapter = make_adapter(tmp_path)
    adapter.start()
    before = adapter.snapshot().current
    assert adapter.propose_form({"print.droplets_per_spot": 3})
    wait_for(lambda: adapter.snapshot().proposal_id == 1)
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
    adapter = make_adapter(tmp_path, llm=LLMClient(lambda: OneReplyModel()))
    adapter.start()
    assert adapter.submit_text("Use three drops per position.")
    wait_for(lambda: adapter.snapshot().proposal_id == 1)
    assert adapter.snapshot().proposed["print"]["droplets_per_spot"] == 3
    assert adapter.snapshot().current["print"]["droplets_per_spot"] == 1
    messages = adapter.drain_messages()
    assert any(message.text.startswith("Proposed Plan updated") for message in messages)
    assert not any("Apply proposal #1?" in message.text for message in messages)
    adapter.stop()


def test_discard_leaves_authoritative_state_unchanged(tmp_path):
    adapter = make_adapter(tmp_path)
    adapter.start()
    adapter.propose_form({"print.paper_start_column": 3})
    wait_for(lambda: adapter.snapshot().proposal_id == 1)
    adapter.submit_text("no")
    wait_for(lambda: adapter.snapshot().proposal_id is None)
    assert adapter.snapshot().revision == 0
    assert adapter.snapshot().current["print"]["paper_start_column"] == 1
    adapter.stop()


def test_validate_and_simulate_use_the_session_and_stay_offline(tmp_path):
    calls = []

    def execute(path, simulate, log):
        calls.append((path, simulate))
        return 0

    adapter = make_adapter(tmp_path, executor=execute)
    adapter.start()
    adapter.validate()
    wait_for(lambda: any("VALIDATION" in message.text for message in adapter.drain_messages()))
    assert adapter.simulate()
    wait_for(lambda: bool(calls))
    assert calls[0][1] is True
    wait_for(lambda: adapter.snapshot().status == "SIMULATION COMPLETE")
    adapter.stop()


def test_shared_plan_model_drives_both_snapshots(tmp_path):
    adapter = make_adapter(tmp_path)
    adapter.start()
    current = adapter.snapshot()
    assert [section.title for section in current.current_sections] == [
        "DILUTIONS", "PRINTING", "DECK", "LIQUIDS", "PIPETTING", "LAB-OWNED PARAMETERS"]
    adapter.propose_form({"print.droplets_per_spot": 2})
    wait_for(lambda: adapter.snapshot().proposal_id == 1)
    proposed = adapter.snapshot()
    printing = next(section for section in proposed.proposed_sections if section.title == "PRINTING")
    assert printing.value("Drops per position").startswith("2")
    adapter.stop()
