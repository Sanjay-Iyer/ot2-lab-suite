"""Pre-air-chase tests for the four-clover paper print (v12).

The chase is air aspirated BEFORE the liquid so it ends up on the piston side and
follows the liquid out; air_gap_ul is aspirated AFTER the liquid and leaves first.
These tests pin that distinction, the P20 capacity rule, and the rule that air
never counts against source liquid. No OT-2 and no simulation required.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO / "src/protocols/printing/12_four_clover_paper_print.py"
PAPER_JSON = REPO / "labware/paper_print_96_flat.json"
AIR_CHASE = REPO / "configs/printing/four_clover_air_chase_v12.yaml"
AIR_CHASE_LOCATIONS = REPO / "configs/printing/four_clover_air_chase_locations.yaml"
V12 = REPO / "configs/printing/four_clover_v12.yaml"
LOCATIONS = REPO / "configs/printing/four_clover_locations.yaml"

KEYS = ("d1", "d2", "d3", "d4")
P20_MAX = 20.0


def _module():
    spec = importlib.util.spec_from_file_location("four_clover_air_chase", PROTOCOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _well_xy(name):
    wells = json.loads(PAPER_JSON.read_text(encoding="utf-8"))["wells"]
    well = wells[str(name).upper()]
    return float(well["x"]), float(well["y"])


def _config(printing, centers=None):
    return {
        "destination": {
            "default_clover_geometry": {"half_width_mm": 2.0, "half_height_mm": 2.0},
            "clover_grid": {"enabled": False},
            "manual_clover_centers": centers
            or [{"name": "one", "reference_well": "E6"}],
            "paper_bounds": {
                "x_dimension_mm": 127.76, "y_dimension_mm": 85.48,
                "grid_inset_x_mm": 14.38, "grid_inset_y_mm": 11.24,
                "boundary_mode": "grid", "edge_margin_mm": 4.5,
            },
        },
        "printing": {"layers": 1, **printing},
        "order": {"mode": "clover_by_clover"},
    }


def _resolved_yaml(run_config: Path, locations: Path) -> dict:
    config = yaml.safe_load(run_config.read_text(encoding="utf-8"))
    config.pop("run_modes")
    config.pop("destination_config")
    config["destination"] = yaml.safe_load(
        locations.read_text(encoding="utf-8")
    )["destination"]
    return config


# ── Test 1: no air chase ──────────────────────────────────────────────────────────

def test_zero_chase_leaves_only_the_liquid_aspiration():
    module = _module()
    load = module._piston_load(0.0, 5.0, 0.0)
    assert load["pre_air_chase"] == 0.0
    assert load["liquid"] == 5.0
    assert load["total"] == 5.0


def test_zero_chase_reverts_cleanly_with_the_air_gap_still_present():
    module = _module()
    load = module._piston_load(0.0, 5.0, 1.5)
    assert load["pre_air_chase"] == 0.0
    assert load["total"] == 6.5  # liquid + trailing gap, the original behaviour


def test_zero_chase_is_the_default_when_the_key_is_absent():
    module = _module()
    module.CONFIG = _config({"droplet_volume_ul": 5.0})
    clover = module._resolve_clovers(_well_xy)[0]
    assert clover["pre_air_chase_ul"] == 0.0
    assert clover["pre_air_chase_source"] == "global"


# ── Tests 2 and 3: piston load ────────────────────────────────────────────────────

def test_three_microlitre_chase_gives_an_eight_microlitre_piston_load():
    module = _module()
    load = module._piston_load(3.0, 5.0, 0.0)
    assert load["total"] == 8.0
    assert load["liquid"] == 5.0
    assert load["air_total"] == 3.0


def test_five_microlitre_chase_gives_a_ten_microlitre_piston_load():
    module = _module()
    load = module._piston_load(5.0, 5.0, 0.0)
    assert load["total"] == 10.0
    assert load["liquid"] == 5.0
    assert load["air_total"] == 5.0


def test_piston_load_includes_the_trailing_air_gap_when_present():
    module = _module()
    load = module._piston_load(5.0, 5.0, 1.5)
    assert load["total"] == 11.5
    assert load["air_total"] == 6.5
    assert load["liquid"] == 5.0


@pytest.mark.parametrize("chase,expected", [(0.0, 5.0), (1.5, 6.5), (3.0, 8.0), (5.0, 10.0)])
def test_the_documented_chase_ladder_resolves_as_advertised(chase, expected):
    module = _module()
    assert module._piston_load(chase, 5.0, 0.0)["total"] == expected


# ── Test 4: capacity ──────────────────────────────────────────────────────────────

def _clovers_with_chase(module, chase, volume=5.0):
    module.CONFIG = _config(
        {"droplet_volume_ul": volume, "pre_air_chase_ul": chase}
    )
    return module._resolve_clovers(_well_xy)


def test_fifteen_air_plus_ten_liquid_is_rejected_with_a_clear_message():
    module = _module()
    clovers = _clovers_with_chase(module, 15.0, volume=10.0)
    errors = module._capacity_errors(clovers, 10.0, 0.0, P20_MAX)
    assert len(errors) == 1
    message = errors[0]
    assert "25" in message and "exceeds the P20 capacity" in message
    assert "pre_air_chase_ul 15" in message
    assert "droplet_volume_ul 10" in message


def test_capacity_failure_names_the_offending_clovers():
    module = _module()
    module.CONFIG = _config(
        {"droplet_volume_ul": 5.0, "pre_air_chase_ul": 0.0},
        centers=[
            {"name": "ok", "reference_well": "E6", "pre_air_chase_ul": 5.0},
            {"name": "too_big", "reference_well": "E9", "pre_air_chase_ul": 18.0},
        ],
    )
    clovers = module._resolve_clovers(_well_xy)
    errors = module._capacity_errors(clovers, 5.0, 1.5, P20_MAX)
    assert len(errors) == 1
    assert "too_big" in errors[0]
    assert "ok" not in errors[0].split(":")[0]


def test_capacity_accounts_for_the_trailing_air_gap_too():
    module = _module()
    clovers = _clovers_with_chase(module, 14.0)
    # 14 chase + 5 liquid = 19 uL fits, but the 1.5 uL gap pushes it to 20.5.
    assert module._capacity_errors(clovers, 5.0, 0.0, P20_MAX) == []
    assert module._capacity_errors(clovers, 5.0, 1.5, P20_MAX)


def test_capacity_allows_a_load_exactly_at_the_p20_limit():
    module = _module()
    clovers = _clovers_with_chase(module, 15.0)
    assert module._capacity_errors(clovers, 5.0, 0.0, P20_MAX) == []


def test_negative_chase_is_rejected():
    module = _module()
    module.CONFIG = _config({"droplet_volume_ul": 5.0, "pre_air_chase_ul": -1.0})
    with pytest.raises(ValueError, match="pre_air_chase_ul must be >= 0"):
        module._resolve_clovers(_well_xy)


def test_non_numeric_chase_is_rejected():
    module = _module()
    module.CONFIG = _config({"droplet_volume_ul": 5.0, "pre_air_chase_ul": "lots"})
    with pytest.raises(ValueError, match="must be numeric"):
        module._resolve_clovers(_well_xy)


# ── Test 5: liquid accounting ─────────────────────────────────────────────────────

def test_four_spots_with_a_five_microlitre_chase_still_consume_only_twenty():
    module = _module()
    clovers = _clovers_with_chase(module, 5.0)
    deposits = sum(clover["layers"] for clover in clovers) * len(KEYS)
    volume = module.CONFIG["printing"]["droplet_volume_ul"]

    assert deposits == 4
    assert deposits * volume == 20.0  # NOT 40.0 -- air is not liquid


def test_liquid_consumption_is_independent_of_the_chase_volume():
    module = _module()
    totals = []
    for chase in (0.0, 1.5, 3.0, 5.0):
        clovers = _clovers_with_chase(module, chase)
        deposits = sum(clover["layers"] for clover in clovers) * len(KEYS)
        totals.append(deposits * module.CONFIG["printing"]["droplet_volume_ul"])
    assert totals == [20.0, 20.0, 20.0, 20.0]


def test_layers_scale_liquid_but_air_still_does_not_count():
    module = _module()
    module.CONFIG = _config(
        {"droplet_volume_ul": 5.0, "pre_air_chase_ul": 5.0, "layers": 3}
    )
    clovers = module._resolve_clovers(_well_xy)
    deposits = sum(clover["layers"] for clover in clovers) * len(KEYS)
    assert deposits == 12
    assert deposits * 5.0 == 60.0


# ── Per-clover overrides ──────────────────────────────────────────────────────────

def test_per_clover_chase_overrides_the_global_value():
    module = _module()
    module.CONFIG = _config(
        {"droplet_volume_ul": 5.0, "pre_air_chase_ul": 5.0},
        centers=[
            {"name": "air_0", "reference_well": "E3", "pre_air_chase_ul": 0.0},
            {"name": "air_3", "reference_well": "E6", "pre_air_chase_ul": 3.0},
            {"name": "air_global", "reference_well": "E9"},
        ],
    )
    clovers = {c["name"]: c for c in module._resolve_clovers(_well_xy)}
    assert clovers["air_0"]["pre_air_chase_ul"] == 0.0
    assert clovers["air_0"]["pre_air_chase_source"] == "override"
    assert clovers["air_3"]["pre_air_chase_ul"] == 3.0
    assert clovers["air_global"]["pre_air_chase_ul"] == 5.0
    assert clovers["air_global"]["pre_air_chase_source"] == "global"


def test_grid_chase_overrides_apply_by_generated_name():
    module = _module()
    config = _config({"droplet_volume_ul": 5.0, "pre_air_chase_ul": 5.0})
    config["destination"]["clover_grid"] = {
        "enabled": True, "anchor_well": "C3", "rows": 1, "columns": 2,
        "x_pitch_mm": 27.0, "y_pitch_mm": 27.0,
        "pre_air_chase_overrides": {"clover_r1c2": 0.0},
    }
    module.CONFIG = config
    clovers = {c["name"]: c for c in module._resolve_clovers(_well_xy)}
    assert clovers["clover_r1c1"]["pre_air_chase_ul"] == 5.0
    assert clovers["clover_r1c2"]["pre_air_chase_ul"] == 0.0


# ── Test 6: dry-run reporting ─────────────────────────────────────────────────────

class _CommentRecorder:
    """Stands in for a ProtocolContext: only .comment() is exercised."""

    def __init__(self):
        self.lines = []

    def comment(self, message):
        self.lines.append(str(message))

    @property
    def text(self):
        return "\n".join(self.lines)


def _resolved_stub(module, clovers):
    order_mode, plan = module._print_order(clovers)
    return {
        "clovers": clovers, "plan": plan, "order_mode": order_mode,
        "deposits": sum(c["layers"] for c in clovers) * len(KEYS),
        "source_name": "A2",
    }


def test_dry_run_reports_air_liquid_piston_and_the_numbered_sequence():
    module = _module()
    module.CONFIG = _config(
        {"droplet_volume_ul": 5.0, "pre_air_chase_ul": 5.0, "air_gap_ul": 1.5,
         "dispense_height_mm": 4.0, "push_out_ul": 3.0, "blow_out": False}
    )
    module.CONFIG["source"] = {
        "aspirate_height_mm": 4.0, "park_height_mm": 5.0,
        "kind": "20 mL vial", "material": "BP",
        "loaded_volume_ul": 5000.0, "minimum_remaining_ul": 100.0,
    }
    clovers = module._resolve_clovers(_well_xy)
    recorder = _CommentRecorder()
    module._report_drop_sequence(recorder, _resolved_stub(module, clovers))

    text = recorder.text
    assert "FOUR CLOVER DROP SEQUENCE" in text
    assert "Pre-air chase:      5.0 uL" in text
    assert "Liquid aspiration:  5.0 uL" in text
    assert "Trailing air gap:   1.5 uL" in text
    assert "Total piston load:  11.5 uL" in text
    assert "aspirate 5.0 uL AIR" in text
    assert "Aspirate 5.0 uL LIQUID" in text
    assert "Dispense 11.5 uL piston volume" in text
    assert "Liquid deposited per spot: 5 uL" in text


def test_dry_run_distinguishes_the_chase_from_the_air_gap_in_tip_order():
    module = _module()
    module.CONFIG = _config(
        {"droplet_volume_ul": 5.0, "pre_air_chase_ul": 5.0, "air_gap_ul": 1.5,
         "dispense_height_mm": 4.0, "push_out_ul": 0.0, "blow_out": False}
    )
    module.CONFIG["source"] = {
        "aspirate_height_mm": 4.0, "park_height_mm": 5.0, "kind": "vial",
        "material": "BP", "loaded_volume_ul": 5000.0, "minimum_remaining_ul": 0.0,
    }
    clovers = module._resolve_clovers(_well_xy)
    recorder = _CommentRecorder()
    module._report_drop_sequence(recorder, _resolved_stub(module, clovers))

    text = recorder.text
    assert "[5.0 uL chase air][5.0 uL liquid][1.5 uL gap air]" in text
    assert "liquid exits first and the chase air follows it out" in text
    assert "anti-drip in transit" in text


def test_dry_run_flags_blow_out_as_a_separate_forceful_pulse():
    module = _module()
    module.CONFIG = _config(
        {"droplet_volume_ul": 5.0, "pre_air_chase_ul": 5.0, "air_gap_ul": 1.5,
         "dispense_height_mm": 4.0, "push_out_ul": 3.0, "blow_out": True}
    )
    module.CONFIG["source"] = {
        "aspirate_height_mm": 4.0, "park_height_mm": 5.0, "kind": "vial",
        "material": "BP", "loaded_volume_ul": 5000.0, "minimum_remaining_ul": 0.0,
    }
    clovers = module._resolve_clovers(_well_xy)
    recorder = _CommentRecorder()
    module._report_drop_sequence(recorder, _resolved_stub(module, clovers))
    assert "SEPARATE forceful air" in recorder.text
    assert "blow_out is enabled" in recorder.text


def test_dry_run_says_so_when_there_is_no_chase():
    module = _module()
    module.CONFIG = _config(
        {"droplet_volume_ul": 5.0, "pre_air_chase_ul": 0.0, "air_gap_ul": 1.5,
         "dispense_height_mm": 4.0, "push_out_ul": 3.0, "blow_out": True}
    )
    module.CONFIG["source"] = {
        "aspirate_height_mm": 4.0, "park_height_mm": 5.0, "kind": "vial",
        "material": "BP", "loaded_volume_ul": 5000.0, "minimum_remaining_ul": 0.0,
    }
    clovers = module._resolve_clovers(_well_xy)
    recorder = _CommentRecorder()
    module._report_drop_sequence(recorder, _resolved_stub(module, clovers))
    assert "No pre-air chase: normal liquid aspiration path." in recorder.text
    assert "Total piston load:  6.5 uL" in recorder.text


# ── Shipped configs ───────────────────────────────────────────────────────────────

def test_air_chase_config_is_one_clover_four_spots_twenty_microlitres():
    module = _module()
    module.CONFIG = _resolved_yaml(AIR_CHASE, AIR_CHASE_LOCATIONS)
    clovers = module._resolve_clovers(_well_xy)

    assert len(clovers) == 1
    assert clovers[0]["layers"] == 1
    assert clovers[0]["pre_air_chase_ul"] == 5.0
    deposits = len(clovers) * len(KEYS)
    assert deposits == 4
    assert deposits * module.CONFIG["printing"]["droplet_volume_ul"] == 20.0
    assert module._capacity_errors(clovers, 5.0, 1.5, P20_MAX) == []


def test_air_chase_clover_sits_at_the_paper_centre_and_inside_the_bounds():
    module = _module()
    module.CONFIG = _resolved_yaml(AIR_CHASE, AIR_CHASE_LOCATIONS)
    clovers = module._resolve_clovers(_well_xy)
    bounds = module._paper_bounds(
        _well_xy, list(json.loads(PAPER_JSON.read_text(encoding="utf-8"))["wells"])
    )
    assert clovers[0]["center"] == pytest.approx((127.76 / 2, 85.48 / 2))
    assert module._boundary_violations(clovers, bounds, 1.5) == []


def test_air_chase_config_keeps_dry_run_and_disables_blow_out():
    raw = yaml.safe_load(AIR_CHASE.read_text(encoding="utf-8"))
    assert raw["run_modes"]["dry_run"] is True
    assert raw["printing"]["pre_air_chase_ul"] == 5.0
    assert raw["printing"]["droplet_volume_ul"] == 5.0
    assert raw["printing"]["blow_out"] is False
    assert raw["printing"]["dispense_height_mm"] == 4.0  # unchanged on purpose


def test_existing_sweep_config_is_unchanged_by_the_new_feature():
    module = _module()
    module.CONFIG = _resolved_yaml(V12, LOCATIONS)
    clovers = module._resolve_clovers(_well_xy)
    assert all(clover["pre_air_chase_ul"] == 0.0 for clover in clovers)
    load = module._piston_load(0.0, 5.0, 1.5)
    assert load["total"] == 6.5  # exactly the pre-feature piston load
