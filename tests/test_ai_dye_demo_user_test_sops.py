"""The five user-test SOPs (docs/ai_dye_demo/user_testing/): expected states, scripted user paths, usability fixes.

* Every expected final state (docs/ai_dye_demo/user_testing/expected/) names every conversation-editable setting, is
  reachable through ExperimentState, validates, and runs on the recording fake OT-2 exactly as planned.
* Every scripted user path (src/agents/dye_demo/redteam/sop_paths.py: clean, confused, change of mind, questions, and
  several changes at once for SOPs 4 and 5) is replayed through the real DemoSession with the red-team invariants on
  every turn, and the configuration of every run meets the SOP (src/agents/dye_demo/sop_check.py).
* The safeguards the SOPs rely on happen at the turn each path expects.
* The usability gaps found while writing the SOPs stay fixed.

Simulation only: the session's executor is a recording function; nothing contacts a robot.
"""
from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.dye_demo.intent import TurnContext, analyze_turn, negated_step, step_off_request
from src.agents.dye_demo.llm import LLMClient
from src.agents.dye_demo.model import DEFAULT_CONFIG, EDITABLE_FIELDS, REPO, load_config
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.harness import replay
from src.agents.dye_demo.redteam.invariants import protocol_mismatches
from src.agents.dye_demo.redteam.sop_paths import PATHS, SOP5_START, START_CONFIGS, change, said
from src.agents.dye_demo.sop_check import (
    check_runs,
    expected_paths,
    load_expected,
    session_run_configs,
    start_config,
)
from src.agents.dye_demo.session import DemoSession, SessionSettings
from src.agents.dye_demo.state import PREPARED_FROM_PLAN, ExperimentState, listed_paper_columns

DEFAULT = load_config(DEFAULT_CONFIG)
PATH_IDS = [(sop, name) for sop, paths in PATHS.items() for name in paths]


def kind(text: str, config: dict | None = None) -> str:
    return analyze_turn(text, TurnContext(config=config or DEFAULT)).kind


def talk(tmp_path: Path, *messages, config: dict | None = None) -> dict:
    items = [message if isinstance(message, dict) else {"text": message} for message in messages]
    return replay(items, workdir=tmp_path, keep_transcript=True, return_session=True, config=config)


def output(result: dict, turn: int) -> str:
    return result["transcript"][turn - 1]["output"]


def events(result: dict, turn: int, kind_: str) -> list[dict]:
    return [event for event in result["transcript"][turn - 1]["events"] if event["type"] == kind_]


# ── expected states ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", expected_paths(), ids=[path.stem for path in expected_paths()])
def test_every_expected_run_names_every_editable_setting(path):
    expected = load_expected(int(path.stem.split("_")[1]))
    assert path.name.startswith(f"sop_{expected['sop']:02d}_") and 1 <= expected["difficulty"] <= 5
    for run in expected["runs"]:
        named = set(run.get("settings") or {}) | set(run.get("choices") or {}) | set(expected.get("ignored") or [])
        assert named == set(EDITABLE_FIELDS), f"{path.name} {run['name']}: {set(EDITABLE_FIELDS) ^ named}"
        assert not set(run.get("settings") or {}) & set(run.get("choices") or {})


def test_the_sops_start_from_the_default_plan_they_describe():
    """The SOP documents describe this starting plan; if the default changes, the SOPs must be rewritten."""
    config = DEFAULT
    assert {role: spec["slot"] for role, spec in config["deck"].items()} == {"tuberack": 7, "plate": 4, "paper": 5,
                                                                            "tiprack": 9}
    assert (config["materials"]["water"]["vial"], config["materials"]["dye"]["vial"]) == ("A1", "A2")
    assert config["dilution"]["factors"] == [1, 2, 3, 4, 6, 8, 12, 16]
    assert (str(config["dilution"]["plate_column"]), config["dilution"]["start_row"],
            config["dilution"]["total_volume_ul"]) == ("11", "A", 150.0)
    assert (config["print"]["droplet_volume_ul"], config["print"]["droplets_per_spot"], config["print"]["replicates"],
            config["print"]["paper_start_column"]) == (5.0, 1, 1, 1)
    assert config["mixing"]["reps"] == 2 and config["mixing"]["volume_ul"] == 15.0
    assert config["tips"] == {"start_tip": "A1", "return_tips": False, "policy": "per_liquid"}


