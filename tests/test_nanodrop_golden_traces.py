"""Golden motion traces of the current NanoDrop protocol (v19): the reference for any later engine.

A failure here means the commands the robot would receive changed. Do not regenerate a trace to make a test pass:
read the diff, decide whether the motion change is intended, and record that decision. Passing is software evidence
only: the traces freeze today's behavior, nothing here models liquid, tip state or paper height, and nothing here
contacts a robot.
"""
from __future__ import annotations

import json

import pytest

from tests.golden_traces.cases import CASES, compiled_config
from tests.golden_traces.generate import PHYSICAL_STATUS, SCHEMA, dump, load_golden, trace_path
from tests.golden_traces.recorder import (FIXED_TRASH, PINNED_PYTHON, TracingPipette, analysis_trace, comparable_analysis,
                                          expected_analysis, motion_trace, normalize_analysis, well_depth_mm)

NAMES = sorted(CASES)


def first_difference(stored: list, current: list) -> str:
    for index, (old, new) in enumerate(zip(stored, current)):
        if old != new:
            return f"command {index}: stored {old} but now {new}"
    return f"length changed: stored {len(stored)} commands, now {len(current)}"


def plain(value):
    return json.loads(json.dumps(value, sort_keys=True))


@pytest.mark.parametrize("name", NAMES)
def test_the_protocol_moves_as_the_stored_trace_says(name):
    """The stored config is replayed, so this judges the protocol alone, whatever the compiler does later."""
    golden = load_golden(name)
    assert golden["schema"] == SCHEMA
    motion = plain(motion_trace(golden["config"]))
    assert motion == golden["motion"], f"{name}: {first_difference(golden['motion'], motion)}"


@pytest.mark.parametrize("name", NAMES)
def test_the_compiler_still_produces_the_stored_config(name):
    """Judges the compiler alone. A change here with the test above passing moves the plan, not the protocol."""
    assert plain(compiled_config(name)) == load_golden(name)["config"], f"{name}: the compiled config changed"


@pytest.mark.parametrize("name", NAMES)
def test_the_fake_robot_agrees_with_opentrons_analysis(name):
    """The fast fake trace and the stored pinned analysis describe the same commands, field by field."""
    golden = load_golden(name)
    assert golden["analysis_errors"] == [], f"{name}: pinned analysis reported errors"
    expected = expected_analysis(golden["motion"])
    actual = comparable_analysis(golden["analysis"])
    assert actual == expected, f"{name}: {first_difference(actual, expected)}"


def test_every_trace_says_it_is_not_physical_validation():
    for name in NAMES:
        assert load_golden(name)["physical_status"] == PHYSICAL_STATUS


def test_the_corpus_records_every_kind_of_motion_detail_the_plan_requires():
    events = [event for name in NAMES for event in load_golden(name)["motion"]]
    commands = {event["cmd"] for event in events}
    assert {"load_labware", "load_instrument", "set_flow_rate", "pick_up_tip", "drop_tip", "return_tip", "aspirate",
            "dispense", "air_gap", "blow_out", "touch_tip", "delay"} <= commands
    aspirates = [event for event in events if event["cmd"] == "aspirate"]
    assert all(event["flow_ul_s"] is not None and event["z_mm"] is not None and event["slot"] for event in aspirates)
    dispenses = [event for event in events if event["cmd"] == "dispense"]
    assert all(event["flow_ul_s"] is not None and event["z_mm"] is not None for event in dispenses)
    assert any(event["push_out_ul"] for event in dispenses), "no print dispense recorded its push-out"
    assert any(event["cmd"] == "air_gap" and event["height_mm"] is not None for event in events)
    touches = [event for event in events if event["cmd"] == "touch_tip"]
    assert all({"radius", "v_offset_mm", "speed_mm_s", "well"} <= set(event) for event in touches)
    assert any(event["cmd"] == "delay" and event["seconds"] > 0 for event in events)
    assert all(event["mount"] for event in events if event["cmd"] == "load_instrument")
    assert any(event["cmd"] == "load_labware" and event["namespace"] for event in events)


def motion(name):
    return load_golden(name)["motion"]


def count(name, command):
    return sum(event["cmd"] == command for event in motion(name))


def on_paper(name):
    return [event["well"] for event in motion(name) if event["cmd"] == "dispense"
            and event["labware"] == "paper_print_96_flat"]


def test_the_corpus_covers_each_scenario_the_plan_names():
    assert count("default_eight_dilutions", "dispense") > 8 and len(set(on_paper("default_eight_dilutions"))) == 8
    assert on_paper("dilution_only") == []
    assert not any(event.get("labware", "").startswith("tuberack")
                   for event in motion("print_only_prepared") if event["cmd"] == "aspirate")
    assert len(set(on_paper("replicates"))) == 9
    assert count("stacked_drops", "delay") == 12 and len(set(on_paper("stacked_drops"))) == 3
    assert set(on_paper("explicit_source_map")) == {"A1", "B2", "H12", "D5"}
    assert count("tips_new_every_transfer", "pick_up_tip") > count("tips_per_liquid", "pick_up_tip") > 1
    assert count("default_eight_dilutions", "return_tip") == 1 and count("tips_single_dropped", "drop_tip") == 1
    assert count("no_air_gap_blow_out_or_shake", "air_gap") == 3, "the print cycle keeps its air gap"
    assert count("no_air_gap_blow_out_or_shake", "touch_tip") == 0
    assert {event["slot"] for event in motion("moved_deck_and_vials") if event["cmd"] == "load_labware"} >= {"1", "2", "8", "11"}


