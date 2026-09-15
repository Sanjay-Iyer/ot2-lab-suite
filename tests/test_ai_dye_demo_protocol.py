"""Protocol v19 against a recording fake OT-2.

The operator approves the plan rendered from src/agents/dye_demo/plan.py; the
robot runs src/protocols/printing/13_ai_agent_dilution_print_demo.py. These tests
run the real protocol file against a fake ProtocolContext (no opentrons needed;
src/agents/dye_demo/redteam/fake_opentrons.py, shared with the red-team harness)
and assert that both describe the same motion, that every dilution dispense is
blown out, and that the physically validated print cycle is untouched.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.fake_opentrons import FakeProtocol, load_protocol_module, run_protocol

PLATE = "corning_96_wellplate_360ul_custom"
PAPER = "paper_print_96_flat"
RACK = "tuberack_3dprint_20ml_8vials_v2"


@pytest.fixture(scope="module")
def protocol_module():
    return load_protocol_module()


@pytest.fixture()
def config():
    return load_config(DEFAULT_CONFIG)

def three_dilutions(config):
    config["dilution"].update(factors=[2, 5, 10], total_volume_ul=100.0)
    return config


def print_only_off_deck(config):
    three_dilutions(config)
    config["dilution"]["enabled"] = False
    config["deck"]["tuberack"]["slot"] = "OFF_DECK"
    config["print"].update(droplets_per_spot=3, paper_start_column=2)
    config["tips"]["start_tip"] = "D1"
    return config


def new_tips(config):
    three_dilutions(config)
    config["tips"]["policy"] = "new_tip_every_transfer"
    return config


VARIANTS = {
    "default": lambda config: config,
    "three_dilutions": three_dilutions,
    "print_only_off_deck": print_only_off_deck,
    "new_tip_every_transfer": new_tips,
    "several_volumes": lambda config: config["print"].update(droplet_volume_ul=[5.0, 10.0], replicates=2) or config,
}


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_protocol_motion_matches_the_plan_shown_to_the_operator(protocol_module, config, variant):
    config = VARIANTS[variant](config)
    plan = build_plan(config)
    log = run_protocol(protocol_module, config).log

    assert [entry[1] for entry in log if entry[0] == "pick_up_tip"] == [tip.tip for tip in plan.tips]

    aspirates = [entry for entry in log if entry[0] == "aspirate"]
    transfers = [(key[1], volume) for _, volume, key, _ in aspirates if key[0] == RACK]
    assert transfers == [(op.source, op.volume_ul) for op in plan.operations if op.kind == "transfer"]
    dispenses = [(key[1], volume) for _, volume, key, _ in (e for e in log if e[0] == "dispense") if key[0] == PLATE]
    assert dispenses == [(op.destination, op.volume_ul) for op in plan.operations if op.kind == "transfer"]

    drops = [(key[1]) for _, _, key, _ in (e for e in log if e[0] == "dispense") if key[0] == PAPER]
    expected = [op.destination for op in plan.operations if op.kind == "print" for _ in range(op.droplets)]
    assert drops == expected
    assert sum(1 for entry in log if entry[0] == "mix") == sum(op.kind == "print" for op in plan.operations)


def test_every_dilution_dispense_is_blown_out_where_it_was_dispensed(protocol_module, config):
    log = run_protocol(protocol_module, config).log
    plate_dispenses = [index for index, entry in enumerate(log) if entry[0] == "dispense" and entry[2][0] == PLATE]
    assert plate_dispenses
    for index in plate_dispenses:
        _, _, where, push_out = log[index]
        assert where[2] == "top" and where[3] in (-2.0, -1.0), "dilution dispenses stay above the liquid"
        assert push_out is None, "dilution transfers keep the pipette's default dispense"
        assert log[index + 1] == ("blow_out", where)


def test_the_lab_can_switch_the_dilution_blow_out_off(protocol_module, config):
    config["dilution"]["blow_out_after_dispense"] = False
    log = run_protocol(protocol_module, config).log
    assert not [entry for entry in log if entry[0] == "blow_out" and entry[1][0] == PLATE]


def test_print_cycle_is_the_physically_validated_one(protocol_module, config):
    log = run_protocol(protocol_module, three_dilutions(config)).log
    paper = [index for index, entry in enumerate(log) if entry[0] == "dispense" and entry[2][0] == PAPER]
    assert len(paper) == 3
    for index in paper:
        aspirate, air_gap, dispense, blow_out, delay = log[index - 2], log[index - 1], log[index], log[index + 1], log[index + 2]
        assert aspirate[0] == "aspirate" and aspirate[1] == 5.0 and aspirate[2][0] == PLATE
        assert aspirate[2][2:] == ("bottom", 1.0)
        assert air_gap == ("air_gap", 1.5, 5.0)
        assert dispense[1] == 6.5 and dispense[2][2:] == ("bottom", 1.1) and dispense[3] == 3.0
        assert blow_out == ("blow_out", dispense[2])
        assert delay == ("delay", 2.0)


def test_no_transfer_is_below_the_p20_minimum(protocol_module, config):
    log = run_protocol(protocol_module, config).log          # 16x leaves a 0.63 uL remainder
    volumes = [entry[1] for entry in log if entry[0] == "aspirate"]
    assert min(volumes) >= 1.0
    water_into_h11 = [entry[1] for entry in log if entry[0] == "dispense" and entry[2][:2] == (PLATE, "H11")]
    assert round(sum(water_into_h11), 1) == 150.0            # 140.63 water + 9.38 dye


def test_off_deck_vial_rack_is_not_loaded_for_a_print_only_run(protocol_module, config):
    context = run_protocol(protocol_module, print_only_off_deck(config))
    assert RACK not in [name for name, _ in context.loaded]
    assert not [entry for entry in context.log if entry[0] == "aspirate" and entry[2][0] == RACK]
    assert [entry[1] for entry in context.log if entry[0] == "pick_up_tip"] == ["D1", "E1", "F1"]
    assert any("Off deck (not loaded): tuberack" in text for text in context.comments)


def test_off_deck_labware_that_a_step_needs_stops_the_run_before_loading(protocol_module, config):
    config["deck"]["tuberack"]["slot"] = "OFF_DECK"          # dilution still enabled
    context = FakeProtocol()
    protocol_module.CONFIG = deepcopy(config)
    protocol_module.DEFAULT_DRY_RUN = False
    with pytest.raises(RuntimeError, match="deck.tuberack is OFF_DECK"):
        protocol_module.run(context)
    assert context.loaded == [] and context.log == []


def test_new_tip_policy_never_puts_a_used_tip_into_another_liquid(protocol_module, config):
    log = run_protocol(protocol_module, new_tips(config)).log
    tips = [entry[1] for entry in log if entry[0] == "pick_up_tip"]
    assert len(tips) == len(set(tips))
    per_tip = {}
    for entry in log:
        if entry[0] == "aspirate":
            per_tip.setdefault(entry[3], set()).add(entry[2][:2])
    assert all(len(sources) == 1 for sources in per_tip.values())


def test_operator_and_revision_are_written_into_the_robot_run_log(protocol_module, config):
    config["session"] = {"operator": "Stephen", "session_label": "2026-09-03 Demo 1", "revision": 4}
    comments = run_protocol(protocol_module, config).comments
    assert "Operator: Stephen | Session: 2026-09-03 Demo 1 | Revision: 4" in comments


def test_dry_run_checks_everything_and_moves_nothing(protocol_module, config):
    context = run_protocol(protocol_module, config, dry_run=True)
    assert context.log == []
    assert any(text.startswith("Pre-flight validation passed") for text in context.comments)
