"""Offline tests for the NiceGUI adapter; no browser, model, or robot is contacted."""
from __future__ import annotations

import time
import json
from copy import deepcopy
import inspect
from types import SimpleNamespace

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
