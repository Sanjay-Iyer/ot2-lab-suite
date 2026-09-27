"""Where the dilutions are made (plate rows) and where they print (paper rows) are independent settings.

2026-09-27 manual GUI report: "the dilutions still should be in rows 1 3 5 and printing in 1 2 3 so they will be
different" ended with the dilutions and the prints on the same rows. Root cause (runs/row_independence/20260927_112413):
the router had ONE row vocabulary, the `rows` selection, defined as "plate rows of the series, each printing on the
same paper row"; `natural.expand_rows` wrote it into dilution.rows, a rows selection cleared any print map, and the plan
printed each dilution on the paper row with its own letter. Gemini read "print in rows 1 2 and 3" as print rows every
time - the structure had nowhere to put them.

Now dilution.rows (plate rows) and print.paper_rows (paper rows) are separate fields, the router has a paper_rows
selection, the plan and the protocol honour explicit paper rows, a change to either leaves the other alone, and a print
map of the dilution series follows the dilutions when they move. Physical checks are unchanged: every row must exist,
each printed dilution needs exactly one paper row, and a paper position is never printed twice.

Simulation only: scripted router replies, the recording fake OT-2 and (when installed) the pinned Opentrons 2.15
simulator. Nothing contacts a robot or an LLM.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.agents.dye_demo import render
from src.agents.dye_demo.llm import ROUTER_PROMPT, RouterContext, router_human_message
from src.agents.dye_demo.model import DEFAULT_CONFIG, EDITABLE_FIELDS, FieldError, load_config, normalize_paper_rows
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.fake_opentrons import load_protocol_module, run_protocol
from src.agents.dye_demo.redteam.harness import ConversationSpec, ReplayDriver, run_conversation
from src.agents.dye_demo.redteam.interpreter import ReplayInterpreter
from src.agents.dye_demo.redteam.simulate_states import BASE_PROTOCOL, PINNED_SIMULATOR, _builder
from src.agents.dye_demo.session import revise_request
from src.agents.dye_demo.state import ExperimentState, ProposalRejected
from src.agents.dye_demo.validation import validate

DEFAULT = load_config(DEFAULT_CONFIG)
PLATE = DEFAULT["deck"]["plate"]["load_name"]
PAPER = DEFAULT["deck"]["paper"]["load_name"]


def series(factors, rows, *, paper_rows=None, start_column=3, replicates=1, source_map=None, dilution=True):
    """The default plan with this dilution series (plate column 11) and print layout."""
    config = deepcopy(DEFAULT)
    config["dilution"].update(enabled=dilution, factors=list(factors), rows=list(rows), start_row=rows[0])
    config["print"].update(paper_start_column=start_column, replicates=replicates)
    if paper_rows is not None:
        config["print"]["paper_rows"] = list(paper_rows)
    if source_map is not None:
        config["print"]["source_map"] = source_map
    return config


def plan_pairs(config):
    return [(op.source, op.destination) for op in build_plan(config).operations if op.kind == "print"]


def made(config):
    return [(well.well, well.factor) for well in build_plan(config).wells] if build_plan(config).do_dilution else []


def protocol_dispenses(config):
    """(plate well, paper position) of EVERY paper dispense (one per drop) of protocol v19 run against the recording
    fake OT-2: the plate well is the one the tip last drew liquid from."""
    context = run_protocol(load_protocol_module(), config)
    last, pairs = None, []
    for entry in context.log:
        if entry[0] == "aspirate" and entry[2][0] == PLATE:
            last = entry[2][1]
        elif entry[0] == "dispense" and entry[2][0] == PAPER:
            pairs.append((last, entry[2][1]))
    return pairs


def protocol_pairs(config):
    """Each printed paper position once, with the plate well it is printed from, in print order."""
    return list(dict.fromkeys(protocol_dispenses(config)))


ACE = [2, 5, 10], ["A", "C", "E"]
ACE_TO_ABC = [("A11", "A3"), ("C11", "B3"), ("E11", "C3")]


# ════════════════════════════════════════════════════════════════════════════════
# the data model, the plan and the protocol: independent rows without any conversation
# ════════════════════════════════════════════════════════════════════════════════

def test_dilutions_in_a_c_e_print_on_paper_rows_a_b_c():
    config = series(*ACE, paper_rows=["A", "B", "C"])
    assert validate(config).ok, validate(config).error_messages()
    assert made(config) == [("A11", 2), ("C11", 5), ("E11", 10)]              # the dilution wells did not move
    assert plan_pairs(config) == ACE_TO_ABC == protocol_pairs(config)


def test_dilutions_in_a_b_c_print_on_paper_rows_a_c_e():
    config = series([2, 5, 10], ["A", "B", "C"], paper_rows=["A", "C", "E"])
    assert validate(config).ok
    assert made(config) == [("A11", 2), ("B11", 5), ("C11", 10)]
    assert plan_pairs(config) == [("A11", "A3"), ("B11", "C3"), ("C11", "E3")] == protocol_pairs(config)


def test_one_source_on_four_paper_rows_and_two_sources_mapped_independently():
    one = series([2], ["A"], source_map=[{"source": "A11", "positions": ["A3", "B3", "C3", "D3"]}])
    assert validate(one).ok and plan_pairs(one) == [("A11", p) for p in ("A3", "B3", "C3", "D3")] == protocol_pairs(one)
    two = series([2, 5], ["A", "C"], source_map=[{"source": "A11", "positions": ["A3", "B3"]},
                                                 {"source": "C11", "positions": ["C3", "D3"]}])
    assert validate(two).ok
    assert plan_pairs(two) == [("A11", "A3"), ("A11", "B3"), ("C11", "C3"), ("C11", "D3")] == protocol_pairs(two)


def test_paper_rows_hold_for_every_column_and_drop_volume():
    config = series(*ACE, paper_rows=["B", "D", "F"], replicates=2)
    config["print"]["droplet_volume_ul"] = [5.0, 10.0]
    assert validate(config).ok
    pairs = plan_pairs(config)
    assert {source: sorted({p[0] for s, p in pairs if s == source}) for source, _ in pairs} == \
        {"A11": ["B"], "C11": ["D"], "E11": ["F"]}
    assert sorted({int(p[1:]) for _, p in pairs}) == [3, 4, 5, 6] and pairs == protocol_pairs(config)


def test_without_paper_rows_each_dilution_still_prints_on_its_own_letter():
    config = series(*ACE)
    assert plan_pairs(config) == [("A11", "A3"), ("C11", "C3"), ("E11", "E3")] == protocol_pairs(config)
    assert build_plan(config).paper_rows == ["A", "C", "E"]


def test_print_only_samples_print_on_their_paper_rows():
    config = series(*ACE, paper_rows=["A", "B", "C"], dilution=False)
    assert validate(config).ok and made(config) == []
    assert plan_pairs(config) == ACE_TO_ABC == protocol_pairs(config)


@pytest.mark.parametrize("paper_rows, reason", [
    (["A", "B"], "3 dilutions would print on 2 paper rows"),
    (["A", "A", "B"], "listed twice"),
    (["A", "B", "I"], "no row I"),
])
def test_physical_rules_stay_strict(paper_rows, reason):
    """One real, distinct paper row per printed dilution - refused by validation AND by the protocol's own pre-flight."""
    config = series(*ACE, paper_rows=paper_rows)
    report = validate(config)
    assert not report.ok and any(reason in message for message in report.error_messages()), report.error_messages()
    with pytest.raises(RuntimeError, match="PRE-FLIGHT VALIDATION FAILED"):
        protocol_pairs(config)


