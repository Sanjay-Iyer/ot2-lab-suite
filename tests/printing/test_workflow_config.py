"""
tests/test_workflow_config.py
=============================
Tests the authoritative build-time normalizer + validator (src/core/workflow_config.py)
against the three shipped example configs and against deliberately invalid inputs.
Covers: P20-only / P300-only / mixed generation, explicit & automatic selection,
single_spot vs column_8up dispatch resolution, material-to-vial resolution, invalid
volume / invalid vial / insufficient-volume rejection, and imaging flag mapping.
"""
from pathlib import Path

import copy
import pytest
import yaml

from src.core.workflow_config import WorkflowConfigError, normalize_and_validate

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "printing"
STEM = "01_vial_dilution_paper_print"   # Workflow-01 config filename stem


def _load(variant):
    return yaml.safe_load((CONFIG_DIR / f"{STEM}.{variant}.yaml").read_text(encoding="utf-8"))


def _groups(nw):
    return {g["name"]: (g["pipette"], g["layout"]) for g in nw.config["print_groups"]}


# ── generation for each pipette mode ──────────────────────────────────────────

def test_p300_only_generation():
    nw = normalize_and_validate(_load("p300_only"))
    g = _groups(nw)
    assert g["orange_30"] == ("p300_multi_gen2", "column_8up")
    assert g["blue_35"] == ("p300_multi_gen2", "column_8up")


def test_p20_only_generation():
    nw = normalize_and_validate(_load("p20_only"))
    g = _groups(nw)
    assert all(pip == "p20_single_gen2" and layout == "single_spot" for pip, layout in g.values())


def test_mixed_generation_dispatches_both():
    nw = normalize_and_validate(_load("mixed"))
    g = _groups(nw)
    assert g["coarse_30ul"] == ("p300_multi_gen2", "column_8up")
    assert g["coarse_35ul"] == ("p300_multi_gen2", "column_8up")
    assert g["fine_5ul"] == ("p20_single_gen2", "single_spot")
    assert g["fine_10ul"] == ("p20_single_gen2", "single_spot")


def test_explicit_selection_honored():
    # fine_10ul in the p20_only config names the pipette explicitly.
    nw = normalize_and_validate(_load("p20_only"))
    assert nw.config["print_groups"][1]["pipette"] == "p20_single_gen2"


def test_material_to_vial_resolution():
    nw = normalize_and_validate(_load("mixed"))
    mats = nw.config["materials"]
    assert mats["water"]["vial"] == "A1"
    assert mats["ethanol"]["vial"] == "A2"    # ethanol represented as a configured solvent
    # dye vials flow into the internal sources block used by the dilution phase.
    assert nw.config["sources"]["orange_dye_vial"] == "A3"
    assert nw.config["sources"]["blue_dye_vial"] == "A4"


def test_imaging_flags_mapped():
    nw = normalize_and_validate(_load("p300_only"))
    assert nw.config["camera"]["capture_before"] is True
    assert nw.config["camera"]["capture_after"] is True


def test_p20_rack_added_for_p20_configs():
    nw = normalize_and_validate(_load("p20_only"))
    assert nw.config["deck"]["tiprack_p20"]["load_name"] == "opentrons_96_tiprack_20ul"


# ── invalid configs are rejected with actionable errors ───────────────────────

def test_invalid_volume_for_explicit_p300_rejected():
    raw = _load("p300_only")
    raw["print_groups"][0]["volume_ul"] = 5
    raw["print_groups"][0]["pipette"] = "p300_multi_gen2"
    with pytest.raises(WorkflowConfigError, match="outside p300_multi_gen2"):
        normalize_and_validate(raw)


def test_invalid_vial_rejected():
    raw = _load("p300_only")
    raw["materials"]["water"]["vial"] = "Z9"
    with pytest.raises(WorkflowConfigError, match="not a well"):
        normalize_and_validate(raw)


def test_insufficient_source_volume_rejected():
    raw = _load("p300_only")
    raw["materials"]["water"]["initial_volume_ul"] = 500
    with pytest.raises(WorkflowConfigError, match="insufficient volume"):
        normalize_and_validate(raw)


def test_out_of_bounds_paper_destination_rejected():
    raw = _load("p300_only")
    raw["print_groups"][0]["destination"]["paper_start_column"] = 12
    raw["print_groups"][0]["replicates"] = 3   # 12,13,14 -> out of bounds
    with pytest.raises(WorkflowConfigError, match="out of bounds"):
        normalize_and_validate(raw)


def test_duplicate_paper_destination_rejected():
    raw = _load("mixed")
    raw["print_groups"][2]["destination"]["paper_start_column"] = 1  # collide with coarse_30ul
    with pytest.raises(WorkflowConfigError, match="duplicate destination"):
        normalize_and_validate(raw)


def test_p20_group_but_no_p20_mounted_rejected():
    raw = _load("p300_only")   # only the P300 is mounted
    raw["print_groups"].append({
        "name": "fine", "volume_ul": 5, "pipette": "auto", "layout": "single_spot",
        "source": {"plate_column": "11", "wells": ["A11"]}, "replicates": 1,
        "destination": {"paper_start_column": 6}, "tips": {"well": "A1"},
    })
    with pytest.raises(WorkflowConfigError, match="No mounted pipette"):
        normalize_and_validate(raw)
