"""Request understanding across a whole conversation: clarification state, grounding, negation and cancellation.

Each test is a real conversation through DemoSession (src/agents/dye_demo/session.py) with a scripted conversational
router - the replies a faithful model would give, including its questions ("clarify" with the parts it understood) -
recorded with everything the router was shown, so the tests also check what the model is told. The red-team invariants
run after every turn (no state change without an explicit yes, no stale proposal, valid deck, protocol matches plan).
Simulation only: the session's executor is a recording function; nothing contacts a robot.

    PROBLEM 1  clarification answers are merged into the request, field by field; nothing unrelated is lost
    PROBLEM 2  grounding accepts any clear wording of a value, asks only about genuine ambiguity, keeps physics strict
    PROBLEM 3  negated, skipped, already-done, cancelled and no-change messages are told apart; the model reads them
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.harness import ConversationSpec, ReplayDriver, run_conversation
from src.agents.dye_demo.redteam.interpreter import ReplayInterpreter

DEFAULT = load_config(DEFAULT_CONFIG)
# Slot 5 holds the paper print plate in the default deck; these start with the paper in slot 8 so slot 5 is free.
FREE_5 = deepcopy(DEFAULT)
FREE_5["deck"]["paper"]["slot"] = 8


# ── a scripted router that records what it was shown ────────────────────────────

@dataclass
class RecordingRouter(ReplayInterpreter):
    seen: list[tuple[int, str]] = field(default_factory=list)

    def invoke(self, messages):
        if "Respond READY" not in messages[-1][1]:
            self.seen.append((self.turn, messages[-1][1]))
        return super().invoke(messages)


def change(path: str, value: Any = None, evidence: str = "", **extra: Any) -> dict[str, Any]:
    data = {"path": path, "evidence": evidence, **extra}
    if "op" not in extra:
        data["value"] = value
    return data


def routed(route: str, *changes: dict[str, Any], clarification: str = "", unresolved=(), preserve=(),
           answer: str = "") -> str:
    return json.dumps({"route": route, "changes": list(changes), "clarification": clarification,
                       "unresolved": list(unresolved), "preserve": list(preserve), "answer": answer,
                       "explanation": "scripted router reply"})


def proposes(*changes: dict[str, Any], preserve=()) -> str:
    return routed("experiment_change", *changes, preserve=preserve)


def asks(question: str, *understood: dict[str, Any], unresolved=()) -> str:
    return routed("clarify", *understood, clarification=question, unresolved=unresolved)


def says(text: str, *replies: str) -> dict[str, Any]:
    """A message and the router's reply (or replies) if the message reaches the router."""
    return {"text": text, "llm": [{"kind": "route", "reply": reply} for reply in replies]}


@dataclass
class Talk:
    result: dict[str, Any]
    router: RecordingRouter

    @property
    def session(self):
        return self.result["session"]

    @property
    def config(self):
        return self.session.state.config

    @property
    def pending(self):
        return self.session.pending

    def out(self, turn: int) -> str:
        return self.result["transcript"][turn - 1]["output"]

    def events(self, turn: int, kind: str) -> list[dict[str, Any]]:
        return [event for event in self.result["transcript"][turn - 1]["events"] if event["type"] == kind]

    def proposal_paths(self, turn: int) -> list[list[str]]:
        return [sorted(event["paths"]) for event in self.events(turn, "proposal")]

    def router_saw(self, turn: int) -> list[str]:
        return [human for number, human in self.router.seen if number == turn]

    def record(self, turn: int) -> dict[str, Any]:
        return self.session.turns[turn - 1]


def talk(tmp_path, *messages: Any, config: dict[str, Any] | None = None) -> Talk:
    items = [message if isinstance(message, dict) else {"text": message} for message in messages]
    script = {number: [dict(item) for item in message.get("llm", [])] for number, message in enumerate(items, start=1)}
    router = RecordingRouter(seed=0, script=script)
    spec = ConversationSpec(seed=0, persona="replay", length=len(items), live_logic=False, interpreter="replay")
    result = run_conversation(spec, workdir=tmp_path, driver=ReplayDriver(items), interpreter=router,
                              keep_transcript=True, return_session=True, config=config)
    conversation = Talk(result, router)
    assert result["violations"] == [], result["violations"]
    return conversation


def after(proposal, dotted: str) -> Any:
    value = proposal.after
    for part in dotted.split("."):
        value = value[part]
    return value


# The user's example request: one unclear part (which plate) and three clear ones.
FOUR_CHANGES = "Move the plate to slot 1, use factors 3x and 16x, and print paper columns 4 and 5."
PLATE_1 = change("deck.plate.slot", 1, "Move the plate to slot 1")
FACTORS_3_16 = change("dilution.factors", [3, 16], "factors 3x and 16x")
PAPER_4_5 = change("paper_columns", [4, 5], "print paper columns 4 and 5")
WHICH_PLATE = asks("Which plate should go to slot 1 - the 96-well dilution plate or the paper print plate?",
                   FACTORS_3_16, PAPER_4_5, unresolved=["deck.plate.slot"])


def assert_four_changes(proposal, *, factors=(3, 16), plate_slot=1):
    assert after(proposal, "deck.plate.slot") == plate_slot
    assert after(proposal, "dilution.factors") == list(factors)
    assert after(proposal, "print.paper_start_column") == 4 and after(proposal, "print.replicates") == 2


# ════════════════════════════════════════════════════════════════════════════════
# PROBLEM 1 - clarification state
# ════════════════════════════════════════════════════════════════════════════════

