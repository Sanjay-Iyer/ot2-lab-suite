import pytest
import pathlib
import shutil
from src.core.config_loader import load_default_config
from src.core.validation.workflow_validator import validate_workflow_against_constraints
from src.utils.preflight import PreflightEngine

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

# --- Preflight AST Checks ---
@pytest.fixture(scope="module")
def preflight_engine():
    return PreflightEngine()

@pytest.fixture
def temp_fixture_dir(tmp_path):
    d = tmp_path / "preflight_test_fixtures"
    d.mkdir()
    return d

def test_preflight_catches_import_winreg(preflight_engine, temp_fixture_dir):
    p = temp_fixture_dir / "test_import_winreg.py"
    p.write_text("import winreg\n")
    res = preflight_engine.validate_file(p)
    assert any("Windows-only module found: 'winreg'" in f.message for f in res.findings)

def test_preflight_catches_from_winreg_import(preflight_engine, temp_fixture_dir):
    p = temp_fixture_dir / "test_from_winreg.py"
    p.write_text("from winreg import OpenKey\n")
    res = preflight_engine.validate_file(p)
    assert any("Windows-only module found: 'winreg'" in f.message for f in res.findings)

def test_preflight_catches_import_msvcrt(preflight_engine, temp_fixture_dir):
    p = temp_fixture_dir / "test_import_msvcrt.py"
    p.write_text("import msvcrt\n")
    res = preflight_engine.validate_file(p)
    assert any("Windows-only module found: 'msvcrt'" in f.message for f in res.findings)

def test_preflight_catches_ctypes_wintypes(preflight_engine, temp_fixture_dir):
    p = temp_fixture_dir / "test_wintypes.py"
    p.write_text("from ctypes import wintypes\n")
    res = preflight_engine.validate_file(p)
    assert any("Windows-only module found: 'wintypes'" in f.message for f in res.findings)

def test_preflight_allows_safe_imports(preflight_engine, temp_fixture_dir):
    p = temp_fixture_dir / "test_safe_imports.py"
    p.write_text("import json\nimport pathlib\nimport typing\nfrom opentrons import protocol_api\ndef run(ctx): pass\nmetadata = {'apiLevel': '2.15'}\n")
    res = preflight_engine.validate_file(p)
    # Should not flag anything related to windows imports
    win_errs = [f for f in res.findings if "Windows-only module" in f.message]
    assert len(win_errs) == 0
