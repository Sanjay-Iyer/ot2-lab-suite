"""Replicates are a count; where they print is decided by a deterministic paper allocator (placement.py).

Reported 2026-09-28: with the plan printing in paper column 12, "do 2 replicates" was refused - "the print plan needs
paper column 13, past the paper's 12 columns" - although most of the paper was free. The default layout (replicates side
by side from the first paper column) had become a physical limit. Now the side-by-side layout is kept whenever it fits,
and otherwise every print that fits stays where it is and the others go on the nearest free paper positions, as an
explicit print map that validation, the plan, the recording fake OT-2 and the pinned Opentrons simulator all agree on.
Real limits stay strict: a full paper, and a drop the P20 cannot hold (35 µL). Simulation only.
"""
from __future__ import annotations

import re
from copy import deepcopy

import pytest

from src.agents.dye_demo.gui.labware_svg import render_paper_svg
from src.agents.dye_demo.model import DEFAULT_CONFIG, ROWS, load_config
from src.agents.dye_demo.placement import Need, PlacementError, allocate, allocate_side_by_side
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.fake_opentrons import load_protocol_module, run_protocol
from src.agents.dye_demo.redteam.simulate_states import BASE_PROTOCOL, PINNED_SIMULATOR, _builder
from src.agents.dye_demo.state import ExperimentState, ProposalRejected
from src.agents.dye_demo.validation import validate
from tests.test_ai_dye_demo_request_understanding import change, proposes, says, talk

ALL = [f"{row}{column}" for row in ROWS for column in range(1, 13)]
PAPER = "paper_print_96_flat"


def single_at_a12():
    """One dilution (plate well A11), printed at paper position A12."""
    config = load_config(DEFAULT_CONFIG)
    config["dilution"]["factors"] = [1]
    config["print"]["paper_start_column"] = 12
    return config


def series_at_column_12():
    """The default eight dilutions (A11-H11), printed in paper column 12."""
    config = load_config(DEFAULT_CONFIG)
    config["print"]["paper_start_column"] = 12
    return config


def propose(config, changes, words, *, printed=()):
    state = ExperimentState(config, printed_positions=printed)
    return state.propose(changes, request=words, known_sources=[source.well for source in build_plan(config).print_sources])


def printed_by(config):
    return [(source.well, list(source.positions)) for source in build_plan(config).print_sources]


REPLICATES_2 = [change("print.replicates", 2, "do 2 replicates")]


# ── the reported case: a replicate is not "the next column" ─────────────────────────────────────────────────────────

def test_two_replicates_at_a12_finds_a_second_position_instead_of_column_13():
    proposal = propose(single_at_a12(), REPLICATES_2, "do 2 replicates")
    assert printed_by(proposal.after) == [("A11", ["A12", "A11"])]           # A12 stays; the new print is next to it
    assert proposal.report.ok and "A13" not in build_plan(proposal.after).print_positions
    placed = next(item for item in proposal.changes if item.path == "print.source_map")
    assert placed.kind == "dependent" and "paper column 13, past the paper's 12 columns" in placed.why


def test_add_two_more_replicates_adds_two_prints_to_the_existing_one():
    proposal = propose(single_at_a12(), [change("print.replicates", evidence="add 2 more replicates", op="add",
                                                amount=2)], "add 2 more replicates")
    assert proposal.after["print"]["replicates"] == 3                        # "add 2 more" = 2 more than there are
    assert printed_by(proposal.after) == [("A11", ["A12", "A11", "A10"])]


def test_a_series_at_the_right_edge_keeps_its_replicates_side_by_side_in_the_nearest_free_column():
    proposal = propose(series_at_column_12(), REPLICATES_2, "do 2 replicates")
    assert printed_by(proposal.after) == [(f"{row}11", [f"{row}12", f"{row}11"]) for row in ROWS]
    assert proposal.report.ok


def test_the_side_by_side_layout_is_kept_when_it_fits():
    proposal = propose(load_config(DEFAULT_CONFIG), REPLICATES_2, "do 2 replicates")
    assert [item.path for item in proposal.changes] == ["print.replicates"]  # no print map: nothing to place
    assert proposal.after["print"].get("source_map") is None
    assert sorted({position[1:] for position in build_plan(proposal.after).print_positions}) == ["1", "2"]