def test_a_one_question_in_a_four_change_request_keeps_the_other_three(tmp_path):
    c = talk(tmp_path, says(FOUR_CHANGES, WHICH_PLATE),
             says("The 96-well plate.", proposes(PLATE_1, FACTORS_3_16, PAPER_4_5)), "yes")
    assert not c.proposal_paths(1) and c.record(1)["clarifying_after"]
    assert "I kept the rest of your request: Dilution factors: 3× | 16×; Paper columns: 4, 5." in c.out(1)
    # the answer is read WITH the question, the request and the understood changes (never glued onto the text)
    notes = c.router_saw(2)[0]
    assert 'You asked the scientist: "Which plate should go to slot 1' in notes and FOUR_CHANGES in notes
    assert "UNDERSTOOD CHANGES" in notes and '"paper_columns"' in notes and "UNRESOLVED" in notes
    assert "SCIENTIST'S MESSAGE:\nThe 96-well plate." in notes
    [paths] = c.proposal_paths(2)
    assert {"deck.plate.slot", "dilution.factors", "print.paper_start_column", "print.replicates"} <= set(paths)
    config = c.config
    assert config["deck"]["plate"]["slot"] == 1 and config["dilution"]["factors"] == [3, 16]
    assert config["print"]["paper_start_column"] == 4 and config["print"]["replicates"] == 2


def test_a_answer_merge_does_not_depend_on_the_model_repeating_the_request(tmp_path):
    # the router returns only what the answer settles; the understood changes are merged in by Python
    c = talk(tmp_path, says(FOUR_CHANGES, WHICH_PLATE), says("The 96-well plate.", proposes(PLATE_1)))
    assert c.pending is not None
    assert_four_changes(c.pending)


def test_b_two_sequential_questions_keep_everything_settled_so_far(tmp_path):
    request = "Move the rack to slot 6, make 2x and 4x dilutions, and print 3 drops of each at a few microliters."
    rack = change("deck.tuberack.slot", 6, "the rack to slot 6")
    factors = change("dilution.factors", [2, 4], "2x and 4x dilutions")
    drops = change("print.droplets_per_spot", 3, "3 drops of each")
    c = talk(tmp_path,
             says(request, asks("Which rack - the vial rack or the P20 tip rack?", factors, drops,
                                unresolved=["deck.tuberack.slot"])),
             says("The vial rack.", asks("How many microliters should each drop be?", rack, factors, drops,
                                         unresolved=["print.droplet_volume_ul"])),
             says("4 uL", proposes(change("print.droplet_volume_ul", "4 uL", "4 uL"))), "yes")
    assert c.record(1)["clarifying_after"] and c.record(2)["clarifying_after"]
    assert "How many microliters" in c.out(2) and "Which rack" not in c.out(2)       # no completed question repeats
    assert "Vial rack location: Slot 6" in c.out(2)                                   # the first answer is kept
    assert "Which rack" not in c.out(3) and "How many microliters" not in c.out(3)
    config = c.config
    assert config["deck"]["tuberack"]["slot"] == 6 and config["dilution"]["factors"] == [2, 4]
    assert config["print"]["droplets_per_spot"] == 3 and config["print"]["droplet_volume_ul"] == 4.0


def test_c_a_short_answer_fills_only_the_missing_field(tmp_path):
    columns = change("paper_columns", [2, 3], "paper columns 2 and 3")
    paper = change("deck.paper.slot", 8, "move the paper to slot 8")
    c = talk(tmp_path,
             says("Print paper columns 2 and 3 with a few drops each, and move the paper to slot 8.",
                  asks("How many drops per spot?", columns, paper, unresolved=["print.droplets_per_spot"])),
             says("4", proposes(change("print.droplets_per_spot", 4, "4"))), "yes")
    config = c.config
    assert config["print"]["droplets_per_spot"] == 4 and config["deck"]["paper"]["slot"] == 8
    assert config["print"]["paper_start_column"] == 2 and config["print"]["replicates"] == 2


def test_d_a_full_sentence_answer_is_an_answer_not_a_new_request(tmp_path):
    # before the redesign a sentence-length answer was dispatched as a NEW request and the rest of the request was lost
    c = talk(tmp_path, says(FOUR_CHANGES, WHICH_PLATE),
             says("I meant the 96-well dilution plate, please move that one to slot 1.",
                  proposes(change("deck.plate.slot", 1, "move that one to slot 1"))))
    assert c.pending is not None and c.session.clarifying is None
    assert_four_changes(c.pending)
    assert "UNDERSTOOD CHANGES" in c.router_saw(2)[0]


def test_e_an_answer_can_correct_one_earlier_field_and_nothing_else(tmp_path):
    c = talk(tmp_path, says(FOUR_CHANGES, WHICH_PLATE),
             says("The 96-well plate. And make the factors 3x and 8x instead.",
                  proposes(PLATE_1, change("dilution.factors", [3, 8], "factors 3x and 8x instead"))))
    assert_four_changes(c.pending, factors=(3, 8))


def test_e_an_answer_can_name_different_labware_and_a_different_slot(tmp_path):
    # "Which plate goes in slot 1?" - "Actually use the vial rack and put it in slot 2."
    c = talk(tmp_path, says(FOUR_CHANGES, WHICH_PLATE),
             says("Actually use the vial rack and put it in slot 2.",
                  proposes(change("deck.tuberack.slot", 2, "use the vial rack and put it in slot 2"),
                           change("deck.plate.slot", op="drop"))))
    proposal = c.pending
    assert after(proposal, "deck.tuberack.slot") == 2 and after(proposal, "deck.plate.slot") == 4
    assert after(proposal, "dilution.factors") == [3, 16] and after(proposal, "print.paper_start_column") == 4


def test_f_an_answer_can_add_one_more_legitimate_change(tmp_path):
    c = talk(tmp_path, says(FOUR_CHANGES, WHICH_PLATE),
             says("The 96-well plate, and use 4 drops per spot too.",
                  proposes(PLATE_1, change("print.droplets_per_spot", 4, "4 drops per spot"))), "yes")
    config = c.config
    assert config["deck"]["plate"]["slot"] == 1 and config["dilution"]["factors"] == [3, 16]
    assert config["print"]["paper_start_column"] == 4 and config["print"]["droplets_per_spot"] == 4


