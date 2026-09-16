"""Tests for flexible natural language source plate selection."""
from __future__ import annotations

from src.agents.dye_demo.natural import selected_rows
from src.agents.dye_demo.redteam.interpreter import rule_changes


def test_flexible_row_expressions():
    assert selected_rows("first 3 rows") == ["A", "B", "C"]
    assert selected_rows("rows 1 to 3") == ["A", "B", "C"]
    assert selected_rows("rows 1 through 3") == ["A", "B", "C"]
    assert selected_rows("top three wells") == ["A", "B", "C"]
    assert selected_rows("first three wells in column 12") == ["A", "B", "C"]
    assert selected_rows("row 3") == ["C"]


def test_rule_changes_source_column_phrasings():
    phrasings = [
        "use plate column 12",
        "pull from column 12",
        "use the samples in column 12",
        "they're in column 12",
        "actually use column 5 instead",
        "print from plate column 5 to paper column 3",
        "same thing but source column 8",
    ]
    for text in phrasings:
        changes = rule_changes(text)
        assert any(c["path"] == "dilution.plate_column" for c in changes), f"Failed to parse source column for: {text}"