def test_paper_rows_and_a_print_map_together_are_refused():
    config = series(*ACE, paper_rows=["A", "B", "C"], source_map=[{"source": "A11", "positions": ["A1"]}])
    assert "print.paper_rows_with_map" in [issue.code for issue in validate(config).errors]
    with pytest.raises(RuntimeError, match="cannot be combined"):
        protocol_pairs(config)


def test_paper_rows_normalisation():
    assert normalize_paper_rows(["1", "2", "3"]) == ["A", "B", "C"]
    assert normalize_paper_rows("rows 1, 3 and 5") == ["A", "C", "E"]
    assert normalize_paper_rows(["c", "A", "b"]) == ["A", "B", "C"]         # filled top to bottom in series order
    assert normalize_paper_rows(None) is None and normalize_paper_rows("default") is None
    with pytest.raises(FieldError):
        normalize_paper_rows(["A", "I"])                                    # refused, never silently dropped
    assert "print.paper_rows" in EDITABLE_FIELDS and EDITABLE_FIELDS["dilution.rows"][0] == "Dilution plate rows"


@pytest.mark.skipif(not PINNED_SIMULATOR.exists(), reason="pinned Opentrons 2.15 simulator not installed")
@pytest.mark.parametrize("config, expected", [
    (series(*ACE, paper_rows=["A", "B", "C"]), ACE_TO_ABC),
    (series([2, 5, 10], ["A", "B", "C"], paper_rows=["A", "C", "E"]), [("A11", "A3"), ("B11", "C3"), ("C11", "E3")]),
], ids=["ACE_to_ABC", "ABC_to_ACE"])
def test_the_real_simulator_executes_the_independent_rows(tmp_path, config, expected):
    """The generated protocol in the pinned Opentrons 2.15 simulator: every paper dispense traced back to the plate well
    it was aspirated from, and every dilution transfer to the well it filled."""
    builder = _builder()
    full = deepcopy(config)
    run_modes = full.pop("run_modes", {}) or {}
    run_modes["dry_run"] = False
    path = tmp_path / "row_independence.py"
    path.write_text(builder.build_source(BASE_PROTOCOL.read_text(encoding="utf-8"), full, run_modes), encoding="utf-8")
    ok, output = builder.simulate(path, str(PINNED_SIMULATOR))
    assert ok, output[-1500:]
    aspirate = re.compile(r"^\s*Aspirating [\d.]+ uL from ([A-H]\d+) of .+? on slot (\d+)")
    dispense = re.compile(r"^\s*Dispensing [\d.]+ uL into ([A-H]\d+) of .+? on slot (\d+)")
    plate, paper, rack = (str(config["deck"][role]["slot"]) for role in ("plate", "paper", "tuberack"))
    last, from_rack, pairs, filled = None, False, [], []
    for line in output.splitlines():
        if match := aspirate.match(line):
            from_rack, last = match.group(2) == rack, (match.group(1) if match.group(2) == plate else last)
        elif (match := dispense.match(line)) and match.group(2) == paper:
            pairs.append((last, match.group(1)))
        elif match and match.group(2) == plate and from_rack and match.group(1) not in filled:
            filled.append(match.group(1))
    assert pairs == expected
    assert sorted(filled) == sorted(well for well, _ in made(config))