def test_g_an_unrelated_reply_gets_the_same_focused_question_and_loses_nothing(tmp_path):
    rack = change("deck.tuberack.slot", 6, "the rack to slot 6")
    factors = change("dilution.factors", [2, 5], "2x and 5x")
    c = talk(tmp_path,
             says("Put the rack in slot 6 and make 2x and 5x dilutions.", proposes(rack, factors)),
             says("Also print 3 drops per spot.", proposes(change("print.droplets_per_spot", 3, "3 drops per spot"))),
             says("The vial rack.", proposes(change("deck.tuberack.slot", 6, "The vial rack"))), "yes")
    assert "Which do you mean: the P20 tip rack or the vial rack?" in c.out(1) and not c.proposal_paths(1)
    # the reply did not say which rack: the same question again, with the new change kept
    assert "Which do you mean: the P20 tip rack or the vial rack?" in c.out(2) and not c.proposal_paths(2)
    assert "Drops per paper position: 3" in c.out(2) and "Dilution factors: 2× | 5×" in c.out(2)
    assert "If you meant a column" not in c.out(1)            # "the rack" names labware: the question is which one
    config = c.config
    assert config["deck"]["tuberack"]["slot"] == 6 and config["dilution"]["factors"] == [2, 5]
    assert config["print"]["droplets_per_spot"] == 3 and config["deck"]["tiprack"]["slot"] == 9


def test_g_a_side_question_is_answered_and_the_question_keeps_waiting(tmp_path):
    c = talk(tmp_path, says(FOUR_CHANGES, WHICH_PLATE),
             says("What is a replicate?", routed("experiment_question", answer="A side-by-side repeat column.")),
             says("The 96-well plate.", proposes(PLATE_1)))
    assert "A side-by-side repeat column." in c.out(2) and "Still waiting for your answer" in c.out(2)
    assert c.record(2)["clarifying_after"] and not c.proposal_paths(2)
    assert_four_changes(c.pending)


def test_h_one_rejected_change_does_not_discard_the_accepted_ones(tmp_path):
    changes = [change("deck.plate.slot", 2, "plate to slot 2"), change("dilution.factors", [2, 5], "2x and 5x"),
               change("print.droplets_per_spot", 3, "3 drops"), change("print.droplet_volume_ul", "25 uL", "25 uL")]
    c = talk(tmp_path,
             says("Move the plate to slot 2, make 2x and 5x dilutions, 3 drops of 25 uL each.", proposes(*changes)),
             says("10 uL then.", proposes(change("print.droplet_volume_ul", "10 uL", "10 uL"))), "yes")
    assert not c.proposal_paths(1) and "What drop volume do you want instead?" in c.out(1)
    assert "I kept the rest of your request" in c.out(1)
    config = c.config
    assert config["deck"]["plate"]["slot"] == 2 and config["dilution"]["factors"] == [2, 5]
    assert config["print"]["droplets_per_spot"] == 3 and config["print"]["droplet_volume_ul"] == 10.0


def test_h_an_invented_value_is_asked_about_and_the_rest_is_kept(tmp_path):
    c = talk(tmp_path,
             says("Put the dilution plate in 8, move the vial rack out of the way, and use 2 drops.",
                  proposes(change("deck.plate.slot", 8, "dilution plate in 8"),
                           change("deck.tuberack.slot", 10, "move the vial rack out of the way"),
                           change("print.droplets_per_spot", 2, "2 drops"))),
             says("Slot 3.", proposes(change("deck.tuberack.slot", 3, "Slot 3"))))
    assert "Where should the Vial rack go?" in c.out(1) and not c.proposal_paths(1)
    proposal = c.pending
    assert after(proposal, "deck.tuberack.slot") == 3 and after(proposal, "deck.plate.slot") == 8
    assert after(proposal, "print.droplets_per_spot") == 2


def test_a_correction_that_replaces_the_question_leaves_that_part_out_and_says_so(tmp_path):
    c = talk(tmp_path,
             says("Put the dilutions in slot 3.", proposes(change("deck.plate.slot", 3, "slot 3"))),
             says("Sorry, I meant plate column 3.", proposes(change("dilution.plate_column", "3", "plate column 3"))))
    assert "Which labware should go to slot 3?" in c.out(1)
    assert c.pending is not None and after(c.pending, "deck.plate.slot") == 4
    assert str(after(c.pending, "dilution.plate_column")) == "3"
    assert "your answer replaced it" in c.out(2)


def test_cancel_during_a_question_drops_the_whole_request(tmp_path):
    c = talk(tmp_path, says(FOUR_CHANGES, WHICH_PLATE), "Never mind, cancel that.")
    assert "I dropped that request. Nothing was changed." in c.out(2)
    assert c.session.clarifying is None and c.pending is None and not c.proposal_paths(2)
    assert not c.router_saw(2) and c.session.state.revision == 0


def test_run_while_a_request_waits_for_an_answer_does_not_run_the_old_plan(tmp_path):
    c = talk(tmp_path, says(FOUR_CHANGES, WHICH_PLATE), "run")
    assert "Not running" in c.out(2) and not c.events(2, "run") and c.session.clarifying is not None


# ════════════════════════════════════════════════════════════════════════════════
# PROBLEM 2 - grounding
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text, evidence", [
    ("Move the plate to slot 5.", "plate to slot 5"),
    ("Put the plate at 5.", "plate at 5"),
    ("Plate -> 5.", "Plate -> 5"),
    ("put the plate at five", "plate at five"),
    ("Put the microplate in the fifth deck position.", "microplate in the fifth deck position"),
    ("Move the 96-well plate to position five.", "96-well plate to position five"),
    ("Put the 96-well plate in the fifth deck position.", "96-well plate in the fifth deck position"),
    ("Move the microplate to position five.", "microplate to position five"),
    ("plate 5", "plate 5"),
])
def test_every_clear_wording_of_a_move_is_the_same_proposal(tmp_path, text, evidence):
    c = talk(tmp_path, says(text, proposes(change("deck.plate.slot", 5, evidence))), config=FREE_5)
    assert c.proposal_paths(1) == [["deck.plate.slot"]]
    assert [event["unverified"] for event in c.events(1, "proposal")] == [[]]        # accepted, not flagged
    assert after(c.pending, "deck.plate.slot") == 5 and not c.events(1, "clarification")


