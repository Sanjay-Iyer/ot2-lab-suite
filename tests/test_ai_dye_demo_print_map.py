"""Print maps: which plate well prints on which paper positions, independent of the dilution series.

One source well may feed many paper positions, several wells may split the prints, and a print-only run needs no
dilution factors. Making dilutions still puts exactly one factor in each selected row. The plan the scientist approves
and the protocol the robot runs must describe the same motion (checked against a recording fake OT-2 and, when the
pinned simulator is installed, against the real Opentrons 2.15 simulator). No robot is contacted.
"""
from __future__ import annotations

import re
from copy import deepcopy

import pytest

from src.agents.dye_demo.model import DEFAULT_CONFIG, FieldError, liquid_handling, load_config, normalize_source_map
from src.agents.dye_demo.natural import SelectionError, expand_print_map
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.fake_opentrons import load_protocol_module, run_protocol
from src.agents.dye_demo.redteam.simulate_states import BASE_PROTOCOL, PINNED_SIMULATOR, _builder
from src.agents.dye_demo.state import SOURCES_PRESENT, ExperimentState, ProposalRejected
from src.agents.dye_demo.validation import validate

PLATE = "corning_96_wellplate_360ul_custom"
PAPER = "paper_print_96_flat"


@pytest.fixture()
def config():
    return load_config(DEFAULT_CONFIG)


def with_map(config, value, *, dilution=False):
    """The config after the print-map selection `value` (exactly as a proposal would expand it)."""
    config = deepcopy(config)
    config["dilution"]["enabled"] = dilution
    changes, _ = expand_print_map(value, config)
    for change in changes:
        section, key = change["path"].split(".")
        config[section][key] = change["value"]
    return config


def mapping(config):
    return [(source.well, list(source.positions)) for source in build_plan(config).print_sources]


# ── the reported failure and the rule it came from ───────────────────────────────

