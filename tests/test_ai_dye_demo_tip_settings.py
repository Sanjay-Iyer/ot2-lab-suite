"""Tip settings in Agent NanoDrop: the tip rack card on the NiceGUI page, and chat requests that change the tips.

The chat tests are real conversations through DemoSession with a scripted router (the reply a faithful model gives for
the scientist's words); grounding, validation, proposals and approval are the real session's, and the red-team
invariants run after every turn. The page tests build the real NiceGUI page around a GUI adapter whose chat model is a
scripted router. Simulation only: nothing contacts a robot or a model.
"""
from __future__ import annotations

import asyncio
import re
from copy import deepcopy

import pytest
from nicegui import Client, core, ui

from src.agents.dye_demo.gui.app import build_page
from src.agents.dye_demo.gui.labware_svg import render_tiprack_svg, tip_settings
from src.agents.dye_demo.model import OFF_DECK, TIP_ORDER, FieldError, normalize_tip
from src.agents.dye_demo.plan import build_plan
from tests.test_ai_dye_demo_gui import make_adapter, router, started, wait_for
from tests.test_ai_dye_demo_request_understanding import DEFAULT, change, proposes, says, talk

GREEN = 'fill="#22c55e"'


def tips_config(**tips):
    config = deepcopy(DEFAULT)
    config["tips"].update(tips)
    return config


def per_liquid(**tips):
    """The multi-tip drawing cases: one tip per liquid, set explicitly (the demo default uses one tip for the run)."""
    return tips_config(policy="per_liquid", return_tips=False, **tips)


def tip_circle(svg: str, tip: str) -> str:
    """The circle drawn for one rack position, found by its tooltip."""
    match = re.search(rf'<circle [^>]*><title>Tip {tip}:[^<]*</title></circle>', svg)
    assert match, f"no circle for tip {tip}"
    return match.group(0)


def deck_slot(svg: str, slot: int) -> str:
    """The deck-map square drawn for one deck slot, found by its tooltip."""
    match = re.search(rf'<rect [^>]*><title>Slot {slot}:[^<]*</title></rect>', svg)
    assert match, f"no deck square for slot {slot}"
    return match.group(0)


# ── the tip rack card: where the rack is, which tips this run takes ─────────────────────────────────────────────────

def test_tip_rack_card_shows_the_rack_slot_in_green_and_the_tips_this_run_uses():
    config = per_liquid()
    svg = render_tiprack_svg(config)
    plan = build_plan(config)
    used = [assignment.tip for assignment in plan.tips]
    assert "P20 Tip Rack — Slot 9" in svg
    assert used == list(TIP_ORDER[:10]) and plan.tips_needed == 10          # water, dye, one per printed dilution
    for tip in used:
        assert GREEN in tip_circle(svg, tip)
    assert 'stroke="#14532d" stroke-width="2.5"' in tip_circle(svg, "A1")   # the start tip stands out
    assert "start tip" in tip_circle(svg, "A1")
    assert GREEN not in tip_circle(svg, "C2")                               # the next tips stay in the rack
    assert "10 tips this run: A1-B2" in svg
    # the deck map: the tip rack's slot is the only green square; the other labware is labelled in grey
    assert GREEN in deck_slot(svg, 9) and "P20 tip rack" in deck_slot(svg, 9)
    for slot, what in ((4, "plate"), (5, "paper"), (7, "vials")):
        assert GREEN not in deck_slot(svg, slot) and what in deck_slot(svg, slot)
    assert "fixed trash" in deck_slot(svg, 12)
    assert svg.count(GREEN) == len(used) + 1


def test_tip_rack_card_follows_the_start_tip_and_the_rack_slot():
    config = per_liquid(start_tip="G1")
    config["deck"]["tiprack"]["slot"] = 6
    svg = render_tiprack_svg(config)
    assert "P20 Tip Rack — Slot 6" in svg
    assert GREEN in deck_slot(svg, 6) and GREEN not in deck_slot(svg, 9)
    assert GREEN in tip_circle(svg, "G1") and "start tip" in tip_circle(svg, "G1")
    for tip in ("A1", "F1"):                            # before the start tip: not used by this run
        assert "stroke-dasharray" in tip_circle(svg, tip) and "before the start tip" in tip_circle(svg, tip)
    assert GREEN in tip_circle(svg, "H2") and GREEN not in tip_circle(svg, "A3")
    assert "10 tips this run: G1-H2" in svg


def test_a_new_tip_every_transfer_takes_more_tips_from_the_rack():
    config = tips_config(policy="new_tip_every_transfer")
    plan = build_plan(config)
    svg = render_tiprack_svg(config)
    assert plan.tips_needed > build_plan(DEFAULT).tips_needed
    assert svg.count(GREEN) == plan.tips_needed + 1                          # every tip taken, and the rack's slot
    assert f"{plan.tips_needed} tips this run: A1-" in svg


