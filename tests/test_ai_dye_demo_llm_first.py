"""Focused, robot-free checks for the GUI demo's LLM-first path."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from src.agents.dye_demo.llm import LLMClient, parse_interpretation
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.session import DemoSession, SessionSettings, _Clarifying
from src.agents.dye_demo.state import TIPS_USED, WELL_VOLUMES


class Router:
    def __init__(self, replies):
        self.replies = replies
        self.seen = []

    def invoke(self, messages):
        message = messages[-1][1].split("SCIENTIST'S MESSAGE:\n")[-1].strip()
        self.seen.append(message)
        answer = self.replies[message]
        return SimpleNamespace(content=json.dumps(answer))


class FakeExecutor:
    def __init__(self):
        self.calls = 0
        self.last_status = {"started": True, "robot_status": "succeeded"}

    def check_robot(self):
        return None

    def __call__(self, _path, _simulate, _log):
        self.calls += 1
        return 0


def session(tmp_path: Path, replies: dict, *, live=False, config=None):
    model = Router(replies)
    executor = FakeExecutor()
    output = []
    settings = SessionSettings(simulate=not live, config_source=DEFAULT_CONFIG,
                               working_config=tmp_path / "working.yaml", run_dir=tmp_path / "run",
                               session_label="LLM first", operator="Tester", skip_llm_startup=True,
                               run_button="Run on OT-2", llm_first=True, raise_errors=True)
    demo = DemoSession(settings, deepcopy(config) if config is not None else load_config(DEFAULT_CONFIG),
                       llm=LLMClient(lambda: model),
                       executor=executor, input_fn=lambda _prompt: "yes", output_fn=output.append,
                       sleep=lambda _: None)
    demo.operator = "Tester"
    demo.request_run_confirmation = lambda: output.append("CONFIRM RUN")
    return demo, model, executor, output


def change(path, value):
    return {"route": "experiment_change", "changes": [{"path": path, "value": value}]}


def test_parser_keeps_structured_changes_when_route_label_is_wrong():
    result = parse_interpretation(json.dumps({"route": "experiment_question", "answer": "Okay",
                                               "changes": [{"path": "print.droplets_per_spot", "value": 3}]}))
    assert result.route == "experiment_change"
    assert result.changes[0]["value"] == 3


def test_only_explicit_value_contradictions_are_vetoed(tmp_path):
    replies = {
        "3 replicates": change("print.replicates", 8),
        "35 µL drop": change("print.droplet_volume_ul", 5),
        "put the dye in vial A3": {"route": "experiment_change", "changes": [
            {"path": "materials.sample.vial", "value": "B3", "evidence": "dye in vial A3"}]},
        "print A11 on A3": {"route": "experiment_change", "changes": [
            {"path": "print.source_map", "value": [{"source": "A11", "destination": "B9"}]}]},
    }
    demo, _, _, output = session(tmp_path, replies)
    for words in replies:
        demo._handle(words)
        assert demo.pending is None
    assert sum("but the proposal used" in line for line in output) >= 3
    assert any("positions in the proposal differ" in line for line in output)


def test_polite_request_bare_volume_sparse_drops_and_run_reach_the_model(tmp_path):
    replies = {
        "Could you make three drops per spot?": change("print.droplets_per_spot", 3),
        "make the drops 5": change("print.droplet_volume_ul", 5),
        "1 drop on A1 and 3 drops on A2": {"route": "experiment_change", "changes": [
            {"path": "dilution.enabled", "value": False},
            {"path": "print.source_map", "value": [
                {"source": "A11", "destination": "A1", "drops": 1},
                {"source": "A11", "destination": "A2", "drops": 3}]}]},
        "go ahead and run the current experiment": {"route": "request_run"},
        "yes": {"route": "approve_proposal"},
    }
    demo, model, executor, output = session(tmp_path, replies)
    for words in list(replies)[:3]:
        assert demo._handle(words)
        if words == "make the drops 5":
            assert demo.state.config["print"]["droplet_volume_ul"] == 5
            assert demo.pending is None  # already the current value, without a unit challenge
            continue
        assert demo.pending is not None, (words, output)
        assert demo._handle("yes")
    plan = build_plan(demo.state.config)
    assert [(op.destination, op.droplets) for op in plan.operations if op.kind == "print"] == [
        ("A1", 1), ("A2", 3)]
    assert demo._handle("go ahead and run the current experiment")
    assert "CONFIRM RUN" in output
    assert executor.calls == 0
    assert [message for message in model.seen if message != "yes"] == list(replies)[:4]


def test_new_destination_with_drops_uses_current_identified_source(tmp_path):
    config = load_config(DEFAULT_CONFIG)
    config["dilution"]["enabled"] = False
    config["print"]["source_map"] = [{"source": "A11", "positions": ["A1"]}]
    replies = {
        "3 drops on B1": change("print.source_map", [
            {"source": "A11", "destination": "A1"},
            {"source": "A11", "destination": "B1", "drops": 3}]),
        "yes": {"route": "approve_proposal"},
    }
    demo, model, _, output = session(tmp_path, replies, config=config)
    demo._handle("3 drops on B1")
    assert demo.pending is not None, output
    demo._handle("yes")
    prints = [(op.destination, op.droplets) for op in build_plan(demo.state.config).operations
              if op.kind == "print"]
    assert prints == [("A1", 1), ("B1", 3)]
    assert model.seen[0] == "3 drops on B1"


def test_exact_sparse_print_and_column_twelve_anchor(tmp_path):
    replies = {
        "print A11 on A3, C3 and E8": {"route": "experiment_change", "changes": [
            {"path": "dilution.enabled", "value": False},
            {"path": "print.source_map", "value": [
                {"source": "A11", "destination": "A3"},
                {"source": "A11", "destination": "C3"},
                {"source": "A11", "destination": "E8"}]}]},
        "3 replicates starting in column 12": {"route": "experiment_change", "changes": [
            {"path": "print.replicates", "value": 3},
            {"path": "print.paper_start_column", "value": 12}]},
        "yes": {"route": "approve_proposal"},
    }
    demo, model, _, output = session(tmp_path, replies)
    demo._handle("print A11 on A3, C3 and E8")
    assert demo.pending is not None, output
    demo._handle("yes")
    assert [op.destination for op in build_plan(demo.state.config).operations if op.kind == "print"] == [
        "A3", "C3", "E8"]
    # A separate plan tests the anchor without an existing explicit map.
    demo, anchor_model, _, anchor_output = session(tmp_path / "anchor", replies)
    demo._handle("3 replicates starting in column 12")
    assert demo.pending is not None, anchor_output
    demo._handle("yes")
    positions = [op.destination for op in build_plan(demo.state.config).operations if op.kind == "print"]
    assert len(positions) == 24 and len(set(positions)) == 24
    assert "A12" in positions and "A11" in positions and "A10" in positions
    assert model.seen[0] == "print A11 on A3, C3 and E8"
    assert anchor_model.seen[0] == "3 replicates starting in column 12"


def test_no_replicates_shaking_off_and_prepared_samples(tmp_path):
    config = load_config(DEFAULT_CONFIG)
    config["print"]["replicates"] = 2
    replies = {
        "no replicates": {"route": "experiment_change", "changes": [
            {"path": "print.replicates", "op": "none"}]},
        "turn shaking off": change("liquid_handling.well_plate_shake.enabled", False),
        "the dilutions are already made, just print them": change("dilution.enabled", False),
        "yes": {"route": "approve_proposal"},
    }
    demo, model, _, output = session(tmp_path, replies, config=config)
    for words in list(replies)[:3]:
        demo._handle(words)
        assert demo.pending is not None, (words, output)
        demo._handle("yes")
    assert demo.state.config["print"]["replicates"] == 1
    assert demo.state.config["liquid_handling"]["well_plate_shake"]["enabled"] is False
    assert demo.state.config["dilution"]["enabled"] is False
    assert model.seen[::2] == list(replies)[:3]


def test_repeat_on_new_paper_phrase_resets_occupancy(tmp_path):
    replies = {
        "print those same dilutions again on new paper": {"route": "experiment_change", "physical_actions": [
            {"action": "replace_paper"}], "changes": [{"path": "dilution.enabled", "value": False}]},
        "yes": {"route": "approve_proposal"},
    }
    demo, model, _, output = session(tmp_path, replies)
    demo.state.printed_positions.add("A1")
    demo._handle("print those same dilutions again on new paper")
    assert demo.pending is not None, output
    demo._handle("yes")
    assert demo.state.physical["paper_id"] == 2
    assert demo.state.printed_positions == set()
    assert model.seen[0] == "print those same dilutions again on new paper"


def test_replacement_actions_are_reviewed_and_isolate_each_physical_object(tmp_path):
    replies = {
        "the plate was swapped for a clean one": {"route": "experiment_change", "physical_actions": [
            {"action": "replace_plate"}]},
        "I put down fresh paper": {"route": "experiment_change", "physical_actions": [
            {"action": "replace_paper"}]},
        "I replaced the tip rack": {"route": "experiment_change", "physical_actions": [
            {"action": "replace_tip_rack"}]},
        "yes": {"route": "approve_proposal"},
    }
    demo, _, _, _ = session(tmp_path, replies)
    demo.state.physical[TIPS_USED] = ["A1"]
    demo.state.physical[WELL_VOLUMES] = {"A11": {"volume_ul": 90, "plate_id": 1}}
    demo.state.printed_positions.add("A1")
    assert any("already contain liquid" in reason for reason in demo.state.run_blockers())
    for words in list(replies)[:3]:
        assert demo._handle(words)
        assert demo.pending is not None
        assert demo._handle("yes")
        if words == "the plate was swapped for a clean one":
            assert not any("already contain liquid" in reason for reason in demo.state.run_blockers())
    assert demo.state.physical["plate_id"] == 2
    assert demo.state.physical["paper_id"] == 2
    assert demo.state.physical["tip_rack_id"] == 2
    assert demo.state.physical[WELL_VOLUMES] == {}
    assert demo.state.printed_positions == set()
    assert demo.state.physical[TIPS_USED] == []


def test_refill_one_well_does_not_erase_another_well(tmp_path):
    replies = {"A11 now contains 90 µL": {"route": "experiment_change", "physical_actions": [
                   {"action": "refill_well", "well": "A11", "volume_ul": 90}]},
               "yes": {"route": "approve_proposal"}}
    demo, _, _, _ = session(tmp_path, replies)
    demo.state.physical[WELL_VOLUMES] = {"C11": {"volume_ul": 70, "plate_id": 1}}
    demo._handle("A11 now contains 90 µL")
    demo._handle("yes")
    assert demo.state.recorded_volumes() == {"A11": 90, "C11": 70}


def test_optional_question_can_be_superseded_by_run_but_physical_question_cannot(tmp_path):
    replies = {"please start the robot now": {"route": "request_run"}}
    demo, _, executor, output = session(tmp_path, replies)
    demo.clarifying = _Clarifying("layout", "layout", [], purpose="intent")
    assert not demo.clarification_blocks_run()
    demo._handle("please start the robot now")
    assert "CONFIRM RUN" in output and demo.clarifying is None and executor.calls == 0
    demo.clarifying = _Clarifying("sample", "sample", [], purpose="made_not_printed")
    assert demo.clarification_blocks_run()
    demo._handle("please start the robot now")
    assert demo.clarifying is not None and output[-1].startswith("agent> Answer or cancel")


def test_three_fake_live_runs_auto_advance_tips_and_reset_new_plate_paper(tmp_path):
    replies = {
        "fresh paper; print the prepared dilutions": {"route": "experiment_change", "physical_actions": [
            {"action": "replace_paper"}], "changes": [{"path": "dilution.enabled", "value": False}]},
        "new plate and new paper": {"route": "experiment_change", "physical_actions": [
            {"action": "replace_plate"}, {"action": "replace_paper"}]},
        "yes": {"route": "approve_proposal"},
    }
    demo, _, executor, _ = session(tmp_path, replies, live=True)
    demo._run()  # fake executor; no robot or HTTP client is used
    assert demo.pending is None
    assert demo.state.config["tips"]["start_tip"] == "B1"
    demo._handle("fresh paper; print the prepared dilutions")
    demo._handle("yes")
    assert demo.state.run_blockers() == []
    demo._run()
    assert demo.pending is None
    assert demo.state.config["tips"]["start_tip"] == "C1"
    demo._handle("new plate and new paper")
    demo._handle("yes")
    assert demo.state.config["dilution"]["enabled"] is True
    assert demo.state.run_blockers() == []
    demo._run()
    assert executor.calls == 3
    assert demo.state.physical["plate_id"] == 2
    assert demo.state.physical["paper_id"] == 3
    assert demo.state.config["tips"]["start_tip"] == "D1"
