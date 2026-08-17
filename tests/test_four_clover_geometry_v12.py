"""Geometry tests for the four-clover paper print (v12).

These exercise the resolver functions directly with a well-coordinate lookup taken
from labware/paper_print_96_flat.json, so nothing here needs a robot, a
ProtocolContext or a simulation run.
"""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO / "src/protocols/printing/12_four_clover_paper_print.py"
PAPER_JSON = REPO / "labware/paper_print_96_flat.json"
LOCATIONS = REPO / "configs/printing/four_clover_locations.yaml"
GRID_LOCATIONS = REPO / "configs/printing/four_clover_grid_locations.yaml"
V12 = REPO / "configs/printing/four_clover_v12.yaml"
V12_GRID = REPO / "configs/printing/four_clover_grid_v12.yaml"

KEYS = ("d1", "d2", "d3", "d4")


def _module():
    spec = importlib.util.spec_from_file_location("four_clover_v12", PROTOCOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _paper_wells() -> dict:
    return json.loads(PAPER_JSON.read_text(encoding="utf-8"))["wells"]


def _well_xy(name):
    well = _paper_wells()[str(name).upper()]
    return float(well["x"]), float(well["y"])


def _bounds_config():
    return {
        "x_dimension_mm": 127.76,
        "y_dimension_mm": 85.48,
        "grid_inset_x_mm": 14.38,
        "grid_inset_y_mm": 11.24,
        "boundary_mode": "grid",
        "edge_margin_mm": 4.5,
    }


def _config(destination, layers=1):
    return {
        "destination": {"paper_bounds": _bounds_config(), **destination},
        "printing": {"droplet_volume_ul": 5.0, "layers": layers},
        "order": {"mode": "clover_by_clover"},
    }


def _resolved_yaml_config(run_config: Path, locations: Path) -> dict:
    config = yaml.safe_load(run_config.read_text(encoding="utf-8"))
    config.pop("run_modes")
    config.pop("destination_config")
    config["destination"] = yaml.safe_load(
        locations.read_text(encoding="utf-8")
    )["destination"]
    return config


def _offsets(clover):
    return {key: clover["droplets"][key]["offset"] for key in KEYS}


def _absolute(clover):
    return {key: clover["droplets"][key]["absolute"] for key in KEYS}


# ── Symmetric expansion ───────────────────────────────────────────────────────────

def test_symmetric_square_expands_to_the_four_corners():
    module = _module()
    offsets = module._geometry_from_spec(
        {"half_width_mm": 2.0, "half_height_mm": 2.0}, "geometry"
    )
    assert offsets == {
        "d1": (-2.0, 2.0),
        "d2": (2.0, 2.0),
        "d3": (-2.0, -2.0),
        "d4": (2.0, -2.0),
    }


def test_symmetric_half_offsets_are_half_the_droplet_separation():
    """half_width_mm 2.0 means opposing droplets are 4.0 mm apart, not 2.0."""
    module = _module()
    offsets = module._geometry_from_spec(
        {"half_width_mm": 2.0, "half_height_mm": 1.5}, "geometry"
    )
    assert offsets["d2"][0] - offsets["d1"][0] == pytest.approx(4.0)
    assert offsets["d1"][1] - offsets["d3"][1] == pytest.approx(3.0)


def test_rectangle_uses_independent_x_and_y_half_offsets():
    module = _module()
    offsets = module._geometry_from_spec(
        {"half_width_mm": 3.0, "half_height_mm": 1.5}, "geometry"
    )
    assert offsets == {
        "d1": (-3.0, 1.5),
        "d2": (3.0, 1.5),
        "d3": (-3.0, -1.5),
        "d4": (3.0, -1.5),
    }


def test_droplet_overrides_shift_one_droplet_only():
    module = _module()
    offsets = module._geometry_from_spec(
        {
            "half_width_mm": 2.0,
            "half_height_mm": 2.0,
            "droplet_overrides": {"d3": {"x_mm": -3.5}},
        },
        "geometry",
    )
    assert offsets["d3"] == (-3.5, -2.0)
    assert offsets["d1"] == (-2.0, 2.0)
    assert offsets["d2"] == (2.0, 2.0)
    assert offsets["d4"] == (2.0, -2.0)


# ── Explicit / asymmetric geometry ────────────────────────────────────────────────

def test_explicit_asymmetric_offsets_are_kept_verbatim():
    module = _module()
    spec = {
        "d1": {"x_mm": -1.5, "y_mm": 2.0},
        "d2": {"x_mm": 2.5, "y_mm": 2.25},
        "d3": {"x_mm": -1.0, "y_mm": -3.0},
        "d4": {"x_mm": 4.0, "y_mm": -0.5},
    }
    offsets = module._geometry_from_spec(spec, "geometry")
    assert offsets["d1"] == (-1.5, 2.0)
    assert offsets["d2"] == (2.5, 2.25)
    assert offsets["d3"] == (-1.0, -3.0)
    assert offsets["d4"] == (4.0, -0.5)


def test_diamond_geometry_is_expressible():
    module = _module()
    offsets = module._geometry_from_spec(
        {
            "d1": {"x_mm": 0.0, "y_mm": 2.0},
            "d2": {"x_mm": -2.0, "y_mm": 0.0},
            "d3": {"x_mm": 2.0, "y_mm": 0.0},
            "d4": {"x_mm": 0.0, "y_mm": -2.0},
        },
        "geometry",
    )
    assert offsets["d1"] == (0.0, 2.0)
    assert offsets["d4"] == (0.0, -2.0)


def test_partial_explicit_geometry_is_rejected():
    module = _module()
    with pytest.raises(ValueError, match="missing d3, d4"):
        module._geometry_from_spec(
            {"d1": {"x_mm": 0.0, "y_mm": 0.0}, "d2": {"x_mm": 1.0, "y_mm": 0.0}},
            "geometry",
        )


def test_non_numeric_offset_is_rejected():
    module = _module()
    with pytest.raises(ValueError, match="must be numeric"):
        module._geometry_from_spec(
            {"half_width_mm": "wide", "half_height_mm": 2.0}, "geometry"
        )


def test_unknown_geometry_field_is_rejected():
    module = _module()
    with pytest.raises(ValueError, match="unknown field"):
        module._geometry_from_spec(
            {"half_width_mm": 2.0, "half_height_mm": 2.0, "spacing_mm": 4.0},
            "geometry",
        )


# ── Center translation ────────────────────────────────────────────────────────────

def test_center_translation_moves_all_four_droplets_identically():
    module = _module()
    geometry = {"half_width_mm": 2.0, "half_height_mm": 2.0}

    module.CONFIG = _config(
        {
            "default_clover_geometry": geometry,
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [
                {"name": "base", "reference_well": "D6",
                 "x_offset_mm": 0.0, "y_offset_mm": 0.0},
            ],
        }
    )
    base = _absolute(module._resolve_clovers(_well_xy)[0])

    module.CONFIG = _config(
        {
            "default_clover_geometry": geometry,
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [
                {"name": "moved", "reference_well": "D6",
                 "x_offset_mm": 10.0, "y_offset_mm": -5.0},
            ],
        }
    )
    moved = _absolute(module._resolve_clovers(_well_xy)[0])

    for key in KEYS:
        assert moved[key][0] - base[key][0] == pytest.approx(10.0)
        assert moved[key][1] - base[key][1] == pytest.approx(-5.0)


def test_center_offset_does_not_change_intra_clover_geometry():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [
                {"name": "a", "reference_well": "D6"},
                {"name": "b", "reference_well": "D6",
                 "x_offset_mm": 3.0, "y_offset_mm": 4.0},
            ],
        }
    )
    clovers = module._resolve_clovers(_well_xy)
    assert _offsets(clovers[0]) == _offsets(clovers[1])
    assert clovers[0]["extents"]["width"] == pytest.approx(
        clovers[1]["extents"]["width"]
    )


