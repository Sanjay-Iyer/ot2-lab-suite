"""Conversation state of the semantic pipeline (Level 1 + 2 of the edge-case suite): the startup print selection,
ADD / MODIFY / REPLACE across turns, the RECENT FOCUS the router reads, the safety question's lifecycle, proposal
revisions, unresolved references, grounded counts and compile determinism.

A scripted router stands in for the model: these tests check what Python does with a reading, and what Python shows
the router, never how the model reads words. No robot is contacted; one test runs the pinned Opentrons simulator.
"""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.agents.dye_demo.conversation import compiled_lines, focus_of, focus_lines, selection_is_default
from src.agents.dye_demo.llm import LLMClient
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.session import DemoSession, SessionSettings


class Router:
    """Replies by message; keeps what the model was shown each time."""

    def __init__(self, replies):
        self.replies, self.seen, self.prompts = replies, [], []

    def invoke(self, messages):
        message = messages[-1][1].split("SCIENTIST'S MESSAGE:\n")[-1].strip()
        self.seen.append(message)
        self.prompts.append(messages[-1][1])
        return SimpleNamespace(content=json.dumps(self.replies[message]))


class Executor:
    last_status = {"started": True, "robot_status": "succeeded"}

    def check_robot(self):
        return None

    def __call__(self, *_):
        return 0


def session(tmp_path, replies, config=None):
    model, output = Router(replies), []
    settings = SessionSettings(simulate=True, config_source=DEFAULT_CONFIG, working_config=tmp_path / "working.yaml",
                               run_dir=tmp_path / "run", session_label="state", operator="Tester",
                               skip_llm_startup=True, run_button="Run on OT-2", llm_first=True, raise_errors=True)
    demo = DemoSession(settings, deepcopy(config) if config is not None else load_config(DEFAULT_CONFIG),
                       llm=LLMClient(lambda: model), executor=Executor(), input_fn=lambda _p: "yes",
                       output_fn=output.append, sleep=lambda _: None)
    demo.operator = "Tester"
    demo.request_run_confirmation = lambda: output.append("CONFIRM RUN")
    return demo, model, output


def change(modes, *operations, response="", revises=False):
    return {"kind": "plan_change", "response": response, "revises": revises, "edit_modes": modes,
            "operations": [dict(operation) for operation in operations]}


def question(answer):
    return {"kind": "question", "response": answer}


def groups(*items):
    """print.source set to these groups: (source, count) pairs."""
    return {"op": "set", "path": "print.source", "value": [{"source": source, "count": count}
                                                            for source, count in items]}


def one(source, **value):
    return {"op": "set", "path": "print.source", "source": source, "value": value}


def factors(value, **extra):
    return {"op": "set", "path": "dilution.factors", "value": value, **extra}


def enabled(value):
    return {"op": "set", "path": "dilution.enabled", "value": value}


def say(demo, message, *, apply=True):
    revision = demo.state.revision
    demo._handle(message)
    if apply:
        assert demo.pending is not None, f"no proposal for {message!r}"
        demo._handle("yes")
        assert demo.state.revision == revision + 1


def printing(demo):
    """The print groups as (source, count, rows) - every group, so a hidden one is seen."""
    result = []
    for item in demo.state.config["print"].get("source") or []:
        item = item if isinstance(item, dict) else {"source": item}
        result.append((item["source"], item.get("count"), tuple(item.get("rows") or ())))
    return result


def unchanged(demo):
    return deepcopy(demo.state.config), demo.state.revision, demo.pending.id if demo.pending else None


def still(demo, snapshot):
    config, revision, pending = snapshot
    assert demo.state.config == config and demo.state.revision == revision
    assert (demo.pending.id if demo.pending else None) == pending


def spec(model):
    """The PLAN SPEC part of the last router prompt."""
    return model.prompts[-1].split("SCIENTIST'S MESSAGE:")[0]


def samples():
    """Samples already in plate wells A11-H11: the dilution step is off."""
    config = load_config(DEFAULT_CONFIG)
    config["dilution"]["enabled"] = False
    return config