# ════════════════════════════════════════════════════════════════════════════════
# selections: paper rows change only where the samples print, plate rows only where they are made
# ════════════════════════════════════════════════════════════════════════════════

def state_with(config) -> ExperimentState:
    return ExperimentState(config)


def propose(state, *changes, request, preserved=()):
    return state.propose(list(changes), request=request, preserved=preserved)


def test_the_paper_rows_selection_keeps_the_dilution_wells():
    state = state_with(series(*ACE))
    proposal = propose(state, {"path": "paper_rows", "value": ["A", "B", "C"], "evidence": "rows 1 2 and 3"},
                       request="but i want to print in rows 1 2 and 3")
    assert [change.path for change in proposal.changes] == ["print.paper_rows"]
    assert made(proposal.after) == [("A11", 2), ("C11", 5), ("E11", 10)]
    assert plan_pairs(proposal.after) == ACE_TO_ABC


def test_paper_rows_written_to_the_field_are_the_same_selection():
    state = state_with(series(*ACE))
    proposal = propose(state, {"path": "print.paper_rows", "value": ["1", "2", "3"], "evidence": "rows 1 2 and 3"},
                       request="print in rows 1 2 and 3")
    assert plan_pairs(proposal.after) == ACE_TO_ABC and made(proposal.after) == made(series(*ACE))


def test_one_sample_on_several_paper_rows_becomes_a_print_map():
    state = state_with(series([2], ["A"]))
    proposal = propose(state, {"path": "paper_rows", "value": ["A", "B", "C", "D"], "evidence": "rows A to D"},
                       request="print it on paper rows A to D")
    assert plan_pairs(proposal.after) == [("A11", p) for p in ("A3", "B3", "C3", "D3")]
    assert proposal.after["print"].get("paper_rows") is None and made(proposal.after) == [("A11", 2)]


def test_two_sources_each_on_their_own_paper_rows():
    state = state_with(series([2, 5], ["A", "C"]))
    value = [{"source": "A11", "rows": ["A", "B"], "columns": [3]}, {"source": "C11", "rows": ["C", "D"], "columns": [3]}]
    proposal = propose(state, {"path": "print_map", "value": value, "evidence": "A11 on rows A and B, C11 on C and D"},
                       request="print A11 on rows A and B and C11 on rows C and D in paper column 3")
    assert plan_pairs(proposal.after) == [("A11", "A3"), ("A11", "B3"), ("C11", "C3"), ("C11", "D3")]
    assert made(proposal.after) == [("A11", 2), ("C11", 5)]