def test_reference_well_supplies_the_origin():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 1.0, "half_height_mm": 1.0},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [{"name": "c", "reference_well": "A1"}],
        }
    )
    clover = module._resolve_clovers(_well_xy)[0]
    # A1 center is (14.38, 74.24) in the labware definition.
    assert clover["center"] == pytest.approx((14.38, 74.24))
    assert clover["droplets"]["d1"]["absolute"] == pytest.approx((13.38, 75.24))


# ── Grid generation ───────────────────────────────────────────────────────────────

def test_grid_generates_row_times_column_centers_at_the_configured_pitch():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
            "clover_grid": {
                "enabled": True,
                "anchor_well": "B2",
                "rows": 3,
                "columns": 4,
                "x_pitch_mm": 27.0,
                "y_pitch_mm": 27.0,
            },
        }
    )
    clovers = module._resolve_clovers(_well_xy)
    assert len(clovers) == 12
    assert [clover["name"] for clover in clovers[:4]] == [
        "clover_r1c1", "clover_r1c2", "clover_r1c3", "clover_r1c4"
    ]

    anchor_x, anchor_y = _well_xy("B2")
    assert clovers[0]["center"] == pytest.approx((anchor_x, anchor_y))
    # Columns march +x, rows march -y (row A is at high y on an OT-2 plate).
    assert clovers[1]["center"][0] - clovers[0]["center"][0] == pytest.approx(27.0)
    assert clovers[1]["center"][1] == pytest.approx(clovers[0]["center"][1])
    assert clovers[4]["center"][1] - clovers[0]["center"][1] == pytest.approx(-27.0)
    assert clovers[4]["center"][0] == pytest.approx(clovers[0]["center"][0])


