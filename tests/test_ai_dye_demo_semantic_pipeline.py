"""The semantic pipeline of the GUI demo: TurnInterpretation -> patch operations -> PlanSpec -> compile -> run.

A scripted router stands in for the model (the language reading is the model's job; these tests check what Python
does with a reading): questions never touch the plan, edits are incremental patches, an unfinished plan can be
edited, prints follow their dilutions by reference, and the exact print map exists only once the plan is compiled.
No robot is contacted; the last test runs the compiled protocol in the pinned Opentrons simulator.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.agents.dye_demo.compiler import compile_plan
from src.agents.dye_demo.conversation import parse_turn
from src.agents.dye_demo.llm import LLMClient
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.patches import PlanOperation, apply_operations
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.simulate_states import BASE_PROTOCOL, PINNED_SIMULATOR, _builder
from src.agents.dye_demo.session import DemoSession, SessionSettings


class Router:
    """Replies by message; records what the model was shown."""

    def __init__(self, replies):
        self.replies = replies
        self.seen, self.prompts = [], []

    def invoke(self, messages):
        message = messages[-1][1].split("SCIENTIST'S MESSAGE:\n")[-1].strip()
        self.seen.append(message)
        self.prompts.append(messages[-1][1])
        return SimpleNamespace(content=json.dumps(self.replies[message]))


class FakeExecutor:
    def __init__(self):
        self.configs = []
        self.last_status = {"started": True, "robot_status": "succeeded"}

    def check_robot(self):
        return None

    def __call__(self, path, _simulate, _log):
        self.configs.append(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
        return 0


def session(tmp_path, replies, config=None):
    model, executor, output = Router(replies), FakeExecutor(), []
    settings = SessionSettings(simulate=True, config_source=DEFAULT_CONFIG, working_config=tmp_path / "working.yaml",
                               run_dir=tmp_path / "run", session_label="semantic", operator="Tester",
                               skip_llm_startup=True, run_button="Run on OT-2", llm_first=True, raise_errors=True)
    demo = DemoSession(settings, deepcopy(config) if config is not None else load_config(DEFAULT_CONFIG),
                       llm=LLMClient(lambda: model), executor=executor, input_fn=lambda _prompt: "yes",
                       output_fn=output.append, sleep=lambda _: None)
    demo.operator = "Tester"
    demo.request_run_confirmation = lambda: output.append("CONFIRM RUN")
    return demo, model, executor, output


def change(*operations, response="", revises=False):
    return {"kind": "plan_change", "response": response, "revises": revises,
            "operations": [{"op": op, "path": path, **({} if value is ... else {"value": value})}
                           for op, path, value in operations]}


def question(answer):
    return {"kind": "question", "response": answer}


def say(demo, *messages, apply=True):
    """Each message, and Apply on the proposal it makes."""
    for message in messages:
        revision = demo.state.revision
        demo._handle(message)
        if apply and demo.pending is not None:
            demo._handle("yes")
            assert demo.state.revision == revision + 1


def printed(compiled):
    return [(op.source, op.destination, op.droplets) for op in compiled.plan.operations if op.kind == "print"]


THREE_IN_COLUMN_10 = change(("set", "dilution.factors", [2, 4, 8]), ("set", "dilution.plate_column", "10"))


# ── TEST 1: the exact print map exists only once the plan is compiled ─────────────────────────────────────────────

def test_placement_stays_semantic_until_the_run_compiles_it(tmp_path):
    demo, _, executor, _ = session(tmp_path, {
        "Make 2x, 4x and 8x dilutions in column 10.": THREE_IN_COLUMN_10,
        "Print rows 1, 3 and 5.": change(("set", "print.paper_rows", ["A", "C", "E"])),
    })
    say(demo, "Make 2x, 4x and 8x dilutions in column 10.", "Print rows 1, 3 and 5.")
    spec = demo.state.config
    assert spec["print"]["paper_rows"] == ["A", "C", "E"]
    assert spec["print"].get("source_map") is None                              # editing never generated one
    assert yaml.safe_load((tmp_path / "working.yaml").read_text(encoding="utf-8"))["print"].get("source_map") is None
    compiled = demo.state.compile()
    assert compiled.ok, compiled.errors
    assert compiled.source_map == [{"source": "A10", "positions": ["A1"]}, {"source": "B10", "positions": ["C1"]},
                                   {"source": "C10", "positions": ["E1"]}]
    demo._run()                                                                   # simulate: fake executor, no robot
    executed = executor.configs[-1]
    assert executed["print"]["source_map"] == compiled.source_map and executed["print"].get("paper_rows") is None
    assert demo.state.config["print"].get("source_map") is None                   # the PlanSpec is still semantic


# ── TEST 2 / TEST 7: an unfinished plan answers questions and takes edits; only the run waits ─────────────────────

def test_question_and_edit_on_an_unfinished_plan(tmp_path):
    demo, model, _, output = session(tmp_path, {
        "Make 2x and 5x dilutions.": change(("set", "dilution.factors", [2, 5])),
        "Print on paper row A.": change(("set", "print.paper_rows", ["A"])),
        "What dilution factors do I currently have?": question("You have two dilutions: 2x in A11 and 5x in B11."),
        "Make it 3 drops each.": change(("set", "print.droplets_per_spot", 3)),
    })
    say(demo, "Make 2x and 5x dilutions.", "Print on paper row A.")
    assert demo.state.revision == 2                                               # applied although it cannot run
    blocked = demo.run_block_reasons()
    assert any("2 dilutions would print on 1 paper row" in reason for reason in blocked)
    before = (demo.state.revision, demo.state.fingerprint())
    shown = len(output)
    demo._handle("What dilution factors do I currently have?")
    assert (demo.state.revision, demo.state.fingerprint()) == before and demo.pending is None
    answer = "\n".join(output[shown:])
    assert "2x in A11 and 5x in B11" in answer
    assert not re.search(r"print map|paper position|cannot continue|did not change anything", answer, re.I)
    assert "RUN STATUS: not runnable yet" in model.prompts[-1]                    # the model knows; it was not asked
    say(demo, "Make it 3 drops each.")                                            # an unrelated edit still applies
    assert demo.state.config["print"]["droplets_per_spot"] == 3
    demo._handle("Print on paper row A.")
    assert demo.pending is None                                                   # already set: nothing to propose


# ── TEST 3: a print follows its dilution when the dilutions move ──────────────────────────────────────────────────

def test_print_follows_its_dilution_to_its_new_wells(tmp_path):
    demo, _, _, _ = session(tmp_path, {
        "Make 2x, 4x and 8x dilutions in column 10.": THREE_IN_COLUMN_10,
        "Print the first dilution in rows 1, 3 and 5.": change(("set", "print.source", ["dilution:1"]),
                                                               ("set", "print.paper_rows", ["A", "C", "E"])),
        "Move the dilutions from rows A-C to rows C-E.": change(("set", "dilution.rows", ["C", "D", "E"])),
        "Actually move them to column 11.": change(("set", "dilution.plate_column", "11")),
    })
    say(demo, "Make 2x, 4x and 8x dilutions in column 10.", "Print the first dilution in rows 1, 3 and 5.")
    assert printed(demo.state.compile()) == [("A10", "A1", 1), ("A10", "C1", 1), ("A10", "E1", 1)]
    say(demo, "Move the dilutions from rows A-C to rows C-E.")
    assert demo.state.config["print"]["source"] == ["dilution:1"]                 # nothing downstream was rewritten
    assert demo.state.history[-1]["changes"] == [
        {"path": "dilution.rows", "label": "Dilution plate rows", "before": None, "after": ["C", "D", "E"],
         "kind": "requested", "why": "", "verified": True, "concern": ""}]
    assert printed(demo.state.compile()) == [("C10", "A1", 1), ("C10", "C1", 1), ("C10", "E1", 1)]
    say(demo, "Actually move them to column 11.")
    compiled = demo.state.compile()
    assert compiled.ok and printed(compiled) == [("C11", "A1", 1), ("C11", "C1", 1), ("C11", "E1", 1)]
    assert [well.well for well in compiled.plan.wells] == ["C11", "D11", "E11"]


# ── TEST 4: follow-up references are the model's; Python keeps each edit to what it names ────────────────────────

def test_follow_up_references_build_the_semantic_state(tmp_path):
    demo, _, _, _ = session(tmp_path, {
        "Make 3 dilutions.": {"kind": "plan_change",
                              "operations": [{"op": "set_count", "path": "dilution.factors", "count": 3}]},
        "Print the first one in rows 1, 3 and 5.": change(("set", "print.source", ["dilution:1"]),
                                                          ("set", "print.paper_rows", ["A", "C", "E"])),
        "Make those 2 drops each.": change(("set", "print.droplets_per_spot", 2)),
        "Actually use the second dilution.": change(("set", "print.source", ["dilution:2"])),
    })
    say(demo, "Make 3 dilutions.", "Print the first one in rows 1, 3 and 5.", "Make those 2 drops each.",
        "Actually use the second dilution.")
    spec = demo.state.config
    assert spec["dilution"]["factors"] == [1, 2, 3]
    assert spec["print"]["source"] == ["dilution:2"] and spec["print"]["paper_rows"] == ["A", "C", "E"]
    assert spec["print"]["droplets_per_spot"] == 2 and spec["print"].get("source_map") is None
    assert printed(demo.state.compile()) == [("B11", "A1", 2), ("B11", "C1", 2), ("B11", "E1", 2)]


# ── TEST 5: a question in the middle of editing changes nothing; the next edit still works ───────────────────────

def test_question_mid_edit_then_edit(tmp_path):
    demo, model, _, output = session(tmp_path, {
        "Dilute A11 2x.": change(("set", "dilution.factors", [2]), ("set", "dilution.plate_column", "11"),
                                 ("set", "dilution.start_row", "A")),
        "Print it into rows 1 through 3.": change(("set", "print.source", ["A11"]),
                                                  ("set", "print.paper_rows", ["A", "B", "C"])),
        "Which well is that print coming from?": question("From plate well A11 - the 2x dilution (dilution 1)."),
        "Make it 2 drops each.": change(("set", "print.droplets_per_spot", 2)),
    })
    say(demo, "Dilute A11 2x.", "Print it into rows 1 through 3.")
    # A11 is where this run makes the dilution, so the print source is stored as that dilution, not as a fixed well
    assert demo.state.config["print"]["source"] == ["dilution:1"]
    revision = demo.state.revision
    demo._handle("Which well is that print coming from?")
    assert demo.state.revision == revision and demo.pending is None
    assert "A11 (dilution:1) -> 3 prints at A1-C1" in model.prompts[-1]           # the compiled view it answered from
    say(demo, "Make it 2 drops each.")
    assert printed(demo.state.compile()) == [("A11", "A1", 2), ("A11", "B1", 2), ("A11", "C1", 2)]


# ── TEST 6: a genuinely ambiguous reference is one short question, not a guess or a validation error ─────────────

def test_ambiguous_reference_asks_one_question(tmp_path):
    ask = {"kind": "clarification", "clarification": "Do you mean the second dilution (2x) or the second print "
                                                     "position (C1)?", "unresolved": ["print.drops_at"]}
    demo, model, _, output = session(tmp_path, {
        "Make 2x, 4x and 8x dilutions in column 10.": THREE_IN_COLUMN_10,
        "Print the first one in rows 1, 3 and 5.": change(("set", "print.source", ["dilution:1"]),
                                                          ("set", "print.paper_rows", ["A", "C", "E"])),
        "Make the second one 4 drops.": ask,
        "the second print position": change(("set", "print.drops_at", {"C1": 4})),
    })
    say(demo, "Make 2x, 4x and 8x dilutions in column 10.", "Print the first one in rows 1, 3 and 5.")
    revision, shown = demo.state.revision, len(output)
    demo._handle("Make the second one 4 drops.")
    said = "\n".join(output[shown:])
    assert "second dilution (2x) or the second print position" in said
    assert "did not change anything" not in said and demo.pending is None and demo.state.revision == revision
    assert demo.clarifying is not None and demo.run_block_reasons() == []          # an optional question
    demo._handle("the second print position")
    assert "You asked the scientist" in model.prompts[-1]                         # the question travelled with it
    assert demo.pending is not None and demo.clarifying is None
    demo._handle("yes")
    assert printed(demo.state.compile()) == [("A10", "A1", 1), ("A10", "C1", 4), ("A10", "E1", 1)]


# ── local failure, hard invariants, routing ───────────────────────────────────────────────────────────────────────

def test_a_bad_operation_fails_alone_and_physics_stays_hard():
    spec = load_config(DEFAULT_CONFIG)
    result = apply_operations(spec, [PlanOperation("set", "print.droplets_per_spot", 2),
                                     PlanOperation("set", "print.drops_at", {"A15": 3}),
                                     PlanOperation("set", "print.z_mm", 3.0),
                                     PlanOperation("set", "print.replicates", 2)])
    assert result.paths == ["print.droplets_per_spot", "print.replicates"]
    assert [what for what, _ in result.failed] == ["Drops at particular paper positions", "print.z_mm"]
    assert "A1-H12" in result.failed[0][1] and "lab-owned" in result.failed[1][1]


def test_partial_proposal_says_what_was_not_applied(tmp_path):
    demo, _, _, output = session(tmp_path, {
        "2 drops each and 3 drops on A15": change(("set", "print.droplets_per_spot", 2),
                                                  ("set", "print.drops_at", {"A15": 3})),
        "move the plate onto the paper": change(("set", "deck.plate.slot", 5)),
    })
    demo._handle("2 drops each and 3 drops on A15")
    assert demo.pending is not None and demo.pending.paths == ["print.droplets_per_spot"]
    assert any("NOT APPLIED" in line and "A15" in line for line in output)
    demo._handle("no")
    demo._handle("move the plate onto the paper")                                 # two labware in one slot: hard
    assert demo.pending is None and demo.state.revision == 0


def test_parse_turn_reads_kinds_and_the_earlier_shape():
    assert parse_turn('{"kind": "question", "response": "8", "operations": [{"path": "x", "value": 1}]}').operations == []
    old = parse_turn('{"route": "experiment_question", "changes": [{"path": "print.replicates", "value": 3}]}')
    assert old.kind == "plan_change" and old.operations[0]["value"] == 3
    assert parse_turn('{"route": "request_run"}').kind == "run_request"
    assert parse_turn("Just text.").kind == "question"
    assert parse_turn('{"kind": "undo", "revision": 2}').revision == 2


def test_undo_restores_stored_values(tmp_path):
    demo, _, _, _ = session(tmp_path, {
        "Make it 3 drops each.": change(("set", "print.droplets_per_spot", 3)),
        "undo that": {"kind": "undo"},
    })
    say(demo, "Make it 3 drops each.", "undo that")
    assert demo.state.config["print"]["droplets_per_spot"] == 1 and demo.state.revision == 2


def test_overflowing_replicates_are_placed_at_compile_time_not_stored():
    spec = load_config(DEFAULT_CONFIG)
    spec["print"].update(replicates=3, paper_start_column=12)
    compiled = compile_plan(spec, occupied={"A11"})                               # A11 was printed by a live run
    positions = [op.destination for op in compiled.plan.operations if op.kind == "print"]
    assert compiled.ok and len(positions) == 24 and len(set(positions)) == 24 and "A11" not in positions
    assert spec["print"].get("source_map") is None


# ── TEST 8: a combined dilution + print plan compiles, validates and runs in the Opentrons simulator ─────────────

_DISPENSE = re.compile(r"Dispensing\s+[\d.]+\s+uL\s+into\s+([A-H]\d{1,2})\s+of\s+.+?\s+on\s+(?:slot\s+)?(\d+)", re.I)


@pytest.mark.skipif(not PINNED_SIMULATOR.exists(), reason="pinned Opentrons 2.15 simulator not installed")
def test_compiled_combined_plan_runs_in_the_opentrons_simulator(tmp_path):
    spec = load_config(DEFAULT_CONFIG)
    operations = [PlanOperation("set", "dilution.factors", [2, 4, 8]), PlanOperation("set", "dilution.plate_column", "10"),
                  PlanOperation("set", "print.source", ["dilution:1", "dilution:3"]),
                  PlanOperation("set", "print.paper_rows", ["B", "F"]),
                  PlanOperation("set", "print.droplets_per_spot", 2), PlanOperation("set", "print.replicates", 2),
                  PlanOperation("set", "tips.policy", "per_liquid")]
    spec = apply_operations(spec, operations).after
    compiled = compile_plan(spec)
    assert compiled.ok, compiled.errors
    assert compiled.source_map == [{"source": "A10", "positions": ["B1", "B2"]},
                                   {"source": "C10", "positions": ["F1", "F2"]}]
    builder = _builder()
    config = deepcopy(compiled.config)
    run_modes = config.pop("run_modes", {})
    run_modes["dry_run"] = False
    config["protocol_version"] = 19
    path = tmp_path / "semantic_combined.py"                                      # never generated/protocols/current
    path.write_text(builder.build_source(BASE_PROTOCOL.read_text(encoding="utf-8"), config, run_modes),
                    encoding="utf-8")
    ok, output = builder.simulate(path, str(PINNED_SIMULATOR))
    assert ok, output[-1500:]
    paper = str(config["deck"]["paper"]["slot"])
    drops = [match.group(1) for match in map(_DISPENSE.search, output.splitlines()) if match and match.group(2) == paper]
    assert drops == [op.destination for op in compiled.plan.operations if op.kind == "print" for _ in range(op.droplets)]
    plate = str(config["deck"]["plate"]["slot"])
    made = {match.group(1) for match in map(_DISPENSE.search, output.splitlines()) if match and match.group(2) == plate}
    assert made == {"A10", "B10", "C10"}
    assert build_plan(spec).print_positions == compiled.plan.print_positions


# ── the terminal uses the same pipeline; its typed commands stay commands ─────────────────────────────────────────

def test_terminal_semantic_pipeline_keeps_typed_commands_and_run(tmp_path):
    demo, model, executor, output = session(tmp_path, {
        "Make it 2 drops each.": change(("set", "print.droplets_per_spot", 2)),
        "go ahead and run it": {"kind": "run_request"},
    })
    demo.settings.run_button = ""                       # the terminal: no page buttons
    demo.request_run_confirmation = None
    demo._handle("plan")
    assert model.seen == [] and any("CURRENT PLAN" in line for line in output)
    say(demo, "Make it 2 drops each.")
    demo._handle("run")                                 # typed run: the run itself, compiled
    assert len(executor.configs) == 1 and executor.configs[0]["print"]["source_map"]
    demo._handle("go ahead and run it")                 # read by the model: one explicit yes, then the same run
    assert len(executor.configs) == 2 and model.seen == ["Make it 2 drops each.", "go ahead and run it"]