# ── TEST 1 / 2 / 4: the startup print selection, and a chosen one ───────────────────────────────────────────────────

def test_01_startup_default_replaced_by_a_named_selection_prints_one_source(tmp_path):
    demo, model, _ = session(tmp_path, {
        "Print the first dilution three times.": change({"printing": "replace"}, groups(("dilution:1", 3))),
    })
    assert selection_is_default(demo.state.config)
    say(demo, "Print the first dilution three times.")
    assert "PRINT SELECTION: STARTUP DEFAULT" in spec(model)              # what the router was told before
    assert printing(demo) == [("dilution:1", 3, ())] and not selection_is_default(demo.state.config)
    compiled = demo.state.compile()
    prints = [op for op in compiled.plan.operations if op.kind == "print"]
    assert compiled.ok and len(prints) == 3 and {op.source for op in prints} == {"A11"}


def test_02_startup_default_kept_when_the_scientist_says_so(tmp_path):
    demo, _, _ = session(tmp_path, {
        "Keep the other dilutions printing, but make the first dilution three times too.":
            change({"printing": "modify"}, one("dilution:1", count=3)),
    })
    say(demo, "Keep the other dilutions printing, but make the first dilution three times too.")
    assert printing(demo) == [("dilution:1", 3, ())] + [(f"dilution:{n}", None, ()) for n in range(2, 9)]


def test_04_to_07_a_chosen_selection_persists_through_modify_add_replace(tmp_path):
    demo, model, _ = session(tmp_path, {
        "Only print A11 and B11 once each.": change({"printing": "replace"}, groups(("dilution:1", 1), ("dilution:2", 1))),
        "Make B11 three times.": change({"printing": "modify"}, one("dilution:2", count=3)),
        "Also print C11 twice.": change({"printing": "add"}, groups(("dilution:3", 2))),
        "Actually, only print C11 four times.": change({"printing": "replace"}, groups(("dilution:3", 4))),
    })
    say(demo, "Only print A11 and B11 once each.")
    say(demo, "Make B11 three times.")
    assert "PRINT SELECTION: CHOSEN" in spec(model)
    assert printing(demo) == [("dilution:1", 1, ()), ("dilution:2", 3, ())]
    say(demo, "Also print C11 twice.")
    assert printing(demo) == [("dilution:1", 1, ()), ("dilution:2", 3, ()), ("dilution:3", 2, ())]
    say(demo, "Actually, only print C11 four times.")
    assert printing(demo) == [("dilution:3", 4, ())]


def test_07_repeated_mode_switching_leaves_no_hidden_group(tmp_path):
    steps = [
        ("Only print A11 once.", change({"printing": "replace"}, groups(("dilution:1", 1))),
         [("dilution:1", 1, ())]),
        ("Also print B11 twice.", change({"printing": "add"}, groups(("dilution:2", 2))),
         [("dilution:1", 1, ()), ("dilution:2", 2, ())]),
        ("Make A11 three times.", change({"printing": "modify"}, one("dilution:1", count=3)),
         [("dilution:1", 3, ()), ("dilution:2", 2, ())]),
        ("Replace all printing with C11 twice.", change({"printing": "replace"}, groups(("dilution:3", 2))),
         [("dilution:3", 2, ())]),
        ("Add A11 once too.", change({"printing": "add"}, groups(("dilution:1", 1))),
         [("dilution:3", 2, ()), ("dilution:1", 1, ())]),
        ("Make C11 five times.", change({"printing": "modify"}, one("dilution:3", count=5)),
         [("dilution:3", 5, ()), ("dilution:1", 1, ())]),
    ]
    demo, _, _ = session(tmp_path, {message: reply for message, reply, _ in steps})
    for message, _, expected in steps:
        say(demo, message)
        assert printing(demo) == expected, message
    counts = {}
    for entry in demo.state.compile().source_map:
        counts[entry["source"]] = counts.get(entry["source"], 0) + len(entry["positions"])
    assert counts == {"C11": 5, "A11": 1}                   # nothing else prints