def test_grid_offsets_translate_the_whole_grid_without_changing_pitch():
    module = _module()
    grid = {
        "enabled": True, "anchor_well": "B2", "rows": 2, "columns": 2,
        "x_pitch_mm": 20.0, "y_pitch_mm": 20.0,
    }
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 1.0, "half_height_mm": 1.0},
            "clover_grid": dict(grid),
        }
    )
    plain = [clover["center"] for clover in module._resolve_clovers(_well_xy)]

    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 1.0, "half_height_mm": 1.0},
            "clover_grid": dict(grid, x_offset_mm=2.0, y_offset_mm=-3.0),
        }
    )
    shifted = [clover["center"] for clover in module._resolve_clovers(_well_xy)]

    for before, after in zip(plain, shifted):
        assert after[0] - before[0] == pytest.approx(2.0)
        assert after[1] - before[1] == pytest.approx(-3.0)


def test_grid_geometry_and_layer_overrides_apply_by_generated_name():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
            "clover_grid": {
                "enabled": True, "anchor_well": "B2", "rows": 1, "columns": 3,
                "x_pitch_mm": 20.0, "y_pitch_mm": 20.0,
                "geometry_overrides": {
                    "clover_r1c2": {"half_width_mm": 3.0, "half_height_mm": 1.0}
                },
                "layer_overrides": {"clover_r1c3": 3},
            },
        }
    )
    clovers = {clover["name"]: clover for clover in module._resolve_clovers(_well_xy)}
    assert _offsets(clovers["clover_r1c1"])["d1"] == (-2.0, 2.0)
    assert _offsets(clovers["clover_r1c2"])["d1"] == (-3.0, 1.0)
    assert clovers["clover_r1c1"]["geometry_source"] == "default"
    assert clovers["clover_r1c2"]["geometry_source"] == "override"
    assert clovers["clover_r1c1"]["layers"] == 1
    assert clovers["clover_r1c3"]["layers"] == 3


def test_grid_rejects_nonpositive_pitch():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 1.0, "half_height_mm": 1.0},
            "clover_grid": {
                "enabled": True, "anchor_well": "B2", "rows": 2, "columns": 2,
                "x_pitch_mm": 0.0, "y_pitch_mm": 20.0,
            },
        }
    )
    with pytest.raises(ValueError, match="x_pitch_mm must be > 0"):
        module._resolve_clovers(_well_xy)


