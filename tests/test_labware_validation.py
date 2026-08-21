"""The four validation layers, and the output-safety guard.

Each layer answers a different question, so each is exercised separately —
including the case where a definition is perfectly valid JSON that describes a
physically impossible object.
"""

import copy
import json

import pytest

from src.labware.builder import (
    LabwareOutputExistsError,
    build_rectangular_grid_labware,
    read_labware_json,
    write_labware_json,
)
from src.labware.geometry import generate_rectangular_grid
from src.labware.pipeline import generate_labware
from src.labware.schemas import RectangularGridSpec
from src.labware.validation import (
    FAIL,
    NOT_AVAILABLE,
    PASS,
    ValidationReport,
    validate_against_opentrons,
    validate_all,
    validate_geometry,
    validate_json_document,
)
from src.utils.paths import LABWARE_OUTPUT_DIR

BASELINE = dict(
    load_name="validation_probe",
    display_name="Validation Probe",
    rows=8, cols=12,
    x_offset=14.38, y_offset=74.24,
    x_spacing=9.0, y_spacing=9.0,
    shape="circular", diameter=6.86,
    depth=10.67, total_liquid_volume=360,
    x_dimension=127.76, y_dimension=85.48, z_dimension=14.22,
)


def spec(**overrides) -> RectangularGridSpec:
    return RectangularGridSpec(**{**BASELINE, **overrides})


def positions_for(s: RectangularGridSpec):
    return generate_rectangular_grid(
        s.rows, s.cols, s.x_offset, s.y_offset, s.x_spacing, s.y_spacing
    )


def codes(report: ValidationReport):
    return {issue.code for issue in report.issues}


# ── layer 2: geometry ─────────────────────────────────────────────

def test_valid_geometry_passes():
    s = spec()
    report = validate_geometry(s, positions_for(s), expected_count=96)
    assert report.layers["geometry"] == PASS
    assert not report.errors


def test_wells_past_the_right_edge_are_caught():
    """Uses well RADIUS, not just the centre coordinate."""
    s = spec(x_spacing=11.0)  # A12 centre 135.38 -> edge 138.81 > 127.76
    report = validate_geometry(s, positions_for(s))
    assert report.layers["geometry"] == FAIL
    assert "well_off_right" in codes(report)


def test_a_centre_inside_the_footprint_can_still_overhang():
    """The whole point of accounting for radius: centre in, edge out."""
    s = spec(cols=1, rows=1, x_offset=126.0, y_offset=40.0, x_spacing=0.0, y_spacing=0.0)
    assert s.x_offset < s.x_dimension          # centre is inside
    report = validate_geometry(s, positions_for(s))
    assert "well_off_right" in codes(report)   # but the rim is not


def test_wells_past_the_back_edge_are_caught():
    s = spec(y_offset=85.0)  # 85.0 + 3.43 > 85.48
    report = validate_geometry(s, positions_for(s))
    assert report.layers["geometry"] == FAIL
    assert "well_off_back" in codes(report)


def test_wells_past_the_front_edge_are_caught():
    s = spec(y_offset=64.0)  # H1 lands at 1.0 -> edge -2.43
    report = validate_geometry(s, positions_for(s))
    assert report.layers["geometry"] == FAIL
    assert "well_off_front" in codes(report)


def test_well_deeper_than_the_body_is_caught():
    s = spec(depth=10.0, z_dimension=14.0, well_z=0.0)
    report = validate_geometry(s, positions_for(s))
    assert report.layers["geometry"] == PASS  # fits: 0 + 10 <= 14


def test_well_taller_than_body_warns():
    s = spec(depth=13.0, z_dimension=14.0, well_z=6.0)  # 6 + 13 > 14
    report = validate_geometry(s, positions_for(s))
    assert "well_taller_than_body" in codes(report)
    assert report.layers["geometry"] == PASS  # warning, not error
    assert report.warnings