# ── TEST 8-12: the RECENT FOCUS the router reads ─────────────────────────────────────────────────────────────────────

def focus_line(model):
    lines = spec(model).splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("RECENT FOCUS"))
    return "\n".join(lines[start:start + 3])


def test_09_10_11_focus_follows_the_latest_change_and_survives_a_question(tmp_path):
    demo, model, _ = session(tmp_path, {
        "Only print A11 twice and B11 once.": change({"printing": "replace"}, groups(("dilution:1", 2), ("dilution:2", 1))),
        "Make A11 four times.": change({"printing": "modify"}, one("dilution:1", count=4)),
        "How many total spots is that?": question("5 spots."),
        "Put those on rows 1,3,5,7.": change({"printing": "modify"}, one("dilution:1", rows=["A", "C", "E", "G"])),
        "Now make B11 three times.": change({"printing": "modify"}, one("dilution:2", count=3)),
        "Put those on rows 2,4,6.": change({"printing": "modify"}, one("dilution:2", rows=["B", "D", "F"])),
    })
    say(demo, "Only print A11 twice and B11 once.")
    say(demo, "Make A11 four times.")
    demo._handle("How many total spots is that?")
    assert "print group 1: dilution 1" in focus_line(model) and "dilution 2" not in focus_line(model)
    demo._handle("Put those on rows 1,3,5,7.")
    # the question in between did not move the focus away from A11
    assert "print group 1: dilution 1" in focus_line(model) and "4 prints" in focus_line(model)
    demo._handle("yes")
    assert printing(demo) == [("dilution:1", None, ("A", "C", "E", "G")), ("dilution:2", 1, ())]
    assert not demo.state.config["print"].get("paper_rows")             # never a plan-wide setting
    say(demo, "Now make B11 three times.")
    demo._handle("Put those on rows 2,4,6.")
    assert "print group 2: dilution 2" in focus_line(model) and "group 1" not in focus_line(model)
    demo._handle("yes")
    assert printing(demo) == [("dilution:1", None, ("A", "C", "E", "G")), ("dilution:2", None, ("B", "D", "F"))]


def test_focus_of_names_the_changed_groups_or_every_group_and_changed_dilutions():
    before = load_config(DEFAULT_CONFIG)
    after = deepcopy(before)
    after["print"]["source"] = [{"source": "dilution:1", "count": 2}, {"source": "dilution:2", "count": 1}]
    assert [line.split(":")[0] for line in focus_of(before, after)["printing"]] == ["print group 1", "print group 2"]
    later = deepcopy(after)
    later["print"]["droplets_per_spot"] = 2
    assert focus_of(after, later)["printing"][0].startswith("every print group")
    short, longer = deepcopy(after), deepcopy(after)
    short["dilution"]["factors"], longer["dilution"]["factors"] = [2, 4], [2, 4, 8]
    assert focus_of(short, longer) == {"dilution": ["dilution:3 = 8× in plate well C11"]}
    moved = deepcopy(longer)
    moved["dilution"]["plate_column"] = "10"
    assert focus_of(longer, moved)["dilution"][0].startswith("the whole dilution series (dilution:1 in A10")
    assert focus_lines({})[0].startswith("RECENT FOCUS: nothing yet")


def test_12_an_answered_clarification_moves_the_focus_to_the_answer(tmp_path):
    demo, model, _ = session(tmp_path, {
        "Only print A11 twice and B11 twice.": change({"printing": "replace"}, groups(("dilution:1", 2), ("dilution:2", 2))),
        "Make the second one four.": {"kind": "clarification", "clarification": "4 prints of B11, or 4 drops?",
                                      "unresolved": ["print.source"]},
        "I mean B11.": change({"printing": "modify"}, one("dilution:2", count=4)),
        "Put those on rows 2,4,6,8.": change({"printing": "modify"}, one("dilution:2", rows=["B", "D", "F", "H"])),
    })
    say(demo, "Only print A11 twice and B11 twice.")
    demo._handle("Make the second one four.")
    assert demo.pending is None and demo.clarifying is not None
    say(demo, "I mean B11.")
    demo._handle("Put those on rows 2,4,6,8.")
    assert "print group 2: dilution 2" in focus_line(model)
    demo._handle("yes")
    assert printing(demo) == [("dilution:1", 2, ()), ("dilution:2", None, ("B", "D", "F", "H"))]