def test_paper_rows_move_a_print_map_and_keep_its_columns():
    """The scientist's own session: gapped paper columns 3 and 12 made a map with each source on its own letter."""
    source_map = [{"source": w, "positions": [f"{w[0]}3", f"{w[0]}12"]} for w in ("A11", "C11", "E11")]
    state = state_with(series(*ACE, source_map=source_map))
    proposal = propose(state, {"path": "paper_rows", "value": ["A", "B", "C"], "evidence": "printing in 1 2 3"},
                       request="printing in rows 1 2 3")
    assert plan_pairs(proposal.after) == [("A11", "A3"), ("A11", "A12"), ("C11", "B3"), ("C11", "B12"),
                                          ("E11", "C3"), ("E11", "C12")]
    assert made(proposal.after) == [("A11", 2), ("C11", 5), ("E11", 10)]


def test_too_few_paper_rows_for_the_samples_is_a_question_not_a_guess():
    state = state_with(series(*ACE))
    with pytest.raises(ProposalRejected) as raised:
        propose(state, {"path": "paper_rows", "value": ["A", "B"], "evidence": "rows A and B"},
                request="print on paper rows A and B")
    assert raised.value.kind == "selection"
    assert raised.value.question == "Which 3 paper rows should A11, C11 and E11 print on (one row each)?"


def test_some_rows_of_the_default_layout_pick_which_dilutions_print():
    """85-test E04 "Do columns 1 through 4 but only rows B, D and H" on the eight-dilution plan: whether the router calls
    them plate rows or paper rows, each dilution prints on its own letter, so they can only pick dilutions B, D, H."""
    for path in ("rows", "paper_rows"):
        proposal = propose(state_with(DEFAULT), {"path": path, "value": ["B", "D", "H"], "evidence": "rows B, D and H"},
                           request="Print only rows B, D and H.")
        assert made(proposal.after) == [("B11", 2), ("D11", 4), ("H11", 16)], path
        assert plan_pairs(proposal.after) == [("B11", "B1"), ("D11", "D1"), ("H11", "H1")], path


def test_moving_the_dilutions_keeps_explicit_paper_rows():
    state = state_with(series(*ACE, paper_rows=["A", "B", "C"]))
    proposal = propose(state, {"path": "rows", "value": ["B", "D", "H"], "evidence": "B11, D11 and H11"},
                       request="Change only the dilution wells to B11, D11 and H11.")
    assert made(proposal.after) == [("B11", 2), ("D11", 5), ("H11", 10)]
    assert plan_pairs(proposal.after) == [("B11", "A3"), ("D11", "B3"), ("H11", "C3")]     # destinations unchanged
    assert "print.paper_rows" not in [change.path for change in proposal.changes]


def test_the_dilution_rows_field_keeps_explicit_paper_rows_too():
    state = state_with(series(*ACE, paper_rows=["A", "B", "C"]))
    proposal = propose(state, {"path": "dilution.rows", "value": ["B", "D", "H"], "evidence": "rows B, D and H"},
                       request="move the dilutions to rows B, D and H")
    assert plan_pairs(proposal.after) == [("B11", "A3"), ("D11", "B3"), ("H11", "C3")]


def test_a_print_map_of_the_dilutions_follows_them_when_they_move():
    source_map = [{"source": w, "positions": [f"{w[0]}3", f"{w[0]}12"]} for w in ("A11", "C11", "E11")]
    state = state_with(series(*ACE, source_map=source_map))
    proposal = propose(state, {"path": "rows", "value": ["B", "D", "H"], "evidence": "rows B, D and H"},
                       request="make the rows B, D and H")
    assert made(proposal.after) == [("B11", 2), ("D11", 5), ("H11", 10)]
    assert plan_pairs(proposal.after) == [("B11", "A3"), ("B11", "A12"), ("D11", "C3"), ("D11", "C12"),
                                          ("H11", "E3"), ("H11", "E12")]
    moved = next(change for change in proposal.changes if change.path == "print.source_map")
    assert moved.kind == "dependent"


def test_new_factors_in_new_rows_keep_the_series_positions():
    """New factors AND new rows in one request: no factor matches, so each dilution keeps the positions of the one at
    the same place in the series - the map never keeps pointing at wells nothing fills."""
    source_map = [{"source": w, "positions": [f"{w[0]}3", f"{w[0]}12"]} for w in ("A11", "C11", "E11")]
    state = state_with(series(*ACE, source_map=source_map))
    proposal = propose(state, {"path": "dilution.factors", "value": [3, 6, 12], "evidence": "3x, 6x and 12x"},
                       {"path": "rows", "value": ["B", "D", "H"], "evidence": "rows B, D and H"},
                       request="Make 3x, 6x and 12x dilutions in rows B, D and H.")
    assert made(proposal.after) == [("B11", 3), ("D11", 6), ("H11", 12)]
    assert plan_pairs(proposal.after) == [("B11", "A3"), ("B11", "A12"), ("D11", "C3"), ("D11", "C12"),
                                          ("H11", "E3"), ("H11", "E12")]