def test_a_count_on_a_print_map_is_no_longer_ignored():
    config = load_config(DEFAULT_CONFIG)
    config["dilution"]["enabled"] = False
    config["print"]["source_map"] = [{"source": "A11", "positions": ["A12"]}]
    proposal = propose(config, REPLICATES_2, "do 2 replicates")
    assert printed_by(proposal.after) == [("A11", ["A12", "A11"])]           # it used to still print only A12
    fewer = deepcopy(proposal.after)
    fewer["print"]["replicates"] = 2
    back = propose(fewer, [change("print.replicates", 1, "just one print")], "just one print")
    assert printed_by(back.after) == [("A11", ["A12"])]                      # a lower count keeps the first prints


def test_a_count_that_does_not_fit_a_print_map_asks_instead_of_guessing():
    config = load_config(DEFAULT_CONFIG)
    config["dilution"]["enabled"] = False
    config["print"]["source_map"] = [{"source": "A11", "positions": [f"A{c}" for c in range(1, 7)]},
                                     {"source": "B11", "positions": [f"B{c}" for c in range(1, 5)]}]
    with pytest.raises(ProposalRejected) as raised:
        propose(config, REPLICATES_2, "do 2 replicates")
    assert raised.value.kind == "paper_layout" and "A11 6 times, B11 4 times" in str(raised.value)
    assert raised.value.question.startswith("How many prints of each sample should there be")


# ── where the scientist says where: exact positions, rows, columns, side by side ────────────────────────────────────

@pytest.mark.parametrize("words,entry,expected", [
    ("Put 3 replicates in column 5", {"source": "A11", "count": 3, "columns": [5]}, ["A5", "B5", "C5"]),
    ("Put replicates in columns 3 and 8", {"source": "A11", "columns": [3, 8]}, ["A3", "A8"]),
    ("Print A11 at A3, C3 and E8", {"source": "A11", "positions": ["A3", "C3", "E8"]}, ["A3", "C3", "E8"]),
    ("Put them in E8, A3 and C3", {"source": "A11", "positions": ["E8", "A3", "C3"]}, ["E8", "A3", "C3"]),
    ("Put three next to each other", {"source": "A11", "count": 3, "placement": "adjacent"}, ["A10", "A11", "A12"]),
    ("Three prints on row B", {"source": "A11", "count": 3, "rows": ["B"]}, ["B12", "B11", "B10"]),
    ("Three in the same column", {"source": "A11", "count": 3, "placement": "same_column"}, ["A12", "B12", "C12"]),
])
def test_placement_wishes_are_kept_exactly(words, entry, expected):
    proposal = propose(single_at_a12(), [change("print_map", [entry], words)], words)
    assert printed_by(proposal.after) == [("A11", expected)]                # sparse and unsorted lists stay as named
    assert proposal.report.ok


def test_each_source_keeps_its_own_prints():
    config = series_at_column_12()
    config["dilution"].update(factors=[1, 2], rows=["A", "C"], start_row="A")
    proposal = propose(config, REPLICATES_2, "do 2 replicates")
    assert printed_by(proposal.after) == [("A11", ["A12", "A11"]), ("C11", ["C12", "C11"])]


# ── the allocator: nearest free first, never a position twice, refused only when the paper is full ──────────────────

def test_a_full_right_edge_continues_leftwards_then_into_the_next_rows():
    assert allocate([Need("A11", 3, ("A12",), "A")]) == [["A12", "A11", "A10"]]
    row_a_full = {f"A{column}" for column in range(1, 12)}
    assert allocate([Need("A11", 3, ("A12",), "A")], row_a_full) == [["A12", "B12", "B11"]]


def test_prints_already_planned_or_printed_are_never_reused():
    first, second = allocate([Need("A11", 3, home_row="A"), Need("B11", 3, home_row="B", rows=("A",))])
    assert first == ["A1", "A2", "A3"] and second == ["A4", "A5", "A6"]         # B11 asked for row A: after A11's
    assert allocate([Need("A11", 2, ("A12",), "A")], {"A11"}) == [["A12", "A10"]]   # A11 printed by an earlier run


