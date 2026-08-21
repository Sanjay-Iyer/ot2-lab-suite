"""Structured AI/digital boundary tests for modern printing families."""
from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.printing.compiler import apply_workflow_patch
from src.printing.config import load_printing_config, resolve_repo_path
from src.printing.schemas import (
    ComplementaryColumnPatch,
    FourCloverGeometry,
    FourCloverPatch,
    StandardWellGridPatch,
    parse_printing_request,
)
from src.printing.validation import validate_four_clover_config, validate_standard_config
from src.printing.workflows import resolve_printing_request


FOUR_CLOVER = "configs/printing/four_clover_spacing_v13.yaml"
STANDARD = "configs/printing/plate_well_direct_print_v9.yaml"


def test_request_family_is_discriminated_and_live_mode_is_not_a_field():
    request = parse_printing_request(
        {
            "family": "design",
            "workflow_name": "four_clover_spacing",
            "design_name": "four_clover",
            "parameters": {"droplet_volume_ul": 5.0},
        }
    )
    assert request.family.value == "design"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        parse_printing_request(
            {
                "family": "design",
                "workflow_name": "four_clover_spacing",
                "design_name": "four_clover",
                "execution_mode": "live",
            }
        )


def test_invalid_printing_family_is_rejected():
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        parse_printing_request({"family": "arbitrary", "workflow_name": "x"})


def test_negative_droplet_volume_is_rejected_by_python():
    with pytest.raises(ValidationError, match="greater than 0"):
        FourCloverPatch(droplet_volume_ul=-1)


def test_unknown_patch_fields_cannot_reach_config_or_hardware():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        FourCloverPatch.model_validate(
            {"droplet_volume_ul": 5, "source": {"well": "A7"}}
        )


def test_registered_request_validates_nested_parameters_end_to_end():
    with pytest.raises(ValueError, match="invalid parameters"):
        resolve_printing_request(
            {
                "family": "design",
                "workflow_name": "four_clover_spacing",
                "design_name": "four_clover",
                "parameters": {"droplet_volume_ul": -1},
            }
        )
    with pytest.raises(ValueError, match="extra_forbidden"):
        resolve_printing_request(
            {
                "family": "design",
                "workflow_name": "four_clover_spacing",
                "design_name": "four_clover",
                "parameters": {"source": {"well": "A7"}},
            }
        )


def test_unknown_workflow_and_design_are_rejected():
    with pytest.raises(KeyError, match="unknown printing workflow"):
        resolve_printing_request(
            {"family": "standard", "workflow_name": "not_registered"}
        )
    with pytest.raises(ValueError, match="unknown design"):
        resolve_printing_request(
            {
                "family": "design",
                "workflow_name": "four_clover_spacing",
                "design_name": "spiral",
            }
        )


def test_four_clover_geometry_requires_positive_real_half_offsets():
    with pytest.raises(ValidationError, match="greater than 0"):
        FourCloverGeometry(half_width_mm=0, half_height_mm=2)
    with pytest.raises(ValidationError, match="requires d1, d2, d3, and d4"):
        FourCloverGeometry(d1={"x_mm": 0, "y_mm": 0})


def test_destination_config_is_resolved_without_moving_historical_files():
    config = load_printing_config(FOUR_CLOVER)
    assert "destination_config" not in config
    assert len(config["destination"]["manual_clover_centers"]) == 4
    assert config["protocol_version"] == 18


def test_config_loader_rejects_paths_outside_the_repository():
    with pytest.raises(ValueError, match="inside the repository"):
        resolve_repo_path("../outside.yaml")


def test_standard_patch_is_allowlisted_and_preserves_source_and_hardware():
    base = load_printing_config(STANDARD)
    original_source = deepcopy(base["source"])
    original_deck = deepcopy(base["deck"])
    original_pipette = deepcopy(base["pipette"])
    config = apply_workflow_patch(
        base,
        StandardWellGridPatch(
            droplet_volume_ul=4,
            replicate_columns=[6, 7],
            layers_by_row={"A": 1, "B": 2},
        ),
    )
    assert config["print"]["volume_ul"] == 4
    assert config["print"]["replicate_columns"] == [6, 7]
    assert config["source"] == original_source
    assert config["deck"] == original_deck
    assert config["pipette"] == original_pipette


def test_shipped_standard_config_validates_before_build():
    report = validate_standard_config(
        load_printing_config(STANDARD), workflow_name="plate_well_direct_v9"
    )
    assert report.valid, report.model_dump()
    assert report.calculated["deposit_count"] == 42
    assert report.calculated["liquid_required_ul"] == pytest.approx(210.0)


def test_standard_pipette_capacity_violation_is_deterministic():
    config = apply_workflow_patch(
        load_printing_config(STANDARD),
        StandardWellGridPatch(droplet_volume_ul=19.0),
    )
    report = validate_standard_config(config, workflow_name="plate_well_direct_v9")
    assert not report.valid
    assert "pipette_capacity" in {issue.code for issue in report.errors}


def test_v10_layer_keys_are_workflow_specific_and_never_fail_in_compiler():
    with pytest.raises(ValidationError, match="column from 1 through 12"):
        ComplementaryColumnPatch(layers={"A": 2})
    with pytest.raises(ValueError, match="invalid parameters"):
        resolve_printing_request(
            {
                "family": "standard",
                "workflow_name": "complementary_dmmp_v10b",
                "parameters": {"layers": {"12": 2}},
            }
        )