# ── TEST 13-17: the safety question's lifecycle ──────────────────────────────────────────────────────────────────────

SAFETY = {"Print A11 twice.": change({"printing": "replace"}, groups(("A11", 2)))}


def safety_session(tmp_path, replies):
    demo, model, output = session(tmp_path, {**SAFETY, **replies})
    demo._handle("Print A11 twice.")
    assert demo.pending is None and demo.safety is not None and "A11" in demo.safety.question
    return demo, model, output


def test_13_unrelated_edit_keeps_the_safety_question_open_and_no_run_starts(tmp_path):
    demo, model, output = safety_session(tmp_path, {
        "Make B11 print four times.": change({"printing": "replace"}, groups(("dilution:2", 4))),
        "run it": {"kind": "run_request"},
    })
    held = demo.safety
    demo._handle("Make B11 print four times.")
    assert "SAFETY question" in model.prompts[-1]                        # the router knew it was open
    assert demo.safety is held and demo.pending is not None             # an unrelated edit is proposed beside it
    assert [change["path"] for change in held.changes] == ["print.source"]
    # the reply names the open question and says what a bare yes would do now
    assert any("Still open" in line and "A11" in line for line in output)
    demo._handle("yes")                                                  # applies the B11 proposal, not the answer
    assert demo.safety is held and printing(demo) == [("dilution:2", 4, ())]
    assert any("safety question is open" in reason for reason in demo.run_block_reasons())
    demo._handle("run it")
    assert "CONFIRM RUN" not in output


def test_13c_a_second_request_that_needs_the_answer_is_held_with_the_first(tmp_path):
    demo, _, output = safety_session(tmp_path, {
        "Make B11 print four times.": change({"printing": "replace"}, groups(("B11", 4))),
        "Neither, make the dilutions first.": change({"dilution": "modify"}, enabled(True)),
    })
    demo._handle("Make B11 print four times.")                     # a plain well again: the same question is needed
    assert demo.pending is None and demo.safety is not None
    assert "A11" in demo.safety.question and "B11" in demo.safety.question         # one question, both wells
    assert [change["value"] for change in demo.safety.changes] == [[{"source": "A11", "count": 2}],
                                                                   [{"source": "B11", "count": 4}]]
    demo._handle("Neither, make the dilutions first.")
    demo._handle("yes")
    assert printing(demo) == [("dilution:1", 2, ()), ("dilution:2", 4, ())] and demo.safety is None


def test_13b_answering_after_an_unrelated_waiting_proposal_keeps_both(tmp_path):
    demo, _, _ = safety_session(tmp_path, {
        "Make B11 print four times.": change({"printing": "add"}, groups(("dilution:2", 4))),
        "No, make the dilutions first.": change({"printing": "replace"}, groups(("A11", 2)), enabled(True)),
    })
    demo._handle("Make B11 print four times.")
    assert demo.pending is not None and demo.safety is not None
    demo._handle("No, make the dilutions first.")
    assert demo.safety is None and demo.pending is not None
    demo._handle("yes")
    assert printing(demo) == [("dilution:1", 2, ()), ("dilution:2", 4, ())]
    assert demo.state.config["dilution"]["enabled"] is True


def test_14_a_change_that_removes_the_conflict_closes_the_question(tmp_path):
    demo, _, output = safety_session(tmp_path, {
        "Move the dilution series to column 10.": change({"dilution": "modify"},
                                                         {"op": "set", "path": "dilution.plate_column", "value": "10"}),
    })
    demo._handle("Move the dilution series to column 10.")
    assert demo.safety is not None                    # not applied yet: still open
    demo._handle("yes")
    assert demo.safety is None                        # A11 is no longer a well the dilution step fills
    assert any("no longer applies" in line for line in output)
    assert not any("safety question" in reason for reason in demo.run_block_reasons())