def test_sop5_start_config_is_the_default_with_the_tip_rack_in_slot_8():
    sop5 = load_config(SOP5_START)
    expected = deepcopy(DEFAULT)
    expected["deck"]["tiprack"]["slot"] = 8
    assert sop5 == expected, "copy the default into the SOP 5 start config again and set the tip rack to slot 8"
    assert load_expected(5)["start_config"] == "configs/workflows/user_test_sops/ai_dye_demo_sop05_start.yaml"
    assert START_CONFIGS[5] == SOP5_START


def _changes(*pairs):
    return [{"path": path, "value": value, "evidence": ""} for path, value in pairs]


def _through_the_engine(sop: int) -> list[dict]:
    """The SOP's end state(s), built with ExperimentState as a conversation would (every change proposed and applied)."""
    state = ExperimentState(start_config(load_expected(sop)))

    def apply(pairs, request, physical=None):
        state.apply(state.propose(_changes(*pairs), request=request, physical=physical), operator="test")
        return deepcopy(state.config)

    if sop == 1:
        return [apply([("dilution.factors", [2, 4, 8, 16]), ("dilution.total_volume_ul", "200 uL"),
                       ("materials.sample.vial", "B1")], "dilutions 2x, 4x, 8x and 16x, 200 uL total, dye vial B1")]
    if sop == 2:
        return [apply([("dilution.factors", [5, 10, 20]), ("print.droplets_per_spot", 2), ("print.paper_start_column", 2)],
                      "dilutions 5x, 10x and 20x, 2 drops on each spot from paper column 2")]
    if sop == 3:
        return [apply([("dilution.factors", [3, 6, 12]), ("dilution.plate_column", "3"), ("dilution.start_row", "D"),
                       ("print.replicates", 2), ("print.paper_start_column", 3)],
                      "dilutions 3x, 6x and 12x in plate column 3 from row D, 2 replicate columns from paper column 3")]
    if sop == 4:
        first = apply([("dilution.factors", [4, 8, 16, 32]), ("dilution.total_volume_ul", "200 uL"),
                       ("dilution.plate_column", "6"), ("print.replicates", 2)],
                      "dilutions 4x, 8x, 16x and 32x, 200 uL total, plate column 6, 2 replicate columns")
        second = apply([("dilution.enabled", False), ("dilution.prepared_volume_ul", "190 uL"),
                        ("print.droplets_per_spot", 3), ("print.paper_start_column", 4), ("tips.start_tip", "G1")],
                       "the dilutions are already made, 190 uL left, 3 drops, paper column 4, tip G1",
                       physical={"dilutions_prepared": PREPARED_FROM_PLAN})
        return [first, second]
    apply([("deck.tiprack.slot", 11), ("deck.plate.slot", 8)], "move the tip rack to slot 11 and the plate to slot 8")
    return [apply([("dilution.factors", [3, 9, 27]), ("dilution.total_volume_ul", "180 uL"), ("print.droplets_per_spot", 2),
                   ("print.replicates", 2), ("print.paper_start_column", 6), ("tips.start_tip", "A2"),
                   ("tips.policy", "new_tip_every_transfer")],
                  "dilutions 3x, 9x and 27x, 180 uL total, 2 drops, 2 replicate columns from paper column 6, tip A2, "
                  "new tip every transfer")]


@pytest.mark.parametrize("sop", [1, 2, 3, 4, 5])
def test_expected_end_states_are_reachable_valid_and_run_as_planned(sop):
    configs = _through_the_engine(sop)
    failed = [finding for finding in check_runs(configs, load_expected(sop)) if not finding.ok]
    assert not failed, failed
    for config in configs:
        assert protocol_mismatches(config) == []


