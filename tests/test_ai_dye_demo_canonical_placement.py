"""Canonical paper placement (Cases 1-8) in the semantic pipeline: the PlanSpec keeps what the scientist asked for -
drops, paper rows and columns, replicate counts, or the exact positions they dictated - and the compiled plan
(compiler.py) has the one exact print map the run executes. Edits never generate a print map of their own."""
from __future__ import annotations

from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.plan import explicit_paper_rows
from src.agents.dye_demo.state import ExperimentState


def create_state():
    return ExperimentState(load_config(DEFAULT_CONFIG))


def propose(state, operations, request):
    return state.propose_patch(operations, request=request, accepted=["made_not_printed"])


def compiled(proposal, state):
    state.apply(proposal, operator="Tester")
    result = state.compile()
    assert result.ok, result.errors
    return result


def test_case_1_drops_per_position_on_row_based_placement():
    """CASE 1: the normal row layout, then "1 drop on A1, 3 drops on B1 and 2 drops on C1": drops on those positions,
    no print map in the plan, and the compiled map carries the counts. A position nothing prints on blocks only the
    run, with the reason."""
    state = create_state()
    proposal = propose(state, [{"path": "drops_at", "value": {"A1": 1, "B1": 3, "C1": 2}}],
                       "Put 1 drop on A1, 3 drops on B1, and 2 drops on C1.")
    assert proposal.report.errors == []
    assert proposal.after["print"]["drops_at"] == {"A1": 1, "B1": 3, "C1": 2}
    assert proposal.after["print"].get("source_map") is None and explicit_paper_rows(proposal.after) is None
    result = compiled(proposal, state)
    assert result.source_map[1] == {"source": "B11", "positions": ["B1"], "drops": {"B1": 3}}
    outside = propose(state, [{"path": "drops_at", "value": {"C4": 2}}], "2 drops on C4")
    assert any("nothing prints there" in message for message in outside.requirements)


def test_case_2_new_destination_and_drop_count_together():
    """CASE 2: "Take A11 and print 3 drops on B1": source, destination and drops in one request."""
    state = create_state()
    proposal = propose(state, [{"path": "print_map", "value": [{"source": "A11", "positions": ["B1"],
                                                                 "drops": {"B1": 3}}]}],
                       "Take A11 and print 3 drops on B1.")
    assert proposal.report.errors == []
    # A11 is where this run makes dilution 1: the dictated map prints that dilution, by reference
    assert proposal.after["print"]["source_map"] == [{"source": "dilution:1", "positions": ["B1"], "drops": {"B1": 3}}]
    assert compiled(proposal, state).source_map == [{"source": "A11", "positions": ["B1"], "drops": {"B1": 3}}]


def test_case_3_exact_destinations_preserved():
    """CASE 3: "Print A11 on A3, C3, and E8": the exact destinations."""
    state = create_state()
    proposal = propose(state, [{"path": "print_map", "value": [{"source": "A11", "positions": ["A3", "C3", "E8"]}]}],
                       "Print A11 on A3, C3, and E8.")
    assert proposal.report.errors == []
    assert compiled(proposal, state).source_map == [{"source": "A11", "positions": ["A3", "C3", "E8"]}]
    assert explicit_paper_rows(state.config) is None


def test_case_4_replicates_expand_explicit_map():
    """CASE 4: a dictated map, then "2 replicates for each spot": the map's position stays and its second print goes
    on the nearest free position."""
    state = create_state()
    state.apply(propose(state, [{"path": "print_map", "value": [{"source": "A11", "positions": ["A3"]}]}],
                        "Print A11 on A3."), operator="Tester")
    p2 = propose(state, [{"path": "print.replicates", "value": 2}], "2 replicates for each spot")
    assert p2.report.errors == []
    [entry] = p2.after["print"]["source_map"]
    assert entry["source"] == "dilution:1" and len(entry["positions"]) == 2 and entry["positions"][0] == "A3"
    assert compiled(p2, state).source_map[0]["positions"] == entry["positions"]


def test_case_5_column_12_anchor():
    """CASE 5: "3 replicates starting in column 12": the PlanSpec says exactly that; the compiled run places them
    from column 12 without ever needing a column 13."""
    state = create_state()
    proposal = propose(state, [{"path": "paper_columns", "value": [12], "mode": "anchor"},
                               {"path": "print.replicates", "value": 3}], "3 replicates starting in column 12")
    assert proposal.report.errors == []
    assert (proposal.after["print"]["paper_start_column"], proposal.after["print"]["replicates"]) == (12, 3)
    assert proposal.after["print"].get("source_map") is None
    result = compiled(proposal, state)
    columns = [int(op.destination[1:]) for op in result.plan.operations if op.kind == "print"]
    assert all(1 <= column <= 12 for column in columns) and max(columns) == 12 and len(columns) == 24


def test_case_6_shorthand_replaces_explicit_map():
    """CASE 6: a dictated map, then "print column 4 rows 1 3 8": the new placement replaces the map; the sample it
    printed keeps printing."""
    state = create_state()
    state.apply(propose(state, [{"path": "print_map", "value": [{"source": "A11",
                                                                 "positions": ["A3", "C3", "E8"]}]}],
                        "Print A11 on A3, C3, and E8."), operator="Tester")
    p2 = propose(state, [{"path": "paper_rows", "value": ["A", "C", "H"]},
                         {"path": "paper_columns", "value": [4], "mode": "exact"}], "print column 4 rows 1 3 8")
    assert p2.report.errors == []
    assert p2.after["print"].get("source_map") is None
    assert (p2.after["print"]["source"], p2.after["print"]["paper_rows"]) == (["dilution:1"], ["A", "C", "H"])
    result = compiled(p2, state)
    assert result.source_map == [{"source": "A11", "positions": ["A4", "C4", "H4"]}]


def test_case_7_paper_vs_plate_column():
    """CASE 7: "print column 12 on paper": the paper placement changes, the dilution plate column does not."""
    state = create_state()
    initial_plate_col = state.config["dilution"]["plate_column"]
    proposal = propose(state, [{"path": "paper_columns", "value": [12], "target": "paper"}], "print column 12 on paper")
    assert proposal.report.errors == []
    assert proposal.after["dilution"]["plate_column"] == initial_plate_col
    assert proposal.after["print"]["paper_start_column"] == 12
    assert {position[1:] for entry in compiled(proposal, state).source_map for position in entry["positions"]} == {"12"}


def test_case_8_no_replicates():
    """CASE 8: 3 replicates, then "no replicates": one copy per condition again."""
    state = create_state()
    state.apply(propose(state, [{"path": "print.replicates", "value": 3}], "3 replicates"), operator="Tester")
    p2 = propose(state, [{"path": "print.replicates", "op": "none"}], "no replicates")
    assert p2.report.errors == [] and p2.after["print"]["replicates"] == 1
    for entry in compiled(p2, state).source_map:
        assert len(entry["positions"]) == 1