def test_a_print_map_of_the_dilutions_follows_a_plate_column_change():
    state = state_with(series(*ACE, source_map=[{"source": "A11", "positions": ["A3"]}, {"source": "C11",
                                                "positions": ["B3"]}, {"source": "E11", "positions": ["C3"]}]))
    proposal = propose(state, {"path": "dilution.plate_column", "value": "5", "evidence": "plate column 5"},
                       request="use plate column 5")
    assert plan_pairs(proposal.after) == [("A5", "A3"), ("C5", "B3"), ("E5", "C3")]


def test_leaving_out_a_dilution_keeps_each_remaining_one_on_its_paper_row():
    state = state_with(series(*ACE, paper_rows=["A", "B", "C"]))
    proposal = propose(state, {"path": "rows", "value": ["C", "E"], "evidence": "the 5x and 10x"},
                       request="print only the 5x and 10x")
    assert made(proposal.after) == [("C11", 5), ("E11", 10)]
    assert plan_pairs(proposal.after) == [("C11", "B3"), ("E11", "C3")]


def test_keep_my_dilution_wells_holds_against_a_rows_selection():
    """A router that still read the print rows as plate rows cannot move dilutions the scientist asked to keep."""
    state = state_with(series(*ACE))
    proposal = propose(state, {"path": "rows", "value": ["A", "B", "C"], "evidence": "printing rows to A, B and C"},
                       {"path": "print.droplets_per_spot", "value": 2, "evidence": "2 drops"},
                       request="Keep my dilution wells the same but use 2 drops and print rows A, B and C.",
                       preserved=["dilution.rows"])
    assert made(proposal.after) == made(series(*ACE))
    assert any("keep the dilution plate rows" in note for note in proposal.notes)


def test_a_new_print_map_replaces_explicit_paper_rows_and_keeps_their_positions():
    state = state_with(series(*ACE, paper_rows=["A", "B", "C"]))
    proposal = propose(state, {"path": "paper_columns", "value": [3, 12], "evidence": "paper columns 3 and 12"},
                       request="print in paper columns 3 and 12")
    assert proposal.after["print"].get("paper_rows") is None
    assert plan_pairs(proposal.after) == [("A11", "A3"), ("A11", "A12"), ("C11", "B3"), ("C11", "B12"),
                                          ("E11", "C3"), ("E11", "C12")]
    # the map lines say where each well prints; the folded-in paper rows are not shown as "(not set)"
    assert render.interpretation_lines(proposal) == ["print A11 on 2 paper positions (A3, A12)",
                                                     "print C11 on 2 paper positions (B3, B12)",
                                                     "print E11 on 2 paper positions (C3, C12)"]


def test_the_scientists_typo_turn_keeps_the_paper_rows_they_chose(tmp_path):
    """runs/ai_dye_demo_gui/20260927_111453 turn 10 with Gemini's REAL reply: "printing in 12 3" was read as paper
    columns 12 and 3. The columns follow that reading, but the dilutions stay in A, C, E and the prints stay on the
    paper rows A, B, C chosen in the turn before - before the fix they went back to A, C and E."""
    session, _ = talk(
        tmp_path, MAKE_ACE, "yes",
        says("but i want to print in rows 1 2 and 3",
             proposes(change("paper_rows", ["A", "B", "C"], "i want to print in rows 1 2 and 3"))), "yes",
        says("the dilutions still should be in rows 1 3 5 and printing in 12 3 so they wil lbe different",
             proposes(change("dilution.rows", ["A", "C", "E"], "the dilutions still should be in rows 1 3 5"),
                      change("print.paper_start_column", 12, "printing in 12 3"),
                      change("paper_columns", [12, 3], "printing in 12 3"))), "yes")
    config = session.state.config
    assert made(config) == [("A11", 2), ("C11", 5), ("E11", 10)]
    assert plan_pairs(config) == [("A11", "A3"), ("A11", "A12"), ("C11", "B3"), ("C11", "B12"),
                                  ("E11", "C3"), ("E11", "C12")] == protocol_pairs(config)