def test_the_checker_fails_what_the_sops_forbid():
    sop5 = _through_the_engine(5)[0]
    swapped = deepcopy(sop5)
    swapped["deck"]["tiprack"]["slot"] = 4                 # a valid deck, but SOP 5 needs slot 4 empty
    assert [finding.requirement for finding in check_runs([swapped], load_expected(5)) if not finding.ok] == \
        ["deck.tiprack.slot is one of 1, 2, 3, 6, 9, 10, 11"]
    first, second = _through_the_engine(4)
    assert [finding.run for finding in check_runs([first], load_expected(4)) if not finding.ok] == ["session"]
    reused = deepcopy(second)
    reused["tips"]["start_tip"] = "A1"                     # the second printing would reuse run 1's tips
    unmet = [finding.requirement for finding in check_runs([first, reused], load_expected(4)) if not finding.ok]
    assert "tips.start_tip is G1 or a later tip in rack order" in unmet
    assert any(requirement.startswith("plan tips_exclude") for requirement in unmet)
    lab_owned = deepcopy(_through_the_engine(1)[0])
    lab_owned["print"]["z_mm"] = 0.5
    assert [finding.requirement for finding in check_runs([lab_owned], load_expected(1)) if not finding.ok] == \
        ["lab-owned settings are those of the starting configuration"]


class _QueuedModel:
    """READY for the startup check, then the recorded interpretation replies in order."""

    def __init__(self, replies):
        self.replies = list(replies)

    def invoke(self, messages):
        if "Respond READY" in messages[-1][1]:
            return SimpleNamespace(content="READY")
        return SimpleNamespace(content=self.replies.pop(0))


def test_the_scorer_reads_a_finished_session_folder(tmp_path, capsys):
    """A session run the way a tester runs one (recording executor instead of the simulator), scored by the CLI."""
    path = PATHS[1]["clean"]
    replies = [call["reply"] for message in path for call in message.get("llm", []) if call["kind"] == "interpret"]
    inputs = [message["text"] for message in path]

    def read(prompt):
        if not inputs:
            raise EOFError
        return inputs.pop(0)

    folder = tmp_path / "session"
    settings = SessionSettings(simulate=True, config_source=DEFAULT_CONFIG, working_config=tmp_path / "working.yaml",
                               run_dir=folder, session_label="User test SOP 1", operator="Tester",
                               skip_llm_startup=True, raise_errors=True)
    session = DemoSession(settings, load_config(DEFAULT_CONFIG), llm=LLMClient(lambda: _QueuedModel(replies)),
                          executor=lambda config_path, simulate, log: 0, input_fn=read, output_fn=lambda text: None)
    assert session.run() == 0 and (folder / "executed_config_run1.yaml").is_file()

    spec = importlib.util.spec_from_file_location("check_ai_dye_demo_sop", REPO / "scripts" / "check_ai_dye_demo_sop.py")
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    assert scorer.main(["--sop", "1", "--session", str(folder)]) == 0
    assert "RESULT: PASS" in capsys.readouterr().out
    assert scorer.main(["--sop", "2", "--session", str(folder), "--failures-only"]) == 1
    printed = capsys.readouterr().out
    assert "RESULT: FAIL" in printed and "FAIL  dilution.factors is [5, 10, 20]" in printed
    assert scorer.main(["--sop", "1", "--config", str(tmp_path / "working.yaml")]) == 0


# ── scripted user paths ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def replays(tmp_path_factory):
    results = {}
    for sop, name in PATH_IDS:
        results[(sop, name)] = replay(PATHS[sop][name], workdir=tmp_path_factory.mktemp(f"sop{sop}_{name}"),
                                      keep_transcript=True, return_session=True, config=load_config(START_CONFIGS[sop]))
    return results


def test_every_sop_has_the_required_user_paths():
    for sop, paths in PATHS.items():
        assert {"confused", "change_of_mind", "questions"} <= set(paths)
        assert {"clean", "one_at_a_time"} & set(paths)
    assert "multi_change" in PATHS[4] and "multi_change" in PATHS[5]


@pytest.mark.parametrize("sop, name", PATH_IDS, ids=[f"sop{sop}-{name}" for sop, name in PATH_IDS])
def test_scripted_user_path_reaches_the_expected_final_state(replays, sop, name):
    result = replays[(sop, name)]
    session = result["session"]
    assert not result["violations"], result["violations"]
    assert session.pending is None and session.clarifying is None
    expected = load_expected(sop)
    assert len(session.state.runs) == len(expected["runs"])
    failed = [finding for finding in check_runs(session_run_configs(session), expected) if not finding.ok]
    assert not failed, failed


