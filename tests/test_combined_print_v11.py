"""Regression tests for the one-run combined BP + DMMP v11 protocol."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO / "src/protocols/printing/11_combined_bp_dmmp_paper_print.py"
CONFIG_PATH = REPO / "configs/printing/combined_bp_dmmp_print_v11.yaml"
LOCATIONS = REPO / "configs/printing/complementary_v10_locations.yaml"


def _module():
    spec = importlib.util.spec_from_file_location("combined_print_v11", PROTOCOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolved_config():
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    config.pop("run_modes")
    config.pop("destination_config")
    config["destination"] = yaml.safe_load(
        LOCATIONS.read_text(encoding="utf-8")
    )["destination"]
    return config


def test_combined_plan_runs_bp_then_dmmp_on_same_grid():
    module = _module()
    module.CONFIG = _resolved_config()
    rows, columns, plans = module._resolve_plans()

    assert rows == ["A", "B", "C"]
    assert columns == [1, 2, 3]
    assert [plan["part"]["material"] for plan in plans] == ["BP", "DMMP"]
    assert [sum(plan["spots"].values()) for plan in plans] == [42, 18]
    assert all(len(plan["spots"]) == 9 for plan in plans)
    assert len(plans[0]["passes"]) == 10
    assert len(plans[1]["passes"]) == 3


def test_combined_plan_uses_20_minute_delay_and_separate_tips():
    config = _resolved_config()

    assert config["between_parts_delay_minutes"] == 20.0
    assert [part["print_tip"] for part in config["parts"]] == ["A1", "A2"]
    assert config["parts"][0]["source_role"] == "bp_source"
    assert config["parts"][1]["source_role"] == "dmmp_source"
    assert config["parts"][1]["source_well"].upper() == "A1"


def test_combined_v11_is_registered_with_builder_and_runner():
    from scripts.build_vial_dilution_print import PROTOCOL_VERSIONS
    import scripts.run_vial_print_robot as runner

    assert PROTOCOL_VERSIONS[12] == (PROTOCOL, "combined_bp_dmmp_print_v11")
    assert runner._PROTOCOL_BY_VERSION[12].name == "combined_bp_dmmp_print_v11_latest.py"
    assert 12 in runner.API_215_VERSIONS
    assert 12 in runner.IMAGELESS_VERSIONS
    assert 12 in runner.NO_MATRIX_VERSIONS
