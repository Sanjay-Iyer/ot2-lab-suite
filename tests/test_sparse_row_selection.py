import pytest
from src.agents.dye_demo.natural import selected_rows, expand_rows
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.state import ExperimentState
from src.agents.dye_demo.gui.labware_svg import render_plate_svg, render_paper_svg
from src.agents.dye_demo.gui.app import experiment_flow

def test_selected_rows_sparse():
    assert selected_rows("rows A C E") == ["A", "C", "E"]
    assert selected_rows("rows 1 3 5") == ["A", "C", "E"]
    assert selected_rows("just A and H") == ["A", "H"]
    assert selected_rows("rows B D F") == ["B", "D", "F"]
    assert selected_rows("A5 C5 E5") == ["A", "C", "E"]

def test_expand_rows_sparse():
    config = {
        "dilution": {"enabled": True, "factors": [1.0, 2.0, 4.0], "start_row": "A", "plate_column": "5"},
        "print": {"enabled": True, "paper_start_column": 3, "replicates": 1, "droplets_per_spot": 1},
        "deck": {"plate": {"slot": 4}, "paper": {"slot": 5}, "tuberack": {"slot": 1}, "tiprack": {"slot": 2}},
    }
    changes, note = expand_rows(["A", "C", "E"], config)
    assert any(c["path"] == "dilution.rows" and c["value"] == ["A", "C", "E"] for c in changes)
    assert "A, C, E" in note

def test_sparse_rows_in_plan_and_svg():
    config = {
        "dilution": {"enabled": True, "factors": [1.0, 2.0, 4.0], "rows": ["A", "C", "E"], "plate_column": "5"},
        "print": {"enabled": True, "droplet_volume_ul": 10.0, "paper_start_column": 3, "replicates": 1, "droplets_per_spot": 1},
        "deck": {"plate": {"slot": 4}, "paper": {"slot": 5}, "tuberack": {"slot": 1}, "tiprack": {"slot": 2}},
    }
    plan = build_plan(config)
    assert plan.rows == ["A", "C", "E"]

    plate_svg = render_plate_svg(config)
    assert 'Well A5' in plate_svg and 'fill="#22c55e"' in plate_svg
    assert 'Well B5 (Unused)' in plate_svg or 'Well B5' in plate_svg

    paper_svg = render_paper_svg(config)
    assert 'Position A3' in paper_svg
    assert 'Position C3' in paper_svg
    assert 'Position E3' in paper_svg

def test_experiment_flow_sparse_rows():
    config = {
        "dilution": {"enabled": True, "factors": [1.0, 2.0, 4.0], "rows": ["A", "C", "E"], "plate_column": "5"},
        "print": {"enabled": True, "droplet_volume_ul": 10.0, "paper_start_column": 3, "replicates": 1, "droplets_per_spot": 1},
        "deck": {"plate": {"slot": 4}, "paper": {"slot": 5}, "tuberack": {"slot": 1}, "tiprack": {"slot": 2}},
    }
    steps = experiment_flow(config)
    print_step = [s for s in steps if "PRINT" in s.title][0]
    assert "A, C, E" in print_step.destination or "A3" in print_step.destination or "columns 3" in print_step.destination