@pytest.mark.parametrize("sop, name, turn, text", [
    (1, "confused", 1, "Factor 1× 2× 3× 4×"),                               # "4 dilutions" keeps the first four factors
    (1, "confused", 5, "You gave 200 without a unit"),
    (1, "confused", 8, 'You said "vial 5". Vials are named A1-B4.'),
    (1, "change_of_mind", 4, "replaces #2"),
    (2, "confused", 1, "The current plan has no 5×, 10×, 20× dilutions"),
    (2, "confused", 2, "nothing in this session records that those wells already hold the dilutions"),
    (2, "confused", 8, "did not mention the replicate paper columns"),
    (3, "confused", 1, 'If you meant a column, say "plate column 3"'),
    (3, "confused", 4, "8 dilutions starting at row D run past row H"),      # the series is still 8 long
    (3, "confused", 7, "Paper columns 3 | 4"),
    (3, "confused", 9, 'You said "plate." Which plate do you mean?'),
    (3, "confused", 10, "physically move the 96-well dilution plate from Slot 4 to Slot 3"),
    (4, "confused", 3, "different drop counts per column need two runs"),
    (4, "confused", 7, "DILUTIONS made in this run Dilutions 4 in plate column 6 (rows A-D)"),   # would remake them
    (4, "one_at_a_time", 10, "Saying yes records plate wells A6-D6 as already holding the dilutions"),
    (5, "clean", 1, "Slot 8 is currently occupied by the P20 tip rack."),
    (5, "clean", 1, '"move the tip rack to slot 1 and the dilution plate to slot 8"'),
    (5, "clean", 1, "Not OFF DECK: every step needs P20 tips."),
    (5, "confused", 2, "Slot 8 is currently occupied by the P20 tip rack."),
    (5, "confused", 3, "the P20 tip rack is OFF DECK, but every step needs P20 tips"),
    (5, "confused", 8, "You gave 180 without a unit"),
])
def test_the_safeguards_happen_where_each_path_expects(replays, sop, name, turn, text):
    row = replays[(sop, name)]["transcript"][turn - 1]
    # compared with runs of spaces collapsed, so a plan row's column alignment does not matter
    assert " ".join(text.split()) in " ".join(row["output"].split()), \
        f"SOP {sop} {name} turn {turn} ({row['text']!r}):\n{row['output']}"


def test_listing_a_singular_column_pair_is_not_flagged(replays):
    proposal = [event for event in replays[(3, "confused")]["transcript"][6]["events"] if event["type"] == "proposal"]
    assert proposal and proposal[0]["unverified"] == []


# ── usability gaps found while writing the SOPs ─────────────────────────────────

@pytest.mark.parametrize("text", ["Start at row B.", "Start the dilutions at row D.", "I need 2x, 4x, 8x and 16x.",
                                  "Sorry, I was wrong, the dye is in vial B1.", "Stack the three drops on the same spot.",
                                  "Print each dilution twice on the same spot."])
def test_short_natural_requests_are_instructions(text):
    assert kind(text) == "instruction"


def test_a_row_alone_is_a_value_and_its_answer_is_not_lost(tmp_path):
    result = talk(tmp_path, said("Make three dilutions starting at row C.", change("dilution.factors", None, "three",
                                                                                  op="set_count", count=3),
                                 change("dilution.start_row", "C", "row C")), "yes")
    assert result["session"].state.config["dilution"]["start_row"] == "C"


def test_an_answer_to_a_what_should_it_be_question_joins_the_request(tmp_path):
    result = talk(tmp_path, "Set the replicate paper columns.", said("2", change("print.replicates", 2, "2")), "yes")
    assert "What should the replicate paper columns be?" in output(result, 1)
    assert events(result, 2, "proposal") and result["session"].state.config["print"]["replicates"] == 2