@pytest.mark.parametrize("text", ["Move the plate to slot 5.", "Put the plate at five."])
def test_a_clear_wording_into_an_occupied_slot_is_still_a_collision(tmp_path, text):
    # the default deck has the paper in slot 5: flexible wording, strict physics
    c = talk(tmp_path, says(text, proposes(change("deck.plate.slot", 5, text.rstrip(".")))))
    assert not c.proposal_paths(1) and "Slot 5 is currently occupied by the Paper print plate." in c.out(1)
    assert "Where should the Paper print plate go instead?" in c.out(1) and c.config["deck"]["plate"]["slot"] == 4


def test_a_collision_answer_keeps_the_move_and_the_rest(tmp_path):
    c = talk(tmp_path,
             says("Move the plate to slot 5 and use 3 drops per spot.",
                  proposes(change("deck.plate.slot", 5, "plate to slot 5"), change("print.droplets_per_spot", 3, "3 drops"))),
             says("Put the paper in slot 8.", proposes(change("deck.paper.slot", 8, "paper in slot 8"))), "yes")
    config = c.config
    assert config["deck"]["plate"]["slot"] == 5 and config["deck"]["paper"]["slot"] == 8
    assert config["print"]["droplets_per_spot"] == 3


@pytest.mark.parametrize("text, path, value, evidence", [
    ("Move the vial rack to slot 6.", "deck.tuberack.slot", 6, "vial rack to slot 6"),
    ("Move the tip rack to slot 6.", "deck.tiprack.slot", 6, "tip rack to slot 6"),
    ("Move the paper to slot 8.", "deck.paper.slot", 8, "paper to slot 8"),
    ("Move the paper print plate to slot 8.", "deck.paper.slot", 8, "paper print plate to slot 8"),
    ("Put the microplate in 6.", "deck.plate.slot", 6, "microplate in 6"),
    ("Put the box of tips in slot 6.", "deck.tiprack.slot", 6, "box of tips in slot 6"),
    ("Use vial B1 for the dye.", "materials.sample.vial", "B1", "vial B1"),
])
def test_named_labware_is_accepted_without_a_question(tmp_path, text, path, value, evidence):
    c = talk(tmp_path, says(text, proposes(change(path, value, evidence))))
    assert c.proposal_paths(1) == [[path]] and not c.events(1, "clarification")


@pytest.mark.parametrize("text, path, question", [
    ("Put the rack in slot 6.", "deck.tuberack.slot", "Which do you mean: the P20 tip rack or the vial rack?"),
    ("Move the sample holder to slot 6.", "deck.plate.slot", "Which do you mean: the 96-well dilution plate or the vial"),
    ("Put it at 5.", "deck.plate.slot", "Which labware should go to slot 5?"),
])
def test_a_genuinely_ambiguous_reference_is_asked_about_never_guessed(tmp_path, text, path, question):
    c = talk(tmp_path, says(text, proposes(change(path, int(text.split()[-1].strip(".")), text.rstrip(".")))))
    assert question in c.out(1) and not c.proposal_paths(1) and c.session.clarifying is not None
    assert c.session.clarifying.unresolved == (path,)


def test_it_resolves_to_the_labware_just_discussed(tmp_path):
    c = talk(tmp_path, says("Move the plate to slot 6.", proposes(change("deck.plate.slot", 6, "plate to slot 6"))),
             says("Put it in slot 8 instead.", proposes(change("deck.plate.slot", 8, "Put it in slot 8"))))
    assert c.proposal_paths(2) == [["deck.plate.slot"]] and not c.events(2, "clarification")
    assert after(c.pending, "deck.plate.slot") == 8


def test_it_after_two_labware_were_discussed_is_asked_about(tmp_path):
    c = talk(tmp_path,
             says("Move the plate to slot 6 and the paper to slot 8.",
                  proposes(change("deck.plate.slot", 6, "plate to slot 6"), change("deck.paper.slot", 8, "paper to slot 8"))),
             says("Put it in slot 2 instead.", proposes(change("deck.plate.slot", 2, "Put it in slot 2"))))
    assert "Which labware should go to slot 2?" in c.out(2) and not c.proposal_paths(2)


def test_swapping_names_each_rack_by_where_it_goes(tmp_path):
    c = talk(tmp_path, says("Swap the plate and the rack.",
                            proposes(change("deck.plate.slot", 7, "plate"), change("deck.tuberack.slot", 4, "rack"))))
    assert c.proposal_paths(1) == [["deck.plate.slot", "deck.tuberack.slot"]]


@pytest.mark.parametrize("text", ["Start the dilutions at row B.", "Start the dilutions at the second row.",
                                  "Begin the series on the 2nd row."])
def test_a_starting_row_in_any_wording(tmp_path, text):
    c = talk(tmp_path, says(text, proposes(change("dilution.factors", [2, 4], "dilutions"),
                                           change("dilution.start_row", "B", "row"))))
    assert c.proposal_paths(1) and after(c.pending, "dilution.start_row") == "B"


def test_a_count_is_not_a_starting_row(tmp_path):
    # "row" is mentioned, but the only number is a drop count: the model's row B is never taken from it
    c = talk(tmp_path, says("Start the dilutions at a later row and use 2 drops.",
                            proposes(change("print.droplets_per_spot", 2, "2 drops"),
                                     change("dilution.factors", [2, 4], "dilutions"),
                                     change("dilution.start_row", "B", "a later row"))))
    assert not c.proposal_paths(1) and "Which plate row (A-H) should the series start at?" in c.out(1)
    assert "Drops per paper position: 2" in c.out(1)                    # the drops wait for the answer


