"""One tip for the entire run (tips.policy = single_tip): the dye demo's default, and a scientist's explicit choice.

One physical tip is picked up once (tips.start_tip), used for every water and dye transfer, every mix and every print,
and released once at the very end: returned to its rack position when tips.return_tips is true, dropped in the trash
when it is false. The plan counts tips from the policy, so a late start tip such as H12 is valid when the run needs only
one tip and stays refused for the policies that need several. The other two policies keep their meaning.

The protocol tests run the real protocol file on the recording fake OT-2; the chat tests are conversations through the
real DemoSession with a scripted router; the page test builds the real NiceGUI page. Simulation only: nothing contacts
a robot or a model.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from nicegui import Client, core, ui

from src.agents.dye_demo import render
from src.agents.dye_demo.gui.app import build_page
from src.agents.dye_demo.gui.labware_svg import render_tiprack_svg, tip_settings
from src.agents.dye_demo.model import TIP_ORDER, FieldError, normalize_policy
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.fake_opentrons import load_protocol_module, run_protocol
from src.agents.dye_demo.state import ExperimentState, ProposalRejected
from src.agents.dye_demo.validation import validate
from tests.test_ai_dye_demo_audit_fixes import RUN, live
from tests.test_ai_dye_demo_gui import make_adapter, router, started, wait_for
from tests.test_ai_dye_demo_request_understanding import DEFAULT, change, proposes, says, talk
from tests.test_ai_dye_demo_tip_settings import GREEN, tip_cards, tip_circle

PLATE = DEFAULT["deck"]["plate"]["load_name"]
PAPER = DEFAULT["deck"]["paper"]["load_name"]
RACK = DEFAULT["deck"]["tuberack"]["load_name"]
RELEASES = ("return_tip", "drop_tip")
LIQUID = ("aspirate", "dispense", "mix", "blow_out", "air_gap")


def tips(**settings):
    config = deepcopy(DEFAULT)
    config["tips"].update(settings)
    return config


def tip_events(log):
    return [(entry[0], entry[1]) for entry in log if entry[0] in ("pick_up_tip", *RELEASES)]


def without_session(config):
    config = deepcopy(config)
    config.pop("session", None)
    return config


@pytest.fixture(scope="module")
def protocol_module():
    return load_protocol_module()


# ── 1. the plan: exactly one tip ────────────────────────────────────────────────────────────────────────────────────

def test_the_demo_default_is_one_tip_for_the_entire_run_and_needs_exactly_one_tip():
    assert DEFAULT["tips"] == {"start_tip": "A1", "return_tips": True, "policy": "single_tip"}
    plan = build_plan(DEFAULT)
    assert plan.tips_needed == 1 and [tip.tip for tip in plan.tips] == ["A1"] and plan.next_tip == "B1"
    # the same plan under the other policies counts their tips (the count comes from the policy, not a special case)
    assert build_plan(tips(policy="per_liquid")).tips_needed == 10
    every = build_plan(tips(policy="new_tip_every_transfer"))
    assert every.tips_needed == sum(op.kind in ("transfer", "print") for op in every.operations) == 76


def test_reusing_one_tip_is_a_warning_not_a_refusal():
    report = validate(DEFAULT)
    assert report.ok
    assert [(issue.code, issue.message) for issue in report.warnings if issue.code == "tips.single_tip"] == [
        ("tips.single_tip", "One tip will be reused for the entire run. This can cause cross-contamination.")]


# ── 2-4. the protocol: one pick-up, every liquid step, one release ──────────────────────────────────────────────────

@pytest.mark.parametrize("return_tips,release", [(True, "return_tip"), (False, "drop_tip")])
def test_one_pick_up_and_one_release_at_the_very_end(protocol_module, return_tips, release):
    log = run_protocol(protocol_module, tips(return_tips=return_tips)).log
    assert tip_events(log) == [("pick_up_tip", "A1"), (release, "A1")]      # returned, or dropped in the trash


def test_the_same_tip_stays_on_through_the_dilutions_the_mixing_and_the_printing(protocol_module):
    log = run_protocol(protocol_module, tips()).log
    picked = next(index for index, entry in enumerate(log) if entry[0] == "pick_up_tip")
    released = next(index for index, entry in enumerate(log) if entry[0] in RELEASES)
    liquid = [index for index, entry in enumerate(log) if entry[0] in LIQUID]
    assert picked < min(liquid) and max(liquid) < released                  # every liquid step with the tip on
    assert {entry[3] for entry in log if entry[0] == "aspirate"} == {"A1"}
    assert {entry[2][0] for entry in log if entry[0] == "aspirate"} == {RACK, PLATE}     # water, dye, then prints
    assert any(entry[0] == "aspirate" and entry[2][0] == PLATE and following[0] == "dispense" and following[2][0] == PLATE
               for entry, following in zip(log, log[1:]))                     # the dilution mix, same tip
    assert any(entry[0] == "dispense" and entry[2][0] == PAPER for entry in log)


# ── 5-6. a late start tip: valid when one tip is needed, refused when the policy needs more ──────────────────────────

def test_h12_is_a_valid_start_when_the_run_needs_one_tip(protocol_module):
    proposal = ExperimentState(tips()).propose([change("tips.start_tip", "H12", "start tips at H12")],
                                               request="start tips at H12")
    assert proposal.paths == ["tips.start_tip"] and proposal.report.ok
    plan = build_plan(proposal.after)
    assert [tip.tip for tip in plan.tips] == ["H12"] and plan.next_tip is None
    assert tip_events(run_protocol(protocol_module, proposal.after).log) == [("pick_up_tip", "H12"),
                                                                             ("return_tip", "H12")]


@pytest.mark.parametrize("policy", ["per_liquid", "new_tip_every_transfer"])
def test_h12_stays_refused_when_the_policy_needs_more_tips(policy):
    state = ExperimentState(tips(policy=policy))
    before = state.fingerprint()
    with pytest.raises(ProposalRejected, match="start from an earlier tip"):
        state.propose([change("tips.start_tip", "H12", "start tips at H12")], request="start tips at H12")
    assert state.fingerprint() == before


# ── 7-10. chat: switching policies, and one request that sets all three tip settings ────────────────────────────────

@pytest.mark.parametrize("before,text,after", [
    ("per_liquid", "use one tip for the whole run", "single_tip"),
    ("new_tip_every_transfer", "don't change tips", "single_tip"),
    ("single_tip", "use one tip per liquid", "per_liquid"),
])
def test_switching_the_tip_policy_is_one_change_applied_on_yes(tmp_path, before, text, after):
    config = tips(policy=before)
    conversation = talk(tmp_path, says(text, proposes(change("tips.policy", after, text))), "yes", config=config)
    assert conversation.proposal_paths(1) == [["tips.policy"]]
    expected = deepcopy(config)
    expected["tips"]["policy"] = after
    assert without_session(conversation.config) == without_session(expected)     # that setting, and nothing else
    assert build_plan(conversation.config).tips_needed == build_plan(expected).tips_needed
    if after == "single_tip":
        assert build_plan(conversation.config).tips_needed == 1


def test_start_tip_single_tip_and_return_in_one_request(tmp_path):
    config = tips(policy="per_liquid", return_tips=False)
    text = "Start at tip D5, use only that tip, and return it."
    conversation = talk(tmp_path, says(text, proposes(change("tips.start_tip", "D5", "Start at tip D5"),
                                                      change("tips.policy", "single_tip", "use only that tip"),
                                                      change("tips.return_tips", True, "return it"))),
                        "yes", config=config)
    assert conversation.proposal_paths(1) == [["tips.policy", "tips.return_tips", "tips.start_tip"]]
    expected = deepcopy(config)
    expected["tips"] = {"start_tip": "D5", "return_tips": True, "policy": "single_tip"}
    assert without_session(conversation.config) == without_session(expected)
    assert [tip.tip for tip in build_plan(conversation.config).tips] == ["D5"]


# ── 11-12. the tip rack card ────────────────────────────────────────────────────────────────────────────────────────

def test_the_policy_reads_one_tip_for_entire_run():
    assert tip_settings(tips(start_tip="D5")) == [("Start tip", "D5"), ("Return tips", "Yes"),
                                                  ("Tip policy", "One tip for entire run")]
    assert render.format_value("tips.policy", "single_tip") == "one tip for entire run"
    assert normalize_policy("One tip for entire run") == "single_tip"


def test_the_tip_rack_highlights_only_the_one_tip():
    svg = render_tiprack_svg(tips(start_tip="D5"))
    assert svg.count(GREEN) == 1 and GREEN in tip_circle(svg, "D5") and "start tip" in tip_circle(svg, "D5")
    assert "1 tip this run: D5" in svg
    assert all("before the start tip" in tip_circle(svg, tip) for tip in TIP_ORDER[:TIP_ORDER.index("D5")])


# ── 13. the page: the applied policy stays until the proposal is applied ─────────────────────────────────────────────

def policy_reply(message):
    policy = "per_liquid" if "per liquid" in message else "single_tip"
    return {"route": "experiment_change", "explanation": "Proposes the tip policy.",
            "changes": [{"path": "tips.policy", "value": policy, "evidence": message}]}


def test_the_page_shows_the_applied_policy_until_the_proposal_is_applied(tmp_path, monkeypatch):
    asyncio.run(_check_policy_cards(tmp_path, monkeypatch))


async def _check_policy_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
    adapter = started(make_adapter(tmp_path, llm=router(policy_reply)))
    ticks = []
    monkeypatch.setattr(ui, "timer", lambda interval, callback: ticks.append(callback))
    monkeypatch.setattr(ui, "run_javascript", lambda *args, **kwargs: None)
    client = Client(ui.page("/single-tip-test"))

    async def tick():
        ticks[0]()
        await asyncio.sleep(0.05)          # a panel refresh runs on the event loop's next turn

    try:
        with client:
            build_page(adapter)
            assert adapter.submit_text("use one tip per liquid")
            wait_for(lambda: adapter.waiting == "proposal")
            assert adapter.submit_text("yes")
            wait_for(lambda: adapter.waiting == "idle" and adapter.snapshot().revision == 1)
            await tick()
            assert tip_cards(client)["current"]["settings"]["Tip policy"] == ("One tip per liquid", False)

            assert adapter.submit_text("use one tip for the whole run")
            wait_for(lambda: adapter.waiting == "proposal")
            await tick()
            cards = tip_cards(client)
            assert cards["current"]["settings"]["Tip policy"] == ("One tip per liquid", False)   # nothing changed yet
            assert cards["proposed"]["settings"]["Tip policy"] == ("One tip for entire run", True)   # marked amber
            assert cards["current"]["svg"].count(GREEN) == 10 and cards["proposed"]["svg"].count(GREEN) == 1

            assert adapter.submit_text("yes")
            wait_for(lambda: adapter.waiting == "idle" and adapter.snapshot().revision == 2)
            await tick()
            cards = tip_cards(client)
            assert set(cards) == {"current"}
            assert cards["current"]["settings"]["Tip policy"] == ("One tip for entire run", False)
            assert "1 tip this run: A1" in cards["current"]["svg"]
    finally:
        adapter.stop()
        client.delete()


# ── 14. an unknown policy ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["two_tips", "banana"])
def test_an_unknown_tip_policy_is_refused_and_changes_nothing(value):
    with pytest.raises(FieldError, match="tip policy is single_tip, per_liquid or new_tip_every_transfer"):
        normalize_policy(value)
    state = ExperimentState(tips(policy="per_liquid"))
    before = state.fingerprint()
    with pytest.raises(ProposalRejected):
        state.propose([change("tips.policy", value, f"tip policy {value}")], request=f"tip policy {value}")
    assert state.fingerprint() == before


# ── 15-16. the other policies keep their meaning ────────────────────────────────────────────────────────────────────

def test_one_tip_per_liquid_is_unchanged(protocol_module):
    config = tips(policy="per_liquid", return_tips=False)
    plan = build_plan(config)
    assert [tip.tip for tip in plan.tips] == list(TIP_ORDER[:10])      # water, dye, then one per printed dilution
    log = run_protocol(protocol_module, config).log
    assert [tip for kind, tip in tip_events(log) if kind == "pick_up_tip"] == [tip.tip for tip in plan.tips]
    assert [tip for kind, tip in tip_events(log) if kind == "drop_tip"] == [tip.tip for tip in plan.tips]
    vials: dict[str, set] = {}
    for entry in log:
        if entry[0] == "aspirate" and entry[2][0] == RACK:
            vials.setdefault(entry[3], set()).add(entry[2][1])
    assert all(len(sources) == 1 for sources in vials.values())         # every tip draws from one vial only


def test_a_new_tip_every_transfer_is_unchanged(protocol_module):
    config = tips(policy="new_tip_every_transfer", return_tips=False)
    plan = build_plan(config)
    picked = [tip for kind, tip in tip_events(run_protocol(protocol_module, config).log) if kind == "pick_up_tip"]
    assert picked == [tip.tip for tip in plan.tips] and len(set(picked)) == len(picked) == plan.tips_needed == 76


# ── the tip ledger after a live run ─────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("return_tips", [False, True])
def test_a_live_single_tip_run_records_only_its_one_tip(tmp_path, return_tips):
    session, out = live(tmp_path, RUN, config=tips(start_tip="D5", return_tips=return_tips))
    assert [run["tips_used"] for run in session.state.runs] == [["D5"]]
    assert session.state.physical["tips_used"] == ["D5"]          # a returned tip was used too: never picked up again
    assert "Tip D5 was used by this run, so the next unused tip is E5." in out[0]
