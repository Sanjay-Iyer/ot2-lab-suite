"""Edit semantics of the semantic pipeline: a change ADDs to, MODIFIEs or REPLACEs what one scope of the plan has.

The router labels each change with its scope's edit mode ("edit_modes": {"printing": "replace"}); Python executes the
mode deterministically (patches.py) and never reads the scientist's words to decide it. A scripted router stands in
for the model. No robot is contacted.
"""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

from src.agents.dye_demo.conversation import parse_turn
from src.agents.dye_demo.llm import LLMClient
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.patches import PlanOperation, apply_operations, scope_of
from src.agents.dye_demo.session import DemoSession, SessionSettings, revise_request


class Router:
    def __init__(self, replies):
        self.replies, self.seen, self.systems = replies, [], []

    def invoke(self, messages):
        message = messages[-1][1].split("SCIENTIST'S MESSAGE:\n")[-1].strip()
        self.seen.append(message)
        self.systems.append(messages[0][1])
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
                               run_dir=tmp_path / "run", session_label="edit-modes", operator="Tester",
                               skip_llm_startup=True, run_button="Run on OT-2", llm_first=True, raise_errors=True)
    demo = DemoSession(settings, deepcopy(config) if config is not None else load_config(DEFAULT_CONFIG),
                       llm=LLMClient(lambda: model), executor=Executor(), input_fn=lambda _p: "yes",
                       output_fn=output.append, sleep=lambda _: None)
    demo.operator = "Tester"
    demo.request_run_confirmation = lambda: output.append("CONFIRM RUN")
    return demo, model, output


def samples():
    """Samples already in plate wells A11-H11 (nothing is diluted in this run)."""
    config = load_config(DEFAULT_CONFIG)
    config["dilution"]["enabled"] = False
    return config


def edit(modes, *operations, response="", revises=False, kind="plan_change"):
    return {"kind": kind, "response": response, "revises": revises, "edit_modes": modes,
            "operations": [dict(operation) for operation in operations]}


def groups(*items):
    return {"op": "set", "path": "print.source", "value": [dict(zip(("source", "count"), item)) for item in items]}


def one(source, **value):
    return {"op": "set", "path": "print.source", "source": source, "value": value}


def factors(value, **extra):
    return {"op": "set", "path": "dilution.factors", "value": value, **extra}


def apply(config, modes, *operations):
    stamped = parse_turn(json.dumps(edit(modes, *operations))).operations
    return apply_operations(config, [PlanOperation.from_raw(item) for item in stamped])


def sources(config):
    return [(item["source"], item.get("count")) if isinstance(item, dict) else (item, None)
            for item in config["print"]["source"]]


def answer(demo, message):
    """One message and Apply on its proposal; returns the proposal's notes."""
    revision = demo.state.revision
    demo._handle(message)
    assert demo.pending is not None, f"no proposal for {message!r}"
    notes = list(demo.pending.notes)
    demo._handle("yes")
    assert demo.state.revision == revision + 1
    return notes


# ── the router's labels become each operation's mode, by the operation's own scope ──────────────────────────────────

def test_parse_turn_stamps_each_operation_with_its_scope_mode():
    turn = parse_turn(json.dumps(edit({"printing": "replace", "dilutions": "add"}, groups(("dilution:1", 2)),
                                      factors([10]), {"path": "deck.plate.slot", "value": 2},
                                      {"path": "print.replicates", "value": 2, "edit_mode": "modify"})))
    assert [operation.get("edit_mode") for operation in turn.operations] == ["replace", "add", None, "modify"]
    assert turn.edit_modes == {"printing": "replace", "dilution": "add"}
    # one mode for named scopes, and one mode for every scope its operations edit
    turn = parse_turn(json.dumps({"kind": "plan_change", "edit_mode": "add", "scope": "dilution",
                                  "operations": [factors([10]), groups(("dilution:1", 2))]}))
    assert [operation.get("edit_mode") for operation in turn.operations] == ["add", None]
    turn = parse_turn(json.dumps({"kind": "plan_change", "edit_mode": "MODIFY",
                                  "operations": [factors([10]), groups(("dilution:1", 2))]}))
    assert [operation.get("edit_mode") for operation in turn.operations] == ["modify", "modify"]
    # an unknown mode is no mode (the op's plain meaning), and a question never carries operations
    turn = parse_turn(json.dumps({"kind": "plan_change", "edit_modes": {"printing": "overwrite"},
                                  "operations": [{**groups(("A11", 1)), "edit_mode": "merge"}]}))
    assert "edit_mode" not in turn.operations[0] and turn.edit_modes == {}
    turn = parse_turn(json.dumps({"kind": "question", "response": "Yes, you can.", "edit_modes": {"printing": "add"},
                                  "operations": [groups(("A11", 4))]}))
    assert turn.is_question and turn.operations == [] and turn.edit_modes == {}
    assert [scope_of(path) for path in ("print.source", "paper_rows", "dilution.rows", "mixing.reps", "tips.policy")] \
        == ["printing", "printing", "dilution", "dilution", "plan"]


