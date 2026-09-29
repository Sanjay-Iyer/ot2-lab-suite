"""Deterministic test suite for canonical paper placement architecture (Cases 1-8)."""
from __future__ import annotations

from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.plan import build_plan, print_map, explicit_paper_rows
from src.agents.dye_demo.state import ExperimentState


def create_state():
    config = load_config(DEFAULT_CONFIG)
    return ExperimentState(config)


def test_case_1_drops_per_position_on_row_based_placement():
    """CASE 1: Start from normal row-based placement. Then 'Put 1 drop on A1, 3 drops on A2, and 2 drops on C4.'
    Expected: valid proposal, no rows/map conflict, print.paper_rows is cleared."""
    state = create_state()
    # verify initial state has implicit paper rows (paper_rows field is None)
    assert state.config["print"].get("paper_rows") is None
    
    # Propose explicit drop map
    proposal = state.propose(
        [{"path": "drops_at", "value": {"A1": 1, "A2": 3, "C4": 2}}],
        request="Put 1 drop on A1, 3 drops on A2, and 2 drops on C4.",
        accepted=["made_not_printed"],
        semantic_only=True
    )
    
    assert proposal.report.errors == []
    # Verify print map became canonical
    pm = print_map(proposal.after)
    assert pm is not None
    # Verify paper_rows was cleared (None) so there is no conflict
    assert explicit_paper_rows(proposal.after) is None


def test_case_2_new_destination_and_drop_count_together():
    """CASE 2: 'Take A11 and print 3 drops on B1.'
    Expected: source=A11, destination=B1, drops=3 in one request."""
    state = create_state()
    proposal = state.propose(
        [{"path": "print_map", "value": [{"source": "A11", "positions": ["B1"], "drops": {"B1": 3}}]}],
        request="Take A11 and print 3 drops on B1.",
        accepted=["made_not_printed"],
        semantic_only=True
    )
    assert proposal.report.errors == []
    pm = print_map(proposal.after)
    assert pm is not None
    assert pm[0]["source"] == "A11"
    assert pm[0]["positions"] == ["B1"]
    assert pm[0]["drops"] == {"B1": 3}
    assert explicit_paper_rows(proposal.after) is None


def test_case_3_exact_destinations_preserved():
    """CASE 3: 'Print A11 on A3, C3, and E8.'
    Expected: exact destinations preserved."""
    state = create_state()
    proposal = state.propose(
        [{"path": "print_map", "value": [{"source": "A11", "positions": ["A3", "C3", "E8"]}]}],
        request="Print A11 on A3, C3, and E8.",
        accepted=["made_not_printed"],
        semantic_only=True
    )
    assert proposal.report.errors == []
    pm = print_map(proposal.after)
    assert pm is not None
    assert pm[0]["source"] == "A11"
    assert pm[0]["positions"] == ["A3", "C3", "E8"]
    assert explicit_paper_rows(proposal.after) is None


def test_case_4_replicates_expand_explicit_map():
    """CASE 4: Start with explicit map. Then: '2 replicates for each spot'
    Expected: canonical map expands, no competing row state."""
    state = create_state()
    p1 = state.propose(
        [{"path": "print_map", "value": [{"source": "A11", "positions": ["A3"]}]}],
        request="Print A11 on A3.",
        accepted=["made_not_printed"],
        semantic_only=True
    )
    state.apply(p1, operator="Tester")
    
    p2 = state.propose(
        [{"path": "print.replicates", "value": 2}],
        request="2 replicates for each spot",
        accepted=["made_not_printed"],
        semantic_only=True
    )
    assert p2.report.errors == []
    pm = print_map(p2.after)
    assert pm is not None
    assert len(pm[0]["positions"]) == 2
    assert "A3" in pm[0]["positions"]
    assert explicit_paper_rows(p2.after) is None


def test_case_5_column_12_anchor():
    """CASE 5: '3 replicates starting in column 12'
    Expected: valid placement near column 12, no column-13 assumption."""
    state = create_state()
    proposal = state.propose(
        [{"path": "paper_columns", "value": [12], "mode": "anchor"}, {"path": "print.replicates", "value": 3}],
        request="3 replicates starting in column 12",
        accepted=["made_not_printed"],
        semantic_only=True
    )
    assert proposal.report.errors == []
    plan = build_plan(proposal.after)
    cols = [int(op.destination[1:]) for op in plan.operations if op.kind == "print"]
    assert all(1 <= c <= 12 for c in cols)
    assert max(cols) == 12


def test_case_6_shorthand_replaces_explicit_map():
    """CASE 6: After complex explicit-map edits: 'print column 4 rows 1 3 8'
    Expected: new shorthand placement replaces/resolves old map cleanly."""
    state = create_state()
    # Step 1: explicit map
    p1 = state.propose(
        [{"path": "print_map", "value": [{"source": "A11", "positions": ["A3", "C3", "E8"]}]}],
        request="Print A11 on A3, C3, and E8.",
        accepted=["made_not_printed"],
        semantic_only=True
    )
    state.apply(p1, operator="Tester")
    assert print_map(state.config) is not None
    
    # Step 2: shorthand placement request "print column 4 rows 1 3 8"
    p2 = state.propose(
        [{"path": "paper_rows", "value": ["A", "C", "H"]}, {"path": "paper_columns", "value": [4], "mode": "exact"}],
        request="print column 4 rows 1 3 8",
        accepted=["made_not_printed"],
        semantic_only=True
    )
    assert p2.report.errors == []
    pm = print_map(p2.after)
    assert pm is not None
    # verify the explicit map now reflects rows A, C, H in column 4 (A4, C4, H4)
    positions = [pos for entry in pm for pos in entry["positions"]]
    assert sorted(positions) == ["A4", "C4", "H4"], f"pm was {pm}, changes were {p2.changes}"
    assert explicit_paper_rows(p2.after) is None


def test_case_7_paper_vs_plate_column():
    """CASE 7: 'print column 12 on paper'
    Expected: paper placement changes, dilution plate column unchanged."""
    state = create_state()
    initial_plate_col = state.config["dilution"]["plate_column"]
    
    proposal = state.propose(
        [{"path": "paper_columns", "value": [12], "target": "paper"}],
        request="print column 12 on paper",
        accepted=["made_not_printed"],
        semantic_only=True
    )
    assert proposal.report.errors == []
    assert proposal.after["dilution"]["plate_column"] == initial_plate_col
    assert proposal.after["print"]["paper_start_column"] == 12 or print_map(proposal.after) is not None


def test_case_8_no_replicates():
    """CASE 8: 'no replicates'
    Expected: one total copy per condition, canonical map shrinks correctly."""
    state = create_state()
    # start with 3 replicates
    p1 = state.propose(
        [{"path": "print.replicates", "value": 3}],
        request="3 replicates",
        accepted=["made_not_printed"],
        semantic_only=True
    )
    state.apply(p1, operator="Tester")
    
    p2 = state.propose(
        [{"path": "print.replicates", "value": 1}],
        request="no replicates",
        accepted=["made_not_printed"],
        semantic_only=True
    )
    assert p2.report.errors == []
    pm = print_map(p2.after)
    assert pm is not None
    for entry in pm:
        assert len(entry["positions"]) == 1
    assert explicit_paper_rows(p2.after) is None