def test_print_only_from_one_existing_well_needs_no_dilution_factors(config):
    """'i only have sample in 96 well plate column 11 row 1 use this for all prints' used to fail with
    'number of selected rows (1) must match dilution factors count (8)': that rule now applies only when this run
    makes dilutions."""
    config["dilution"].update(enabled=False, rows=["A"], start_row="A")          # the old, row-based reading
    report = validate(config)
    assert report.ok, report.error_messages()
    assert not any("must match dilution factors count" in message for message in report.error_messages())
    mapped = with_map(load_config(DEFAULT_CONFIG), [{"source": "A11", "positions": "all"}])
    assert validate(mapped).ok
    assert mapping(mapped) == [("A11", ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"])]
    assert build_plan(mapped).wells == []                       # no dilution factor is invented for A11


def test_making_dilutions_still_puts_one_factor_in_each_selected_row(config):
    config["dilution"].update(factors=[2, 5, 10], rows=["A", "C"])
    codes = [issue.code for issue in validate(config).errors]
    assert "dilution.rows_count" in codes
    config["dilution"]["rows"] = ["A", "C", "E"]
    assert validate(config).ok
    assert [(well.well, well.factor) for well in build_plan(config).wells] == [("A11", 2), ("C11", 5), ("E11", 10)]


# ── expansion: the router names sources, Python lays out positions ───────────────

def test_one_source_one_print(config):
    assert mapping(with_map(config, [{"source": "A11", "count": 1}])) == [("A11", ["A1"])]


def test_one_source_ten_prints_is_direct(config):
    assert mapping(with_map(config, [{"source": "A11", "count": 10}])) == [("A11", [f"A{c}" for c in range(1, 11)])]


def test_explicit_unequal_split_is_direct(config):
    assert mapping(with_map(config, [{"source": "A11", "count": 6}, {"source": "B11", "count": 4}])) == [
        ("A11", [f"A{c}" for c in range(1, 7)]), ("B11", [f"B{c}" for c in range(1, 5)])]
    assert mapping(with_map(config, [{"source": "A11", "columns": [1, 2, 3, 4, 5, 6]},
                                     {"source": "B11", "columns": [7, 8, 9, 10]}])) == [
        ("A11", [f"A{c}" for c in range(1, 7)]), ("B11", [f"B{c}" for c in range(7, 11)])]


def test_one_source_on_every_current_position(config):
    config["print"]["replicates"] = 2                           # the plan prints 8 rows x 2 columns now
    positions = build_plan(config).print_positions
    assert mapping(with_map(config, [{"source": "C11", "positions": "all"}])) == [("C11", positions)]


def test_two_sources_and_a_total_without_a_split_is_a_question_with_the_even_split_ready(config):
    with pytest.raises(SelectionError) as raised:
        expand_print_map({"sources": ["A11", "B11"], "total": 10}, config)
    assert "Should I print 5 from each of A11 and B11" in raised.value.question
    assert raised.value.fix == [{"source": "A11", "count": 5}, {"source": "B11", "count": 5}]
    with pytest.raises(SelectionError) as uneven:
        expand_print_map({"sources": ["A11", "B11", "C11"], "total": 10}, config)
    assert uneven.value.question == "How should the 10 prints be divided between A11, B11 and C11?"
    assert uneven.value.fix is None


def test_impossible_wells_and_positions_are_still_refused_in_plain_words(config):
    with pytest.raises(SelectionError, match="I can't use 'Z11': the 96-well plate has rows A-H"):
        expand_print_map([{"source": "Z11", "count": 1}], config)
    with pytest.raises(FieldError, match="the paper has rows A-H and columns 1-12"):
        normalize_source_map([{"source": "A11", "positions": ["A13"]}])
    with pytest.raises(FieldError, match="printed twice"):
        normalize_source_map([{"source": "A11", "positions": ["A1"]}, {"source": "B11", "positions": ["A1"]}])
    config["print"]["source_map"] = [{"source": "Z11", "positions": ["A1"]}]
    assert "print.source_map" in [issue.code for issue in validate(config).errors]


def test_a_source_that_would_run_dry_is_still_refused(config):
    config["dilution"]["prepared_volume_ul"] = 40.0              # the scientist said A11 holds 40 uL
    config = with_map(config, [{"source": "A11", "count": 12}])
    codes = [issue.code for issue in validate(config).errors]
    assert "print.mix_draws_air" in codes or "print.aspirate_draws_air" in codes


def test_non_adjacent_paper_columns_become_an_exact_print_map(config):
    """[1, 3, 5] used to become first column 1 + 3 replicates, i.e. columns 1-3 (validation test E10)."""
    from src.agents.dye_demo.natural import expand_paper_columns

    changes, _ = expand_paper_columns([1, 3, 5], config)
    source_map = next(change["value"] for change in changes if change["path"] == "print.source_map")
    config["print"]["source_map"] = source_map
    plan = build_plan(config)
    assert sorted({int(position[1:]) for position in plan.print_positions}) == [1, 3, 5]
    assert all(source.positions == (f"{source.well[0]}1", f"{source.well[0]}3", f"{source.well[0]}5")
               for source in plan.print_sources)


# ── provenance: the scientist's word about a well is recorded once ───────────────

def test_a_stated_source_is_recorded_on_approval_and_not_challenged_again(config):
    state = ExperimentState(config)
    changes = [{"path": "dilution.enabled", "value": False, "kind": "dependent", "why": "sample already in A11"},
               {"path": "print_map", "value": [{"source": "A11", "count": 3}], "evidence": "use A11"}]
    proposal = state.propose(changes, request="There is already sample in A11. Use A11 for three prints.",
                             source="gui-form")
    assert proposal.physical[SOURCES_PRESENT] == {"A11": {"source": "stated by the operator; recorded on approval"}}
    state.apply(proposal, operator="Tester")
    again = state.propose([{"path": "print_map", "value": [{"source": "A11", "count": 5}], "evidence": "A11"}],
                          request="Now print A11 five times.", source="gui-form")
    assert SOURCES_PRESENT not in again.physical              # already recorded: nothing to confirm again
    state.apply(again, operator="Tester")
    # making new dilutions into a well recorded as holding sample is a physical blocker at run time
    making = state.propose([{"path": "dilution.enabled", "value": True, "kind": "dependent", "why": "test"},
                            {"path": "print.source_map", "value": None, "kind": "dependent", "why": "test"}],
                           request="make the dilutions again", source="gui-form")
    state.apply(making, operator="Tester")
    assert state.run_blockers() and "A11" in state.run_blockers()[0]


def test_an_unnamed_source_well_is_not_used(config):
    state = ExperimentState(config)
    with pytest.raises(ProposalRejected) as raised:
        state.propose([{"path": "print_map", "value": [{"source": "B11", "count": 2}], "evidence": "print twice"}],
                      request="print it twice", source="conversation")
    assert raised.value.question == "Which plate well holds the sample to print?"
    # (grounding only: whether the sample is already in A11, which the dilution step would also fill, is its own
    # question - test_a_dilution_that_fills_the_named_sample_well_asks_first)
    proposal = state.propose([{"path": "print_map", "value": [{"source": "A11", "count": 2}], "evidence": "row 1"}],
                             request="the sample is in column 11 row 1, print it twice", source="conversation",
                             accepted=("made_not_printed",))
    assert [change.path for change in proposal.changes] == ["print.source_map"]


def test_a_dilution_that_fills_the_named_sample_well_asks_first(config):
    # 2026-09-27 novice validation: "I only have sample in A11. Use that everywhere." kept the dilution step on - it
    # would dispense the 8-step series into column 11, the scientist's A11 sample included, then print only A11
    state = ExperimentState(config)
    with pytest.raises(ProposalRejected) as raised:
        state.propose([{"path": "print_map", "value": [{"source": "A11", "positions": "all"}],
                        "evidence": "I only have sample in A11"}],
                      request="I only have sample in A11. Use that everywhere.", source="conversation")
    assert raised.value.kind == "made_not_printed"
    assert raised.value.question.startswith("Is your sample already in plate well A11?")
    # skipping the dilutions prints what is in A11, recorded as the scientist's statement on approval
    proposal = state.propose([{"path": "print_map", "value": [{"source": "A11", "positions": "all"}],
                               "evidence": "I only have sample in A11"},
                              {"path": "dilution.enabled", "value": False, "kind": "dependent",
                               "why": "you said the sample is already in the plate"}],
                             request="I only have sample in A11. Use that everywhere.", source="conversation")
    assert proposal.after["dilution"]["enabled"] is False and "A11" in proposal.physical["sources_present"]
    # printing every dilution the run makes is the plan itself: nothing to ask
    words = "print A11, B11, C11, D11, E11, F11, G11 and H11 1 time each"
    every = state.propose([{"path": "print_map", "value": [{"source": f"{row}11", "count": 1} for row in "ABCDEFGH"],
                            "evidence": words}], request=words, source="conversation", dry_run=True)
    assert [change.path for change in every.changes] == ["print.source_map"]


# ── the robot runs the approved map (recording fake OT-2) ────────────────────────

@pytest.fixture(scope="module")
def protocol_module():
    return load_protocol_module()


def _variants():
    base = load_config(DEFAULT_CONFIG)
    sparse = deepcopy(base)
    sparse["dilution"].update(factors=[2, 4, 16], rows=["B", "D", "H"], start_row="B")
    sparse["print"]["replicates"] = 4
    made_then_mapped = with_map(base, [{"source": "B11", "count": 3}, {"source": "D11", "positions": ["H12"]}],
                                dilution=True)
    new_tips = with_map(base, [{"source": "A11", "count": 6}, {"source": "B11", "count": 4}])
    new_tips["tips"]["policy"] = "new_tip_every_transfer"
    return {
        "sparse_rows_B_D_H": sparse,
        "one_source_ten_prints": with_map(base, [{"source": "A11", "count": 10}]),
        "explicit_6_4": with_map(base, [{"source": "A11", "count": 6}, {"source": "B11", "count": 4}]),
        "made_then_mapped": made_then_mapped,
        "map_new_tip_every_transfer": new_tips,
        "stacked_drops_from_one_well": with_map(dict(base, print={**base["print"], "droplets_per_spot": 3}),
                                                [{"source": "E11", "count": 4}]),
    }


VARIANTS = _variants()


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_protocol_motion_matches_the_approved_plan(protocol_module, variant):
    config = VARIANTS[variant]
    assert validate(config).ok, validate(config).error_messages()
    plan = build_plan(config)
    log = run_protocol(protocol_module, config).log
    assert [entry[1] for entry in log if entry[0] == "pick_up_tip"] == [tip.tip for tip in plan.tips]
    gap = liquid_handling(config)["air_gap_ul"]
    # transfers: plate dispenses that do not follow a plate aspiration (a dilution mix does)
    plate_dispenses = [(entry[2][1], entry[1]) for index, entry in enumerate(log)
                       if entry[0] == "dispense" and entry[2][0] == PLATE
                       and not (log[index - 1][0] == "aspirate" and log[index - 1][2][0] == PLATE)]
    assert plate_dispenses == [(op.destination, op.volume_ul + gap) for op in plan.operations if op.kind == "transfer"]
    drops = [key[1] for _, _, key, _ in (e for e in log if e[0] == "dispense") if key[0] == PAPER]
    assert drops == [op.destination for op in plan.operations if op.kind == "print" for _ in range(op.droplets)]
    # print draws: plate aspirations that do not go straight back into the well (a dilution mix does)
    drawn_from = [entry[2][1] for index, entry in enumerate(log) if entry[0] == "aspirate" and entry[2][0] == PLATE
                  and not (log[index + 1][0] == "dispense" and log[index + 1][2][0] == PLATE)]
    assert drawn_from == [op.source for op in plan.operations if op.kind == "print" for _ in range(op.droplets)]


def test_explicit_rows_are_the_rows_the_protocol_dilutes(protocol_module):
    """Validation test E04: rows B, D and H were approved; the protocol used to dilute B, C and D."""
    log = run_protocol(protocol_module, VARIANTS["sparse_rows_B_D_H"]).log
    diluted = sorted({key[1] for _, _, key, _ in (e for e in log if e[0] == "dispense") if key[0] == PLATE})
    assert diluted == ["B11", "D11", "H11"]


# ── and the real Opentrons simulator agrees (pinned API 2.15 interpreter) ─────────

_DISPENSE = re.compile(r"Dispensing\s+[\d.]+\s+uL\s+into\s+([A-H]\d{1,2})\s+of\s+.+?\s+on\s+(?:slot\s+)?(\d+)", re.I)
_ASPIRATE = re.compile(r"Aspirating\s+([\d.]+)\s+uL\s+from\s+([A-H]\d{1,2})\s+of\s+.+?\s+on\s+(?:slot\s+)?(\d+)", re.I)


@pytest.mark.skipif(not PINNED_SIMULATOR.exists(), reason="pinned Opentrons 2.15 simulator not installed")
@pytest.mark.parametrize("variant", ["one_source_ten_prints", "explicit_6_4", "sparse_rows_B_D_H"])
def test_opentrons_simulator_prints_exactly_the_plan(tmp_path, variant):
    builder = _builder()
    config = deepcopy(VARIANTS[variant])
    plan = build_plan(config)
    run_modes = config.pop("run_modes", {})
    run_modes["dry_run"] = False
    config["protocol_version"] = 19
    path = tmp_path / f"{variant}.py"                    # never src/protocols/generated
    path.write_text(builder.build_source(BASE_PROTOCOL.read_text(encoding="utf-8"), config, run_modes),
                    encoding="utf-8")
    ok, output = builder.simulate(path, str(PINNED_SIMULATOR))
    assert ok, output[-1500:]
    paper_slot, plate_slot = str(config["deck"]["paper"]["slot"]), str(config["deck"]["plate"]["slot"])
    drops = [match.group(1) for match in map(_DISPENSE.search, output.splitlines())
             if match and match.group(2) == paper_slot]
    assert drops == [op.destination for op in plan.operations if op.kind == "print" for _ in range(op.droplets)]
    drop_volume = float(plan.operations[-1].volume_ul)
    sources = [match.group(2) for match in map(_ASPIRATE.search, output.splitlines())
               if match and match.group(3) == plate_slot and abs(float(match.group(1)) - drop_volume) < 1e-6]
    assert sources == [op.source for op in plan.operations if op.kind == "print" for _ in range(op.droplets)]
