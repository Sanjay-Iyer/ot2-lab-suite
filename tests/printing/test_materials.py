"""
tests/test_materials.py
=======================
Tests for the materials / vial-source schema + validation (src/core/materials.py).
Uses the real labware index (labware/*.json), so it also exercises the
filename==loadName rule against the actual custom rack
`tuberack_3dprint_20ml_8vials_v2` (wells A1..A4, B1..B4).
"""
import pytest

from src.core.materials import (
    Material,
    MaterialsConfigError,
    build_labware_index,
    parse_materials,
    raise_if_errors,
    validate_materials,
)

RACK = "tuberack_3dprint_20ml_8vials_v2"
INDEX = build_labware_index()


def _materials(**over):
    base = {
        "water":   {"role": "solvent", "labware": RACK, "vial": "A1", "initial_volume_ul": 15000},
        "ethanol": {"role": "solvent", "labware": RACK, "vial": "A2", "initial_volume_ul": 15000},
        "nanoparticle_stock": {"role": "stock", "labware": RACK, "vial": "A3", "initial_volume_ul": 8000},
    }
    base.update(over)
    return parse_materials({"materials": base})


def test_water_and_ethanol_resolve_to_vials():
    mats = _materials()
    assert mats["water"].vial == "A1" and mats["water"].role == "solvent"
    assert mats["ethanol"].vial == "A2"
    assert mats["nanoparticle_stock"].role == "stock"


def test_real_rack_filename_matches_loadname():
    assert INDEX[RACK].filename_matches_loadname is True
    assert set(["A1", "A2", "A3", "A4", "B1"]).issubset(set(INDEX[RACK].wells))


def test_valid_materials_pass():
    mats = _materials()
    issues = validate_materials(mats, consumed_ul={"water": 1000, "ethanol": 500}, labware_index=INDEX)
    assert not [i for i in issues if i.severity == "error"]


def test_invalid_vial_name_rejected():
    mats = _materials(water={"role": "solvent", "labware": RACK, "vial": "Z9", "initial_volume_ul": 15000})
    issues = validate_materials(mats, labware_index=INDEX)
    assert any("vial 'Z9' is not a well" in i.message for i in issues if i.severity == "error")


def test_missing_labware_rejected_with_hint():
    # The un-versioned name does not exist on disk; should hint the real one.
    mats = _materials(water={"role": "solvent", "labware": "tuberack_3dprint_20ml_8vials",
                             "vial": "A1", "initial_volume_ul": 15000})
    issues = validate_materials(mats, labware_index=INDEX)
    errs = [i for i in issues if i.severity == "error"]
    assert any("no JSON" in i.message and "Did you mean" in i.message for i in errs)


def test_duplicate_vial_assignment_rejected():
    mats = _materials(ethanol={"role": "solvent", "labware": RACK, "vial": "A1", "initial_volume_ul": 15000})
    issues = validate_materials(mats, labware_index=INDEX)
    assert any("already assigned" in i.message for i in issues if i.severity == "error")


def test_duplicate_vial_allowed_when_opted_in():
    mats = _materials(ethanol={"role": "solvent", "labware": RACK, "vial": "A1",
                               "initial_volume_ul": 15000, "allow_shared_vial": True})
    issues = validate_materials(mats, labware_index=INDEX)
    assert not any("already assigned" in i.message for i in issues if i.severity == "error")


def test_insufficient_volume_with_dead_volume():
    # 2000 uL available, dead volume 1000, consume 1500 -> needs 2500 > 2000.
    mats = _materials(water={"role": "solvent", "labware": RACK, "vial": "A1",
                             "initial_volume_ul": 2000, "dead_volume_ul": 1000})
    issues = validate_materials(mats, consumed_ul={"water": 1500}, labware_index=INDEX)
    assert any("insufficient volume" in i.message for i in issues if i.severity == "error")


def test_dead_volume_pushes_over_limit():
    # Consume exactly the initial minus a hair, but dead volume tips it over.
    mats = _materials(water={"role": "solvent", "labware": RACK, "vial": "A1",
                             "initial_volume_ul": 5000, "dead_volume_ul": 1000})
    ok = validate_materials(mats, consumed_ul={"water": 3999}, labware_index=INDEX)
    bad = validate_materials(mats, consumed_ul={"water": 4001}, labware_index=INDEX)
    assert not [i for i in ok if i.severity == "error" and "insufficient" in i.message]
    assert any("insufficient volume" in i.message for i in bad if i.severity == "error")


def test_invalid_aspiration_height_rejected():
    # Vial depth is 55 mm; ask for 100 mm.
    mats = _materials(water={"role": "solvent", "labware": RACK, "vial": "A1",
                             "initial_volume_ul": 15000, "aspirate_height_mm": 100})
    issues = validate_materials(mats, labware_index=INDEX)
    assert any("exceeds vial depth" in i.message for i in issues if i.severity == "error")


def test_unknown_material_referenced_by_step():
    mats = _materials()
    issues = validate_materials(mats, referenced_names={"acetone"}, labware_index=INDEX)
    assert any("unknown material 'acetone'" in i.message for i in issues if i.severity == "error")


def test_unknown_role_warns_only():
    mats = _materials(reporter={"role": "reporter_solution", "labware": RACK, "vial": "A4",
                                "initial_volume_ul": 5000})
    issues = validate_materials(mats, labware_index=INDEX)
    assert any(i.severity == "warning" and "role" in i.message for i in issues)
    assert not [i for i in issues if i.severity == "error"]


def test_raise_if_errors():
    mats = _materials(water={"role": "solvent", "labware": RACK, "vial": "Z9", "initial_volume_ul": 15000})
    with pytest.raises(MaterialsConfigError):
        raise_if_errors(validate_materials(mats, labware_index=INDEX))