def test_tip_rack_card_warns_when_the_rack_runs_short_or_is_off_the_deck():
    assert "Needs 10 tips, only 1 left from H12" in render_tiprack_svg(per_liquid(start_tip="H12"))
    config = deepcopy(DEFAULT)
    config["deck"]["tiprack"]["slot"] = OFF_DECK
    svg = render_tiprack_svg(config)
    assert "P20 Tip Rack — OFF DECK" in svg and "The tip rack is OFF DECK" in svg
    assert not any(GREEN in deck_slot(svg, slot) for slot in range(1, 13))


@pytest.mark.parametrize("tips,shown", [
    ({}, [("Start tip", "A1"), ("Return tips", "Yes"), ("Tip policy", "One tip for entire run")]),
    ({"policy": "per_liquid", "return_tips": False},
     [("Start tip", "A1"), ("Return tips", "No"), ("Tip policy", "One tip per liquid")]),
    ({"start_tip": "g1", "return_tips": True, "policy": "new_tip_every_transfer"},
     [("Start tip", "G1"), ("Return tips", "Yes"), ("Tip policy", "New tip every transfer")]),
])
def test_tip_settings_are_shown_in_the_scientists_words(tips, shown):
    assert tip_settings(tips_config(**tips)) == shown


# ── chat requests: each becomes a proposal of exactly that change, applied only on yes ──────────────────────────────

TIP_REQUESTS = [
    # what the scientist types, the tips before, the field the router proposes, its value
    ("start from tip G1", {}, "tips.start_tip", "G1"),
    ("use G1 as the first tip", {}, "tips.start_tip", "G1"),
    ("use tip B3", {}, "tips.start_tip", "B3"),
    ("change start tip to B3", {}, "tips.start_tip", "B3"),
    ("return the tips", {"return_tips": False}, "tips.return_tips", True),
    ("set return tips to yes", {"return_tips": False}, "tips.return_tips", True),
    ("don't return the tips", {"return_tips": True}, "tips.return_tips", False),
    ("set return tips to no", {"return_tips": True}, "tips.return_tips", False),
    ("use a fresh tip every transfer", {}, "tips.policy", "new_tip_every_transfer"),
    ("new tip every transfer", {}, "tips.policy", "new_tip_every_transfer"),
    ("use one tip per liquid", {"policy": "new_tip_every_transfer"}, "tips.policy", "per_liquid"),
    ("reuse tips per liquid", {"policy": "new_tip_every_transfer"}, "tips.policy", "per_liquid"),
]


@pytest.mark.parametrize("text,tips,path,value", TIP_REQUESTS)
def test_tip_requests_become_a_proposal_of_that_change_applied_only_on_yes(tmp_path, text, tips, path, value):
    config = tips_config(**tips)
    conversation = talk(tmp_path, says(text, proposes(change(path, value, text))), "yes", config=config)
    assert conversation.proposal_paths(1) == [[path]]
    expected = deepcopy(config)
    expected["tips"][path.split(".")[1]] = value
    applied = deepcopy(conversation.config)
    applied.pop("session", None)
    expected.pop("session", None)
    assert applied == expected                                              # that setting, and nothing else


def test_tip_rack_move_and_start_tip_in_one_request(tmp_path):
    text = "move the tip rack to slot 6 and start from tip C4"
    conversation = talk(tmp_path, says(text, proposes(change("deck.tiprack.slot", 6, "move the tip rack to slot 6"),
                                                      change("tips.start_tip", "C4", "start from tip C4"))), "yes")
    assert conversation.proposal_paths(1) == [["deck.tiprack.slot", "tips.start_tip"]]
    assert "physically move the P20 tip rack from Slot 9 to Slot 6" in conversation.out(1)
    assert conversation.config["deck"]["tiprack"]["slot"] == 6 and conversation.config["tips"]["start_tip"] == "C4"


def test_a_tip_rack_move_to_its_own_slot_changes_only_the_start_tip(tmp_path):
    text = "move the tip rack to slot 9 and start from tip C4"
    conversation = talk(tmp_path, says(text, proposes(change("deck.tiprack.slot", 9, "move the tip rack to slot 9"),
                                                      change("tips.start_tip", "C4", "start from tip C4"))))
    assert conversation.proposal_paths(1) == [["tips.start_tip"]]


def test_a_tip_that_is_not_on_the_rack_is_refused_in_one_clear_sentence(tmp_path):
    conversation = talk(tmp_path, says("start from tip Z99", proposes(change("tips.start_tip", "Z99",
                                                                            "start from tip Z99"))))
    out = conversation.out(1)
    assert conversation.pending is None and conversation.config["tips"]["start_tip"] == "A1"
    assert "tips are A1-H12 on the 96-tip rack, got 'Z99'" in out
    assert out.count("got 'Z99'") == 1                                      # not the same sentence twice
    with pytest.raises(FieldError, match=r"row letter first \(for example G1\)"):
        normalize_tip("1G")                                                 # a reversed name still gets the hint