def test_14b_a_new_print_selection_never_drops_the_held_request_silently(tmp_path):
    demo, _, output = safety_session(tmp_path, {
        "Actually only print the second dilution once.": change({"printing": "replace"}, groups(("dilution:2", 1))),
    })
    held = demo.safety
    demo._handle("Actually only print the second dilution once.")
    assert demo.safety is held and demo.pending is not None             # still open; it says so
    assert any("Still open" in line for line in output)
    demo._handle("yes")                                                  # applies the proposal
    assert demo.safety is held                                           # the held request still needs the answer
    demo._handle("cancel")                                               # dropping it is the scientist's call
    assert demo.safety is None


@pytest.mark.parametrize("answer, reply", [
    ("yes", None),
    ("Yes, my sample is already in A11.", change({"printing": "replace", "dilution": "modify"}, groups(("A11", 2)),
                                                 enabled(False))),
    ("I already loaded the sample.", change({"dilution": "modify"}, enabled(False))),
])
def test_15_sample_already_there_turns_the_whole_dilution_step_off(tmp_path, answer, reply):
    demo, _, _ = safety_session(tmp_path, {answer: reply} if reply else {})
    demo._handle(answer)
    assert demo.safety is None and demo.pending is not None
    assert any("whole dilution step is off" in note for note in demo.pending.notes)
    demo._handle("yes")
    assert demo.state.config["dilution"]["enabled"] is False and printing(demo) == [("A11", 2, ())]


@pytest.mark.parametrize("answer, reply", [
    ("no", None),
    ("No, make the dilutions first.", change({"printing": "replace", "dilution": "modify"}, groups(("A11", 2)),
                                             enabled(True))),
    ("Those wells are empty.", change({"dilution": "modify"}, enabled(True))),
])
def test_16_wells_empty_makes_the_dilutions_and_prints_the_dilution(tmp_path, answer, reply):
    demo, _, _ = safety_session(tmp_path, {answer: reply} if reply else {})
    demo._handle(answer)
    assert demo.safety is None and demo.pending is not None
    demo._handle("yes")
    assert demo.state.config["dilution"]["enabled"] is True and printing(demo) == [("dilution:1", 2, ())]


def test_15b_the_answer_proposal_never_says_it_was_already_done(tmp_path):
    demo, _, _ = session(tmp_path, {
        "Print A11 twice.": {**change({"printing": "replace"}, groups(("A11", 2))),
                             "response": "This would print A11 twice."},
        "It is already there.": {**change({"printing": "replace", "dilution": "modify"}, groups(("A11", 2)),
                                          enabled(False)),
                                 "response": "I have applied your request and disabled the dilution step."},
    })
    demo._handle("Print A11 twice.")
    demo._handle("It is already there.")
    assert demo.pending is not None and demo.pending.explanation == "This would print A11 twice."
    assert any("whole dilution step is off" in note for note in demo.pending.notes)
    assert demo.state.revision == 0                                     # nothing is done before Apply


def test_16b_a_reply_that_does_not_say_is_never_read_as_an_answer(tmp_path):
    demo, _, _ = safety_session(tmp_path, {
        "Print it, I said.": change({"printing": "replace"}, groups(("A11", 2))),
        "Make the drops 2 each.": change({"printing": "modify"},
                                         {"op": "set", "path": "print.droplets_per_spot", "value": 2}),
    })
    demo._handle("Print it, I said.")                  # the same request again: asked again, nothing proposed
    assert demo.pending is None and demo.safety is not None
    demo._handle("Make the drops 2 each.")
    assert demo.safety is not None and demo.pending is not None
    assert demo.pending.after["dilution"]["enabled"] is True and demo.pending.after["print"].get("source") is None


def test_17_a_question_while_the_safety_question_is_open_leaves_it_open(tmp_path):
    demo, _, output = safety_session(tmp_path, {
        "What happens if I say the sample is already there?":
            question("Then no dilutions are made and A11 prints what is already there."),
    })
    held, snapshot = demo.safety, unchanged(demo)
    demo._handle("What happens if I say the sample is already there?")
    still(demo, snapshot)
    assert demo.safety is held and any("Still open" in line for line in output)