def test_grid_rejects_invalid_clover_count():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 1.0, "half_height_mm": 1.0},
            "clover_grid": {
                "enabled": True, "anchor_well": "B2", "rows": 0, "columns": 2,
                "x_pitch_mm": 20.0, "y_pitch_mm": 20.0,
            },
        }
    )
    with pytest.raises(ValueError, match="rows must be an integer >= 1"):
        module._resolve_clovers(_well_xy)


# ── Boundary ──────────────────────────────────────────────────────────────────────

def test_grid_boundary_box_is_the_well_grid_grown_by_the_margin():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 1.0, "half_height_mm": 1.0},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [{"name": "c", "reference_well": "D6"}],
        }
    )
    bounds = module._paper_bounds(_well_xy, list(_paper_wells()))
    assert bounds["min_x"] == pytest.approx(14.38 - 4.5)
    assert bounds["max_x"] == pytest.approx(113.38 + 4.5)
    assert bounds["min_y"] == pytest.approx(11.24 - 4.5)
    assert bounds["max_y"] == pytest.approx(74.24 + 4.5)


def test_labware_boundary_box_reconstructs_the_full_footprint():
    module = _module()
    config = _config(
        {
            "default_clover_geometry": {"half_width_mm": 1.0, "half_height_mm": 1.0},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [{"name": "c", "reference_well": "D6"}],
        }
    )
    config["destination"]["paper_bounds"]["boundary_mode"] = "labware"
    config["destination"]["paper_bounds"]["edge_margin_mm"] = 0.0
    module.CONFIG = config
    bounds = module._paper_bounds(_well_xy, list(_paper_wells()))
    # 14.38 - 14.38 = 0 and 113.38 + 14.38 = 127.76: the declared labware footprint.
    assert bounds["min_x"] == pytest.approx(0.0)
    assert bounds["max_x"] == pytest.approx(127.76)
    assert bounds["min_y"] == pytest.approx(0.0)
    assert bounds["max_y"] == pytest.approx(85.48)
    assert bounds["notes"] == []


def test_clover_at_the_paper_edge_fails_the_boundary_check():
    module = _module()
    module.CONFIG = _config(
        {
            # Half-offsets far too large for a corner well: d1 lands beyond both
            # the left and the top edge of the usable box.
            "default_clover_geometry": {"half_width_mm": 6.0, "half_height_mm": 6.0},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [{"name": "corner", "reference_well": "A1"}],
        }
    )
    clovers = module._resolve_clovers(_well_xy)
    bounds = module._paper_bounds(_well_xy, list(_paper_wells()))
    violations = module._boundary_violations(clovers, bounds, 1.5)
    assert violations, "a clover hanging off the corner must be reported"
    assert any("corner.d1" in message for message in violations)


def test_clover_well_inside_the_paper_passes_the_boundary_check():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 2.5, "half_height_mm": 2.5},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [{"name": "middle", "reference_well": "D6"}],
        }
    )
    clovers = module._resolve_clovers(_well_xy)
    bounds = module._paper_bounds(_well_xy, list(_paper_wells()))
    assert module._boundary_violations(clovers, bounds, 1.5) == []


def test_boundary_check_uses_the_droplet_footprint_not_just_the_center():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 4.4, "half_height_mm": 1.0},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [{"name": "edge", "reference_well": "A1"}],
        }
    )
    clovers = module._resolve_clovers(_well_xy)
    bounds = module._paper_bounds(_well_xy, list(_paper_wells()))
    # d1 center sits at x 9.98, just inside the 9.88 edge...
    assert module._boundary_violations(clovers, bounds, 0.0) == []
    # ...but a 1.5 mm ring radius pushes it off the usable area.
    assert module._boundary_violations(clovers, bounds, 1.5)


# ── Distances ─────────────────────────────────────────────────────────────────────