def test_listing_side_by_side_paper_columns_states_the_replicate_count(tmp_path):
    result = talk(tmp_path, said("Print in paper columns 3 and 4.", change("print.paper_start_column", 3, "columns 3"),
                                 change("print.replicates", 2, "columns 3 and 4")), "yes")
    assert result["session"].state.config["print"]["replicates"] == 2
    assert result["session"].state.config["print"]["paper_start_column"] == 3


def test_columns_that_are_not_side_by_side_need_two_runs(tmp_path):
    result = talk(tmp_path, said("Print in paper columns 1 and 3.", change("print.paper_start_column", 1, "columns 1"),
                                 change("print.replicates", 2, "columns 1 and 3")))
    assert "Paper columns 1, 3 are not side by side" in output(result, 1) and not events(result, 1, "proposal")


@pytest.mark.parametrize("text, groups", [("paper columns 3 and 4", [[3, 4]]), ("column 3 and 4", [[3, 4]]),
                                          ("columns 3, 4 and 5", [[3, 4, 5]]), ("columns 2-5", [[2, 3, 4, 5]]),
                                          ("plate columns 3 and 4", []), ("paper column 2 and 3 drops", []),
                                          ("columns 1 and 2, then columns 4 and 5", [[1, 2], [4, 5]])])
def test_listed_paper_columns(text, groups):
    assert listed_paper_columns(text) == groups


def test_printing_the_named_dilutions_depends_on_the_plan():
    unsupported = analyze_turn("Print the 5x, 10x and 20x dilutions onto paper column 2.", TurnContext(config=DEFAULT))
    assert unsupported.kind == "unsupported"
    assert unsupported.details["unsupported"][0].startswith("The current plan has no 5×, 10×, 20× dilutions")
    planned = deepcopy(DEFAULT)
    planned["dilution"]["factors"] = [5, 10, 20]
    assert kind("Print the 5x, 10x and 20x dilutions onto paper column 2.", planned) == "instruction"
    some = analyze_turn("Print the 5x and 10x dilutions onto paper column 2.", TurnContext(config=planned))
    assert some.kind == "unsupported" and "cannot print only one or some of them" in some.details["unsupported"][0]
    every = analyze_turn("Print the 5x, 10x and 20x dilutions.", TurnContext(config=planned))
    assert every.kind == "unsupported" and "already prints" in every.details["unsupported"][0]


@pytest.mark.parametrize("text", ["Print 1 drop in paper columns 1 and 2 and 3 drops in paper columns 4 and 5.",
                                  "Print one drop in paper columns 1 and 2, then three stacked drops in columns 4 and 5."])
def test_different_drop_counts_per_column_are_explained(text):
    analysis = analyze_turn(text, TurnContext(config=DEFAULT))
    assert analysis.kind == "unsupported" and "need two runs" in analysis.details["unsupported"][0]


@pytest.mark.parametrize("text, step", [("Don't print anything this time.", "print"), ("Do not print.", "print"),
                                        ("Don't make the dilutions.", "dilution"), ("Don't move the plate.", None)])
def test_negated_steps(text, step):
    assert negated_step(text) == step


@pytest.mark.parametrize("text, step", [("No printing this run, just dilutions.", "print"), ("Dilutions only.", "print"),
                                        ("No dilutions this time, only printing.", "dilution"), ("thanks", None)])
def test_leaving_a_step_out_without_an_instruction(text, step):
    assert step_off_request(text) == step


@pytest.mark.parametrize("text", ["Don't print anything this time.", "No printing this run, just dilutions."])
def test_saying_not_to_print_explains_that_the_plan_still_prints(tmp_path, text):
    result = talk(tmp_path, {"text": text, "label": {"category": "negation", "may_propose": False}})
    assert "this plan still prints in this run" in output(result, 1) and 'say "skip printing"' in output(result, 1)
    record = result["session"].turns[0]
    assert record["state_before"] == record["state_after"] and not events(result, 1, "answer")