def test_small_remainder_case_really_rebalances():
    dye = [event["volume_ul"] for event in motion("small_remainder_rebalanced") if event["cmd"] == "aspirate"
           and event["well"] == "A2" and event["labware"].startswith("tuberack")]
    assert sum(dye) == pytest.approx(95.511, abs=0.01)
    assert dye[:-2] == [19.0] * (len(dye) - 2) and dye[-1] < 19.0 and dye[-2] < 19.0, "the last two transfers share"
    assert min(dye) >= 1.0, "no transfer is left under the P20 minimum"


def test_the_print_release_branches_are_all_recorded():
    released = motion("print_release_variations")
    drops = [event for event in released if event["cmd"] == "dispense" and event["labware"] == "paper_print_96_flat"]
    assert drops and all(event["push_out_ul"] is None for event in drops)
    assert not any(event["cmd"] == "delay" for event in released)
    assert not any(event["cmd"] == "blow_out" and event["labware"] == "paper_print_96_flat" for event in released)
    assert {event["z_mm"] for event in motion("paper_release_at_0_5_mm")
            if event["cmd"] == "dispense" and event["labware"] == "paper_print_96_flat"} == {0.5}


def test_other_physical_variations_change_the_trace():
    shake = [event for event in motion("mixing_and_shake_variations") if event["cmd"] == "touch_tip"]
    assert {(event["radius"], event["v_offset_mm"], event["speed_mm_s"]) for event in shake} == {(0.8, -2.0, 40.0)}
    assert {event["ul_s"] for event in motion("flow_rates_changed") if event["cmd"] == "set_flow_rate"} == {5.0, 7.0}
    assert len(set(on_paper("many_replicates_overflow"))) == 42, "replicates past the paper edge are placed, not dropped"
    assert not {"A1", "B1", "C1"} & set(on_paper("second_run_avoids_printed_positions"))
    assert count("several_tips_returned", "return_tip") == count("several_tips_returned", "pick_up_tip") > 1
    assert count("dry_run_moves_nothing", "aspirate") == 0 and count("dry_run_moves_nothing", "pick_up_tip") == 0


def test_a_changed_height_is_detected_as_a_motion_change():
    """The guard itself: a real change to a physical value must not pass as equal."""
    config = load_golden("replicates")["config"]
    before = motion_trace(config)
    config["print"]["z_mm"] = float(config["print"]["z_mm"]) + 0.1
    assert motion_trace(config) != before


def test_a_setting_the_recorder_does_not_understand_fails_loudly():
    pipette = TracingPipette("p20_single_gen2", "left", [], [])
    with pytest.raises(AssertionError, match="default_speed"):
        pipette.default_speed = 50


def test_regenerating_needs_the_explicit_flag(monkeypatch):
    from tests.golden_traces import generate

    monkeypatch.setattr("sys.argv", ["generate"])
    with pytest.raises(SystemExit):
        generate.main()


def test_stored_files_are_exactly_what_the_writer_produces():
    for name in NAMES:
        text = trace_path(name).read_text(encoding="utf-8")
        assert text.replace("\r\n", "\n") == dump(load_golden(name)), f"{name}: stored file was edited by hand"


def test_well_depth_comes_from_the_labware_definition():
    assert well_depth_mm("corning_96_wellplate_360ul_custom") == pytest.approx(10.67)


@pytest.mark.skipif(not PINNED_PYTHON.exists(), reason="pinned Opentrons 7.0.2 simulator not installed")
@pytest.mark.parametrize("name", ["default_eight_dilutions", "per_source_counts_and_rows"])
def test_pinned_analysis_still_matches_the_stored_trace(name):
    """Slow: re-runs Opentrons itself on two representative plans."""
    golden = load_golden(name)
    result = analysis_trace(golden["config"])
    assert result["errors"] == []
    actual = plain(result["commands"])
    assert actual == golden["analysis"], f"{name}: {first_difference(golden['analysis'], actual)}"


def test_normalize_analysis_keeps_every_parameter_and_replaces_ids():
    analysis = {"labware": [{"id": "lw1", "loadName": "plate"}], "commands": [
        {"commandType": "custom", "params": {"message": "skip me"}},
        {"commandType": "dropTip", "params": {"pipetteId": "p1", "labwareId": "lw1", "wellName": "A1", "homeAfter": False,
                                              "alternateDropLocation": True}},
        {"commandType": "aspirate", "result": {"position": {"x": 1, "y": 2, "z": 3}},
         "params": {"labwareId": "lw1", "wellName": "A1", "volume": 2.5, "flowRate": 3,
                    "wellLocation": {"origin": "top", "offset": {"x": 0, "y": 0, "z": -1}}}}]}
    assert normalize_analysis(analysis) == [
        {"cmd": "dropTip", "labware": "plate", "wellName": "A1", "homeAfter": False, "alternateDropLocation": True},
        {"cmd": "aspirate", "labware": "plate", "wellName": "A1", "volume": 2.5, "flowRate": 3.0, "origin": "top",
         "offset_mm": [0.0, 0.0, -1.0], "position_mm": [1.0, 2.0, 3.0]}]


def test_trash_and_return_to_the_rack_are_different_commands():
    trash = expected_analysis([{"cmd": "drop_tip", "tip": "A1"}])
    returned = expected_analysis([{"cmd": "return_tip", "rack": "rack", "slot": "9", "tip": "A1"}])
    assert trash == [{"cmd": "dropTip", "labware": FIXED_TRASH, "wellName": "A1"}]
    assert trash != returned