def test_intra_clover_minimum_distance_is_the_shorter_side():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 2.0, "half_height_mm": 0.75},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [{"name": "rect", "reference_well": "D6"}],
        }
    )
    clovers = module._resolve_clovers(_well_xy)
    intra, inter = module._distance_report(clovers)
    # Sides are 4.0 mm (x) and 1.5 mm (y); the vertical pair is the tightest.
    assert intra[0]["min_distance"] == pytest.approx(1.5)
    assert inter == []


def test_inter_clover_minimum_distance_is_edge_to_edge_not_center_to_center():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
            "clover_grid": {
                "enabled": True, "anchor_well": "C3", "rows": 1, "columns": 2,
                "x_pitch_mm": 27.0, "y_pitch_mm": 27.0,
            },
        }
    )
    clovers = module._resolve_clovers(_well_xy)
    intra, inter = module._distance_report(clovers)
    assert len(inter) == 1
    # Centers are 27 mm apart; the facing droplets each sit 2 mm inboard.
    assert inter[0]["min_distance"] == pytest.approx(27.0 - 2.0 - 2.0)
    assert set(inter[0]["pair"]) <= set(KEYS)
    assert all(entry["min_distance"] == pytest.approx(4.0) for entry in intra)


def test_inter_clover_distance_is_computed_for_every_pair():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 1.0, "half_height_mm": 1.0},
            "clover_grid": {
                "enabled": True, "anchor_well": "B2", "rows": 2, "columns": 2,
                "x_pitch_mm": 18.0, "y_pitch_mm": 18.0,
            },
        }
    )
    clovers = module._resolve_clovers(_well_xy)
    _, inter = module._distance_report(clovers)
    assert len(inter) == 6  # 4 clovers -> 4*3/2 pairs
    diagonal = math.hypot(18.0, 18.0) - 2 * math.hypot(1.0, 1.0)
    assert max(entry["min_distance"] for entry in inter) == pytest.approx(diagonal)


def test_duplicate_droplet_positions_collapse_to_zero_distance():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {
                "d1": {"x_mm": 0.0, "y_mm": 0.0},
                "d2": {"x_mm": 0.0, "y_mm": 0.0},
                "d3": {"x_mm": 2.0, "y_mm": 0.0},
                "d4": {"x_mm": -2.0, "y_mm": 0.0},
            },
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [{"name": "stacked", "reference_well": "D6"}],
        }
    )
    intra, _ = module._distance_report(module._resolve_clovers(_well_xy))
    assert intra[0]["min_distance"] == pytest.approx(0.0)
    assert intra[0]["pair"] == ("d1", "d2")


# ── Ordering and layers ───────────────────────────────────────────────────────────

def test_clover_by_clover_finishes_one_clover_before_the_next():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [
                {"name": "one", "reference_well": "C3"},
                {"name": "two", "reference_well": "C6", "layers": 2},
            ],
        }
    )
    clovers = module._resolve_clovers(_well_xy)
    mode, plan = module._print_order(clovers)
    assert mode == "clover_by_clover"
    assert len(plan) == 4 + 8
    assert [(c["name"], layer, key) for c, layer, key in plan[:5]] == [
        ("one", 1, "d1"), ("one", 1, "d2"), ("one", 1, "d3"), ("one", 1, "d4"),
        ("two", 1, "d1"),
    ]
    assert plan[-1][0]["name"] == "two" and plan[-1][1] == 2


def test_position_by_position_groups_every_clover_by_droplet_index():
    module = _module()
    config = _config(
        {
            "default_clover_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [
                {"name": "one", "reference_well": "C3"},
                {"name": "two", "reference_well": "C6"},
            ],
        }
    )
    config["order"]["mode"] = "position_by_position"
    module.CONFIG = config
    clovers = module._resolve_clovers(_well_xy)
    mode, plan = module._print_order(clovers)
    assert mode == "position_by_position"
    assert [(c["name"], key) for c, _, key in plan] == [
        ("one", "d1"), ("two", "d1"),
        ("one", "d2"), ("two", "d2"),
        ("one", "d3"), ("two", "d3"),
        ("one", "d4"), ("two", "d4"),
    ]


