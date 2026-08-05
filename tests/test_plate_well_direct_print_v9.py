"""Regression tests for the direct plate-well paper print v9 plan."""
from __future__ import annotations

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
PROTOCOL = (
    REPO / "src" / "protocols" / "printing" / "09_plate_well_direct_paper_print_v9.py"
)


def _load_protocol_module():
    spec = importlib.util.spec_from_file_location("plate_well_direct_print_v9", PROTOCOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_plan_is_triplicate_1_3_10():
    module = _load_protocol_module()
    passes, layers = module._layer_plan()

    assert layers == {"A": 1, "B": 3, "C": 10}
    assert len(passes) == 10
    assert passes[0]["rows"] == ["A", "B", "C"]
    assert passes[1]["rows"] == ["B", "C"]
    assert passes[2]["rows"] == ["B", "C"]
    assert all(spec["rows"] == ["C"] for spec in passes[3:])
    assert [spec["rest_minutes"] for spec in passes] == [5.0] * 9 + [0.0]


def test_default_volume_and_source_budget():
    module = _load_protocol_module()
    config = module.CONFIG
    layers = config["print"]["layers_by_row"]
    columns = config["print"]["replicate_columns"]
    drops = sum(layers.values()) * len(columns)
    required = drops * config["print"]["volume_ul"]

    assert config["source"]["well"].upper() == "A1"
    assert columns == [7, 8, 9]
    assert drops == 42
    assert required == 210.0
    assert config["source"]["loaded_volume_ul"] >= (
        required + config["source"]["minimum_remaining_ul"]
    )


def test_v9_is_registered_with_builder_and_runner():
    from scripts.build_vial_dilution_print import PROTOCOL_VERSIONS
    import scripts.run_vial_print_robot as runner

    base_protocol, generated_stem = PROTOCOL_VERSIONS[9]
    assert base_protocol == PROTOCOL
    assert generated_stem == "plate_well_direct_print_v9"
    assert runner._PROTOCOL_BY_VERSION[9].name == "plate_well_direct_print_v9_latest.py"
    assert 9 in runner.API_215_VERSIONS
    assert 9 in runner.IMAGELESS_VERSIONS
    assert 9 in runner.NO_MATRIX_VERSIONS