def test_keeping_the_plate_where_it_is_removes_only_the_plate_move(tmp_path):
    result = talk(tmp_path, said("Move the dilution plate to slot 8 and make three dilutions, 2x, 4x and 8x.",
                                 change("deck.plate.slot", 8, "plate to slot 8"),
                                 change("dilution.factors", [2, 4, 8], "2x, 4x and 8x")),
                  "Actually, keep the plate where it is.", "yes")
    assert [event["paths"] for event in events(result, 2, "proposal")] == [["dilution.factors"]]
    config = result["session"].state.config
    assert config["deck"]["plate"]["slot"] == 4 and config["dilution"]["factors"] == [2, 4, 8]
    idle = talk(tmp_path, "Actually, keep the plate where it is.")
    assert "96-well dilution plate location stays Slot 4" in output(idle, 1)


def test_a_statement_about_used_tips_shows_which_setting_is_meant(tmp_path):
    result = talk(tmp_path, said("The first column of tips is already used, start at A2.",
                                 change("tips.start_tip", "A2", "start at A2")), "yes")
    assert result["session"].state.config["tips"]["start_tip"] == "A2"


def test_a_prepared_dilution_report_keeps_the_volume_left_in_each_well(tmp_path):
    analysis = analyze_turn("The four dilutions from the first run are already made, each well has about 190 µL left.",
                            TurnContext(config=DEFAULT))
    assert analysis.kind == "physical_report" and analysis.facts[0].kind == "dilutions_prepared"
    assert analysis.actionable == "each well has about 190 µL left"
    result = talk(tmp_path, "We already made all the dilutions.", "yes",
                  said("Each well now holds about 140 uL.", change("dilution.prepared_volume_ul", "140 uL", "140 uL")),
                  "yes")
    assert result["session"].state.config["dilution"]["prepared_volume_ul"] == 140.0


def test_changing_your_mind_about_a_waiting_value_refers_to_that_proposal(tmp_path):
    result = talk(tmp_path, said("Make each dilution 250 uL total.", change("dilution.total_volume_ul", "250 uL", "250")),
                  said("Actually no, make it 200 uL instead.", change("dilution.total_volume_ul", "200 uL", "200 uL")),
                  "yes")
    assert "replaces #1" in output(result, 2)
    assert result["session"].state.config["dilution"]["total_volume_ul"] == 200.0


def test_a_slot_named_for_the_dilutions_suggests_the_column_words(tmp_path):
    result = talk(tmp_path, said("Make the dilutions in slot 3.", change("deck.plate.slot", 3, "slot 3")))
    assert 'say "plate column 3"' in output(result, 1) and 'or "paper column 3"' in output(result, 1)
    assert result["session"].state.revision == 0


@pytest.mark.parametrize("text", ["Call the dye crystal violet.", "Name the dye crystal violet."])
def test_naming_the_dye_keeps_the_name(tmp_path, text):
    result = talk(tmp_path, said(text, change("materials.sample.label", "crystal violet", "crystal violet")), "yes")
    assert not events(result, 1, "clarification")
    assert [event["unverified"] for event in events(result, 1, "proposal")] == [[]]
    assert result["session"].state.config["materials"]["dye"]["label"] == "crystal violet"


def test_a_serial_dilution_request_suggests_giving_the_factors():
    analysis = analyze_turn("Make a two-fold serial dilution of the dye, 2x to 16x.", TurnContext(config=DEFAULT))
    assert analysis.kind == "unsupported" and "give the fold factors instead" in analysis.details["unsupported"][0]


def test_the_sop_paths_carry_model_replies_only_as_recorded_json():
    for paths in PATHS.values():
        for messages in paths.values():
            for message in messages:
                for call in message.get("llm", []):
                    if call["kind"] == "interpret":
                        assert json.loads(call["reply"])["changes"]


def test_sop_end_states_print_what_the_sop_documents_say():
    plans = {sop: [build_plan(config) for config in _through_the_engine(sop)] for sop in (1, 2, 3, 4, 5)}
    assert [op.destination for op in plans[3][0].operations if op.kind == "print"] == ["D3", "D4", "E3", "E4", "F3", "F4"]
    assert plans[4][1].total_drops == 24 and [tip.tip for tip in plans[4][1].tips] == ["G1", "H1", "A2", "B2"]
    assert plans[5][0].tips_needed == 34 and plans[5][0].next_tip == "C6"
    assert plans[1][0].vial_use_ul == {"solvent": 612.5, "sample": 187.5}
