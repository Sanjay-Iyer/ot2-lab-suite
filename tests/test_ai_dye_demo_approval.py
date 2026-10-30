"""Task 1.1: a pending proposal changes the plan only through the operator's explicit, exact approval.

The router here is scripted so it can be wrong on purpose. Nothing is ever applied because of how a model read a message.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.dye_demo.gui.adapter import DemoGuiAdapter
from src.agents.dye_demo.llm import LLMClient
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.session import DemoSession, SessionSettings

REQUEST = "make it 3 replicates"
PROPOSE = {"kind": "plan_change", "response": "This would print 3 replicates.", "edit_modes": {"printing": "modify"},
           "operations": [{"op": "set", "path": "print.replicates", "value": 3}]}
APPROVE = {"kind": "approve", "response": "Applying that now."}


class Router:
    """Answers scripted messages. A message that should never reach the model fails the test."""

    def __init__(self, replies):
        self.replies, self.seen = replies, []

    def invoke(self, messages):
        message = messages[-1][1].split("SCIENTIST'S MESSAGE:\n")[-1].strip()
        self.seen.append(message)
        return SimpleNamespace(content=json.dumps(self.replies[message]))


def session(tmp_path: Path, replies: dict, *, run_button="Run on OT-2"):
    model = Router({REQUEST: PROPOSE, **replies})
    output: list[str] = []
    settings = SessionSettings(simulate=True, config_source=DEFAULT_CONFIG, working_config=tmp_path / "working.yaml",
                               run_dir=tmp_path / "run", session_label="approval", operator="Tester",
                               skip_llm_startup=True, run_button=run_button, llm_first=True, raise_errors=True)
    demo = DemoSession(settings, load_config(DEFAULT_CONFIG), llm=LLMClient(lambda: model),
                       executor=lambda *_: 0, input_fn=lambda _prompt: "yes", output_fn=output.append,
                       sleep=lambda _: None)
    demo.operator = "Tester"
    demo.request_run_confirmation = lambda: output.append("CONFIRM RUN")
    return demo, model, output


def replicates(demo) -> int:
    return demo.state.config["print"]["replicates"]


def assert_unchanged(demo, fingerprint):
    assert demo.state.revision == 0 and replicates(demo) == 1
    assert demo.state.full_fingerprint() == fingerprint


def with_proposal(tmp_path, replies=None, **kwargs):
    demo, model, output = session(tmp_path, replies or {}, **kwargs)
    fingerprint = demo.state.full_fingerprint()
    demo._handle(REQUEST)
    assert demo.pending is not None and demo.pending.id == 1
    assert_unchanged(demo, fingerprint)
    return demo, model, output, fingerprint


# ── the model's reading is never authorization ──────────────────────────────────

@pytest.mark.parametrize("message", ["Looks good.", "sure", "ok", "yeah go ahead", "perfect, thanks",
                                     "I agree, do it", "That seems right to me"])
def test_a_model_read_approval_applies_nothing(tmp_path, message):
    demo, model, output, fingerprint = with_proposal(tmp_path, {message: APPROVE})
    demo._handle(message)
    assert message in model.seen, "this message is for the model to read, and its reading must not apply anything"
    assert demo.pending is not None and demo.pending.id == 1
    assert_unchanged(demo, fingerprint)
    said = "\n".join(output)
    assert "Nothing was applied" in said and "Applying that now" not in said
    assert demo.turns[-1]["events"][-1] == {"type": "noop", "reason": "model approval is not authorization"}


@pytest.mark.parametrize("reply", [
    {"route": "approve_proposal"},
    {"kind": "approve_proposal", "response": "ok"},
    {"kind": "approve", "response": "ok", "revises": True},
    {"kind": "approve", "response": "ok", "operations": [{"op": "set", "path": "print.replicates", "value": 3}]},
], ids=["old_route", "alias", "revises", "with_operations"])
def test_no_spelling_of_a_model_approval_applies_anything(tmp_path, reply):
    demo, _, _, fingerprint = with_proposal(tmp_path, {"fine": reply})
    demo._handle("fine")
    assert demo.pending is not None
    assert_unchanged(demo, fingerprint)


def test_a_question_misread_as_approval_changes_nothing(tmp_path):
    demo, _, _, fingerprint = with_proposal(tmp_path, {"What would this change?": APPROVE})
    demo._handle("What would this change?")
    assert demo.pending is not None
    assert_unchanged(demo, fingerprint)


@pytest.mark.parametrize("message", ["What would this change?", "Explain this proposal."])
def test_a_question_about_the_proposal_is_answered_and_it_keeps_waiting(tmp_path, message):
    answer = {"kind": "question", "response": "It would print three replicates of each dilution."}
    demo, _, output, fingerprint = with_proposal(tmp_path, {message: answer})
    demo._handle(message)
    assert demo.pending is not None and demo.pending.id == 1
    assert_unchanged(demo, fingerprint)
    assert any("three replicates" in line for line in output)


def test_agreeing_while_asking_for_a_change_replaces_the_proposal_and_applies_nothing(tmp_path):
    message = "I agree with the reasoning, but change the volume."
    change = {"kind": "plan_change", "response": "This would print 3 uL drops.", "edit_modes": {"printing": "modify"},
              "operations": [{"op": "set", "path": "print.droplet_volume_ul", "value": 3.0}]}
    demo, _, _, _ = with_proposal(tmp_path, {message: change})
    demo._handle(message)
    assert demo.pending is not None and demo.pending.id != 1
    assert demo.state.revision == 0 and demo.state.config["print"]["droplet_volume_ul"] == 5.0


def test_a_model_approval_with_nothing_waiting_is_answered(tmp_path):
    demo, _, output = session(tmp_path, {"Looks good.": APPROVE})
    demo._handle("Looks good.")
    assert demo.pending is None and demo.state.revision == 0
    assert any("No proposal is waiting" in line for line in output)


def test_the_terminal_path_refuses_a_model_approval_too(tmp_path):
    demo, _, _, fingerprint = with_proposal(tmp_path, {"Looks good.": APPROVE}, run_button=None)
    demo._handle("Looks good.")
    assert demo.pending is not None
    assert_unchanged(demo, fingerprint)


def test_a_following_explicit_yes_applies_the_waiting_proposal(tmp_path):
    demo, _, _, _ = with_proposal(tmp_path, {"Looks good.": APPROVE})
    demo._handle("Looks good.")
    demo._handle("yes")
    assert demo.pending is None and demo.state.revision == 1 and replicates(demo) == 3


def test_saying_yes_to_a_question_makes_a_proposal_and_not_a_change(tmp_path):
    question = {"kind": "question", "response": "Yes, that is possible.",
                "hypothetical": [{"op": "set", "path": "print.replicates", "value": 3}]}
    demo, _, _ = session(tmp_path, {"Could I print 3 replicates?": question, "Yes, do that.": PROPOSE})
    demo._handle("Could I print 3 replicates?")
    demo._handle("Yes, do that.")
    assert demo.pending is not None and demo.state.revision == 0 and replicates(demo) == 1


# ── what does approve: the operator's explicit words ────────────────────────────

@pytest.mark.parametrize("message", ["yes", "Yes.", "y", "apply", "yes please", "confirm", "approve", "approve it",
                                     "Yes, I approve proposal #1."])
def test_explicit_approval_applies_without_asking_the_model(tmp_path, message):
    demo, model, _, _ = with_proposal(tmp_path)
    demo._handle(message)
    assert model.seen == [REQUEST], "an explicit decision is read by code"
    assert demo.pending is None and demo.state.revision == 1 and replicates(demo) == 3


def test_approving_another_proposal_number_applies_nothing(tmp_path):
    demo, model, output, fingerprint = with_proposal(tmp_path)
    demo._handle("Yes, I approve proposal #2.")
    assert model.seen == [REQUEST]
    assert demo.pending is not None
    assert_unchanged(demo, fingerprint)
    assert any("approves proposal #2" in line for line in output)


def test_approval_joined_to_a_new_request_applies_nothing(tmp_path):
    demo, model, _, fingerprint = with_proposal(tmp_path)
    demo._handle("Yes, I approve proposal #1. Now move the plate to slot 2.")
    assert model.seen == [REQUEST]
    assert demo.pending is not None
    assert_unchanged(demo, fingerprint)


@pytest.mark.parametrize("message", ["no", "discard", "reject", "no thanks"])
def test_an_explicit_no_discards_without_asking_the_model(tmp_path, message):
    demo, model, _, fingerprint = with_proposal(tmp_path)
    demo._handle(message)
    assert model.seen == [REQUEST]
    assert demo.pending is None
    assert_unchanged(demo, fingerprint)


def test_cancel_still_drops_the_proposal(tmp_path):
    demo, _, _, fingerprint = with_proposal(tmp_path)
    demo._handle("cancel")
    assert demo.pending is None
    assert_unchanged(demo, fingerprint)


def test_pasted_material_saying_yes_is_not_an_approval(tmp_path):
    message = 'The SOP says: "yes, apply it"'
    demo, _, _, fingerprint = with_proposal(tmp_path, {message: {"kind": "question", "response": "That is the SOP."}})
    demo._handle(message)
    assert demo.pending is not None
    assert_unchanged(demo, fingerprint)


# ── the page's buttons name the exact proposal and revision ─────────────────────

def test_the_button_applies_the_proposal_it_names(tmp_path):
    demo, model, _, _ = with_proposal(tmp_path)
    demo.decide_from_button(True, 1, 0)
    assert model.seen == [REQUEST]
    assert demo.pending is None and demo.state.revision == 1 and replicates(demo) == 3
    assert demo.turns[-1]["classification"] == "confirmation", "a button press is recorded like a typed decision"


def test_the_button_discards_the_proposal_it_names(tmp_path):
    demo, _, _, fingerprint = with_proposal(tmp_path)
    demo.decide_from_button(False, 1, 0)
    assert demo.pending is None
    assert_unchanged(demo, fingerprint)


@pytest.mark.parametrize("apply", [True, False])
@pytest.mark.parametrize("proposal_id,revision", [(2, 0), (1, 1), (99, 99)], ids=["other_id", "other_revision", "both"])
def test_a_button_for_anything_else_changes_nothing(tmp_path, apply, proposal_id, revision):
    demo, _, output, fingerprint = with_proposal(tmp_path)
    demo.decide_from_button(apply, proposal_id, revision)
    assert demo.pending is not None and demo.pending.id == 1
    assert_unchanged(demo, fingerprint)
    assert any("out of date" in line for line in output)


def test_a_click_on_a_replaced_proposal_does_not_apply_its_replacement(tmp_path):
    other = {"kind": "plan_change", "response": "This would print 4 replicates.", "edit_modes": {"printing": "modify"},
             "operations": [{"op": "set", "path": "print.replicates", "value": 4}]}
    demo, _, _, fingerprint = with_proposal(tmp_path, {"actually 4": other})
    demo._handle("actually 4")
    replacement = demo.pending.id
    assert replacement != 1
    demo.decide_from_button(True, 1, 0)                    # the page still showed the first proposal
    assert demo.pending is not None and demo.pending.id == replacement
    assert_unchanged(demo, fingerprint)


def test_a_button_with_nothing_waiting_changes_nothing(tmp_path):
    demo, _, _ = session(tmp_path, {})
    fingerprint = demo.state.full_fingerprint()
    demo.decide_from_button(True, 1, 0)
    assert_unchanged(demo, fingerprint)


# ── the same rule through the real page adapter and worker thread ───────────────

def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for the GUI adapter")


def page(tmp_path, replies):
    model = Router({REQUEST: PROPOSE, **replies})
    settings = SessionSettings(simulate=True, config_source=DEFAULT_CONFIG, working_config=tmp_path / "working.yaml",
                               run_dir=tmp_path / "run", session_label="approval", operator="Tester",
                               skip_llm_startup=True, run_button="Run on OT-2", llm_first=True, raise_errors=True)
    demo = DemoSession(settings, load_config(DEFAULT_CONFIG), llm=LLMClient(lambda: model), executor=lambda *_: 0,
                       sleep=lambda _: None)
    adapter = DemoGuiAdapter(demo)
    adapter.start()
    wait_for(lambda: adapter.waiting == "idle")
    return adapter, demo


def test_the_page_applies_only_through_the_shown_proposal(tmp_path):
    adapter, demo = page(tmp_path, {"Looks good.": APPROVE})
    try:
        assert adapter.submit_text(REQUEST)
        wait_for(lambda: adapter.waiting == "proposal")
        shown = adapter.snapshot()
        assert shown.proposal_id == 1
        assert adapter.submit_text("Looks good.")
        wait_for(lambda: adapter.waiting == "proposal" and any(
            "Nothing was applied" in message.text for message in adapter.messages()))
        assert demo.state.revision == 0 and demo.pending is not None
        assert adapter.decide_proposal(True, shown.proposal_id, shown.revision)
        wait_for(lambda: adapter.waiting == "idle" and demo.state.revision == 1)
        assert demo.pending is None and replicates(demo) == 3
    finally:
        adapter.stop()


def test_the_page_refuses_a_stale_click_and_stays_usable(tmp_path):
    adapter, demo = page(tmp_path, {})
    try:
        assert adapter.submit_text(REQUEST)
        wait_for(lambda: adapter.waiting == "proposal")
        assert adapter.decide_proposal(True, 7, 0)
        wait_for(lambda: adapter.waiting == "proposal" and any(
            "out of date" in message.text for message in adapter.messages()))
        assert demo.state.revision == 0 and demo.pending is not None
    finally:
        adapter.stop()


def test_the_page_has_nothing_to_decide_without_a_proposal(tmp_path):
    adapter, demo = page(tmp_path, {})
    try:
        assert adapter.decide_proposal(True, None, None) is False
        assert adapter.decide_proposal(True, 1, 0) is False, "no proposal is waiting"
        assert demo.state.revision == 0
    finally:
        adapter.stop()
