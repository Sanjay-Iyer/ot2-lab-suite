"""Regression tests for the API-2.15 vial dilution / paper print v3 workflow."""
from __future__ import annotations

import ast
import math
from pathlib import Path

import yaml

import scripts.run_vial_print_robot as runner
from scripts.build_vial_dilution_print import validate_v3
from scripts.validate_vial_print import (
    _expected_v3_dilution_cycles,
    _protocol_api_level,
    _v3_static_problems,
)
from src.core.workflow_config import normalize_and_validate


REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO / "configs" / "printing" / "bp_20260723_v3.yaml"
PROTOCOL_PATH = (
    REPO / "src" / "protocols" / "printing"
    / "03_vial_dilution_paper_print_v3.py"
)


def _raw() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _normalized() -> dict:
    return normalize_and_validate(_raw()).config


def test_v3_protocol_api_is_exactly_2_15():
    source = PROTOCOL_PATH.read_text(encoding="utf-8")
    assert _protocol_api_level(source) == (2, 15)
    tree = ast.parse(source)
    requirements = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "requirements"
            for target in node.targets
        )
    )
    assert requirements == {"robotType": "OT-2", "apiLevel": "2.15"}


def test_v3_source_has_no_partial_nozzle_api():
    source = PROTOCOL_PATH.read_text(encoding="utf-8")
    assert "configure_" + "nozzle_layout" not in source
    assert not _v3_static_problems(source, _raw())


def test_v3_builder_safety_checks_pass():
    source = PROTOCOL_PATH.read_text(encoding="utf-8")
    assert validate_v3(_normalized(), source) == []


def test_v3_pipette_roles_and_deck():
    config = _normalized()
    assert config["deck"]["tuberack"]["slot"] == 7
    assert config["deck"]["plate"]["slot"] == 4
    assert config["deck"]["paper"]["slot"] == 5
    assert config["deck"]["tiprack"]["slot"] == 8
    assert config["deck"]["tiprack_p20"]["slot"] == 9
    mounted = {entry["name"]: entry["mount"] for entry in config["pipettes"]}
    assert mounted == {
        "p300_multi_gen2": "right",
        "p20_single_gen2": "left",
    }
    assert config["dilution"]["transfer_pipette"] == "p20_single_gen2"
    assert all(
        group["pipette"] == "p20_single_gen2"
        for group in config["print_groups"]
    )


def test_v3_default_volume_fixture_and_cycle_counts():
    raw = _raw()
    config = _normalized()
    dilution = config["dilution"]
    total = dilution["total_volume_ul"]
    factors = dilution["factors"]["explicit"]
    maximum = dilution["max_transfer_ul"]

    stock_cycles = sum(
        math.ceil((total / factor - 0.01) / maximum) for factor in factors
    )
    water_cycles = sum(
        0
        if total - total / factor <= 0.01
        else math.ceil((total - total / factor - 0.01) / maximum)
        for factor in factors
    )
    assert stock_cycles == 19
    assert water_cycles == 48
    assert _expected_v3_dilution_cycles(raw) == 67

    for factor in factors:
        stock = total / factor
        water = total - stock
        assert stock + water == total
    assert total <= 340
    assert total >= 50 + dilution["dead_volume_ul"]


def test_v3_explicit_tip_budget():
    tips = _normalized()["tips"]
    assigned = [tips["p20"]["water"], tips["p20"]["stock"]]
    assigned.extend(tips["p20"]["print_by_row"][row] for row in "ABCDEFGH")
    assert assigned == [
        "A1", "A2", "A3", "A4", "A5",
        "A6", "A7", "A8", "A9", "A10",
    ]
    assert len(set(assigned)) == 10
    assert tips["p300"]["mix_block_column"] == 1


def test_v3_pinned_simulator_requirement():
    requirement = (
        REPO / "requirements-ot2-api-2.15.txt"
    ).read_text(encoding="utf-8")
    assert "opentrons==7.0.2" in requirement


def test_runner_selects_v3_artifact():
    assert runner._config_version(str(CONFIG_PATH)) == 3
    assert runner._protocol_for_config(str(CONFIG_PATH)).name == (
        "vial_dilution_print_v3_latest.py"
    )


def test_runner_omits_unsupported_runtime_parameters(monkeypatch):
    captured = {}

    def fake_request(method, robot_ip, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = kwargs["json"]
        return {"data": {"id": "run-id"}}

    monkeypatch.setattr(runner, "_request", fake_request)
    run_id = runner._create_run(
        "169.254.46.57",
        "protocol-id",
        dry_run=False,
        do_dilution=True,
        do_print=True,
        send_runtime_parameters=False,
    )
    assert run_id == "run-id"
    assert captured["body"] == {"data": {"protocolId": "protocol-id"}}