def test_a_nearly_full_paper_uses_the_positions_that_are_left():
    free = {"C7", "F2", "H12"}
    assert allocate([Need("A11", 3, anchor_column=12)], set(ALL) - free) == [["C7", "F2", "H12"]]


def test_a_full_paper_is_a_real_limit_and_is_refused_plainly():
    with pytest.raises(PlacementError, match="not enough free paper positions for 1 more print of A11: 0 of the "
                                             "paper's 96 positions are free"):
        allocate([Need("A11", 1)], set(ALL))
    with pytest.raises(ProposalRejected) as raised:                          # the same through a proposal
        propose(single_at_a12(), REPLICATES_2, "do 2 replicates", printed=set(ALL) - {"A12"})
    assert raised.value.kind == "paper_layout" and "0 of the paper's 96 positions are free" in str(raised.value)


def test_side_by_side_needs_one_free_column_for_every_row_else_each_sample_on_its_own():
    needs = [Need(f"{row}11", 2, (f"{row}12",), row) for row in "ABC"]
    assert allocate_side_by_side(needs) == [["A12", "A11"], ["B12", "B11"], ["C12", "C11"]]
    assert allocate_side_by_side(needs, {"B11"}) == [["A12", "A10"], ["B12", "B10"], ["C12", "C10"]]
    assert allocate_side_by_side([Need("A11", 2, ("A12",), "A"), Need("B11", 3, ("B12",), "B")]) is None


# ── real limits stay strict ─────────────────────────────────────────────────────────────────────────────────────────

def test_a_35_ul_drop_on_the_p20_is_still_refused():
    with pytest.raises(ProposalRejected, match=r"over the P20's 20 µL"):
        propose(single_at_a12(), [change("print.droplet_volume_ul", 35, "I want to print a 35 µL droplet")],
                "I want to print a 35 µL droplet")


def test_a_first_paper_column_the_scientist_names_is_their_constraint_not_a_default():
    with pytest.raises(ProposalRejected, match="past the paper's 12 columns; start further left"):
        propose(load_config(DEFAULT_CONFIG), [change("print.droplet_volume_ul", [5, 10, 15]),
                                              change("print.paper_start_column", 11)],
                "print 5, 10 and 15 µL drops starting at paper column 11")


# ── a conversation: proposal, approval, and the invariants after every turn (the protocol matches the plan) ─────────

def test_do_2_replicates_in_a_conversation_is_a_proposal_applied_on_yes(tmp_path):
    conversation = talk(tmp_path, says("do 2 replicates", proposes(change("print.replicates", 2, "do 2 replicates"))),
                        "yes", config=single_at_a12())
    assert conversation.proposal_paths(1) == [["print.replicates", "print.source_map"]]
    out = conversation.out(1)
    assert "I did not change anything" not in out
    assert "so the prints stay where they are and the new ones go on the nearest free paper positions (A11)" in out
    assert printed_by(conversation.config) == [("A11", ["A12", "A11"])]


def test_placing_the_plans_own_sample_needs_no_well_name(tmp_path):
    # live Gemini 2026-09-28: "put 3 replicates in column 5" came back as the count plus a print map of A11 - the plan's
    # only sample - and A11 was refused as "not named": only print-map sources counted as already in play
    words = "put 3 replicates in column 5"
    conversation = talk(tmp_path, says(words, proposes(
        change("print.replicates", 3, "put 3 replicates"),
        change("print_map", [{"source": "A11", "count": 3, "columns": [5]}], "in column 5"))), "yes",
        config=single_at_a12())
    assert printed_by(conversation.config) == [("A11", ["A5", "B5", "C5"])]
    assert conversation.config["print"]["replicates"] == 3


# ── the robot prints exactly the allocated positions (recording fake OT-2, pinned Opentrons simulator) ──────────────

