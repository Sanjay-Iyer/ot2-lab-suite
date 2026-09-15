"""Natural-language intent must reach the OT-2 plan exactly: regressions from the 2026-09-14 review.

Every conversation here is one of the failure-style conversations from that review, replayed through the real
DemoSession with recorded model replies (redteam.harness.replay; the red-team invariants run on every turn). Each test
checks both what the scientist sees (the reply, whether a proposal was shown, the revision) and the resulting physical
plan: the plan's print operations, and the paper wells protocol v19 itself dispenses into on the recording fake OT-2.

  1. Paper columns named in a request are compared with the columns the plan would print.
  3. A short answer to "Which side-by-side paper columns?" replaces the refused columns.
  4. "The paper print plate" is labware: it never switches printing off.
  5. "Once the dilutions are made ..." is the order of the run, not a report that they exist.
  6. Naming every dilution while printing is off asks to print them.
  7. Rejecting the only change of a waiting proposal discards it.
  8. Two labware moved into one slot: OFF DECK is offered only for labware the plan can run without.

(2, the Ctrl-C / robot stop path, is in tests/test_ai_dye_demo_robot_abort.py.) Simulation only.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from src.agents.dye_demo import render
from src.agents.dye_demo.columns import (
    column_answer,
    column_conflict,
    paper_column_mentions,
    paper_columns_printed,
    rewrite_paper_columns,
)
from src.agents.dye_demo.intent import TurnContext, analyze_turn, negated_step, step_off_request
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.fake_opentrons import load_protocol_module, run_protocol
from src.agents.dye_demo.redteam.harness import replay
from src.agents.dye_demo.redteam.invariants import protocol_mismatches
from src.agents.dye_demo.redteam.sop_paths import PATHS, PREPARED_190, change, run, said
from src.agents.dye_demo.sop_check import session_run_configs
from src.agents.dye_demo.state import PREPARED_FROM_PLAN, ExperimentState, ProposalRejected

DEFAULT = load_config(DEFAULT_CONFIG)
YES = {"text": "yes"}
NO = {"text": "no"}


def talk(tmp_path, *messages, config=None):
    items = [message if isinstance(message, dict) else {"text": message} for message in messages]
    result = replay(items, workdir=tmp_path, keep_transcript=True, return_session=True, config=config)
    assert not result["violations"], result["violations"]
    return result


def said_only(text, category="chat"):
    """A message that must never propose anything."""
    return {"text": text, "label": {"category": category, "may_propose": False}}


def output(result, turn):
    return result["transcript"][turn - 1]["output"]


def row(label, value):
    """One plan row as the terminal shows it: a 20-character label column, then the value."""
    return f"  {label:<20}  {value}"


def events(result, turn, kind):
    return [event for event in result["transcript"][turn - 1]["events"] if event["type"] == kind]


def proposal_paths(result, turn):
    return [sorted(event["paths"]) for event in events(result, turn, "proposal")]


def plan_columns(config):
    return sorted({op.column for op in build_plan(config).operations if op.kind == "print"})


def robot_paper_columns(config):
    """The paper columns protocol v19 dispenses into when run on the recording fake OT-2."""
    paper = config["deck"]["paper"]["load_name"]
    log = run_protocol(load_protocol_module(), config).log
    return sorted({int(entry[2][1][1:]) for entry in log if entry[0] == "dispense" and entry[2][0] == paper})


def assert_physical_plan(config, columns):
    assert plan_columns(config) == columns
    assert robot_paper_columns(config) == columns
    assert protocol_mismatches(config) == []


def config_with(**sections):
    config = deepcopy(DEFAULT)
    for section, values in sections.items():
        config[section].update(values)
    return config


# ── 1. requested paper columns vs the columns the plan prints ───────────────────

def test_columns_the_model_did_not_set_are_never_proposed(tmp_path):
    """"Print in paper columns 3 and 4." answered with only replicates = 2 used to be a verified proposal printing 1-2."""
    result = talk(tmp_path, said("Print in paper columns 3 and 4.", change("print.replicates", 2, "paper columns 3 and 4")),
                  YES, YES)
    assert not events(result, 1, "proposal") and result["transcript"][0]["revision_after"] == 0
    assert "You asked for paper columns 3-4, but this change would print paper columns 1-2. Nothing was changed." \
        in output(result, 1)
    assert "Should this run print paper columns 3-4 (first paper column 3, 2 side-by-side replicate columns)?" \
        in output(result, 1)
    assert proposal_paths(result, 2) == [["print.paper_start_column", "print.replicates"]]
    assert events(result, 2, "proposal")[0]["unverified"] == []
    assert row("Paper columns", "3 | 4") in output(result, 2) and row("Print positions", "16   (8 rows × 2 columns)") \
        in output(result, 2)
    config = result["session"].state.config
    assert (config["print"]["paper_start_column"], config["print"]["replicates"]) == (3, 2)
    assert_physical_plan(config, [3, 4])


def test_a_wrong_first_column_is_blocked_and_no_run_starts_while_it_is_unsettled(tmp_path):
    result = talk(tmp_path, said("Print in paper columns 3 and 4.", change("print.paper_start_column", 4, "columns 3 and 4"),
                                 change("print.replicates", 2, "paper columns 3 and 4")),
                  run(), YES, YES, run())
    assert not events(result, 1, "proposal")
    assert "this change would print paper columns 4-5" in output(result, 1)
    assert not events(result, 2, "run") and "Not running: the paper columns you asked for are not settled" \
        in output(result, 2)
    assert events(result, 5, "run")
    [executed] = session_run_configs(result["session"])
    assert_physical_plan(executed, [3, 4])


def test_two_drop_volumes_print_exactly_the_named_columns(tmp_path):
    """With 5 µL and 10 µL drops, replicates = 2 would print paper columns 3-6."""
    two_volumes = config_with(print={"droplet_volume_ul": [2.0, 5.0]})
    result = talk(tmp_path, said("Print in paper columns 3 and 4.", change("print.paper_start_column", 3, "columns 3"),
                                 change("print.replicates", 2, "paper columns 3 and 4")), YES, YES, config=two_volumes)
    assert not events(result, 1, "proposal") and "this change would print paper columns 3-6" in output(result, 1)
    assert "1 side-by-side replicate column for each drop volume" in output(result, 1)
    assert proposal_paths(result, 2) == [["print.paper_start_column"]]
    config = result["session"].state.config
    assert config["print"]["replicates"] == 1
    assert_physical_plan(config, [3, 4])
    volumes = {op.column: op.volume_ul for op in build_plan(config).operations if op.kind == "print"}
    assert volumes == {3: 2.0, 4: 5.0}


def test_sop4_second_print_never_lands_on_the_first_runs_columns(tmp_path):
    """SOP 4 run 2: "Print three stacked drops in paper columns 4 and 5." with a reply that leaves the start column at 1."""
    first_run = PATHS[4]["multi_change"][:3]
    result = talk(tmp_path, *first_run,
                  said("The dilutions from run 1 are already made, each well has about 190 uL left.", PREPARED_190), YES,
                  said("Print three stacked drops in paper columns 4 and 5.",
                       change("print.droplets_per_spot", 3, "three stacked drops")), YES, YES, run())
    assert not events(result, 6, "proposal") and "this change would print paper columns 1-2" in output(result, 6)
    assert proposal_paths(result, 7) == [["print.droplets_per_spot", "print.paper_start_column"]]
    first, second = session_run_configs(result["session"])
    assert plan_columns(first) == [1, 2]
    assert_physical_plan(second, [4, 5])
    assert {op.droplets for op in build_plan(second).operations if op.kind == "print"} == {3}
    assert not build_plan(second).do_dilution


def test_values_already_set_do_not_hide_a_column_mismatch(tmp_path):
    result = talk(tmp_path, said("Use 2 replicate paper columns.", change("print.replicates", 2, "2 replicate paper columns")),
                  YES, said("Print in paper columns 3 and 4.", change("print.replicates", 2, "paper columns 3 and 4")))
    assert "Those values are already set" not in output(result, 3)
    assert "You asked for paper columns 3-4, but this change would print paper columns 1-2." in output(result, 3)
    assert plan_columns(result["session"].state.config) == [1, 2]


def test_a_named_first_column_is_checked(tmp_path):
    result = talk(tmp_path, said("Print three drops starting at paper column 4.",
                                 change("print.droplets_per_spot", 3, "three drops")), YES, YES)
    assert not events(result, 1, "proposal")
    assert "You asked to start printing at paper column 4, but this change would print paper column 1." in output(result, 1)
    assert "Should this run print paper column 4 (first paper column 4)?" in output(result, 1)
    assert proposal_paths(result, 2) == [["print.droplets_per_spot", "print.paper_start_column"]]
    assert_physical_plan(result["session"].state.config, [4])


def test_declining_the_offered_columns_changes_nothing(tmp_path):
    result = talk(tmp_path, said("Print in paper columns 3 and 4.", change("print.replicates", 2, "paper columns 3 and 4")),
                  NO)
    assert "Tell me which side-by-side paper columns this run should print" in output(result, 2)
    assert result["session"].state.revision == 0 and plan_columns(result["session"].state.config) == [1]


def test_named_columns_the_model_got_right_are_proposed_at_once(tmp_path):
    result = talk(tmp_path, said("Print in paper columns 3 and 4.", change("print.paper_start_column", 3, "columns 3"),
                                 change("print.replicates", 2, "paper columns 3 and 4")), YES)
    assert proposal_paths(result, 1) == [["print.paper_start_column", "print.replicates"]]
    assert row("Paper columns", "3 | 4") in output(result, 1)
    assert_physical_plan(result["session"].state.config, [3, 4])


def test_approving_only_unrelated_changes_is_still_possible(tmp_path):
    result = talk(tmp_path, said("Make dilutions of 2x, 4x and 8x and print them in paper columns 3 and 4.",
                                 change("dilution.factors", [2, 4, 8], "2x, 4x and 8x"),
                                 change("print.paper_start_column", 3, "paper columns 3"),
                                 change("print.replicates", 2, "paper columns 3 and 4")),
                  {"text": "only the dilutions", "label": {"category": "partial_approval", "may_propose": None}}, YES)
    assert proposal_paths(result, 2) == [["dilution.factors"]]
    config = result["session"].state.config
    assert config["dilution"]["factors"] == [2, 4, 8] and plan_columns(config) == [1]


def test_the_plan_summary_and_proposal_name_the_printed_columns():
    config = config_with(print={"paper_start_column": 3, "replicates": 2})
    step = render.render_print_step(config, build_plan(config))
    assert "Paper columns printed: 3-4   (first paper column 3" in step
    proposal = ExperimentState(DEFAULT).propose([change("print.paper_start_column", 5, "paper column 5")],
                                                request="start printing at paper column 5")
    shown = render.render_proposal(proposal)
    assert row("Paper columns", "5") in shown and "1 -> 5" not in shown


def test_the_state_refuses_a_proposal_whose_printed_columns_differ():
    state = ExperimentState(DEFAULT)
    with pytest.raises(ProposalRejected) as caught:
        state.propose([change("print.replicates", 2, "columns 3 and 4")], request="Print in paper columns 3 and 4.")
    assert caught.value.kind == "paper_columns" and state.revision == 0
    assert [(item["path"], item["value"]) for item in caught.value.fix_changes] == [
        ("print.paper_start_column", 3), ("print.replicates", 2)]


@pytest.mark.parametrize("text, found", [
    ("Print in paper columns 3 and 4.", [((3, 4), "group")]),
    ("Print starting at paper column 2.", [((2,), "start")]),
    ("Print only in paper column 6.", [((6,), "only")]),
    ("Print three drops, not in paper columns 1 and 2.", [((1, 2), "avoid")]),
    ("Print in column 3.", [((3,), "start")]),
    ("Print in columns 2-5.", [((2, 3, 4, 5), "group")]),
    ("The paper columns 1 and 2 are already used.", []),
    ("Set the replicate paper columns 2.", []),
    ("Make the dilutions in plate column 3.", []),
    ("Make the dilutions in column 3.", []),
    ("Start the tips at column 2 and print 3 drops.", []),
])
def test_paper_column_mentions(text, found):
    assert [(mention.columns, mention.role) for mention in paper_column_mentions(text)] == found


def test_column_conflicts_between_words_and_plan():
    printing_3_4 = config_with(print={"paper_start_column": 3, "replicates": 2})
    assert column_conflict("Print in paper columns 3 and 4.", printing_3_4) is None
    assert column_conflict("Print in paper columns 3 and 5.", DEFAULT).kind == "paper_layout"
    assert "separate run" in column_conflict("Print in paper columns 1 and 2, then paper columns 4 and 5.", DEFAULT).message
    printing_off = config_with(print={"enabled": False})
    conflict = column_conflict("Print in paper columns 3 and 4.", printing_off)
    assert "this plan does not print in this run" in conflict.message
    assert ("print.enabled", True) in [(item["path"], item["value"]) for item in conflict.fix]
    odd = column_conflict("Print in paper columns 3, 4 and 5.", config_with(print={"droplet_volume_ul": [2.0, 5.0]}))
    assert odd.fix is None and "cannot be printed exactly" in odd.message
    avoided = column_conflict("Print three drops, not in paper column 1.", DEFAULT)
    assert avoided is not None and avoided.fix is None
    assert paper_columns_printed(printing_off) == []


# ── 3. a short answer replaces the refused paper columns ────────────────────────

LAYOUT = (change("print.paper_start_column", 3, "columns 3"), change("print.replicates", 2, "columns 3 and 4"))


@pytest.mark.parametrize("answer", ["Columns 3 and 4.", "3 and 4", "paper columns 3-4"])
def test_a_short_column_answer_replaces_the_refused_columns(tmp_path, answer):
    result = talk(tmp_path, said("Print in paper columns 3 and 5.", *LAYOUT), run(), said(answer, *LAYOUT), YES)
    assert "Paper columns 3, 5 are not side by side" in output(result, 1)
    assert "Which side-by-side paper columns should this run print?" in output(result, 1)
    assert not events(result, 2, "run") and "Not running" in output(result, 2)
    [answered] = events(result, 3, "clarification_answer")
    assert answered["combined"] == "Print in paper columns 3 and 4."
    assert proposal_paths(result, 3) == [["print.paper_start_column", "print.replicates"]]
    assert_physical_plan(result["session"].state.config, [3, 4])


def test_a_column_answer_after_a_report_keeps_the_report(tmp_path):
    """"The dilutions are already made. Print three drops in paper columns 1 and 3." answered with "Columns 1 and 2.":
    the whole message is read again, so the prepared-dilutions record is still part of the proposal."""
    drops = change("print.droplets_per_spot", 3, "three drops")
    result = talk(tmp_path, said("The dilutions are already made. Print three drops in paper columns 1 and 3.", drops,
                                 change("print.replicates", 2, "paper columns 1 and 3")),
                  said("Columns 1 and 2.", drops, change("print.replicates", 2, "paper columns 1 and 2")), YES, run())
    assert "Paper columns 1, 3 are not side by side" in output(result, 1) and not events(result, 1, "proposal")
    [proposal] = events(result, 2, "proposal")
    assert proposal["source"] == "physical-report" and proposal["physical"] == ["dilutions_prepared"]
    assert sorted(proposal["paths"]) == ["dilution.enabled", "print.droplets_per_spot", "print.replicates"]
    session = result["session"]
    assert session.unreconciled_report is None and events(result, 4, "run")
    assert session.state.physical["dilutions_prepared"]["wells"] == [f"{row}11" for row in "ABCDEFGH"]
    [executed] = session_run_configs(session)
    assert not build_plan(executed).do_dilution
    assert_physical_plan(executed, [1, 2])


def test_a_second_gap_is_refused_again_and_nothing_changes(tmp_path):
    result = talk(tmp_path, said("Print in paper columns 3 and 5.", *LAYOUT), said("3 and 5", *LAYOUT))
    assert not events(result, 2, "proposal") and "Paper columns 3, 5 are not side by side" in output(result, 2)
    assert result["session"].state.revision == 0 and result["session"].clarifying is not None


@pytest.mark.parametrize("text, columns", [("Columns 3 and 4.", [3, 4]), ("3 and 4", [3, 4]), ("paper columns 3-4", [3, 4]),
                                           ("4", [4]), ("3 to 5", [3, 4, 5]), ("Print in paper columns 3 and 4 instead.",
                                                                               [3, 4]),
                                           ("2 drops", None), ("no", None)])
def test_column_answers(text, columns):
    assert column_answer(text) == columns


def test_rewriting_the_request_keeps_everything_but_the_columns():
    assert rewrite_paper_columns("Print in paper columns 3 and 5.", [3, 4]) == "Print in paper columns 3 and 4."
    assert rewrite_paper_columns("Print three drops in column 3 and 5, please.", [3, 4]) == \
        "Print three drops in paper columns 3 and 4, please."
    kept = rewrite_paper_columns("Print three drops, not in paper columns 1 and 2.", [4, 5])
    assert "not in paper columns 1 and 2" in kept and kept.endswith("in paper columns 4 and 5")


# ── 4. the paper print plate is labware, not the print step ─────────────────────

@pytest.mark.parametrize("text", ["Don't move the paper print plate.", "Keep the paper print plate where it is."])
def test_keeping_the_print_plate_is_not_about_printing(tmp_path, text):
    result = talk(tmp_path, said_only(text, "negation"))
    assert "Paper print plate location stays Slot 5" in output(result, 1)
    assert "still prints" not in output(result, 1) and "skip printing" not in output(result, 1)
    assert result["session"].state.revision == 0 and build_plan(result["session"].state.config).do_print


def test_a_model_that_turns_printing_off_for_the_print_plate_is_refused(tmp_path):
    result = talk(tmp_path, said("Keep the print plate in slot 5.", change("print.enabled", False, "print plate")),
                  said("Move the paper print plate to slot 6.", change("deck.paper.slot", 6, "paper print plate to slot 6"),
                       change("print.enabled", False, "paper print plate")))
    for turn in (1, 2):
        assert not events(result, turn, "proposal")
        assert "You did not ask to skip printing, so I did not turn printing off." in output(result, turn)
    config = result["session"].state.config
    assert result["session"].state.revision == 0 and config["deck"]["paper"]["slot"] == 5
    assert_physical_plan(config, [1])


def test_skip_printing_still_turns_printing_off(tmp_path):
    result = talk(tmp_path, said("Skip printing this run.", change("print.enabled", False, "Skip printing")), YES)
    assert proposal_paths(result, 1) == [["print.enabled"]]
    assert not build_plan(result["session"].state.config).do_print


@pytest.mark.parametrize("text, step", [("Don't move the paper print plate.", None), ("Keep the print plate in slot 5.", None),
                                        ("Keep the paper print plate where it is.", None),
                                        ("Don't change the drop volume for printing.", None),
                                        ("Don't print anything this time.", "print"), ("I don't want to print today.", "print"),
                                        ("Don't make the dilutions.", "dilution")])
def test_only_a_whole_step_is_a_negated_step(text, step):
    assert negated_step(text) == step


def test_a_run_without_the_print_plate_is_not_a_print_step_request():
    assert step_off_request("Run without print plate.") is None
    assert step_off_request("No printing this run, just dilutions.") == "print"


def test_the_state_needs_step_wording_in_the_right_direction():
    state = ExperimentState(DEFAULT)
    with pytest.raises(ProposalRejected) as caught:
        state.propose([change("print.enabled", False, "print plate")], request="Keep the print plate in slot 5.")
    assert caught.value.kind == "step_not_requested"
    assert state.propose([change("print.enabled", False, "skip printing")], request="Skip printing this run.").paths == \
        ["print.enabled"]
    with pytest.raises(ProposalRejected, match="did not say that the dilutions are already made"):
        state.propose([change("dilution.enabled", False, "dilutions are made")],
                      request="Once the dilutions are made, print them.", physical={"dilutions_prepared": PREPARED_FROM_PLAN})


# ── 5. temporal clauses are not completed-state reports ─────────────────────────

def test_once_the_dilutions_are_made_is_the_order_of_the_run(tmp_path):
    result = talk(tmp_path, said("Once the dilutions are made, print them onto paper column 2.",
                                 change("print.paper_start_column", 2, "paper column 2")), YES, run())
    [proposal] = events(result, 1, "proposal")
    assert proposal["paths"] == ["print.paper_start_column"] and proposal["physical"] == []
    assert 'I read "Once the dilutions are made" as when that happens in the run' in output(result, 1)
    session = result["session"]
    assert session.unreconciled_report is None and session.state.physical["dilutions_prepared"] is None
    assert events(result, 3, "run")
    [executed] = session_run_configs(session)
    assert build_plan(executed).do_dilution
    assert_physical_plan(executed, [2])


def test_declining_a_temporal_request_never_blocks_the_run(tmp_path):
    result = talk(tmp_path, said("Once the dilutions are made, print them onto paper column 2.",
                                 change("print.paper_start_column", 2, "paper column 2")), NO, run())
    assert "not in the record" not in output(result, 2) and events(result, 3, "run")


def test_let_me_know_when_the_dilutions_are_ready_is_not_a_report(tmp_path):
    result = talk(tmp_path, said_only("Let me know when the dilutions are ready.", "detour_question"), run())
    assert result["transcript"][0]["classification"] == "question" and not events(result, 1, "proposal")
    assert "first the dilutions are made in plate wells A11-H11" in output(result, 1)
    session = result["session"]
    assert session.unreconciled_report is None and events(result, 2, "run")
    assert build_plan(session_run_configs(session)[0]).do_dilution


@pytest.mark.parametrize("text, actionable", [
    ("Once the dilutions are made, print them onto paper column 2.", "print them onto paper column 2."),
    ("After the dilutions are done, print 3 drops of each.", "print 3 drops of each."),
    ("As soon as the dilutions are made, print them.", "print them."),
    ("Print them once the dilutions are ready.", "Print them."),
    ("Let me know when the dilutions are ready.", ""),
    ("Make sure the dilutions are mixed.", ""),
])
def test_temporal_clauses_have_no_facts(text, actionable):
    analysis = analyze_turn(text, TurnContext(config=DEFAULT))
    assert analysis.facts == [] and analysis.kind != "physical_report"
    assert analysis.actionable == actionable


@pytest.mark.parametrize("text", ["The dilutions are already made.", "The dilutions from the first run are already made.",
                                  "We already made all the dilutions.", "The dilutions were made yesterday."])
def test_completed_state_statements_are_still_reports(text):
    analysis = analyze_turn(text, TurnContext(config=DEFAULT))
    assert analysis.kind == "physical_report" and analysis.facts[0].kind == "dilutions_prepared"


# ── 6. naming every dilution while printing is off ──────────────────────────────

PRINTING_OFF = config_with(dilution={"factors": [5, 10, 20]}, print={"enabled": False})


def test_print_the_named_dilutions_turns_printing_on_when_it_is_off(tmp_path):
    result = talk(tmp_path, {"text": "Print the 5x, 10x and 20x dilutions.",
                             "label": {"category": "instruction", "may_propose": None}}, YES, config=PRINTING_OFF)
    assert "already prints" not in output(result, 1)
    assert proposal_paths(result, 1) == [["print.enabled"]] and result["transcript"][0]["llm"] == []
    assert row("Changes", "printing on") in output(result, 1)
    assert "PRINTING" + "in this run".rjust(72 - len("PRINTING")) in output(result, 1)
    assert row("Paper rows", "A | B | C   (one row per dilution)") in output(result, 1)
    config = result["session"].state.config
    assert build_plan(config).do_print and build_plan(config).do_dilution
    assert [op.destination for op in build_plan(config).operations if op.kind == "print"] == ["A1", "B1", "C1"]
    assert_physical_plan(config, [1])


def test_print_the_named_dilutions_with_a_column_when_printing_is_off(tmp_path):
    message = said("Print the 5x, 10x and 20x dilutions onto paper column 2.",
                   change("print.paper_start_column", 2, "paper column 2"))
    # what the scientist asked for: printing on (added by the session) and the first paper column (read by the model)
    message["label"]["intended"] = [change("print.enabled", True, "Print"), change("print.paper_start_column", 2, "2")]
    result = talk(tmp_path, message, YES, config=PRINTING_OFF)
    assert proposal_paths(result, 1) == [["print.enabled", "print.paper_start_column"]]
    assert_physical_plan(result["session"].state.config, [2])


def test_print_the_named_dilutions_when_they_already_print_is_unchanged():
    planned = config_with(dilution={"factors": [5, 10, 20]})
    analysis = analyze_turn("Print the 5x, 10x and 20x dilutions.", TurnContext(config=planned))
    assert analysis.kind == "unsupported" and "already prints" in analysis.details["unsupported"][0]


# ── 7. rejecting the only change of a waiting proposal ──────────────────────────

MOVE_PLATE_8 = said("Move the dilution plate to slot 8.", change("deck.plate.slot", 8, "plate to slot 8"))


@pytest.mark.parametrize("rejection", ["Actually, keep the plate where it is.", "Don't move the plate."])
def test_rejecting_the_only_change_discards_the_proposal(tmp_path, rejection):
    result = talk(tmp_path, MOVE_PLATE_8, {"text": rejection, "label": {"category": "negation", "may_propose": False}},
                  {"text": "yes", "label": {"category": "stale_yes", "may_propose": False}})
    assert "could not match" not in output(result, 2)
    assert "Discarded proposal #1. Nothing was changed." in output(result, 2)
    assert "96-well dilution plate location stays Slot 4" in output(result, 2)
    assert events(result, 2, "discarded") and result["transcript"][1]["pending_after"] is None
    assert "There is no proposed change waiting for approval" in output(result, 3)
    config = result["session"].state.config
    assert result["session"].state.revision == 0 and config["deck"]["plate"]["slot"] == 4
    assert protocol_mismatches(config) == []


def test_rejecting_one_of_several_changes_still_keeps_the_others(tmp_path):
    result = talk(tmp_path, said("Move the dilution plate to slot 8 and make three dilutions, 2x, 4x and 8x.",
                                 change("deck.plate.slot", 8, "plate to slot 8"),
                                 change("dilution.factors", [2, 4, 8], "2x, 4x and 8x")),
                  "Actually, keep the plate where it is.", YES)
    assert proposal_paths(result, 2) == [["dilution.factors"]]
    config = result["session"].state.config
    assert config["deck"]["plate"]["slot"] == 4 and config["dilution"]["factors"] == [2, 4, 8]


# ── 8. several labware moved into one slot ──────────────────────────────────────

def test_two_required_labware_are_not_offered_off_deck(tmp_path):
    result = talk(tmp_path, said("Move the dilution plate and the paper print plate to slot 6.",
                                 change("deck.plate.slot", 6, "dilution plate to slot 6"),
                                 change("deck.paper.slot", 6, "paper print plate to slot 6")))
    text = output(result, 1)
    assert "The 96-well dilution plate and the Paper print plate cannot all go to Slot 6." in text
    assert ("Not OFF DECK: the 96-well dilution plate is needed (the dilutions are made in it and printing draws from "
            "it); the Paper print plate is needed (printing needs the paper).") in text
    assert "or OFF DECK" not in text and "the that labware" not in text
    assert not events(result, 1, "proposal") and result["session"].state.revision == 0


def test_off_deck_is_offered_only_for_the_labware_the_plan_can_run_without():
    state = ExperimentState(DEFAULT)
    state.apply(state.propose([change("dilution.enabled", False, "dilutions are already made")],
                              request="the dilutions are already made",
                              physical={"dilutions_prepared": PREPARED_FROM_PLAN}), operator="test")
    with pytest.raises(ProposalRejected) as caught:
        state.propose([change("deck.tuberack.slot", 6, "vial rack to slot 6"), change("deck.tiprack.slot", 6, "tip rack")],
                      request="move the vial rack and the tip rack to slot 6")
    text = render.render_conflict(caught.value.before, caught.value.after, caught.value.conflicts)
    assert "Not OFF DECK: the P20 tip rack is needed (every step needs P20 tips)." in text
    assert "or OFF DECK (only the Vial rack)" in text
    assert '"OFF DECK" means the Vial rack has been physically removed from the OT-2 deck.' in text