@pytest.mark.parametrize(
    ("path", "name", "expected_deposits"),
    [
        ("configs/printing/complementary_bp_print_v10a.yaml", "complementary_bp_v10a", 42),
        ("configs/printing/complementary_dmmp_print_v10b.yaml", "complementary_dmmp_v10b", 18),
        ("configs/printing/combined_bp_dmmp_print_v11.yaml", "combined_bp_dmmp_v11", 60),
    ],
)
def test_other_registered_standard_families_validate(path, name, expected_deposits):
    report = validate_standard_config(load_printing_config(path), workflow_name=name)
    assert report.valid, report.model_dump()
    assert report.calculated["deposit_count"] == expected_deposits


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda cfg: cfg["print"].update(air_gap_ul=-1), "negative_air_gap"),
        (lambda cfg: cfg["source"].update(loaded_volume_ul=1000), "source_overfill"),
        (lambda cfg: cfg["source"].update(minimum_remaining_ul=-1), "negative_reserve"),
        (lambda cfg: cfg["deck"]["paper"].update(slot=6), "fixed_deck_slot"),
    ],
)
def test_config_controlled_physical_invariants_are_checked(mutation, expected_code):
    config = load_printing_config(STANDARD)
    mutation(config)
    report = validate_standard_config(config, workflow_name="plate_well_direct_v9")
    assert not report.valid
    assert expected_code in {issue.code for issue in report.errors}


def test_nonpositive_standard_layer_counts_are_rejected():
    config = load_printing_config(STANDARD)
    config["print"]["layers_by_row"] = {"A": 0}
    report = validate_standard_config(config, workflow_name="plate_well_direct_v9")
    assert not report.valid
    assert "layer_count" in {issue.code for issue in report.errors}


def test_combined_workflow_rejects_invalid_destinations_and_part_delays():
    config = load_printing_config("configs/printing/combined_bp_dmmp_print_v11.yaml")
    config["destination"]["rows"] = ["Z"]
    config["parts"][0]["rest_minutes"] = -1
    config["parts"][1]["layers"] = {"Z": 0}
    report = validate_standard_config(config, workflow_name="combined_bp_dmmp_v11")
    assert not report.valid
    codes = {issue.code for issue in report.errors}
    assert {"paper_location", "negative_delay", "layer_count"} <= codes


def test_shipped_four_clover_config_validates_and_returns_coordinates():
    report = validate_four_clover_config(
        load_printing_config(FOUR_CLOVER), workflow_name="four_clover_spacing"
    )
    assert report.valid, report.model_dump()
    assert report.calculated["clover_count"] == 4
    assert report.calculated["deposit_count"] == 16
    assert set(report.calculated["coordinates"]) == {
        "sep_2mm",
        "sep_3mm",
        "sep_4mm",
        "sep_5mm",
    }


def test_impossible_four_clover_footprint_is_rejected_before_execution():
    base = load_printing_config(FOUR_CLOVER)
    config = apply_workflow_patch(
        base,
        FourCloverPatch(
            default_geometry={"half_width_mm": 20, "half_height_mm": 20},
            manual_centers=[{"name": "edge", "reference_well": "A1"}],
        ),
    )
    report = validate_four_clover_config(config, workflow_name="four_clover_spacing")
    assert not report.valid
    assert "paper_footprint" in {issue.code for issue in report.errors}


def test_invalid_inter_clover_spacing_is_an_error_even_if_legacy_config_warns():
    base = load_printing_config(FOUR_CLOVER)
    config = apply_workflow_patch(
        base,
        FourCloverPatch(
            manual_centers=[
                {"name": "one", "reference_well": "D6"},
                {"name": "two", "reference_well": "D6", "x_offset_mm": 1},
            ]
        ),
    )
    report = validate_four_clover_config(config, workflow_name="four_clover_spacing")
    assert not report.valid
    assert {"inter_clover_spacing", "duplicate_coordinate"} & {
        issue.code for issue in report.errors
    }


def test_four_clover_pipette_capacity_includes_air_chase_and_air_gap():
    config = apply_workflow_patch(
        load_printing_config(FOUR_CLOVER),
        FourCloverPatch(droplet_volume_ul=15, pre_air_chase_ul=5),
    )
    report = validate_four_clover_config(config, workflow_name="four_clover_spacing")
    assert not report.valid
    assert "pipette_capacity" in {issue.code for issue in report.errors}


def test_unknown_four_clover_grid_override_is_rejected():
    base = load_printing_config("configs/printing/four_clover_grid_v12.yaml")
    config = apply_workflow_patch(
        base,
        FourCloverPatch(
            grid={
                "anchor_well": "B2",
                "rows": 1,
                "columns": 2,
                "x_pitch_mm": 27,
                "y_pitch_mm": 0,
                "layer_overrides": {"does_not_exist": 2},
            }
        ),
    )
    report = validate_four_clover_config(config, workflow_name="four_clover_grid")
    assert not report.valid
    assert "unknown_grid_override" in {issue.code for issue in report.errors}