def _allocated_variants():
    series = propose(series_at_column_12(), REPLICATES_2, "do 2 replicates").after
    added = propose(single_at_a12(), [change("print.replicates", evidence="add 2 more replicates", op="add", amount=2)],
                    "add 2 more replicates").after
    two_volumes = propose(series_at_column_12(), [change("print.droplet_volume_ul", [5, 3], "5 and 3 µL drops")],
                          "also print 3 µL drops next to the 5 µL ones").after
    return {"series_at_edge_2_replicates": series, "a12_add_2": added, "two_volumes_at_edge": two_volumes}


VARIANTS = _allocated_variants()


def test_two_drop_volumes_past_the_edge_keep_one_volume_per_print():
    config = VARIANTS["two_volumes_at_edge"]
    assert validate(config).ok, validate(config).error_messages()
    # plate well -> paper position: the 5 µL prints keep paper column 12; the 3 µL ones (column 13 does not exist)
    # go in the nearest free column, 11, for every dilution
    volumes = {(op.source, op.destination): op.volume_ul for op in build_plan(config).operations if op.kind == "print"}
    assert all(volumes[(f"{row}11", f"{row}12")] == 5.0 and volumes[(f"{row}11", f"{row}11")] == 3.0 for row in ROWS)
    assert len(volumes) == 16


@pytest.fixture(scope="module")
def protocol_module():
    return load_protocol_module()


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_the_fake_ot2_prints_exactly_the_allocated_positions(protocol_module, variant):
    config = VARIANTS[variant]
    assert validate(config).ok, validate(config).error_messages()
    plan = build_plan(config)
    log = run_protocol(protocol_module, config).log
    drops = [key[1] for _, _, key, _ in (entry for entry in log if entry[0] == "dispense") if key[0] == PAPER]
    assert drops == [op.destination for op in plan.operations if op.kind == "print" for _ in range(op.droplets)]
    assert [entry[1] for entry in log if entry[0] == "pick_up_tip"] == [tip.tip for tip in plan.tips]


_DISPENSE = re.compile(r"Dispensing\s+[\d.]+\s+uL\s+into\s+([A-H]\d{1,2})\s+of\s+.+?\s+on\s+(?:slot\s+)?(\d+)", re.I)


@pytest.mark.skipif(not PINNED_SIMULATOR.exists(), reason="pinned Opentrons 2.15 simulator not installed")
@pytest.mark.parametrize("variant", ["series_at_edge_2_replicates", "a12_add_2"])
def test_the_opentrons_simulator_dispenses_on_exactly_the_allocated_positions(tmp_path, variant):
    builder = _builder()
    config = deepcopy(VARIANTS[variant])
    plan = build_plan(config)
    run_modes = config.pop("run_modes", {})
    run_modes["dry_run"] = False
    config["protocol_version"] = 19
    path = tmp_path / f"{variant}.py"                                         # never src/protocols/generated
    path.write_text(builder.build_source(BASE_PROTOCOL.read_text(encoding="utf-8"), config, run_modes),
                    encoding="utf-8")
    ok, output = builder.simulate(path, str(PINNED_SIMULATOR))
    assert ok, output[-1500:]
    paper_slot = str(config["deck"]["paper"]["slot"])
    drops = [match.group(1) for match in map(_DISPENSE.search, output.splitlines())
             if match and match.group(2) == paper_slot]
    assert drops == [op.destination for op in plan.operations if op.kind == "print" for _ in range(op.droplets)]
    assert "A13" not in drops and len(drops) == len(plan.print_positions)


# ── the page shows the plan's own positions, the new ones marked ─────────────────────────────────────────────────────

def test_the_paper_drawing_shows_the_allocated_positions_and_marks_the_new_ones():
    current = single_at_a12()
    proposed = propose(current, REPLICATES_2, "do 2 replicates").after
    svg = render_paper_svg(proposed, current)
    assert "Position A12 (1 drop / position)" in svg                          # kept: plain green
    assert re.search(r'stroke="#d97706"[^>]*><title>Position A11 \([^)]*new in this proposal\)', svg)
    assert "Position A13" not in svg and "1 new" in svg
    assert "new in this proposal" not in render_paper_svg(proposed)          # the applied plan: nothing marked