# ── found by the 2026-09-26 re-run of the 85-test Gemini harness (runs/llm_validation/20260926_182318) ──────────

def test_d03_a_misspelled_request_for_the_sample_rack_needs_no_question(tmp_path):
    c = talk(tmp_path, says("move teh sample rack to locaiton 6",
                            proposes(change("deck.tuberack.slot", 6, "move teh sample rack to locaiton 6"))))
    assert c.proposal_paths(1) == [["deck.tuberack.slot"]] and not c.events(1, "clarification")
    assert [event["unverified"] for event in c.events(1, "proposal")] == [[]]


def test_f06_the_labware_to_keep_is_not_the_other_sample_holder(tmp_path):
    c = talk(tmp_path, says("Wherever the well plate is, leave it there. Move the other sample holder to ten.",
                            proposes(change("deck.tuberack.slot", 10, "Move the other sample holder to ten."))))
    assert c.proposal_paths(1) == [["deck.tuberack.slot"]] and not c.events(1, "clarification")


def test_g08_where_my_samples_are_is_asked_about(tmp_path):
    # both the vial rack (the dye stock) and the plate (the dilutions) hold "my samples"
    c = talk(tmp_path, says("Where my samples are, move that to slot 6.",
                            proposes(change("deck.plate.slot", 6, "move that to slot 6"))))
    assert not c.proposal_paths(1)
    assert "Which do you mean: the 96-well dilution plate or the vial rack?" in c.out(1)


def test_i07_everything_to_one_slot_is_a_collision_not_a_question_about_which_labware(tmp_path):
    moves = [change(f"deck.{role}.slot", 1, "Move everything to slot 1.") for role in ("plate", "paper", "tuberack")]
    c = talk(tmp_path, says("Move everything to slot 1.", proposes(*moves)))
    assert not c.proposal_paths(1) and "Which labware should go to slot 1?" not in c.out(1)
    assert "Cannot apply that deck change" in c.out(1) and c.session.state.revision == 0


def test_f03_the_plan_says_which_column_and_a_dropped_column_is_not_a_second_start(tmp_path):
    printing_3 = deepcopy(DEFAULT)
    printing_3["print"]["paper_start_column"] = 3
    c = talk(tmp_path, says("I don't want column 3 anymore; use column 8.",
                            proposes(change("print.paper_start_column", 8, "use column 8"))), config=printing_3)
    assert c.proposal_paths(1) == [["print.paper_start_column"]] and not c.events(1, "clarification")
    assert after(c.pending, "print.paper_start_column") == 8


def test_e05_several_plate_columns_are_asked_about_never_reduced_to_one(tmp_path):
    c = talk(tmp_path, says("Use plate columns 1, 3 and 5 and print them onto paper columns 1, 3 and 5.",
                            proposes(change("dilution.plate_column", "1", "Use plate columns 1, 3 and 5"),
                                     change("paper_columns", [1, 3, 5], "paper columns 1, 3 and 5"))))
    assert not c.proposal_paths(1) and "Which plate column should this run use?" in c.out(1)
    assert "Paper columns: 1, 3, 5" in c.out(1)                          # the destinations wait for the answer


def test_e11_a_step_switched_off_that_the_words_do_not_ask_for_is_flagged(tmp_path):
    c = talk(tmp_path, says("Print the first three rows onto paper columns 10 to 12.",
                            proposes(change("dilution.enabled", False, "Print the first three rows onto paper columns 10 to 12."),
                                     change("rows", ["A", "B", "C"], "first three rows"),
                                     change("paper_columns", [10, 11, 12], "paper columns 10 to 12"))))
    [flagged] = [event["unverified"] for event in c.events(1, "proposal")]
    assert "dilution.enabled" in flagged


@pytest.mark.parametrize("text", ["My samples are in column 6 of the plate. Put them on paper column 9.",
                                  "Take the samples in plate column 2 and spot them on paper column 3.",
                                  "I don't want dilutions; print from A11."])
def test_existing_samples_switch_the_dilutions_off_without_a_flag(tmp_path, text):
    c = talk(tmp_path, says(text, proposes(change("dilution.enabled", False, text.split(".")[0]))))
    assert [event["unverified"] for event in c.events(1, "proposal")] == [[]]


def test_h01_the_models_own_keep_list_never_drops_a_move_the_scientist_asked_for(tmp_path):
    # Gemini's first reading marked every labware it was not moving as "kept"; the answer then moved the tip rack
    guessed = routed("experiment_change", change("deck.paper.slot", 6, "Put it in slot 6."),
                     preserve=["deck.plate.slot", "deck.tuberack.slot", "deck.tiprack.slot"])
    c = talk(tmp_path, says("Put it in slot 6.", guessed),
             says("move the tip rack to slot 6", proposes(change("deck.tiprack.slot", 6, "move the tip rack to slot 6"))))
    assert "Which labware should go to slot 6?" in c.out(1)
    assert c.proposal_paths(2) == [["deck.tiprack.slot"]] and "nothing was changed" not in c.out(2)


def test_h02_a_sample_holder_is_asked_about(tmp_path):
    c = talk(tmp_path, says("Move the sample holder to 8.", proposes(change("deck.tuberack.slot", 8, "Move the sample holder to 8."))))
    assert not c.proposal_paths(1)
    assert "Which do you mean: the 96-well dilution plate or the vial rack?" in c.out(1)


def test_f05_a_move_of_kept_labware_whose_words_name_other_labware_is_asked_about(tmp_path):
    c = talk(tmp_path,
             says("The plate can stay where it is but move the thing with the stock samples to deck position eight.",
                  proposes(change("deck.plate.slot", 8, "move the thing with the stock samples to deck position eight"))),
             says("the vial rack", proposes(change("deck.tuberack.slot", 8, "the vial rack"))))
    assert not c.proposal_paths(1) and "Which labware should go to slot 8?" in c.out(1)
    assert c.proposal_paths(2) == [["deck.tuberack.slot"]]