# ════════════════════════════════════════════════════════════════════════════════
# merges: a revision of one dimension never replaces the other
# ════════════════════════════════════════════════════════════════════════════════

def test_revising_paper_rows_keeps_the_waiting_plate_rows_and_paper_columns():
    request = [{"path": "rows", "value": ["A", "C", "E"]}, {"path": "paper_columns", "value": [3]}]
    merged = revise_request(DEFAULT, request, [{"path": "paper_rows", "value": ["A", "B", "C"]}])
    assert {change["path"] for change in merged} == {"rows", "paper_columns", "paper_rows"}


def test_revising_plate_rows_keeps_the_waiting_paper_rows():
    request = [{"path": "paper_rows", "value": ["A", "B", "C"]}, {"path": "rows", "value": ["A", "C", "E"]}]
    merged = revise_request(DEFAULT, request, [{"path": "rows", "value": ["B", "D", "H"]}])
    assert merged == [{"path": "paper_rows", "value": ["A", "B", "C"]}, {"path": "rows", "value": ["B", "D", "H"]}]


def test_a_revised_print_map_replaces_waiting_paper_rows():
    request = [{"path": "paper_rows", "value": ["A", "B", "C"]}]
    merged = revise_request(DEFAULT, request, [{"path": "print_map", "value": [{"source": "A11", "count": 2}]}])
    assert [change["path"] for change in merged] == ["print_map"]


# ════════════════════════════════════════════════════════════════════════════════
# conversations (scripted router replies through DemoSession; red-team invariants after every turn)
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class RecordingRouter(ReplayInterpreter):
    seen: list[tuple[int, str]] = field(default_factory=list)

    def invoke(self, messages):
        if "Respond READY" not in messages[-1][1]:
            self.seen.append((self.turn, messages[-1][1]))
        return super().invoke(messages)


def change(path, value, evidence=""):
    return {"path": path, "value": value, "evidence": evidence}


def proposes(*changes, revises=False):
    return json.dumps({"route": "experiment_change", "changes": list(changes), "revises": revises,
                       "explanation": "scripted router reply"})


def answers(text):
    return json.dumps({"route": "experiment_question", "answer": text, "changes": []})


def says(text, *replies):
    return {"text": text, "llm": [{"kind": "route", "reply": reply} for reply in replies]}


def talk(tmp_path, *messages, config=None):
    items = [message if isinstance(message, dict) else {"text": message} for message in messages]
    script = {number: [dict(item) for item in message.get("llm", [])] for number, message in enumerate(items, start=1)}
    router = RecordingRouter(seed=0, script=script)
    spec = ConversationSpec(seed=0, persona="replay", length=len(items), live_logic=False, interpreter="replay")
    result = run_conversation(spec, workdir=tmp_path, driver=ReplayDriver(items), interpreter=router,
                              keep_transcript=True, return_session=True, config=config)
    assert result["violations"] == [], result["violations"]      # includes: the protocol does what the plan says
    return result["session"], router


MAKE_ACE = says("Make 2x, 5x and 10x dilutions in A11, C11 and E11.",
                proposes(change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"),
                         change("rows", ["A", "C", "E"], "in A11, C11 and E11")))


def test_the_manual_gui_session_keeps_the_dilutions_in_rows_1_3_5_and_prints_on_1_2_3(tmp_path):
    """runs/ai_dye_demo_gui/20260927_111453, with the router replies the new vocabulary gives."""
    session, _ = talk(
        tmp_path,
        says("Make 2x, 5x and 10x dilutions in rows 1, 3 and 5.",
             proposes(change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"),
                      change("rows", ["A", "C", "E"], "rows 1, 3 and 5"))), "yes",
        says("but i want to print in rows 1 2 and 3",
             proposes(change("paper_rows", ["A", "B", "C"], "print in rows 1 2 and 3"))), "yes",
        says("the dilutions still should be in rows 1 3 5 and printing in 1 2 3 so they will be different",
             proposes(change("rows", ["A", "C", "E"], "the dilutions still should be in rows 1 3 5"),
                      change("paper_rows", ["A", "B", "C"], "printing in 1 2 3"))))
    config = session.state.config
    assert made(config) == [("A11", 2), ("C11", 5), ("E11", 10)]
    assert plan_pairs(config) == [("A11", "A1"), ("C11", "B1"), ("E11", "C1")] == protocol_pairs(config)
    assert session.pending is None                   # the last message restates what is set: nothing new to approve


