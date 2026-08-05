"""Regression tests for the complementary v10a/v10b paper-print pair."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO / "src/protocols/printing/10_complementary_direct_paper_print.py"
LOCATIONS = REPO / "configs/printing/complementary_v10_locations.yaml"
V10A = REPO / "configs/printing/complementary_bp_print_v10a.yaml"
V10B = REPO / "configs/printing/complementary_dmmp_print_v10b.yaml"
V10C = REPO / "configs/printing/complementary_bp_quick_print_v10c.yaml"


def _module():
    spec = importlib.util.spec_from_file_location("complementary_print_v10", PROTOCOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolved_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.pop("run_modes")
    config.pop("destination_config")
    config["destination"] = yaml.safe_load(
        LOCATIONS.read_text(encoding="utf-8")
    )["destination"]
    return config


def test_v10a_bp_plan_is_three_replicates_of_1_3_10():
    module = _module()
    module.CONFIG = _resolved_config(V10A)
    rows, columns, spots, passes = module._layer_plan()

    assert rows == ["A", "B", "C"]
    assert columns == [1, 2, 3]
    assert len(spots) == 9
    assert {spots[f"{row}1"] for row in rows} == {1}
    assert {spots[f"{row}2"] for row in rows} == {3}
    assert {spots[f"{row}3"] for row in rows} == {10}
    assert sum(spots.values()) == 42
    assert module.CONFIG["source"]["loaded_volume_ul"] == 5000.0
    assert len(passes) == 10
    assert [item["rest_minutes"] for item in passes] == [5.0] * 9 + [0.0]


def test_v10b_dmmp_plan_uses_same_nine_locations_and_1_2_3_rows():
    module = _module()
    module.CONFIG = _resolved_config(V10B)
    rows, columns, spots, passes = module._layer_plan()

    assert rows == ["A", "B", "C"]
    assert columns == [1, 2, 3]
    assert len(spots) == 9
    assert {spots[f"A{column}"] for column in columns} == {1}
    assert {spots[f"B{column}"] for column in columns} == {2}
    assert {spots[f"C{column}"] for column in columns} == {3}
    assert sum(spots.values()) == 18
    assert len(passes) == 3
    assert [item["rest_minutes"] for item in passes] == [5.0, 5.0, 0.0]


def test_v10c_prints_once_everywhere_then_ten_extra_at_a3_without_delays():
    module = _module()
    module.CONFIG = _resolved_config(V10C)
    rows, columns, spots, passes = module._layer_plan()

    assert rows == ["A", "B", "C"]
    assert columns == [1, 2, 3]
    assert len(spots) == 9
    assert spots["A3"] == 11
    assert all(count == 1 for name, count in spots.items() if name != "A3")
    assert sum(spots.values()) == 19
    assert len(passes) == 11
    assert set(passes[0]["spots"]) == set(spots)
    assert all(item["spots"] == ["A3"] for item in passes[1:])
    assert all(item["rest_minutes"] == 0.0 for item in passes)
    assert module.CONFIG["print"]["post_dispense_delay_s"] == 0.0


def test_builder_and_runner_register_both_variants():
    from scripts.build_vial_dilution_print import PROTOCOL_VERSIONS
    import scripts.run_vial_print_robot as runner

    assert PROTOCOL_VERSIONS[10] == (PROTOCOL, "complementary_bp_print_v10a")
    assert PROTOCOL_VERSIONS[11] == (PROTOCOL, "complementary_dmmp_print_v10b")
    assert PROTOCOL_VERSIONS[13] == (PROTOCOL, "complementary_bp_quick_print_v10c")
    assert runner._PROTOCOL_BY_VERSION[10].name == "complementary_bp_print_v10a_latest.py"
    assert runner._PROTOCOL_BY_VERSION[11].name == "complementary_dmmp_print_v10b_latest.py"
    assert runner._PROTOCOL_BY_VERSION[13].name == "complementary_bp_quick_print_v10c_latest.py"
    for version in (10, 11, 13):
        assert version in runner.API_215_VERSIONS
        assert version in runner.IMAGELESS_VERSIONS
        assert version in runner.NO_MATRIX_VERSIONS
