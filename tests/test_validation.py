import pytest
from src.core.config_loader import load_default_config
from src.core.validation.workflow_validator import validate_workflow_against_constraints

def test_default_dilution_loads():
    config = load_default_config("dilution")
    assert config["workflow_type"] == "dilution"
    result = validate_workflow_against_constraints(config)
    assert result.valid is True

def test_default_printing_loads():
    config = load_default_config("printing")
    assert config["workflow_type"] == "printing"
    result = validate_workflow_against_constraints(config)
    assert result.valid is True

def test_volume_300ul_standard_plate_passes():
    config = load_default_config("dilution")
    config["dilution"]["final_volume_ul"] = 300
    config["labware"]["destination"]["name"] = "corning_96_wellplate_360ul_flat"
    result = validate_workflow_against_constraints(config)
    assert result.valid is True

def test_volume_1000ul_standard_plate_fails():
    config = load_default_config("dilution")
    config["dilution"]["final_volume_ul"] = 1000
    config["labware"]["destination"]["name"] = "corning_96_wellplate_360ul_flat"
    result = validate_workflow_against_constraints(config)
    assert result.valid is False
    assert any("exceeds the maximum well volume" in e.message for e in result.errors)

def test_volume_1000ul_deep_well_passes():
    config = load_default_config("dilution")
    config["dilution"]["final_volume_ul"] = 1000
    config["labware"]["destination"]["name"] = "nest_96_wellplate_2ml_deep"
    result = validate_workflow_against_constraints(config)
    assert result.valid is True

def test_split_transfer_warning():
    config = load_default_config("dilution")
    config["dilution"]["final_volume_ul"] = 1000
    config["labware"]["destination"]["name"] = "nest_96_wellplate_2ml_deep"
    config["pipette"]["name"] = "p300_single_gen2"
    config["pipette"]["allow_split_transfers"] = True
    result = validate_workflow_against_constraints(config)
    assert result.valid is True
    assert any("exceeds the p300_single_gen2 maximum" in w.message for w in result.warnings)

def test_split_transfer_error_when_disabled():
    config = load_default_config("dilution")
    config["dilution"]["final_volume_ul"] = 1000
    config["labware"]["destination"]["name"] = "nest_96_wellplate_2ml_deep"
    config["pipette"]["name"] = "p300_single_gen2"
    config["pipette"]["allow_split_transfers"] = False
    result = validate_workflow_against_constraints(config)
    assert result.valid is False
    assert any("split transfers are disabled" in e.message for e in result.errors)

def test_invalid_slot_fails():
    config = load_default_config("dilution")
    config["labware"]["source"]["slot"] = 13
    result = validate_workflow_against_constraints(config)
    assert result.valid is False
    assert any("not a valid OT-2 deck slot" in e.message for e in result.errors)

def test_duplicate_slots_fail():
    config = load_default_config("dilution")
    config["labware"]["source"]["slot"] = 1
    config["labware"]["dilution"]["slot"] = 1
    result = validate_workflow_against_constraints(config)
    assert result.valid is False
    assert any("Duplicate deck slot assignments" in e.message for e in result.errors)