def test_safety_question_survives_a_clarification_and_cancel_closes_it(tmp_path):
    demo, _, _ = safety_session(tmp_path, {
        "Add a few drops.": {"kind": "clarification", "clarification": "How many drops per spot?",
                             "unresolved": ["print.droplets_per_spot"]},
    })
    held = demo.safety
    demo._handle("Add a few drops.")
    assert demo.clarifying is not None and demo.safety is held       # one question never replaces the other
    demo._handle("cancel")
    assert demo.clarifying is None and demo.safety is held
    demo._handle("cancel")
    assert demo.safety is None and demo.state.revision == 0


# ── TEST 18 / 19 / 29: questions never touch the plan or the waiting proposal ───────────────────────────────────────

def test_18_19_questions_change_nothing_and_a_follow_up_command_does(tmp_path):
    demo, _, _ = session(tmp_path, {
        "Can I print A11 five times?": question("Yes - dilution 1 would then print 5 times."),
        "What if I print B11 twice?": question("B11 would print twice."),
        "Yes, do that.": change({"printing": "replace"}, groups(("dilution:2", 2))),
    })
    for message in ("Can I print A11 five times?", "What if I print B11 twice?"):
        snapshot = unchanged(demo)
        demo._handle(message)
        still(demo, snapshot)
    say(demo, "Yes, do that.")
    assert printing(demo) == [("dilution:2", 2, ())]


A11_ROWS = {"op": "set", "path": "print.source", "source": "dilution:1", "value": {"rows": ["A", "C", "E", "G"]}}


def hypothetical(answer, *operations):
    return {"kind": "question", "response": answer, "edit_modes": {"printing": "modify"},
            "hypothetical": [dict(operation) for operation in operations]}


def test_could_i_is_kept_as_a_hypothetical_and_could_you_is_proposed(tmp_path):
    demo, model, _ = session(tmp_path, {
        "Could I move the dilutions to column 10?": hypothetical(
            "Yes - the series would then be in A10-H10.", {"op": "set", "path": "dilution.plate_column", "value": "10"}),
        "Could you move the dilutions to column 10?": change({"dilution": "modify"},
                                                              {"op": "set", "path": "dilution.plate_column",
                                                               "value": "10"}),
    })
    snapshot = unchanged(demo)
    demo._handle("Could I move the dilutions to column 10?")
    still(demo, snapshot)
    assert demo._hypothetical is not None
    demo._handle("Could you move the dilutions to column 10?")
    assert demo.pending is not None and demo.pending.after["dilution"]["plate_column"] == "10"
    assert "was NOT proposed or applied" in model.prompts[-1]           # the question's change was in view


def test_a_pronoun_question_keeps_the_focus_and_yes_do_that_applies_it_to_that_group(tmp_path):
    demo, model, _ = session(tmp_path, {
        "Only print A11 twice and B11 once.": change({"printing": "replace"}, groups(("dilution:1", 2), ("dilution:2", 1))),
        "Make A11 four times.": change({"printing": "modify"}, one("dilution:1", count=4)),
        "Can I put those on rows 1,3,5,7?": hypothetical("Yes - the A11 prints could go on rows 1, 3, 5 and 7.", A11_ROWS),
        "Yes, do that.": change({"printing": "modify"}, A11_ROWS),
    })
    say(demo, "Only print A11 twice and B11 once.")
    say(demo, "Make A11 four times.")
    snapshot = unchanged(demo)
    demo._handle("Can I put those on rows 1,3,5,7?")
    still(demo, snapshot)                                               # a question: nothing proposed or changed
    assert demo._focus["printing"] == ["print group 1: dilution 1 (1×, now in A11): 4 prints"]
    say(demo, "Yes, do that.")
    prompt = model.prompts[-1]
    assert "was NOT proposed or applied" in prompt and '"rows": ["A", "C", "E", "G"]' in prompt
    assert "RECENT FOCUS" in prompt and "print group 1: dilution 1" in prompt
    assert printing(demo) == [("dilution:1", None, ("A", "C", "E", "G")), ("dilution:2", 1, ())]
    assert not demo.state.config["print"].get("paper_rows")


