"""Parameter schema for custom labware — what it accepts and what it refuses.

The negative cases matter most: this schema is the guardrail that stops an LLM
(or a half-written YAML) from inventing a physical dimension.
"""

import pytest
import yaml
from pydantic import ValidationError

from src.labware.schemas import CommonLabwareSpec, RectangularGridSpec
from src.labware.templates import list_templates, load_spec, resolve_config_path
from src.utils.paths import LABWARE_CONFIG_DIR

BASELINE = dict(
    load_name="test_grid",
    display_name="Test Grid",
    rows=8, cols=12,
    x_offset=14.38, y_offset=74.24,
    x_spacing=9.0, y_spacing=9.0,
    shape="circular", diameter=6.86,
    depth=10.67, total_liquid_volume=360,
    x_dimension=127.76, y_dimension=85.48, z_dimension=14.22,
)


def spec(**overrides) -> RectangularGridSpec:
    return RectangularGridSpec(**{**BASELINE, **overrides})


# ── valid baselines ───────────────────────────────────────────────

def test_baseline_config_is_valid():
    s = spec()
    assert s.position_count == 96
    assert s.family == "rectangular_grid"


def test_every_repo_config_validates():
    """Every shipped labware config must load under the strict schema."""
    names = list_templates()
    assert names, "expected labware configs in configs/labware/"
    for name in names:
        load_spec(name)  # raises on failure


def test_template_yaml_documents_every_field():
    """_template.yaml is the parameter reference — it must stay in sync."""
    data = yaml.safe_load(
        (LABWARE_CONFIG_DIR / "_template.yaml").read_text(encoding="utf-8")
    )
    documented = set(data) - {"output_dir"}
    assert set(RectangularGridSpec.model_fields) == documented


def test_resolve_config_path_accepts_bare_name_and_filename():
    assert resolve_config_path("paper_print_96_flat").name == "paper_print_96_flat.yaml"
    assert resolve_config_path("paper_print_96_flat.yaml").name == "paper_print_96_flat.yaml"


# ── required geometry: nothing is silently defaulted ──────────────

@pytest.mark.parametrize(
    "field",
    ["rows", "cols", "x_offset", "y_offset", "x_spacing", "y_spacing",
     "depth", "total_liquid_volume", "x_dimension", "y_dimension", "z_dimension"],
)
def test_physical_geometry_has_no_default(field):
    """Omitting a measured dimension must fail, not fall back to a guess."""
    payload = {k: v for k, v in BASELINE.items() if k != field}
    with pytest.raises(ValidationError) as exc:
        RectangularGridSpec(**payload)
    assert field in str(exc.value)


def test_identity_fields_do_have_sane_defaults():
    s = spec()
    assert s.namespace == "custom_beta"
    assert s.version == 1
    assert s.quirks == []
    assert s.is_tiprack is False


# ── negative cases ────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["My Plate", "my-plate", "MyPlate", "my plate", ""])
def test_load_name_must_be_a_safe_string(bad):
    with pytest.raises(ValidationError):
        spec(load_name=bad)


def test_namespace_must_be_a_safe_string():
    with pytest.raises(ValidationError):
        spec(namespace="Custom Beta")


@pytest.mark.parametrize("field", ["x_dimension", "y_dimension", "z_dimension", "depth", "total_liquid_volume"])
def test_negative_dimensions_are_rejected(field):
    with pytest.raises(ValidationError):
        spec(**{field: -1.0})


@pytest.mark.parametrize("field", ["x_dimension", "y_dimension", "z_dimension", "depth"])
def test_zero_dimensions_are_rejected(field):
    with pytest.raises(ValidationError):
        spec(**{field: 0})


def test_zero_diameter_is_rejected():
    with pytest.raises(ValidationError):
        spec(diameter=0)


@pytest.mark.parametrize("rows,cols", [(0, 12), (8, 0), (-1, 12)])
def test_invalid_grid_counts_are_rejected(rows, cols):
    with pytest.raises(ValidationError):
        spec(rows=rows, cols=cols)


def test_zero_x_spacing_with_multiple_columns_is_rejected():
    with pytest.raises(ValidationError) as exc:
        spec(x_spacing=0)
    assert "x_spacing" in str(exc.value)


def test_zero_y_spacing_with_multiple_rows_is_rejected():
    with pytest.raises(ValidationError) as exc:
        spec(y_spacing=0)
    assert "y_spacing" in str(exc.value)


def test_zero_spacing_is_allowed_on_a_single_row():
    """A 1x12 trough legitimately has y_spacing 0."""
    s = spec(rows=1, y_spacing=0.0)
    assert s.position_count == 12


def test_negative_offsets_are_rejected():
    with pytest.raises(ValidationError):
        spec(x_offset=-1.0)


def test_unknown_field_is_rejected_as_a_typo():
    with pytest.raises(ValidationError) as exc:
        spec(well_diameter=6.86)
    assert "well_diameter" in str(exc.value)


# ── shape cross-rules ─────────────────────────────────────────────

def test_circular_requires_diameter():
    payload = {k: v for k, v in BASELINE.items() if k != "diameter"}
    with pytest.raises(ValidationError):
        RectangularGridSpec(**payload)


def test_circular_rejects_rectangular_fields():
    with pytest.raises(ValidationError):
        spec(x_length=8.2, y_length=71.2)


def test_rectangular_requires_both_lengths():
    with pytest.raises(ValidationError):
        spec(shape="rectangular", diameter=None, x_length=8.2)


def test_rectangular_rejects_diameter():
    with pytest.raises(ValidationError):
        spec(shape="rectangular", x_length=8.2, y_length=71.2)


def test_rectangular_well_is_valid():
    s = spec(shape="rectangular", diameter=None, x_length=8.2, y_length=71.2)
    assert s.well_footprint_x == 8.2
    assert s.well_footprint_y == 71.2


# ── tip rack cross-rules ──────────────────────────────────────────

def test_tiprack_requires_tip_length():
    with pytest.raises(ValidationError):
        spec(is_tiprack=True)


def test_tip_length_without_tiprack_is_rejected():
    with pytest.raises(ValidationError):
        spec(tip_length=51.7)


# ── enums ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "field,bad",
    [("plate_format", "96standard"), ("display_category", "plate"),
     ("shape", "round"), ("well_bottom_shape", "conical")],
)
def test_enum_fields_reject_near_misses(field, bad):
    with pytest.raises(ValidationError):
        spec(**{field: bad})


# ── derived values ────────────────────────────────────────────────

def test_well_z_auto_computes_from_depth():
    assert spec(well_z=None).resolved_well_z == pytest.approx(14.22 - 10.67, abs=1e-9)


def test_explicit_well_z_wins():
    """paper_print_96_flat pins 6.0, not the computed 13.9."""
    assert spec(depth=0.1, z_dimension=14.0, well_z=6.0).resolved_well_z == 6.0


def test_common_spec_is_the_shared_base():
    assert issubclass(RectangularGridSpec, CommonLabwareSpec)
    grid_only = set(RectangularGridSpec.model_fields) - set(CommonLabwareSpec.model_fields)
    assert grid_only == {"rows", "cols", "x_offset", "y_offset", "x_spacing", "y_spacing"}