def test_position_by_position_skips_clovers_that_ran_out_of_layers():
    module = _module()
    config = _config(
        {
            "default_clover_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [
                {"name": "one", "reference_well": "C3", "layers": 1},
                {"name": "two", "reference_well": "C6", "layers": 3},
            ],
        }
    )
    config["order"]["mode"] = "position_by_position"
    module.CONFIG = config
    clovers = module._resolve_clovers(_well_xy)
    _, plan = module._print_order(clovers)
    assert len(plan) == 4 + 12
    assert {c["name"] for c, layer, _ in plan if layer > 1} == {"two"}


def test_unknown_order_mode_is_rejected():
    module = _module()
    config = _config(
        {
            "default_clover_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [{"name": "one", "reference_well": "C3"}],
        }
    )
    config["order"]["mode"] = "spiral"
    module.CONFIG = config
    with pytest.raises(ValueError, match="order.mode must be"):
        module._print_order(module._resolve_clovers(_well_xy))


def test_per_clover_layers_override_the_global_default():
    module = _module()
    module.CONFIG = _config(
        {
            "default_clover_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": [
                {"name": "one", "reference_well": "C3"},
                {"name": "two", "reference_well": "C6", "layers": 2},
                {"name": "three", "reference_well": "C9", "layers": 3},
            ],
        },
        layers=1,
    )
    clovers = module._resolve_clovers(_well_xy)
    assert [clover["layers"] for clover in clovers] == [1, 2, 3]


# ── Shipped configurations ────────────────────────────────────────────────────────

def test_shipped_sweep_config_resolves_to_four_distinct_spacings_on_paper():
    module = _module()
    module.CONFIG = _resolved_yaml_config(V12, LOCATIONS)
    clovers = module._resolve_clovers(_well_xy)
    bounds = module._paper_bounds(_well_xy, list(_paper_wells()))

    assert [clover["name"] for clover in clovers] == [
        "sep_2mm", "sep_3mm", "sep_4mm", "sep_5mm"
    ]
    # Names promise droplet separations of 2/3/4/5 mm, i.e. half-offsets 1/1.5/2/2.5.
    for clover, separation in zip(clovers, (2.0, 3.0, 4.0, 5.0)):
        assert clover["extents"]["width"] == pytest.approx(separation)
        assert clover["extents"]["height"] == pytest.approx(separation)
    assert module._boundary_violations(clovers, bounds, 1.5) == []

    _, inter = module._distance_report(clovers)
    threshold = module.CONFIG["validation"]["min_inter_clover_distance_mm"]
    assert min(entry["min_distance"] for entry in inter) > threshold


def test_shipped_sweep_config_stays_in_dry_run():
    raw = yaml.safe_load(V12.read_text(encoding="utf-8"))
    assert raw["run_modes"]["dry_run"] is True
    assert raw["protocol_version"] == 15


def test_shipped_grid_config_resolves_to_twelve_clovers_inside_the_paper():
    module = _module()
    module.CONFIG = _resolved_yaml_config(V12_GRID, GRID_LOCATIONS)
    clovers = module._resolve_clovers(_well_xy)
    bounds = module._paper_bounds(_well_xy, list(_paper_wells()))

    assert len(clovers) == 12
    assert len({clover["name"] for clover in clovers}) == 12
    assert module._boundary_violations(clovers, bounds, 1.5) == []

    _, inter = module._distance_report(clovers)
    threshold = module.CONFIG["validation"]["min_inter_clover_distance_mm"]
    assert min(entry["min_distance"] for entry in inter) > threshold


def test_shipped_grid_config_stays_in_dry_run():
    raw = yaml.safe_load(V12_GRID.read_text(encoding="utf-8"))
    assert raw["run_modes"]["dry_run"] is True