def test_the_hypothetical_lasts_one_message_and_a_question_with_operations_keeps_them_only(tmp_path):
    demo, model, _ = session(tmp_path, {
        "Can I make A11 five times?": {"kind": "question", "response": "Yes.",
                                       "operations": [{"op": "set", "path": "print.source", "source": "dilution:1",
                                                       "value": {"count": 5}}]},
        "How many tips is that?": question("1 tip."),
        "What is the drop volume?": question("5 µL."),
    })
    snapshot = unchanged(demo)
    demo._handle("Can I make A11 five times?")
    still(demo, snapshot)                                               # operations on a question are never applied
    demo._handle("How many tips is that?")
    assert "was NOT proposed or applied" in model.prompts[-1]           # the very next message sees it
    demo._handle("What is the drop volume?")
    assert "was NOT proposed or applied" not in model.prompts[-1]       # and only that one


def test_29_a_question_about_the_waiting_proposal_sees_its_numbers_and_leaves_it(tmp_path):
    demo, model, _ = session(tmp_path, {
        "Only print A11 twice and B11 twice.": change({"printing": "replace"}, groups(("dilution:1", 2), ("dilution:2", 2))),
        "How many spots would this proposal produce?": question("4 spots."),
    })
    demo._handle("Only print A11 twice and B11 twice.")
    snapshot = unchanged(demo)
    demo._handle("How many spots would this proposal produce?")
    still(demo, snapshot)
    shown = spec(model)
    assert "IF PROPOSAL #1 IS APPLIED" in shown and "4 print position(s)" in shown


# ── TEST 20 / 21 / 26: references that do not resolve stay as said ───────────────────────────────────────────────────

def test_20_21_an_unresolved_dilution_stays_editable_and_resolves_later(tmp_path):
    config = load_config(DEFAULT_CONFIG)
    config["dilution"]["factors"] = [2, 4]
    demo, _, _ = session(tmp_path, {
        "Print only dilution 3 three times.": change({"printing": "replace"}, groups(("dilution:3", 3))),
        "Make that five prints instead.": change({"printing": "modify"}, one("dilution:3", count=5)),
        "Make three dilutions: 2x, 4x and 8x.": change({"dilution": "replace"}, factors([2, 4, 8])),
    }, config)
    say(demo, "Print only dilution 3 three times.")
    assert demo.state.config["dilution"]["factors"] == [2, 4]           # no factor invented
    assert not demo.state.compile().ok and any("dilution:3" in reason or "dilution 3" in reason
                                               for reason in demo.run_block_reasons())
    say(demo, "Make that five prints instead.")
    assert printing(demo) == [("dilution:3", 5, ())]
    say(demo, "Make three dilutions: 2x, 4x and 8x.")
    assert printing(demo) == [("dilution:3", 5, ())]                    # printing was not recreated
    compiled = demo.state.compile()
    assert compiled.ok and compiled.source_map[0]["source"] == "C11" and len(compiled.source_map[0]["positions"]) == 5