# ── printing: REPLACE on the startup default, ADD, MODIFY, REPLACE again ───────────────────────────────────────────

def test_naming_sources_replaces_the_startup_default_selection():
    config = load_config(DEFAULT_CONFIG)                    # 8 dilutions, print.source unset: every dilution prints
    result = apply(config, {"printing": "replace"}, groups(("dilution:1", 3), ("dilution:2", 1), ("dilution:3", 2)))
    assert sources(result.after) == [("dilution:1", 3), ("dilution:2", 1), ("dilution:3", 2)]
    assert result.after["dilution"] == config["dilution"]                # another scope never changes
    assert any("no longer printing" in note for note in result.notes)
    # ADD/MODIFY on the default keep every dilution printing, and change only the one named
    added = apply(config, {"printing": "modify"}, one("dilution:2", count=4)).after
    assert len(sources(added)) == 8 and ("dilution:2", 4) in sources(added)


def test_add_modify_replace_on_a_chosen_selection():
    config = apply(samples(), {"printing": "replace"}, groups(("A11", 3), ("B11", 1))).after
    assert sources(config) == [("A11", 3), ("B11", 1)]
    # ADD keeps everything and adds; adding a source already printing updates its group, never duplicates it
    config = apply(config, {"printing": "add"}, groups(("C11", 2))).after
    assert sources(config) == [("A11", 3), ("B11", 1), ("C11", 2)]
    result = apply(config, {"printing": "add"}, groups(("C11", 4)))
    assert sources(result.after) == [("A11", 3), ("B11", 1), ("C11", 4)]
    assert any("not duplicated" in note for note in result.notes)
    # MODIFY changes only the item named
    config = apply(result.after, {"printing": "modify"}, one("B11", count=5)).after
    assert sources(config) == [("A11", 3), ("B11", 5), ("C11", 4)]
    # MODIFY of a source not printing adds it, and says so
    result = apply(config, {"printing": "modify"}, one("D11", count=1))
    assert sources(result.after)[-1] == ("D11", 1)
    assert any("D11 was not printing" in note for note in result.notes)
    # REPLACE: the items named are the whole selection; it says what no longer prints
    result = apply(result.after, {"printing": "replace"}, groups(("A11", 2)))
    assert sources(result.after) == [("A11", 2)]
    assert any("no longer printing" in note and "B11" in note for note in result.notes)


def test_replace_clears_the_print_wide_selection_it_replaces_unless_said_again():
    config = samples()
    config["print"]["paper_rows"] = ["A", "C"]
    config = apply(config, {"printing": "add"}, groups(("A11", 1), ("B11", 1))).after
    assert config["print"]["paper_rows"] == ["A", "C"]                   # ADD keeps the plan-wide rows
    replaced = apply(config, {"printing": "replace"}, groups(("C11", 2))).after
    assert sources(replaced) == [("C11", 2)] and not replaced["print"].get("paper_rows")
    kept = apply(config, {"printing": "replace"}, groups(("C11", 2)),
                 {"op": "set", "path": "print.paper_rows", "value": ["E"]}).after
    assert kept["print"]["paper_rows"] == ["E"]


# ── dilution: the same three modes, and printing is left alone ──────────────────────────────────────────────────

def test_dilution_modes_and_printing_is_untouched():
    config = load_config(DEFAULT_CONFIG)
    config["dilution"]["factors"] = [2, 5]
    config = apply(config, {"printing": "replace"}, groups(("dilution:1", 2), ("dilution:2", 1))).after
    printing = deepcopy(config["print"])
    config = apply(config, {"dilution": "add"}, factors([10])).after
    assert config["dilution"]["factors"] == [2, 5, 10]
    config = apply(config, {"dilution": "modify"}, factors(6, item=5)).after
    assert config["dilution"]["factors"] == [2, 6, 10]
    config = apply(config, {"dilution": "replace"}, factors([3, 9])).after
    assert config["dilution"]["factors"] == [3, 9]
    assert config["print"] == printing                    # prints follow dilution:1 and dilution:2 by reference


# ── the session: replace -> add -> modify -> replace -> a question changes nothing ─────────────────────────────────

FLOW = {
    "Only print A11 three times and B11 once.": edit({"printing": "replace"}, groups(("A11", 3), ("B11", 1))),
    "Also print C11 twice.": edit({"printing": "add"}, groups(("C11", 2))),
    "Actually make B11 four prints.": edit({"printing": "modify"}, one("B11", count=4)),
    "Just print C11 once, nothing else.": edit({"printing": "replace"}, groups(("C11", 1))),
    "Can I make C11 three prints?": {"kind": "question", "response": "Yes - C11 would then print 3 times."},
}