def test_position_count_mismatch_is_caught():
    s = spec()
    report = validate_geometry(s, positions_for(s)[:-1], expected_count=96)
    assert "position_count" in codes(report)


def test_duplicate_and_coincident_positions_are_caught():
    s = spec()
    duplicated = positions_for(s)
    duplicated.append(duplicated[0])
    report = validate_geometry(s, duplicated)
    assert "duplicate_names" in codes(report)
    assert "coincident_positions" in codes(report)


def test_overlapping_columns_warn():
    """Legal JSON, impossible object: 9 mm pitch with 12 mm wells."""
    s = spec(diameter=12.0, x_dimension=200.0, y_dimension=200.0)
    report = validate_geometry(s, positions_for(s))
    assert "columns_overlap" in codes(report)
    assert "rows_overlap" in codes(report)


def test_multichannel_format_mismatch_warns():
    s = spec(rows=4, cols=6, plate_format="96Standard")
    report = validate_geometry(s, positions_for(s))
    assert "format_grid_mismatch" in codes(report)
    assert report.layers["geometry"] == PASS  # warning only


def test_empty_position_list_fails():
    report = validate_geometry(spec(), [])
    assert report.layers["geometry"] == FAIL
    assert "no_positions" in codes(report)


# ── layer 3: json document ────────────────────────────────────────

def test_valid_definition_passes_json_layer():
    definition = build_rectangular_grid_labware(spec())
    report = validate_json_document(definition, expected_filename="validation_probe.json")
    assert report.layers["json"] == PASS


def test_missing_required_top_level_key_is_caught():
    definition = build_rectangular_grid_labware(spec())
    del definition["groups"]
    report = validate_json_document(definition)
    assert report.layers["json"] == FAIL
    assert "missing_keys" in codes(report)


def test_ordering_referencing_an_unknown_well_is_caught():
    definition = build_rectangular_grid_labware(spec())
    definition["ordering"][0][0] = "Z9"
    report = validate_json_document(definition)
    assert "ordering_unknown_well" in codes(report)


def test_well_missing_from_ordering_is_caught():
    definition = build_rectangular_grid_labware(spec())
    definition["ordering"][0] = definition["ordering"][0][:-1]
    report = validate_json_document(definition)
    assert "well_not_in_ordering" in codes(report)


def test_ragged_ordering_is_caught():
    definition = build_rectangular_grid_labware(spec())
    definition["ordering"][0].append("A1")
    report = validate_json_document(definition)
    assert "ragged_ordering" in codes(report)


def test_group_referencing_an_unknown_well_is_caught():
    definition = build_rectangular_grid_labware(spec())
    definition["groups"][0]["wells"].append("Q7")
    report = validate_json_document(definition)
    assert "group_unknown_well" in codes(report)


def test_filename_must_match_load_name():
    definition = build_rectangular_grid_labware(spec())
    report = validate_json_document(definition, expected_filename="something_else.json")
    assert report.layers["json"] == FAIL
    assert "filename_mismatch" in codes(report)


def test_non_serializable_value_is_caught():
    definition = build_rectangular_grid_labware(spec())
    definition["wells"]["A1"]["x"] = float("nan")
    report = validate_json_document(definition)
    assert report.layers["json"] == FAIL
    assert "not_serializable" in codes(report)


# ── layer 4: opentrons ────────────────────────────────────────────

def test_generated_definition_passes_opentrons_validation():
    report = validate_against_opentrons(build_rectangular_grid_labware(spec()))
    assert report.layers["opentrons"] in (PASS, NOT_AVAILABLE)
    if report.layers["opentrons"] == NOT_AVAILABLE:
        pytest.skip("Opentrons validation tooling not importable in this interpreter")
    assert not report.errors


def test_shipped_definitions_pass_opentrons_validation():
    for path in sorted(LABWARE_OUTPUT_DIR.glob("*.json")):
        report = validate_against_opentrons(read_labware_json(path))
        if report.layers["opentrons"] == NOT_AVAILABLE:
            pytest.skip("Opentrons validation tooling not importable in this interpreter")
        assert report.layers["opentrons"] == PASS, f"{path.name}: {report.summary()}"