def test_25_26_dilution_modes_and_a_print_of_a_dilution_that_is_gone(tmp_path):
    config = load_config(DEFAULT_CONFIG)
    config["dilution"]["factors"] = [2, 4, 8]
    config["print"]["source"] = [{"source": "dilution:3", "count": 2}]
    demo, _, _ = session(tmp_path, {
        "Add a 10x dilution.": change({"dilution": "add"}, factors([10])),
        "Change the 4x to 5x.": change({"dilution": "modify"}, factors(5, item=4)),
        "Replace the dilution series with 3x and 6x.": change({"dilution": "replace"}, factors([3, 6])),
        "Use the second dilution instead.": change({"printing": "modify"},
                                                   {"op": "set", "path": "print.source", "source": "dilution:3",
                                                    "value": {"source": "dilution:2"}}),
    }, config)
    say(demo, "Add a 10x dilution.")
    assert demo.state.config["dilution"]["factors"] == [2, 4, 8, 10]
    say(demo, "Change the 4x to 5x.")
    assert demo.state.config["dilution"]["factors"] == [2, 5, 8, 10]
    say(demo, "Replace the dilution series with 3x and 6x.")
    assert demo.state.config["dilution"]["factors"] == [3, 6]
    assert printing(demo) == [("dilution:3", 2, ())]                    # never re-pointed to dilution 2
    assert not demo.state.compile().ok
    say(demo, "Use the second dilution instead.")
    assert printing(demo) == [("dilution:2", 2, ())] and demo.state.compile().ok


# ── TEST 27 / 28: revising a waiting proposal ─────────────────────────────────────────────────────────────────────────

def test_27_28_revisions_keep_earlier_edits_and_a_new_request_replaces_them(tmp_path):
    demo, _, _ = session(tmp_path, {
        "Only print A11 twice and B11 twice.": change({"printing": "replace"}, groups(("dilution:1", 2), ("dilution:2", 2))),
        "Make B11 five instead.": change({"printing": "modify"}, one("dilution:2", count=5), revises=True),
        "And C11 once too.": change({"printing": "add"}, groups(("dilution:3", 1)), revises=True),
        "Actually forget that and only print D11 three times.": change({"printing": "replace"},
                                                                       groups(("dilution:4", 3))),
    })

    def waiting():
        return [(item["source"], item.get("count")) for item in demo.pending.after["print"]["source"]]

    demo._handle("Only print A11 twice and B11 twice.")
    demo._handle("Make B11 five instead.")
    assert waiting() == [("dilution:1", 2), ("dilution:2", 5)]
    demo._handle("And C11 once too.")
    assert waiting() == [("dilution:1", 2), ("dilution:2", 5), ("dilution:3", 1)]
    demo._handle("Actually forget that and only print D11 three times.")
    assert waiting() == [("dilution:4", 3)] and demo.state.revision == 0


# ── TEST 22: the numbers the router quotes are Python's ───────────────────────────────────────────────────────────────

def test_22_counts_are_the_compiled_plans(tmp_path):
    config = load_config(DEFAULT_CONFIG)
    config["dilution"]["factors"] = [2, 4, 8]
    config["print"]["source"] = [{"source": "dilution:1", "count": 2}, {"source": "dilution:3", "count": 1}]
    config["print"]["droplets_per_spot"] = 2
    demo, _, _ = session(tmp_path, {}, config)
    compiled = demo.state.compile()
    plan = compiled.plan
    line = next(line for line in compiled_lines(compiled, demo.state.config, "NOW", "STATUS")
                if line.startswith("COUNTS"))
    transfers = [op for op in plan.operations if op.kind == "transfer"]
    dye = sum(op.volume_ul for op in transfers if op.role == "sample")
    assert f"{len(transfers)} dilution transfer(s)" in line
    assert f"{len(plan.print_positions)} print position(s)" in line and len(plan.print_positions) == 3
    assert f"{plan.total_drops} drop(s) printed" in line and plan.total_drops == 6
    assert f"{plan.tips_needed} tip pickup(s)" in line
    assert f"{dye:g} µL dye" in line or f"{dye:.1f} µL dye" in line


# ── compile determinism, and the semantic plan in the simulator ───────────────────────────────────────────────────────

def test_compile_is_deterministic_and_never_changes_the_plan(tmp_path):
    config = load_config(DEFAULT_CONFIG)
    config["print"]["source"] = [{"source": "dilution:2", "count": 3, "rows": ["B", "D", "F"]},
                                 {"source": "dilution:5", "drops": 3}]
    demo, _, _ = session(tmp_path, {}, config)
    before = deepcopy(demo.state.config)
    first, second = demo.state.compile(), demo.state.compile()
    assert first.source_map == second.source_map and first.errors == second.errors
    assert demo.state.config == before