def test_session_executes_the_routers_modes_and_questions_change_nothing(tmp_path):
    demo, model, output = session(tmp_path, FLOW, config=samples())
    answer(demo, "Only print A11 three times and B11 once.")
    assert sources(demo.state.config) == [("A11", 3), ("B11", 1)]
    answer(demo, "Also print C11 twice.")
    assert sources(demo.state.config) == [("A11", 3), ("B11", 1), ("C11", 2)]
    answer(demo, "Actually make B11 four prints.")
    assert sources(demo.state.config) == [("A11", 3), ("B11", 4), ("C11", 2)]
    notes = answer(demo, "Just print C11 once, nothing else.")
    assert sources(demo.state.config) == [("C11", 1)]
    assert any("no longer printing" in note for note in notes)
    before, revision = deepcopy(demo.state.config), demo.state.revision
    demo._handle("Can I make C11 three prints?")
    assert demo.pending is None and demo.state.revision == revision and demo.state.config == before
    # the router's system prompt is the core contract followed by the NanoDrop skills
    assert all("SKILL nanodrop-edit-semantics" in system and "SKILL nanodrop-printing" in system
               for system in model.systems)
    logged = [json.loads(line) for line in demo.log.path.read_text(encoding="utf-8").splitlines()]
    [skills] = [record for record in logged if record.get("event") == "router_skills"]
    assert [skill["name"] for skill in skills["skills"]][:2] == ["nanodrop-edit-semantics", "nanodrop-printing"]


# ── revising a waiting proposal: items and ADD/MODIFY build on it ──────────────────────────────────────────────────

def test_revision_keys_by_item_and_composes_add_and_modify():
    config = samples()
    request = [{**groups(("A11", 3), ("B11", 1)), "edit_mode": "replace"}]
    merged = revise_request(config, request, [{**groups(("C11", 2)), "edit_mode": "add"}])
    assert [change.get("edit_mode") for change in merged] == ["replace", "add"]
    result = apply_operations(config, [PlanOperation.from_raw(change) for change in merged])
    assert sources(result.after) == [("A11", 3), ("B11", 1), ("C11", 2)]
    # an item replaces only the proposal's change to that same item
    request = merged + [{**one("B11", count=4), "edit_mode": "modify"}]
    merged = revise_request(config, request, [{**one("B11", count=5), "edit_mode": "modify"},
                                              {**one("C11", count=1), "edit_mode": "modify"}])
    assert len(merged) == 4 and [change["value"] for change in merged if change.get("source") == "B11"] == [{"count": 5}]
    result = apply_operations(config, [PlanOperation.from_raw(change) for change in merged])
    assert sources(result.after) == [("A11", 3), ("B11", 5), ("C11", 1)]
    # a whole REPLACE replaces every earlier print.source change, items included
    merged = revise_request(config, merged, [{**groups(("D11", 2)), "edit_mode": "replace"}])
    assert [change.get("source") for change in merged] == [None] and merged[0]["value"] == [{"source": "D11",
                                                                                                "count": 2}]


def test_revised_proposal_in_the_session_keeps_the_waiting_request(tmp_path):
    demo, _, _ = session(tmp_path, {
        "Only print A11 three times.": edit({"printing": "replace"}, groups(("A11", 3))),
        "And C11 twice too.": edit({"printing": "add"}, groups(("C11", 2)), revises=True),
    }, config=samples())
    demo._handle("Only print A11 three times.")
    assert demo.pending is not None
    answer(demo, "And C11 twice too.")
    assert sources(demo.state.config) == [("A11", 3), ("C11", 2)]


# ── the safety question about wells the dilution step would fill: asked, never assumed ─────────────────────────────

SAFETY = {
    "Print A11 twice.": edit({"printing": "replace"}, groups(("A11", 2))),
    "Print it, I said.": edit({"printing": "replace"}, groups(("A11", 2))),
    "No, make the dilutions first.": edit({"printing": "replace", "dilution": "modify"}, groups(("A11", 2)),
                                          {"op": "set", "path": "dilution.enabled", "value": True}),
}


def test_safety_question_is_asked_again_until_answered_never_auto_accepted(tmp_path):
    demo, _, output = session(tmp_path, SAFETY)                            # dilution step on: A11 is dilution 1
    demo._handle("Print A11 twice.")
    assert demo.pending is None and demo.safety is not None and "A11" in demo.safety.question
    # the same request again, without saying what is in the well: asked again, nothing is proposed
    demo._handle("Print it, I said.")
    assert demo.pending is None and demo.safety is not None and "A11" in demo.safety.question
    # answered in words, read by the router as its one field: the dilutions are made, the print is dilution 1
    demo._handle("No, make the dilutions first.")
    assert demo.pending is not None
    demo._handle("yes")
    assert demo.state.config["dilution"]["enabled"] is True
    assert sources(demo.state.config) == [("dilution:1", 2)]


def test_safety_question_yes_skips_the_dilutions(tmp_path):
    demo, _, _ = session(tmp_path, SAFETY)
    demo._handle("Print A11 twice.")
    demo._handle("yes")
    assert demo.pending is not None
    demo._handle("yes")
    assert demo.state.config["dilution"]["enabled"] is False
    assert sources(demo.state.config) == [("A11", 2)]
