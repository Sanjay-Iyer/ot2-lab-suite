"""Unit tests for natural language printing selections."""
from __future__ import annotations

import pytest
from src.agents.dye_demo.natural import selected_rows, selected_columns
from src.agents.dye_demo.state import ExperimentState, _rows_stated
from src.agents.dye_demo.language import find_ambiguities
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config


def test_selected_rows_interpretations():
    assert selected_rows("first 3 rows") == ["A", "B", "C"]
    assert selected_rows("1st 3 rows") == ["A", "B", "C"]
    assert selected_rows("top 3 rows") == ["A", "B", "C"]
    assert selected_rows("rows 1 through 3") == ["A", "B", "C"]
    assert selected_rows("row 3") == ["C"]
    assert selected_rows("third row") == ["C"]
    assert selected_rows("row 2") == ["B"]
    assert selected_rows("rows a b c") == ["A", "B", "C"]


def test_rows_stated_validation():
    assert _rows_stated(["A", "B", "C"], "print the first 3 rows in column 3")
    assert _rows_stated(["A", "B", "C"], "rows 1 through 3")
    assert _rows_stated(["C"], "row 3 in column 2")
    assert _rows_stated(["B"], "actually just row 2")
    assert _rows_stated(["A", "B", "C"], "first 3 rows in paper column 3, use plate column 1")


def test_column_ambiguity_bypassed_for_qualified_text():
    config = load_config(DEFAULT_CONFIG)
    # Sentence specifying both paper column 3 and plate slot/column
    text1 = "i only want to print the 1st 3 rows in column 3 on the paper use the first 3 well rows from the 96 well plate slot 4"
    ambiguities1 = find_ambiguities(text1, config)
    assert not any(amb.question.startswith("You said \"column 3.\" Which column do you mean?") for amb in ambiguities1)

    text2 = "first 3 rows in paper column 3, use plate column 1"
    ambiguities2 = find_ambiguities(text2, config)
    assert not any(amb.question.startswith("You said \"column") for amb in ambiguities2)


def test_propose_natural_printing_request():
    config = load_config(DEFAULT_CONFIG)
    state = ExperimentState(config)

    # 1. "print the first 3 rows in column 3"
    proposal = state.propose(
        [{"path": "paper_columns", "value": [3]}, {"path": "rows", "value": ["A", "B", "C"]}],
        request="print the first 3 rows in column 3"
    )
    assert proposal.after["dilution"]["start_row"] == "A"
    assert proposal.after["print"]["paper_start_column"] == 3

    # 2. "first 3 rows in paper column 3, use plate column 1"
    proposal2 = state.propose(
        [{"path": "dilution.plate_column", "value": "1"}, {"path": "paper_columns", "value": [3]}, {"path": "rows", "value": ["A", "B", "C"]}],
        request="first 3 rows in paper column 3, use plate column 1"
    )
    assert proposal2.after["dilution"]["plate_column"] == "1"
    assert proposal2.after["print"]["paper_start_column"] == 3
    assert proposal2.after["dilution"]["start_row"] == "A"
