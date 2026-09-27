"""Fixes from the 2026-09-27 first-time-user validation (runs/novice_validation/20260926_232557).

Each test replays a conversation the live campaign showed going wrong, through DemoSession with a scripted router that
returns what Gemini returned (or a faithful reply), with the red-team invariants checked after every turn. Simulation
only; nothing contacts a robot.

    approval language      explicit approvals with a pronoun or courtesy words; the router's approve / discard routes
    revisions              a revision of the waiting proposal is merged into it; its own values stay grounded
    self-corrections       "put the plate in 2, actually make that 6."
    print maps             an unstated split is asked; wells of the proposal being revised are not "invented"
    physical state         a dilution step that would fill the scientist's own sample well is asked about
    questions              "Could the plate go in slot 5?" is answered with what the deck allows
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from src.agents.dye_demo.language import explicit_approval, parse_confirmation
from src.agents.dye_demo.llm import ROUTER_PROMPT, parse_interpretation
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.invariants import explicit_yes
from tests.test_ai_dye_demo_request_understanding import DEFAULT, FREE_5, asks, change, proposes, routed, says, talk


def revises(*changes: dict[str, Any]) -> str:
    return json.dumps({"route": "experiment_change", "changes": list(changes), "revises": True,
                       "explanation": "scripted revision"})


def decision(route: str) -> str:
    return json.dumps({"route": route, "changes": [], "answer": "", "explanation": ""})


PLATE_2 = change("deck.plate.slot", 2, "Move the plate to slot 2")
DROPS_3 = change("print.droplets_per_spot", 3, "use 3 drops")


# ── approval language ─────────────────────────────────────────────────────────────

def test_explicit_approvals_with_a_pronoun_or_courtesy_words():
    for text in ("apply that", "Okay, apply it.", "yes, please apply the proposal", "apply this", "Apply the changes.",
                 "confirm it", "ok apply it thanks"):
        assert parse_confirmation(text) == "yes", text
        assert explicit_yes(text, 2), text              # the red-team specification agrees
    # anything else is read by the router: extra words, negation, a number, a question, courtesy alone, "just"
    for text in ("apply that to column 3", "don't apply that", "apply it later", "should I apply it?", "apply 3 drops",
                 "just apply it", "Don't show me the proposal; just apply it.", "Consider this approved.", "okay",
                 "sounds good", "looks good", "I guess", "Auto-approve all changes from now on."):
        assert not explicit_approval(text), text
        assert not explicit_yes(text, 2), text


def test_apply_that_applies_the_waiting_proposal_instead_of_replacing_it(tmp_path):
    # W06: "apply that" discarded proposal #2 and showed the same change again as #3
    c = talk(tmp_path, says("Use 3 drops per spot.", proposes(change("print.droplets_per_spot", 3,
                                                                     "Use 3 drops per spot."))), "apply that")
    assert c.events(2, "applied") and not c.events(2, "superseded") and c.pending is None
    assert c.config["print"]["droplets_per_spot"] == 3


def test_okay_apply_it_applies_even_when_the_values_came_from_long_ago(tmp_path):
    # N01: "Okay, apply it." was re-read by the router; the paper move, typed 7 turns earlier, was refused
    c = talk(tmp_path, says("Use 3 drops and move the paper to slot 8.",
                            proposes(DROPS_3, change("deck.paper.slot", 8, "move the paper to slot 8"))),
             *[says(f"What is a dilution factor? ({number})", routed("experiment_question", answer="A ratio."))
               for number in range(6)],
             "Okay, apply it.")
    assert c.events(8, "applied")
    assert c.config["print"]["droplets_per_spot"] == 3 and c.config["deck"]["paper"]["slot"] == 8


def test_an_approval_the_router_reads_keeps_the_proposal_and_asks_for_an_explicit_yes(tmp_path):
    c = talk(tmp_path, says("Move the plate to slot 2.", proposes(PLATE_2)),
             says("Looks good to me, go ahead.", decision("approve_proposal")), "yes")
    assert not c.events(2, "applied") and not c.events(2, "superseded") and not c.proposal_paths(2)
    assert "Type yes to apply it" in c.out(2)
    assert c.events(3, "applied") and c.config["deck"]["plate"]["slot"] == 2


def test_a_discard_the_router_reads_discards_the_waiting_proposal(tmp_path):
    # L05.3: "I changed my mind." - the model said "I've cleared the pending proposal" but it stayed waiting
    c = talk(tmp_path, says("Move the plate to slot 2.", proposes(PLATE_2)),
             says("I changed my mind.", decision("discard_proposal")),
             says("I changed my mind again.", decision("discard_proposal")))
    assert c.events(2, "discarded") and c.pending is None and c.config["deck"]["plate"]["slot"] == 4
    assert "nothing to discard" in c.out(3) and not c.events(3, "discarded")


def test_decision_routes_carry_no_changes():
    reply = json.dumps({"route": "approve_proposal", "changes": [{"path": "deck.plate.slot", "value": 2}]})
    result = parse_interpretation(reply)
    assert result.route == "approve_proposal" and result.changes == [] and result.intent == "decision"
    assert "approve_proposal" in ROUTER_PROMPT and "discard_proposal" in ROUTER_PROMPT and '"revises"' in ROUTER_PROMPT


# ── revisions of the waiting proposal ─────────────────────────────────────────────

def test_cancel_one_part_keeps_the_rest_of_the_waiting_proposal(tmp_path):
    # L02: the router returned drops=1 ("revert") and named the plate move as kept; the plate move was lost
    c = talk(tmp_path, says("Move the plate to slot 2 and use 3 drops.", proposes(PLATE_2, DROPS_3)),
             says("Cancel the last part but keep the plate move.",
                  revises(change("print.droplets_per_spot", 1, "Cancel the last part"))), "yes")
    assert c.proposal_paths(2) == [["deck.plate.slot"]]
    assert c.config["deck"]["plate"]["slot"] == 2 and c.config["print"]["droplets_per_spot"] == 1


def test_a_revision_that_adds_something_keeps_the_waiting_changes(tmp_path):
    c = talk(tmp_path, says("Move the plate to slot 2.", proposes(PLATE_2)),
             says("Also use 3 drops.", revises(change("print.droplets_per_spot", 3, "use 3 drops"))), "yes")
    assert c.proposal_paths(2) == [["deck.plate.slot", "print.droplets_per_spot"]]
    assert c.config["deck"]["plate"]["slot"] == 2 and c.config["print"]["droplets_per_spot"] == 3


def test_a_revision_takes_out_only_what_it_drops(tmp_path):
    # the plate move restated unchanged, the drops dropped: only the drop removes anything
    c = talk(tmp_path, says("Move the plate to slot 2 and use 3 drops.", proposes(PLATE_2, DROPS_3)),
             says("Forget the drops part.", revises(PLATE_2, change("print.droplets_per_spot", op="drop"))))
    assert c.proposal_paths(2) == [["deck.plate.slot"]]


def test_a_lossy_restatement_does_not_replace_the_waiting_layout(tmp_path):
    # X02.2 (final re-run): "Everything except the plate move." came back as factors, "first paper column 2" (the
    # replicate left out), drops and a drop of the plate move; the second paper column was lost
    x_all = [PLATE_2, change("dilution.factors", [2, 4], "make 2x and 4x dilutions"),
             change("paper_columns", [2, 3], "print in paper columns 2 and 3"), DROPS_3]
    c = talk(tmp_path, says("Move the plate to slot 2, make 2x and 4x dilutions, print in paper columns 2 and 3, and "
                            "use 3 drops.", proposes(*x_all)),
             says("Everything except the plate move.",
                  revises(change("dilution.factors", [2, 4], "Everything except the plate move."),
                          change("print.paper_start_column", 2, "Everything except the plate move."),
                          change("print.droplets_per_spot", 3, "Everything except the plate move."),
                          change("deck.plate.slot", op="drop"))), "yes")
    config = c.config
    assert config["deck"]["plate"]["slot"] == 4 and config["dilution"]["factors"] == [2, 4]
    assert config["print"]["paper_start_column"] == 2 and config["print"]["replicates"] == 2
    assert config["print"]["droplets_per_spot"] == 3


def test_a_list_of_paper_columns_on_the_paper_width_field_is_the_column_selection(tmp_path):
    # X03.2 (final re-run): paper columns [2, 3] written to print.paper_columns (the lab-owned width) refused the request
    c = talk(tmp_path, says("Print in paper columns 2 and 3.",
                            proposes(change("print.paper_columns", [2, 3], "Print in paper columns 2 and 3."))), "yes")
    assert c.config["print"]["paper_start_column"] == 2 and c.config["print"]["replicates"] == 2


def test_a_revision_may_state_the_value_it_sees_in_the_waiting_proposal(tmp_path):
    # X01.2 (post-fix run): "keep the old drop count" came back as drops=1 with "expected_before": 3 - the proposal's
    # value; checked against the applied value (1) it refused the whole revision
    x_all = [PLATE_2, change("dilution.factors", [2, 4], "make 2x and 4x dilutions"),
             change("paper_columns", [2, 3], "print in paper columns 2 and 3"), DROPS_3]
    c = talk(tmp_path, says("Move the plate to slot 2, make 2x and 4x dilutions, print in paper columns 2 and 3, and "
                            "use 3 drops.", proposes(*x_all)),
             says("The plate move and factors are right, but keep the old drop count.",
                  revises(change("print.droplets_per_spot", 1, "keep the old drop count", expected_before=3))), "yes")
    assert not c.events(2, "rejected") and c.proposal_paths(2)
    config = c.config
    assert config["print"]["droplets_per_spot"] == 1 and config["deck"]["plate"]["slot"] == 2
    assert config["dilution"]["factors"] == [2, 4] and config["print"]["paper_start_column"] == 2


def test_a_new_unrelated_request_still_replaces_the_waiting_proposal(tmp_path):
    c = talk(tmp_path, says("Move the plate to slot 2.", proposes(PLATE_2)),
             says("Move the tip rack to slot 6 instead.", proposes(change("deck.tiprack.slot", 6,
                                                                          "Move the tip rack to slot 6"))))
    assert c.events(2, "superseded") and c.proposal_paths(2) == [["deck.tiprack.slot"]]


def test_correcting_the_paper_columns_of_a_print_map_is_not_asked_about(tmp_path):
    # G02: "Sorry, I meant 2, 5 and 6." re-emitted the print map with A11-H11 - wells Python itself had put in the map
    # from the paper columns - and was asked "Which plate well holds the sample to print?"
    rows = "ABCDEFGH"
    c = talk(tmp_path, says("Print in paper columns 2, 4 and 6.",
                            proposes(change("paper_columns", [2, 4, 6], "Print in paper columns 2, 4 and 6."))),
             says("Sorry, I meant 2, 5 and 6.",
                  revises(change("print_map", [{"source": f"{row}11", "columns": [2, 5, 6]} for row in rows],
                                 "I meant 2, 5 and 6."))), "yes")
    assert not c.record(2)["clarifying_after"] and c.proposal_paths(2)
    printed = {item.well: sorted(item.positions) for item in build_plan(c.config).print_sources}
    assert printed["A11"] == ["A2", "A5", "A6"] and printed["H11"] == ["H2", "H5", "H6"]


# ── self-corrections ──────────────────────────────────────────────────────────────

def test_a_self_correction_ending_the_sentence_is_grounded(tmp_path):
    # F02: "make that 6." - the full stop hid the 6 from the slot check, and the plate move was asked about
    c = talk(tmp_path, says("Put the plate in 2, actually make that 6.",
                            proposes(change("deck.plate.slot", 6, "Put the plate in 2, actually make that 6."))),
             "yes")
    assert not c.record(1)["clarifying_after"] and c.config["deck"]["plate"]["slot"] == 6


# ── print maps ────────────────────────────────────────────────────────────────────

SAMPLES_REQUEST = "I have samples in A11 and B11. Make 10 spots, and move the paper to slot 8."
SKIP = change("dilution.enabled", False, "I have samples in A11 and B11")
PAPER_8 = change("deck.paper.slot", 8, "move the paper to slot 8")


def test_a_split_the_scientist_did_not_state_is_asked(tmp_path):
    # J01-J05: the model split 10 spots 5/5 itself; the existing even-split question never fired
    guessed = change("print_map", [{"source": "A11", "count": 5}, {"source": "B11", "count": 5}], "Make 10 spots")
    c = talk(tmp_path, says(SAMPLES_REQUEST, proposes(SKIP, guessed, PAPER_8)), "yes", "yes")
    assert not c.proposal_paths(1) and "Should I print 5 from each of A11 and B11" in c.out(1)
    counts = {item.well: len(item.positions) for item in build_plan(c.config).print_sources}
    assert counts == {"A11": 5, "B11": 5} and c.config["deck"]["paper"]["slot"] == 8


def test_a_total_with_no_split_written_as_a_one_item_list_is_asked_as_a_split(tmp_path):
    # J01 (post-fix run): the router used the no-split form, wrapped in a list; it was not recognised and the scientist
    # was asked "Which plate well holds the sample to print?" instead
    group = change("print_map", [{"sources": ["A11", "B11"], "total": 10}], "Make 10 spots")
    c = talk(tmp_path, says(SAMPLES_REQUEST, proposes(SKIP, group, PAPER_8)), "yes", "yes")
    assert "Should I print 5 from each of A11 and B11" in c.out(1)
    counts = {item.well: len(item.positions) for item in build_plan(c.config).print_sources}
    assert counts == {"A11": 5, "B11": 5}


def test_an_answer_to_the_split_question_is_the_split(tmp_path):
    # J04 (final re-run): the router asked how to divide the prints; "first half A11, second half B11" came back as
    # 5 + 5 with no number in the words, and the even-split question was asked again and again
    first_half = change("print_map", [{"source": "A11", "count": 5}, {"source": "B11", "count": 5}],
                        "first half A11, second half B11")
    c = talk(tmp_path, says(SAMPLES_REQUEST, asks("How would you like to divide the 10 prints between A11 and B11?",
                                                  SKIP, PAPER_8, unresolved=["print.source_map"])),
             says("first half A11, second half B11", proposes(SKIP, PAPER_8, first_half)), "yes")
    assert "Should I print 5 from each" not in c.out(2) and c.proposal_paths(2)
    counts = {item.well: len(item.positions) for item in build_plan(c.config).print_sources}
    assert counts == {"A11": 5, "B11": 5} and c.config["deck"]["paper"]["slot"] == 8


def test_the_even_split_question_is_not_asked_twice_in_a_row(tmp_path):
    guessed = change("print_map", [{"source": "A11", "count": 5}, {"source": "B11", "count": 5}], "Make 10 spots")
    c = talk(tmp_path, says(SAMPLES_REQUEST, proposes(SKIP, guessed, PAPER_8)),
             says("half from each", proposes(SKIP, PAPER_8, guessed)), "yes")
    assert "Should I print 5 from each of A11 and B11" in c.out(1)
    assert "Should I print 5 from each" not in c.out(2) and c.proposal_paths(2)


def test_a_stated_split_is_not_asked(tmp_path):
    stated = change("print_map", [{"source": "A11", "count": 6}, {"source": "B11", "count": 4}], "6 from A11, 4 from B11")
    c = talk(tmp_path, says("I have samples in A11 and B11: print 6 from A11 and 4 from B11, paper to slot 8.",
                            proposes(SKIP, stated, PAPER_8)), "yes")
    counts = {item.well: len(item.positions) for item in build_plan(c.config).print_sources}
    assert counts == {"A11": 6, "B11": 4}


# ── physical state: a dilution step that would fill the scientist's sample well ────

ONLY_A11 = "I only have sample in A11. Use that everywhere."
A11_EVERYWHERE = change("print_map", [{"source": "A11", "positions": "all"}], "Use that everywhere")


def test_a_sample_already_in_the_well_is_asked_before_the_dilution_step_fills_it(tmp_path):
    c = talk(tmp_path, says(ONLY_A11, proposes(A11_EVERYWHERE)), "yes", "yes")
    assert not c.proposal_paths(1) and "Is your sample already in plate well A11?" in c.out(1)
    assert c.config["dilution"]["enabled"] is False
    assert {item.well for item in build_plan(c.config).print_sources} == {"A11"}


def test_making_the_dilutions_first_is_proposed_when_the_answer_is_no(tmp_path):
    c = talk(tmp_path, says("Print only the A11 dilution on every spot.", proposes(A11_EVERYWHERE)), "no", "yes")
    assert c.config["dilution"]["enabled"] is True
    assert {item.well for item in build_plan(c.config).print_sources} == {"A11"}


def test_the_sample_question_is_never_asked_twice_in_a_row(tmp_path):
    # answered in other words that the router reads as "keep the dilution step": proposed as asked, with a note
    c = talk(tmp_path, says("Print only the A11 dilution on every spot.", proposes(A11_EVERYWHERE)),
             says("Make the dilutions first please.", proposes(A11_EVERYWHERE)), "yes")
    assert "Is your sample already in plate well A11?" in c.out(1)
    assert "Is your sample already in plate well A11?" not in c.out(2) and c.proposal_paths(2)
    assert 'say "skip the dilutions"' in c.out(2) and c.config["dilution"]["enabled"] is True


def test_a_request_that_already_skips_the_dilutions_is_not_asked(tmp_path):
    c = talk(tmp_path, says(ONLY_A11, proposes(change("dilution.enabled", False, "I only have sample in A11"),
                                                A11_EVERYWHERE)), "yes")
    assert c.proposal_paths(1) and c.config["dilution"]["enabled"] is False


# ── long sessions: earlier setups ─────────────────────────────────────────────────

FACTORS_2_5_10 = change("dilution.factors", [2, 5, 10], "Make three dilutions: 2x, 5x and 10x.")


def test_the_router_sees_every_approved_change_of_the_session(tmp_path):
    # T01.16: "What columns were we using before this?" was answered with columns never used (turns out of view)
    c = talk(tmp_path, says("Make three dilutions: 2x, 5x and 10x.", proposes(FACTORS_2_5_10)), "yes",
             says("What columns are we printing?", routed("experiment_question", answer="Paper column 1.")))
    seen = c.router_saw(3)[0]
    assert "APPLIED CHANGES THIS SESSION" in seen and "revision 1" in seen and "2× | 5× | 10×" in seen
    # only what was applied: a request's own words can name parts taken out before approval (L05.2, first run)
    assert "scientist:" not in seen.split("APPLIED CHANGES THIS SESSION")[1].split("PROPOSAL WAITING")[0]


def test_the_factors_asked_for_at_the_beginning_are_not_invented(tmp_path):
    # T01.17: the factors of revision 1, read from the applied changes, are state - shown for checking, not refused
    c = talk(tmp_path, says("Make three dilutions: 2x, 5x and 10x.", proposes(FACTORS_2_5_10)), "yes",
             says("Actually make the last one 20x.", proposes(change("dilution.factors", [2, 5, 20],
                                                                      "make the last one 20x"))), "yes",
             says("Go back to the factors I asked for at the very beginning.",
                  proposes(change("dilution.factors", [2, 5, 10], "the factors I asked for at the very beginning"))))
    [factors] = [item for item in c.pending.changes if item.path == "dilution.factors"]
    assert factors.after == [2, 5, 10] and not factors.verified
    assert factors.concern == "the value from an earlier revision of this session"
    assert "FROM AN EARLIER REVISION OF THIS SESSION" in c.out(5)


def test_the_same_paper_columns_as_before_is_a_plan_request_not_a_physical_report(tmp_path):
    # T01.18: "...before I changed them" + "paper columns" was read as a report that the paper had been moved: it asked
    # "Which labware is where?" and blocked the simulated run 13 turns later
    c = talk(tmp_path, says("Print in paper columns 2 and 3.", proposes(change("paper_columns", [2, 3],
                                                                                "paper columns 2 and 3"))), "yes",
             says("Print in paper columns 5 and 6 instead.", proposes(change("paper_columns", [5, 6],
                                                                               "paper columns 5 and 6"))), "yes",
             says("Use the same paper columns we had before I changed them.",
                  proposes(change("paper_columns", [2, 3], "the same paper columns we had before"))), "yes",
             {"text": "run", "label": {"category": "run", "may_propose": False, "may_run": True}})
    assert "Which labware is where?" not in c.out(5) and not c.record(5)["clarifying_after"]
    assert c.config["print"]["paper_start_column"] == 2 and c.config["print"]["replicates"] == 2
    assert c.session.unreconciled_report is None and "Not running" not in c.out(7)


def test_rows_written_to_the_rows_field_with_other_factors_are_the_rows_selection(tmp_path):
    # 85-test D05 (2026-09-27 re-run): "make dilutions again: 3x 6x and 12x" came back as dilution.rows [C, E, G]
    # (the rows holding 3x, 6x and 12x) written to the field: 3 rows for 8 factors, refused as an invalid run
    c = talk(tmp_path, says("ok actualy do mkae dilutions agian: 3x 6x and 12x",
                            proposes(change("dilution.enabled", True, "do mkae dilutions agian"),
                                     change("dilution.rows", ["C", "E", "G"], "3x 6x and 12x"))), "yes",
             config=deepcopy(DEFAULT))
    assert not c.events(1, "rejected") and c.config["dilution"]["enabled"] is True
    assert [well.factor for well in build_plan(c.config).wells] == [3, 6, 12]


def test_dropping_the_print_map_in_a_new_request_returns_to_the_dilution_layout(tmp_path):
    # GUI check G9: on a print-only plan from A11, "use 2x and 4x dilutions, print in paper columns 2 and 3" came back
    # with the print map dropped ("op": "drop" outside a revision) and was refused as an unknown change operation
    request = "Move the paper to slot 8, use 2x and 4x dilutions, print in paper columns 2 and 3, and use 3 drops."
    c = talk(tmp_path, says(ONLY_A11, proposes(change("dilution.enabled", False, "I only have sample in A11"),
                                                A11_EVERYWHERE)), "yes",
             says(request, proposes(PAPER_8, change("dilution.enabled", True, "use 2x and 4x dilutions"),
                                    change("dilution.factors", [2, 4], "use 2x and 4x dilutions"),
                                    change("paper_columns", [2, 3], "print in paper columns 2 and 3"),
                                    change("print.droplets_per_spot", 3, "use 3 drops"),
                                    change("print.source_map", op="drop", evidence="print in paper columns 2 and 3"))),
             "yes")
    assert not c.events(3, "rejected") and c.proposal_paths(3)
    config = c.config
    assert config["print"].get("source_map") is None and config["dilution"]["enabled"] is True
    assert config["dilution"]["factors"] == [2, 4] and config["deck"]["paper"]["slot"] == 8
    assert config["print"]["paper_start_column"] == 2 and config["print"]["replicates"] == 2


# ── questions the router asked through an answer ──────────────────────────────────

def test_an_answer_that_asks_about_an_instruction_is_the_waiting_question(tmp_path):
    # O01.1 (post-fix run): "Move it to slot 6." answered "... which labware ...?" as experiment_question; the question
    # was not kept, so the reply would have been read without it
    c = talk(tmp_path, says("Move it to slot 6.", routed("experiment_question", answer="I'm not sure which labware you "
                                                                           "mean. Which labware should go to slot 6?")),
             says("the vial rack", proposes(change("deck.tuberack.slot", 6, "the vial rack"))), "yes")
    assert c.record(1)["clarifying_after"] and not c.proposal_paths(1)
    assert c.config["deck"]["tuberack"]["slot"] == 6


# ── questions about what is possible ──────────────────────────────────────────────

def test_could_the_plate_go_in_an_occupied_slot_is_answered_with_the_deck(tmp_path):
    # B02 / B06: answered only "That would change the plan, but nothing was changed"
    c = talk(tmp_path, says("Could the plate go in slot 5?", proposes(change("deck.plate.slot", 5,
                                                                             "Could the plate go in slot 5?"))))
    out = c.out(1)
    assert "slot 5 holds the paper print plate" in out and "Free slots right now:" in out
    assert not c.proposal_paths(1) and c.config["deck"]["plate"]["slot"] == 4


def test_could_the_plate_go_in_a_free_slot_is_still_a_proposal(tmp_path):
    c = talk(tmp_path, says("Could the plate go in slot 5?", proposes(change("deck.plate.slot", 5,
                                                                             "Could the plate go in slot 5?"))),
             config=FREE_5)
    assert c.proposal_paths(1) == [["deck.plate.slot"]]
