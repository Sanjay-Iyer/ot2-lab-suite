"""Liquid handling of Agent NanoDrop: the configurable plate heights, the air gap, blow-out and the well-plate shake,
mixing after each dilution (never before printing), and drops per paper position.

The dilution sequence is the one physically tested by scripts/test_single_dilution_c12.py: aspirate at the vial, air
gap, dispense liquid + gap low in the plate well, blow out there, then touch_tip on that well (the "shake"). The protocol
runs on the recording fake OT-2; the plan, the validator and the protocol read the same liquid_handling values.
Simulation only.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

import pytest

from src.agents.dye_demo.gui.labware_svg import render_paper_svg
from src.agents.dye_demo.model import DEFAULT_CONFIG, FieldError, liquid_handling, load_config, normalize_source_map
from src.agents.dye_demo.plan import build_plan, transfer_limit
from src.agents.dye_demo.redteam.fake_opentrons import load_protocol_module, run_protocol
from src.agents.dye_demo.state import ExperimentState, ProposalRejected
from src.agents.dye_demo.validation import validate

DEFAULT = load_config(DEFAULT_CONFIG)
PLATE = DEFAULT["deck"]["plate"]["load_name"]
PAPER = DEFAULT["deck"]["paper"]["load_name"]
RACK = DEFAULT["deck"]["tuberack"]["load_name"]


@pytest.fixture(scope="module")
def protocol_module():
    return load_protocol_module()


def config(**liquid):
    result = deepcopy(DEFAULT)
    result["liquid_handling"].update(liquid)
    return result


def change(path, value, evidence):
    return {"path": path, "value": value, "evidence": evidence}


# ── the defaults and where they live ────────────────────────────────────────────────────────────────────────────

def test_the_demo_defaults_are_the_low_volume_plate_settings():
    lh = liquid_handling(DEFAULT)
    assert (lh["plate_aspirate_height_mm"], lh["plate_dispense_height_mm"], lh["plate_mix_height_mm"]) == (0.3, 0.3, 0.3)
    assert lh["air_gap_ul"] == 1.0 and lh["blow_out"] is True
    assert lh["well_plate_shake"] == {"enabled": True, "radius": 1.0, "v_offset_mm": -1.0, "speed_mm_s": 60.0,
                                      "cycles": 1}
    assert DEFAULT["print"]["z_mm"] == 1.1                     # the paper height is its own setting
    assert DEFAULT["tips"]["policy"] == "single_tip"


def test_an_older_plan_keeps_its_heights_and_gains_the_section(tmp_path):
    old = deepcopy(DEFAULT)
    old.pop("liquid_handling")
    old["print"]["aspirate_height_mm"] = 1.0
    old["mixing"]["height_mm"] = 2.0
    old["dilution"].update(blow_out_after_dispense=False, solvent_dispense_from_top_mm=-2.0)
    path = tmp_path / "old.yaml"
    import yaml
    path.write_text(yaml.safe_dump(old), encoding="utf-8")
    loaded = load_config(path)
    lh = loaded["liquid_handling"]
    assert (lh["plate_aspirate_height_mm"], lh["plate_mix_height_mm"], lh["blow_out"]) == (1.0, 2.0, False)
    assert "aspirate_height_mm" not in loaded["print"] and "solvent_dispense_from_top_mm" not in loaded["dilution"]


# ── the dilution sequence and the shake ────────────────────────────────────────────────────────────────────────

def test_the_shake_is_the_c12_touch_tip_on_plate_wells_only(protocol_module):
    log = run_protocol(protocol_module, deepcopy(DEFAULT)).log
    shakes = [entry for entry in log if entry[0] == "touch_tip"]
    plan = build_plan(DEFAULT)
    transfers = sum(op.kind == "transfer" for op in plan.operations)
    mixes = sum(op.kind == "mix" for op in plan.operations)
    assert len(shakes) == transfers + mixes                    # after every plate dispense and after every mix
    assert {entry[1][0] for entry in shakes} == {PLATE}         # never the vial rack, the paper or the tip rack
    assert {entry[2:] for entry in shakes} == {(1.0, -1.0, 60.0)}   # the C12 script's (Opentrons default) geometry
    for index, entry in enumerate(log):                         # nothing shakes after a paper drop
        if entry[0] == "dispense" and entry[2][0] == PAPER:
            assert log[index + 1][0] == "blow_out" and log[index + 2][0] == "delay"


def test_the_shake_geometry_and_repeats_come_from_the_config(protocol_module):
    shaken = config(well_plate_shake={"enabled": True, "radius": 0.8, "v_offset_mm": -2.0, "speed_mm_s": 40.0,
                                      "cycles": 2})
    log = run_protocol(protocol_module, shaken).log
    first = next(index for index, entry in enumerate(log) if entry[0] == "touch_tip")
    assert log[first][2:] == log[first + 1][2:] == (0.8, -2.0, 40.0)        # two cycles, same well
    assert log[first][1] == log[first + 1][1]


@pytest.mark.parametrize("words", ["turn off shaking", "no shake after dispensing"])
def test_the_shake_is_switched_off_in_conversation(words, protocol_module):
    state = ExperimentState(deepcopy(DEFAULT))
    proposal = state.propose([change("well_plate_shake_enabled", False, words)], request=words)
    assert proposal.paths == ["liquid_handling.well_plate_shake.enabled"]
    assert not [entry for entry in run_protocol(protocol_module, proposal.after).log if entry[0] == "touch_tip"]


def test_plate_heights_are_lab_owned_and_checked_against_the_well(protocol_module):
    with pytest.raises(ProposalRejected, match="plate dispense height is lab-owned"):
        ExperimentState(deepcopy(DEFAULT)).propose([change("liquid_handling.plate_dispense_height_mm", 2.0, "2 mm")],
                                                   request="dispense at 2 mm")
    too_deep = config(plate_aspirate_height_mm=0.0)
    assert "the plate aspirate height must be above 0 mm" in " ".join(validate(too_deep).error_messages())
    with pytest.raises(RuntimeError, match="plate_aspirate_height_mm must be above 0"):
        run_protocol(protocol_module, too_deep)


def test_the_air_gap_counts_toward_the_p20(protocol_module):
    assert transfer_limit(DEFAULT) == 19.0
    log = run_protocol(protocol_module, deepcopy(DEFAULT)).log
    assert max(entry[1] for entry in log if entry[0] == "aspirate" and entry[2][0] == RACK) <= 19.0
    assert max(entry[1] for entry in log if entry[0] == "dispense" and entry[2][0] == PLATE) <= 20.0
    assert transfer_limit(config(air_gap_ul=0.0)) == 20.0
    assert "the air gap must be 0 (off)" in " ".join(validate(config(air_gap_ul=19.5)).error_messages())


def test_printing_never_mixes_and_the_dilutions_are_mixed_after_their_dye(protocol_module):
    log = run_protocol(protocol_module, deepcopy(DEFAULT)).log
    assert not [entry for entry in log if entry[0] == "mix"]
    # a mix: aspirate in the plate well at the aspirate height, dispense straight back at the mix height
    pairs = [(entry, log[index + 1]) for index, entry in enumerate(log[:-1])
             if entry[0] == "aspirate" and entry[2][0] == PLATE and log[index + 1][0] == "dispense"
             and log[index + 1][2][0] == PLATE]
    assert Counter(aspirate[2][1] for aspirate, _ in pairs) == Counter({f"{row}11": 2 for row in "BCDEFGH"})
    assert {aspirate[2][2:] for aspirate, _ in pairs} == {("bottom", 0.3)}
    first_print = next(index for index, entry in enumerate(log) if entry[0] == "dispense" and entry[2][0] == PAPER)
    assert all(log.index(aspirate) < first_print for aspirate, _ in pairs)      # every mix happens before printing


# ── drops per paper position ─────────────────────────────────────────────────────────────────────────────────────

def test_drops_are_dispensed_onto_one_position_and_may_differ_per_position(protocol_module):
    mapped = deepcopy(DEFAULT)
    mapped["dilution"]["factors"] = [2, 5, 10]
    mapped["print"]["source_map"] = [{"source": "A11", "positions": {"A1": 1, "A2": 3}},
                                     {"source": "B11", "positions": ["B5"], "drops": 2}]
    assert validate(mapped).ok, validate(mapped).error_messages()
    plan = build_plan(mapped)
    assert [(op.destination, op.droplets) for op in plan.operations if op.kind == "print"] == \
        [("A1", 1), ("A2", 3), ("B5", 2)]
    log = run_protocol(protocol_module, mapped).log
    assert Counter(entry[2][1] for entry in log if entry[0] == "dispense" and entry[2][0] == PAPER) == \
        Counter({"A1": 1, "A2": 3, "B5": 2})
    svg = render_paper_svg(mapped)
    assert "Position A2 (3 drops on this position" in svg and ">3</text>" in svg and "1-3 drops / position" in svg
    assert [op.droplets for op in build_plan(mapped).operations if op.kind == "print"] == [1, 3, 2]


def test_drops_at_named_positions_keep_every_other_print_as_it_was():
    state = ExperimentState(deepcopy(DEFAULT))
    words = "put 3 drops on A2... I mean on B1, and 2 drops on C1"
    proposal = state.propose([change("drops_at", {"B1": 3, "C1": 2}, words)], request=words)
    after = build_plan(proposal.after)
    before = build_plan(DEFAULT)
    assert [(op.source, op.destination) for op in after.operations if op.kind == "print"] == \
        [(op.source, op.destination) for op in before.operations if op.kind == "print"]      # the same prints
    assert {op.destination: op.droplets for op in after.operations if op.kind == "print"} == \
        {"A1": 1, "B1": 3, "C1": 2, "D1": 1, "E1": 1, "F1": 1, "G1": 1, "H1": 1}
    with pytest.raises(ProposalRejected, match="does not print on A5"):
        ExperimentState(deepcopy(DEFAULT)).propose([change("drops_at", {"A5": 3}, "3 drops on A5")],
                                                   request="3 drops on A5")


def test_drop_counts_are_checked():
    with pytest.raises(FieldError, match="drops per paper position are 1-20"):
        normalize_source_map([{"source": "A11", "positions": {"A1": 0}}])
    with pytest.raises(FieldError, match="does not print on it"):
        normalize_source_map([{"source": "A11", "positions": ["A1"], "drops": {"B1": 2}}])


def test_new_behaviour_never_adds_a_tip_under_single_tip(protocol_module):
    busy = deepcopy(DEFAULT)
    busy["tips"]["start_tip"] = "H12"
    busy["print"]["source_map"] = [{"source": "A11", "positions": {"A1": 2, "A2": 3}},
                                   {"source": "C11", "positions": ["C4", "E8"]}]
    busy["print"]["replicates"] = 2
    assert validate(busy).ok, validate(busy).error_messages()
    log = run_protocol(protocol_module, busy).log
    assert [entry for entry in log if entry[0] in ("pick_up_tip", "return_tip", "drop_tip")] == \
        [("pick_up_tip", "H12"), ("return_tip", "H12")]
