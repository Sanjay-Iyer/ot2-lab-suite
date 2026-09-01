"""The conversational demo's guard rails: what the agent may and may not change."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from scripts.ai_dye_demo import (
    DEFAULT_CONFIG,
    UNSURE,
    _config_problems,
    _dilution_rows,
    _droplet_volumes,
    _extract_json,
    _paper_columns,
    _tip_names,
    _validate_ai_update,
    render_plan,
)


@pytest.fixture()
def config():
    return yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))


def test_default_is_eight_dilutions_printed_at_five_microlitres(config):
    assert config["protocol_version"] == 19
    assert config["pipette"]["name"] == "p20_single_gen2"
    assert config["deck"]["tiprack"]["load_name"] == "opentrons_96_tiprack_20ul"
    assert len(config["dilution"]["factors"]) == 8
    assert _droplet_volumes(config) == [5.0]
    assert _config_problems(config) == []


def test_default_is_live_unless_the_caller_simulates(config):
    assert config["run_modes"]["dry_run"] is False


def test_conversational_edits_are_accepted(config):
    updated = _validate_ai_update(config, {
        "deck": {"plate": {"slot": 6}},
        "dilution": {"factors": [1, 2, 5, 10], "start_row": "D", "plate_column": "3"},
        "materials": {"dye": {"vial": "A3"}},
        "print": {"droplet_volume_ul": 10.0, "paper_start_column": 3, "replicates": 2},
    })
    assert _dilution_rows(updated) == ["D", "E", "F", "G"]
    assert [spot["column"] for spot in _paper_columns(updated)] == [3, 4]
    assert _tip_names(updated, 6) == ["A1", "B1", "C1", "D1", "E1", "F1"]


def test_several_droplet_volumes_take_one_paper_column_each(config):
    updated = _validate_ai_update(config, {"print": {"droplet_volume_ul": [5.0, 10.0]}})
    assert [(s["column"], s["volume_ul"]) for s in _paper_columns(updated)] == [
        (1, 5.0), (2, 10.0)
    ]


@pytest.mark.parametrize("updates, message", [
    ({"pipette": {"name": "p300_single_gen2"}}, "blocked"),
    ({"safety": {"p20_max_volume_ul": 300}}, "blocked"),
    ({"run_modes": {"dry_run": True}}, "blocked"),
    ({"print": {"z_mm": 5.0}}, "calibrated"),
    ({"print": {"air_gap_ul": 0}}, "calibrated"),
    ({"print": {"post_dispense_delay_s": 0}}, "calibrated"),
    ({"dilution": {"max_transfer_ul": 200}}, "calibrated"),
])
def test_hardware_and_calibration_are_not_the_agents_to_change(config, updates, message):
    with pytest.raises(ValueError, match=message):
        _validate_ai_update(config, updates)


@pytest.mark.parametrize("updates, message", [
    ({"print": {"droplet_volume_ul": 25.0}}, "over the P20"),
    ({"print": {"droplet_volume_ul": 0.5}}, "under the P20"),
    ({"deck": {"plate": {"slot": 9}}}, "cannot share a deck slot"),
    ({"deck": {"plate": {"slot": 12}}}, "must be 1-11"),
    ({"dilution": {"factors": [1, 2, 3, 4, 5, 6, 7, 8, 9]}}, "1 to 8 dilutions"),
    ({"dilution": {"start_row": "F", "factors": [1, 2, 4, 8, 16, 32]}}, "past row H"),
    ({"dilution": {"factors": [1, 1000]}}, "under the P20"),
    ({"dilution": {"total_volume_ul": 400}}, "total_volume_ul must be in"),
    ({"materials": {"dye": {"vial": "A1"}}}, "different vials"),
    ({"print": {"droplet_volume_ul": [5, 10, 15], "paper_start_column": 11}},
     "past the paper"),
    ({"tips": {"start_tip": "H12"}}, "start from an earlier tip"),
])
def test_physically_impossible_requests_are_refused(config, updates, message):
    with pytest.raises(ValueError, match=message):
        _validate_ai_update(config, updates)


def test_rejected_edits_leave_the_config_untouched(config):
    before = deepcopy(config)
    with pytest.raises(ValueError):
        _validate_ai_update(config, {"print": {"droplet_volume_ul": 25.0}})
    assert config == before


def test_plan_names_both_steps_and_promises_no_robot_contact(config):
    text = render_plan(
        config, simulate=True, config_path=Path("configs/workflows/user/example.yaml")
    )
    assert "STEP 1 - DILUTIONS" in text
    assert "STEP 2 - PRINTING" in text
    assert "No robot is contacted." in text
    assert "A11" in text and "H11" in text
    assert "total drops  : 8" in text
    assert "5 uL" in text


def test_plan_marks_a_skipped_step(config):
    updated = _validate_ai_update(config, {"print": {"enabled": False}})
    text = render_plan(updated, simulate=True, config_path=Path("x.yaml"))
    assert "[SKIPPED: dilutions only]" in text


@pytest.mark.parametrize("text", [
    "i don't know", "I dont know what I want", "not sure", "no idea",
    "just use the default", "show me a standard example", "you pick",
])
def test_an_unsure_scientist_gets_the_worked_example(text):
    assert UNSURE.search(text)


@pytest.mark.parametrize("text", [
    "8 dilutions in column 11", "print at 5 uL", "move the plate to slot 6",
])
def test_a_specific_request_is_not_mistaken_for_uncertainty(text):
    assert not UNSURE.search(text)


def test_extract_json_tolerates_fenced_replies():
    value = _extract_json('```json\n{"updates":{"print":{"replicates":2}}}\n```')
    assert value["updates"]["print"]["replicates"] == 2
    with pytest.raises(ValueError, match="updates"):
        _extract_json('{"explanation":"nothing"}')
    with pytest.raises(ValueError, match="JSON object"):
        _extract_json("I would rather write you a protocol in Python.")