def test_case_1_print_those_samples_on_paper_rows_a_b_c_in_column_3(tmp_path):
    session, _ = talk(tmp_path, MAKE_ACE, "yes",
                      says("Print those three samples on paper rows A, B and C in paper column 3.",
                           proposes(change("paper_rows", ["A", "B", "C"], "on paper rows A, B and C"),
                                    change("paper_columns", [3], "in paper column 3"))), "yes")
    config = session.state.config
    assert made(config) == [("A11", 2), ("C11", 5), ("E11", 10)]
    assert plan_pairs(config) == ACE_TO_ABC == protocol_pairs(config)


def test_the_reverse_case_dilutions_a_b_c_print_on_paper_rows_a_c_e(tmp_path):
    session, _ = talk(
        tmp_path,
        says("Make 2x, 5x and 10x dilutions in A11, B11 and C11.",
             proposes(change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"),
                      change("dilution.rows", ["A", "B", "C"], "in A11, B11 and C11"))), "yes",
        says("Print those three samples on paper rows A, C and E in paper column 3.",
             proposes(change("paper_rows", ["A", "C", "E"], "on paper rows A, C and E"),
                      change("paper_columns", [3], "in paper column 3"))), "yes")
    config = session.state.config
    assert made(config) == [("A11", 2), ("B11", 5), ("C11", 10)]
    assert plan_pairs(config) == [("A11", "A3"), ("B11", "C3"), ("C11", "E3")] == protocol_pairs(config)


def test_cases_2_to_5_change_one_dimension_at_a_time(tmp_path):
    session, _ = talk(
        tmp_path,
        says("Make 2x, 5x and 10x dilutions in A11, C11 and E11, and print them in paper column 3.",
             proposes(change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"),
                      change("rows", ["A", "C", "E"], "in A11, C11 and E11"),
                      change("paper_columns", [3], "paper column 3"))), "yes",
        # case 2
        says("Keep the dilutions in A11, C11 and E11, but change the print rows to A, B and C.",
             proposes(change("paper_rows", ["A", "B", "C"], "change the print rows to A, B and C"))), "yes",
        # case 3
        says("Change only the printing rows to B, D and F.",
             proposes(change("paper_rows", ["B", "D", "F"], "printing rows to B, D and F"))), "yes",
        # case 4
        says("Change only the dilution wells to B11, D11 and H11.",
             proposes(change("rows", ["B", "D", "H"], "dilution wells to B11, D11 and H11"))), "yes",
        # case 5
        says("The dilution wells and print rows should be different.",
             answers("They are: the dilutions are in B11, D11 and H11 and they print on paper rows B, D and F.")))
    history = [record["changes"] for record in session.state.history]
    case2, case3, case4 = history[1], history[2], history[3]
    assert [c["path"] for c in case2] == ["print.paper_rows"] and [c["path"] for c in case3] == ["print.paper_rows"]
    assert {c["path"] for c in case4} <= {"dilution.rows", "dilution.start_row", "dilution.factors"}
    config = session.state.config
    assert made(config) == [("B11", 2), ("D11", 5), ("H11", 10)]
    assert plan_pairs(config) == [("B11", "B3"), ("D11", "D3"), ("H11", "F3")] == protocol_pairs(config)
    assert session.state.revision == 4 and session.pending is None       # case 5 changed nothing