def test_a_start_tip_too_late_for_the_plan_is_refused_with_the_latest_that_fits(tmp_path):
    text = "keep everything else the same, just change the start tip to H12"
    conversation = talk(tmp_path, says(text, proposes(change("tips.start_tip", "H12", "change the start tip to H12"))),
                        config=per_liquid())
    out = conversation.out(1)
    assert conversation.pending is None and conversation.config["tips"]["start_tip"] == "A1"
    assert "this plan needs 10 tips but only 1 remain from H12" in out and "G11 is the latest that fits" in out
    # G11 does fit: the same request with it is a one-change proposal
    fits = talk(tmp_path, says("start from tip G11", proposes(change("tips.start_tip", "G11", "start from tip G11"))),
                config=per_liquid())
    assert fits.proposal_paths(1) == [["tips.start_tip"]]


def test_a_tip_setting_that_is_already_set_proposes_nothing(tmp_path):
    conversation = talk(tmp_path, says("return the tips", proposes(change("tips.return_tips", True,
                                                                         "return the tips"))))
    assert conversation.pending is None and "already set" in conversation.out(1)


# ── the page: the applied plan and the waiting proposal each show their own tip settings ────────────────────────────

def start_tip_g1_on_slot_6(message):
    return {"route": "experiment_change", "explanation": "Proposes the tip changes.", "changes": [
        {"path": "deck.tiprack.slot", "value": 6, "evidence": "move the tip rack to slot 6"},
        {"path": "tips.start_tip", "value": "G1", "evidence": "start from tip G1"},
        {"path": "tips.return_tips", "value": False, "evidence": "don't return the tips"}]}


def _panel(element) -> str:
    """Which panel an element is drawn in: the applied Experiment Procedure or the waiting proposal."""
    node = element
    while node is not None:
        if "section-plan" in node.classes:
            return "current" if "plan-card" in node.classes else "proposed"
        node = node.parent_slot.parent if node.parent_slot else None
    return "other"


def tip_cards(client) -> dict[str, dict[str, object]]:
    """Per panel: the tip settings shown above the rack ({label: (value, changed)}) and the tip rack SVG."""
    cards: dict[str, dict[str, object]] = {}
    for element in client.elements.values():
        if "tip-settings" in element.classes and element.visible:
            labels = [child for child in element.default_slot.children]
            settings = {labels[i].text.rstrip(":"): (labels[i + 1].text, "tip-setting-changed" in labels[i + 1].classes)
                        for i in range(0, len(labels), 2)}
            cards.setdefault(_panel(element), {})["settings"] = settings
        content = str(getattr(element, "content", ""))
        if content.startswith("<svg") and "P20 Tip Rack" in content:
            cards.setdefault(_panel(element), {})["svg"] = content
    return cards


def test_page_shows_applied_tip_settings_and_the_proposal_separately(tmp_path, monkeypatch):
    asyncio.run(_check_tip_cards(tmp_path, monkeypatch))


async def _check_tip_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
    adapter = started(make_adapter(tmp_path, llm=router(start_tip_g1_on_slot_6)))
    ticks = []
    monkeypatch.setattr(ui, "timer", lambda interval, callback: ticks.append(callback))
    monkeypatch.setattr(ui, "run_javascript", lambda *args, **kwargs: None)
    client = Client(ui.page("/tip-settings-test"))

    async def tick():
        ticks[0]()
        await asyncio.sleep(0.05)          # a panel refresh runs on the event loop's next turn

    try:
        with client:
            build_page(adapter)
            await tick()
            cards = tip_cards(client)
            assert set(cards) == {"current"}                                  # no proposal: one tip rack card
            assert cards["current"]["settings"] == {"Start tip": ("A1", False), "Return tips": ("Yes", False),
                                                    "Tip policy": ("One tip for entire run", False)}
            assert "P20 Tip Rack — Slot 9" in cards["current"]["svg"]

            assert adapter.submit_text("move the tip rack to slot 6, start from tip G1 and don't return the tips")
            wait_for(lambda: adapter.waiting == "proposal")
            await tick()
            cards = tip_cards(client)
            # the applied plan is unchanged until Apply; the proposal shows its own values, changed ones marked
            assert cards["current"]["settings"] == {"Start tip": ("A1", False), "Return tips": ("Yes", False),
                                                    "Tip policy": ("One tip for entire run", False)}
            assert "P20 Tip Rack — Slot 9" in cards["current"]["svg"]
            assert cards["proposed"]["settings"] == {"Start tip": ("G1", True), "Return tips": ("No", True),
                                                     "Tip policy": ("One tip for entire run", False)}
            assert "P20 Tip Rack — Slot 6" in cards["proposed"]["svg"]
            assert GREEN in deck_slot(cards["proposed"]["svg"], 6) and GREEN in deck_slot(cards["current"]["svg"], 9)

            assert adapter.submit_text("yes")
            wait_for(lambda: adapter.waiting == "idle" and adapter.snapshot().revision == 1)
            await tick()
            cards = tip_cards(client)
            assert set(cards) == {"current"}
            assert cards["current"]["settings"] == {"Start tip": ("G1", False), "Return tips": ("No", False),
                                                    "Tip policy": ("One tip for entire run", False)}
            assert "P20 Tip Rack — Slot 6" in cards["current"]["svg"]
            assert GREEN in deck_slot(cards["current"]["svg"], 6) and GREEN not in deck_slot(cards["current"]["svg"], 9)
    finally:
        adapter.stop()
        client.delete()