def test_e06_the_wells_of_a_named_plate_column_are_named_sources(tmp_path):
    column_2 = [{"source": f"{row}2", "columns": [3, 4, 5]} for row in "ABCDEFGH"]
    c = talk(tmp_path, says("Take the samples in plate column 2 and spot them on paper columns 3, 4 and 5.",
                            proposes(change("dilution.enabled", False, "Take the samples in plate column 2"),
                                     change("print.source_map", column_2, "the samples in plate column 2"))))
    assert c.proposal_paths(1) and not c.events(1, "clarification")
    plan = build_plan(c.pending.after)
    assert {item.well for item in plan.print_sources} == {f"{row}2" for row in "ABCDEFGH"}
    assert sorted({int(position[1:]) for position in plan.print_positions}) == [3, 4, 5]


def test_f08_already_made_dilutions_that_differ_from_the_record_are_asked_about(tmp_path):
    # an earlier print-only run recorded (assumed) the default series in A11-H11; the plan now has other factors
    c = talk(tmp_path,
             says("Don't make any dilutions, just print.", proposes(change("dilution.enabled", False, "Don't make any dilutions"))),
             "yes",
             says("Make dilutions of 2x, 5x and 10x.", proposes(change("dilution.enabled", True, "Make dilutions"),
                                                                 change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"))),
             "yes",
             says("Everything is already mixed so skip that part and just print.",
                  proposes(change("dilution.enabled", False, "Everything is already mixed so skip that part"))),
             "yes", "yes")
    assert "does not match the dilutions recorded as prepared" in c.out(5)
    assert "Do plate wells A11, B11, C11 now hold this plan's dilutions (2×, 5×, 10×)?" in c.out(5)
    assert c.proposal_paths(6) and c.config["dilution"]["enabled"] is False
    assert c.session.state.physical["dilutions_prepared"]["factors"] == [2, 5, 10]


def test_a_count_never_verifies_a_row_selection(tmp_path):
    c = talk(tmp_path, says("Use 2 drops.", proposes(change("print.droplets_per_spot", 2, "2 drops"),
                                                     change("rows", ["B"], "2"))))
    # 2026-09-27: the question names PLATE rows - where the dilutions are, never where they print (print.paper_rows)
    assert not c.proposal_paths(1) and "Which plate rows should this run use?" in c.out(1)


def test_an_impossible_slot_is_still_refused(tmp_path):
    c = talk(tmp_path, says("Put the plate in slot 15.", proposes(change("deck.plate.slot", 15, "plate in slot 15"))))
    assert not c.proposal_paths(1) and "I did not change anything" in c.out(1)
    assert c.config["deck"]["plate"]["slot"] == 4


def test_a_collision_is_still_refused_whatever_the_wording(tmp_path):
    c = talk(tmp_path, says("Put the plate at 7.", proposes(change("deck.plate.slot", 7, "plate at 7"))))
    assert not c.proposal_paths(1) and "Slot 7" in c.out(1) and c.config["deck"]["plate"]["slot"] == 4


def test_an_impossible_well_is_still_refused(tmp_path):
    c = talk(tmp_path, says("Print everything from Z11.",
                            proposes(change("print_map", [{"source": "Z11", "positions": "all"}], "from Z11"))))
    assert not c.proposal_paths(1) and "Z11" in c.out(1) and "rows A-H" in c.out(1)


def test_a_bare_count_never_supports_a_slot(tmp_path):
    # "3 drops" says nothing about where the paper goes: the invented slot is asked about, the drops are kept
    c = talk(tmp_path, says("Use 3 drops.", proposes(change("print.droplets_per_spot", 3, "3 drops"),
                                                     change("deck.paper.slot", 3, "3"))))
    assert not c.proposal_paths(1) or [event["unverified"] for event in c.events(1, "proposal")] != [[]]
    assert c.config["deck"]["paper"]["slot"] == 5


def test_a_change_to_something_the_scientist_said_to_keep_is_dropped_with_a_note(tmp_path):
    # CONTRADICTED: the model moved the plate anyway; the plate stays, the rest goes ahead
    c = talk(tmp_path, says("Don't move the plate, just print 3 drops per spot.",
                            proposes(change("deck.plate.slot", 6, "plate"), change("print.droplets_per_spot", 3, "3 drops"))))
    assert c.proposal_paths(1) == [["print.droplets_per_spot"]]
    assert "You asked to keep the 96-well dilution plate location as it is" in c.out(1)


# ════════════════════════════════════════════════════════════════════════════════
# PROBLEM 3 - negation, skip, already done, cancel, no change
# ════════════════════════════════════════════════════════════════════════════════

def test_preserve_and_change_in_one_message(tmp_path):
    c = talk(tmp_path, says("Don't change the paper location, only change drops to 4.",
                            proposes(change("print.droplets_per_spot", 4, "drops to 4"), preserve=["deck.paper.slot"])),
             "yes")
    human = c.router_saw(1)[0]
    assert "SCIENTIST'S MESSAGE:\nDon't change the paper location, only change drops to 4." in human
    assert "The words ask to keep: deck.paper.slot" in human
    assert c.config["print"]["droplets_per_spot"] == 4 and c.config["deck"]["paper"]["slot"] == 5


def test_preserve_and_change_without_a_comma_before_the_change(tmp_path):
    # the whole message is one negated clause for the pre-router; it still reaches the model and the drops change
    c = talk(tmp_path, says("don't move the plate, drops to 4 please",
                            proposes(change("print.droplets_per_spot", 4, "drops to 4"), preserve=["deck.plate.slot"])))
    assert c.router_saw(1) and "Negated clause(s)" in c.router_saw(1)[0]
    assert c.proposal_paths(1) == [["print.droplets_per_spot"]]


def test_skip_a_step(tmp_path):
    c = talk(tmp_path, says("Don't remake the dilutions, just print.",
                            proposes(change("dilution.enabled", False, "Don't remake the dilutions"))), "yes")
    assert c.config["dilution"]["enabled"] is False and c.config["print"]["enabled"] is True
    plan = build_plan(c.config)
    assert plan.do_print and not plan.do_dilution


def test_already_done_is_not_mixing_and_not_cancel(tmp_path):
    c = talk(tmp_path, says("Everything is already mixed, just print.",
                            proposes(change("dilution.enabled", False, "Everything is already mixed"))), "yes")
    assert c.record(1)["classification"] not in {"cancel", "no_change"}
    assert c.config["dilution"]["enabled"] is False and c.config["print"]["enabled"] is True
    assert c.config["mixing"] == DEFAULT["mixing"]            # mixing before printing is unchanged


def test_no_dilutions_print_one_well_on_one_paper_column(tmp_path):
    c = talk(tmp_path, says("I don't want dilutions; use A11 and print column 8.",
                            proposes(change("dilution.enabled", False, "I don't want dilutions"),
                                     change("print_map", [{"source": "A11", "columns": [8]}], "use A11 and print column 8"))),
             "yes")
    config = c.config
    assert config["dilution"]["enabled"] is False
    plan = build_plan(config)
    assert {item.well for item in plan.print_sources} == {"A11"}
    assert {position[1:] for position in plan.print_positions} == {"8"}


@pytest.mark.parametrize("text", ["Never mind, cancel that.", "Discard that proposal.", "cancel that, never mind",
                                  "No, scratch that."])
def test_cancel_clears_the_waiting_proposal_and_creates_nothing(tmp_path, text):
    c = talk(tmp_path, says("Move the plate to slot 6.", proposes(change("deck.plate.slot", 6, "plate to slot 6"))), text,
             "yes")
    assert c.record(2)["classification"] == "cancel"
    assert c.pending is None and "Discarded proposal #1" in c.out(2)
    assert not c.proposal_paths(2) and not c.events(2, "clarification") and not c.router_saw(2)
    assert "There is no proposed change waiting for approval" in c.out(3) and c.session.state.revision == 0


@pytest.mark.parametrize("text", ["Leave everything as it is.", "Don't change anything.",
                                  "Leave everything exactly as it is."])
def test_no_change_is_its_own_kind(tmp_path, text):
    c = talk(tmp_path, text, says("Move the plate to slot 6.", proposes(change("deck.plate.slot", 6, "plate to slot 6"))),
             text)
    assert c.record(1)["classification"] == "no_change" and "nothing was changed" in c.out(1)
    assert not c.router_saw(1) and not c.router_saw(3)
    assert c.pending is None and "Discarded proposal #1" in c.out(3) and c.session.state.revision == 0


def test_printing_some_of_the_dilutions_reaches_the_model_and_is_grounded_by_the_factors(tmp_path):
    # was refused before the model ("cannot print only one or some of them"); rows selections made it possible
    planned = deepcopy(DEFAULT)
    planned["dilution"]["factors"] = [5, 10, 20]
    c = talk(tmp_path, says("Print the 5x and 10x dilutions onto paper column 2.",
                            proposes(change("rows", ["A", "B"], "the 5x and 10x dilutions"),
                                     change("paper_columns", [2], "paper column 2"))), config=planned)
    assert c.router_saw(1) and not c.events(1, "clarification")
    plan = build_plan(c.pending.after)
    assert [well.factor for well in plan.wells] == [5, 10] and after(c.pending, "print.paper_start_column") == 2


def test_a_pure_negation_is_answered_without_the_model(tmp_path):
    c = talk(tmp_path, "Don't move the paper print plate.")
    assert "Paper print plate location stays Slot 5" in c.out(1) and not c.router_saw(1)


# ════════════════════════════════════════════════════════════════════════════════
# MULTI-CHANGE - every independent field survives request -> router -> question -> proposal -> apply
# ════════════════════════════════════════════════════════════════════════════════

SIX = ("Don't remake the dilutions, move the plate to slot 6, keep the paper where it is, use A11 for all prints, "
       "print paper columns 2, 4, and 6, and use four drops per spot.")
SIX_CHANGES = (change("dilution.enabled", False, "Don't remake the dilutions"),
               change("deck.plate.slot", 6, "move the plate to slot 6"),
               change("print_map", [{"source": "A11", "columns": [2, 4, 6]}],
                      "use A11 for all prints, print paper columns 2, 4, and 6"),
               change("print.droplets_per_spot", 4, "four drops per spot"))


def assert_six(config):
    assert config["dilution"]["enabled"] is False                       # prepare dilutions: no
    assert config["deck"]["plate"]["slot"] == 6                          # plate slot 6
    assert config["deck"]["paper"]["slot"] == 5                          # paper: kept where it is
    plan = build_plan(config)
    assert {item.well for item in plan.print_sources} == {"A11"}         # source A11
    assert sorted({int(position[1:]) for position in plan.print_positions}) == [2, 4, 6]   # paper columns 2, 4, 6
    assert config["print"]["droplets_per_spot"] == 4                     # four drops per spot


def test_six_field_request_in_one_message(tmp_path):
    c = talk(tmp_path, says(SIX, proposes(*SIX_CHANGES, preserve=["deck.paper.slot"])), "yes")
    assert_six(c.config)


def test_a_print_map_written_to_the_field_is_read_as_the_selection(tmp_path):
    # the live Gemini reply to SIX (2026-09-26 GUI check) put the selection value on the print.source_map field
    reading = [dict(item) for item in SIX_CHANGES]
    reading[2] = change("print.source_map", [{"source": "A11", "columns": [2, 4, 6]}], "use A11 for all prints")
    c = talk(tmp_path, says(SIX, proposes(*reading)), "yes")
    assert_six(c.config)


def test_six_field_request_with_a_question_in_the_middle(tmp_path):
    # the model asks how many drops ("four" was misheard as "a few"); every other field waits in the pending intent
    few = SIX.replace("four drops", "a few drops")
    c = talk(tmp_path,
             says(few, asks("How many drops per spot?", *SIX_CHANGES[:3], unresolved=["print.droplets_per_spot"])),
             says("four", proposes(change("print.droplets_per_spot", 4, "four"))), "yes")
    assert "I kept the rest of your request" in c.out(1)
    assert_six(c.config)


def test_six_field_request_whose_model_reading_also_moves_the_paper(tmp_path):
    # the model contradicts "keep the paper where it is": that one change is dropped, the other five go ahead
    c = talk(tmp_path, says(SIX, proposes(*SIX_CHANGES, change("deck.paper.slot", 8, "paper"))), "yes")
    assert_six(c.config)


FIVE = ("Move the vial rack to slot 2, make 2x, 5x and 10x dilutions of {volume} uL each, and print 3 drops of each in "
        "paper columns 3 and 4.")


def five_changes(volume: int) -> tuple[dict[str, Any], ...]:
    return (change("deck.tuberack.slot", 2, "vial rack to slot 2"), change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"),
            change("dilution.total_volume_ul", f"{volume} uL", f"{volume} uL each"),
            change("print.droplets_per_spot", 3, "3 drops"), change("paper_columns", [3, 4], "paper columns 3 and 4"))


def assert_five(config, volume: float):
    assert config["deck"]["tuberack"]["slot"] == 2 and config["dilution"]["factors"] == [2, 5, 10]
    assert config["dilution"]["total_volume_ul"] == volume and config["print"]["droplets_per_spot"] == 3
    assert config["print"]["paper_start_column"] == 3 and config["print"]["replicates"] == 2


def test_five_field_request_in_one_message(tmp_path):
    c = talk(tmp_path, says(FIVE.format(volume=200), proposes(*five_changes(200))), "yes")
    assert_five(c.config, 200.0)


def test_five_changes_that_cannot_run_together_keep_all_and_ask_which_gives(tmp_path):
    # 100 uL wells, 3 drops, two paper columns: a well would run dry before mixing. Any one of three changes causes it
    # (the validator decides which, by trying without each); all five are kept and one question asks which to change.
    c = talk(tmp_path, says(FIVE.format(volume=100), proposes(*five_changes(100))),
             says("Make it 200 uL each then.", proposes(change("dilution.total_volume_ul", "200 uL", "200 uL each"))),
             "yes")
    assert not c.proposal_paths(1) and "the tip would draw air" in c.out(1)          # the validator's own reason
    assert "Which should change so that it fits - the final volume per dilution" in c.out(1)
    assert "I kept the rest of your request" in c.out(1) and "Final volume per dilution: 100 µL" in c.out(1)
    assert_five(c.config, 200.0)


# ════════════════════════════════════════════════════════════════════════════════
# SOURCE MAPPING through the session (the protocol side is tests/test_ai_dye_demo_print_map.py)
# ════════════════════════════════════════════════════════════════════════════════

def test_the_reported_sentence_prints_a11_on_every_position(tmp_path):
    c = talk(tmp_path, says("i only have sample in 96 well plate column 11 row 1 use this for all prints",
                            proposes(change("dilution.enabled", False, "i only have sample in 96 well plate column 11 row 1"),
                                     change("print_map", [{"source": "A11", "positions": "all"}], "use this for all prints"))),
             "yes")
    plan = build_plan(c.config)
    assert not plan.do_dilution and {item.well for item in plan.print_sources} == {"A11"}
    assert len(plan.print_positions) == 8


def test_two_wells_and_a_total_ask_once_and_split_evenly(tmp_path):
    c = talk(tmp_path, says("Print 10 times from A11 and B11.",
                            proposes(change("dilution.enabled", False, "from A11 and B11"),
                                     change("print_map", {"sources": ["A11", "B11"], "total": 10}, "10 times from A11 and B11"))),
             "yes", "yes")
    assert "5" in c.out(1) and not c.proposal_paths(1)
    counts = {item.well: len(item.positions) for item in build_plan(c.config).print_sources}
    assert counts == {"A11": 5, "B11": 5}


def test_an_explicit_split_is_proposed_directly(tmp_path):
    c = talk(tmp_path, says("Print 6 from A11 and 4 from B11.",
                            proposes(change("dilution.enabled", False, "Print 6 from A11 and 4 from B11"),
                                     change("print_map", [{"source": "A11", "count": 6}, {"source": "B11", "count": 4}],
                                            "6 from A11 and 4 from B11"))), "yes")
    counts = {item.well: len(item.positions) for item in build_plan(c.config).print_sources}
    assert counts == {"A11": 6, "B11": 4}


def test_a_stated_source_is_not_challenged_again(tmp_path):
    c = talk(tmp_path, says("Print everything from A11.",
                            proposes(change("dilution.enabled", False, "Print everything from A11"),
                                     change("print_map", [{"source": "A11", "positions": "all"}], "everything from A11"))),
             "yes", says("Move the paper to slot 8.", proposes(change("deck.paper.slot", 8, "paper to slot 8"))), "yes")
    assert not c.events(3, "clarification") and c.config["deck"]["paper"]["slot"] == 8
    assert "A11" in c.session.state.physical["sources_present"] and c.session.state.run_blockers() == []


def test_a_single_source_still_cannot_run_dry(tmp_path):
    # 8 positions x 2 drops from one assumed-volume well would leave too little to mix: physics stays strict
    c = talk(tmp_path, says("Print everything from A11.",
                            proposes(change("dilution.enabled", False, "Print everything from A11"),
                                     change("print_map", [{"source": "A11", "positions": "all"}], "everything from A11"))),
             "yes", says("Use 2 drops per spot.", proposes(change("print.droplets_per_spot", 2, "2 drops per spot"))))
    assert not c.proposal_paths(3) and "A11" in c.out(3) and c.config["print"]["droplets_per_spot"] == 1