def test_a_long_session_keeps_both_row_settings_through_unrelated_changes(tmp_path):
    """PHASE 8 of the 2026-09-27 investigation, deterministic: drops, the paper slot, questions, paper columns and the
    well volume are changed over 14 turns; neither row setting drifts, and "keep my dilution wells the same but change
    the printing rows" changes only the paper rows at the end."""
    session, router = talk(
        tmp_path, MAKE_ACE, "yes",
        says("Use 3 drops per spot.", proposes(change("print.droplets_per_spot", 3, "3 drops per spot"))), "yes",
        says("Put the paper in slot 8.", proposes(change("deck.paper.slot", 8, "paper in slot 8"))), "yes",
        says("Why do we mix before printing?", answers("Mixing makes each well uniform before a drop is taken.")),
        says("What can you help me with?", answers("I can plan dilutions and prints and explain the plan.")),
        says("Print in paper columns 2 and 3.", proposes(change("paper_columns", [2, 3], "paper columns 2 and 3"))),
        "yes",
        says("Each dilution should be 200 uL total.",
             proposes(change("dilution.total_volume_ul", "200 uL", "200 uL total"))), "yes",
        says("How much dye will this use?", answers("About 150 µL of dye across the three dilutions.")),
        says("Keep my dilution wells the same but change the printing rows to A, B and C.",
             json.dumps({"route": "experiment_change", "preserve": ["dilution.rows"], "explanation": "scripted",
                         "changes": [change("paper_rows", ["A", "B", "C"], "change the printing rows to A, B and C")]})),
        "yes")
    config = session.state.config
    assert made(config) == [("A11", 2), ("C11", 5), ("E11", 10)]
    assert plan_pairs(config) == [("A11", "A2"), ("A11", "A3"), ("C11", "B2"), ("C11", "B3"), ("E11", "C2"),
                                  ("E11", "C3")] == protocol_pairs(config)
    assert config["print"]["droplets_per_spot"] == 3 and config["deck"]["paper"]["slot"] == 8
    assert len(protocol_dispenses(config)) == 18                               # 6 positions x 3 stacked drops
    assert config["dilution"]["total_volume_ul"] == 200
    last = session.state.history[-1]["changes"]
    assert [change["path"] for change in last] == ["print.paper_rows"]         # only the print rows changed
    # what the router saw on the last turn: both row settings, separately, in the structured state
    shown = [human for turn, human in router.seen if "change the printing rows" in human][-1]
    state = json.loads(shown.split("CURRENT STATE (revision 5):\n", 1)[1].split("\n\nDECK:", 1)[0])
    assert state["dilution.rows"] == ["A", "C", "E"] and state["print.paper_rows"] is None


def test_a_revision_of_the_paper_rows_keeps_the_waiting_column(tmp_path):
    session, _ = talk(tmp_path, MAKE_ACE, "yes",
                      says("Print them in paper column 3.", proposes(change("paper_columns", [3], "paper column 3"))),
                      says("and on paper rows A, B and C", proposes(change("paper_rows", ["A", "B", "C"],
                                                                           "paper rows A, B and C"), revises=True)),
                      "yes")
    assert plan_pairs(session.state.config) == ACE_TO_ABC


# ════════════════════════════════════════════════════════════════════════════════
# what the router is told, and what the scientist sees
# ════════════════════════════════════════════════════════════════════════════════

def test_the_router_sees_plate_rows_and_paper_rows_as_separate_state():
    config = series(*ACE, paper_rows=["A", "B", "C"])
    message = router_human_message("hello", RouterContext(config=config, revision=3,
                                                          plan="\n".join(render.plan_sections(config))))
    state = json.loads(message.split("CURRENT STATE (revision 3):\n", 1)[1].split("\n\nDECK:", 1)[0])
    assert state["dilution.rows"] == ["A", "C", "E"] and state["print.paper_rows"] == ["A", "B", "C"]
    assert "A | B | C   (A11 → A, C11 → B, E11 → C)" in message


def test_the_router_prompt_separates_plate_rows_from_paper_rows():
    assert '{"path": "paper_rows"' in ROUTER_PROMPT and "PLATE ROWS vs PAPER ROWS" in ROUTER_PROMPT
    assert "each printing\n  on the same paper row" not in ROUTER_PROMPT          # the old coupled definition is gone
    assert "never\n  make them match" in ROUTER_PROMPT or "never make them match" in " ".join(ROUTER_PROMPT.split())


def test_the_proposal_says_where_each_dilution_prints_and_that_the_wells_stay():
    state = state_with(series(*ACE))
    proposal = propose(state, {"path": "paper_rows", "value": ["A", "B", "C"], "evidence": "rows 1 2 and 3"},
                       request="but i want to print in rows 1 2 and 3")
    assert render.interpretation_lines(proposal) == [
        "print A11 on paper row A, C11 on paper row B, E11 on paper row C (the dilution wells stay where they are)"]


def test_sparse_dilution_wells_are_listed_not_shown_as_a_range():
    state = state_with(DEFAULT)
    proposal = propose(state, {"path": "dilution.factors", "value": [2, 5, 10], "evidence": "2x, 5x and 10x"},
                       {"path": "rows", "value": ["A", "C", "E"], "evidence": "A11, C11 and E11"},
                       request="Make 2x, 5x and 10x dilutions in A11, C11 and E11.")
    assert any("plate wells A11, C11, E11" in line for line in render.interpretation_lines(proposal))


def test_the_gui_flow_card_shows_the_paper_rows_not_the_plate_rows():
    from src.agents.dye_demo.gui.app import experiment_flow

    step = experiment_flow(series(*ACE, paper_rows=["A", "B", "C"]))[-1]
    assert "dilution wells A11 | C11 | E11" in step.source
    assert "rows A–C (A11→A, C11→B, E11→C)" in step.destination