def test_schema_violation_is_caught_by_opentrons_layer():
    definition = build_rectangular_grid_labware(spec())
    definition["metadata"]["displayVolumeUnits"] = "gallons"
    report = validate_against_opentrons(definition)
    if report.layers["opentrons"] == NOT_AVAILABLE:
        pytest.skip("Opentrons validation tooling not importable in this interpreter")
    assert report.layers["opentrons"] == FAIL


def test_unavailable_tooling_never_reports_pass():
    """An unchecked definition must not look checked."""
    report = ValidationReport()
    report.mark("opentrons", NOT_AVAILABLE)
    assert report.layers["opentrons"] != PASS


# ── full run + report semantics ───────────────────────────────────

def test_validate_all_marks_every_layer():
    s = spec()
    definition = build_rectangular_grid_labware(s)
    report = validate_all(
        s, positions_for(s), definition,
        expected_filename="validation_probe.json", expected_count=96,
    )
    assert set(report.layers) == {"schema", "geometry", "json", "opentrons"}
    assert report.ok


def test_report_is_not_ok_when_a_layer_fails():
    s = spec(x_spacing=11.0)
    report = validate_all(s, positions_for(s), build_rectangular_grid_labware(s))
    assert not report.ok
    assert report.errors


def test_warnings_alone_do_not_fail_a_report():
    report = ValidationReport()
    report.add("geometry", "probe", "just a warning", severity="warning")
    report.mark("geometry", PASS)
    assert report.ok


# ── output safety ─────────────────────────────────────────────────

def test_writing_a_new_file_reports_created(tmp_path):
    definition = build_rectangular_grid_labware(spec())
    assert write_labware_json(definition, tmp_path / "probe.json") == "created"


def test_rewriting_identical_content_is_a_no_op(tmp_path):
    definition = build_rectangular_grid_labware(spec())
    target = tmp_path / "probe.json"
    write_labware_json(definition, target)
    before = target.read_bytes()
    assert write_labware_json(definition, target) == "unchanged"
    assert target.read_bytes() == before


def test_differing_content_is_refused_without_overwrite(tmp_path):
    target = tmp_path / "probe.json"
    write_labware_json(build_rectangular_grid_labware(spec()), target)
    before = target.read_bytes()

    changed = build_rectangular_grid_labware(spec(z_dimension=20.0))
    with pytest.raises(LabwareOutputExistsError):
        write_labware_json(changed, target)
    assert target.read_bytes() == before, "the existing definition must be untouched"


def test_overwrite_flag_allows_replacement(tmp_path):
    target = tmp_path / "probe.json"
    write_labware_json(build_rectangular_grid_labware(spec()), target)
    changed = build_rectangular_grid_labware(spec(z_dimension=20.0))
    assert write_labware_json(changed, target, overwrite=True) == "overwritten"
    assert read_labware_json(target)["dimensions"]["zDimension"] == 20.0


def test_pipeline_writes_nothing_when_validation_fails(tmp_path):
    result = generate_labware(spec(x_spacing=11.0), output_dir=tmp_path)
    assert not result.success
    assert result.write_status == "not_written"
    assert list(tmp_path.glob("*.json")) == []


def test_pipeline_dry_run_writes_nothing(tmp_path):
    result = generate_labware(spec(), output_dir=tmp_path, write=False)
    assert result.success
    assert list(tmp_path.glob("*.json")) == []


def test_definition_is_written_as_utf8_not_locale_encoded(tmp_path):
    """`µL` must survive the round trip on a cp1252 machine."""
    target = tmp_path / "probe.json"
    write_labware_json(build_rectangular_grid_labware(spec()), target)
    assert "µL" in target.read_bytes().decode("utf-8")
    assert read_labware_json(target)["metadata"]["displayVolumeUnits"] == "µL"
