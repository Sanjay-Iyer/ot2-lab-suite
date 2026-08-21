"""Stage 0 regression pins for the Experiment 01 paper-printing baseline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.printing.artifacts import build_prepared_artifact, prepare_printing_request
from src.printing.plans import resolve_print_plan


REPO = Path(__file__).resolve().parents[1]
PAPER = REPO / "labware" / "paper_print_96_flat.json"
LATEST_VALIDATED = REPO / "configs" / "printing" / "four_clover_spacing_v13.yaml"


def test_registered_paper_geometry_remains_the_validated_96_position_grid():
    definition = json.loads(PAPER.read_text(encoding="utf-8"))

    assert definition["parameters"]["loadName"] == "paper_print_96_flat"
    assert definition["namespace"] == "custom_beta"
    assert definition["version"] == 1
    assert definition["dimensions"] == {
        "xDimension": 127.76,
        "yDimension": 85.48,
        "zDimension": 14.0,
    }
    assert len(definition["wells"]) == 96
    assert definition["wells"]["A1"]["x"] == pytest.approx(14.38)
    assert definition["wells"]["A1"]["y"] == pytest.approx(74.24)
    assert definition["wells"]["A1"]["z"] == pytest.approx(6.0)
    assert definition["wells"]["A2"]["x"] - definition["wells"]["A1"]["x"] == pytest.approx(9.0)
    assert definition["wells"]["A1"]["y"] - definition["wells"]["B1"]["y"] == pytest.approx(9.0)


def test_latest_physically_validated_profile_pins_close_paper_standoff_and_p20():
    config = yaml.safe_load(LATEST_VALIDATED.read_text(encoding="utf-8"))

    assert config["protocol_label"] == "v13-spacing"
    assert config["deck"]["paper"] == {
        "slot": 5,
        "load_name": "paper_print_96_flat",
        "namespace": "custom_beta",
        "version": 1,
    }
    assert config["pipette"] == {"name": "p20_single_gen2", "mount": "left"}
    assert config["printing"]["droplet_volume_ul"] == pytest.approx(5.0)
    assert config["printing"]["dispense_height_mm"] == pytest.approx(0.5)


def _latest_validated_request():
    return {
        "family": "design",
        "workflow_name": "four_clover_spacing",
        "design_name": "four_clover",
        "parameters": {},
    }


def test_latest_profile_resolves_release_settings_into_physical_actions():
    plan = resolve_print_plan(_latest_validated_request())

    assert plan.machine.destination_labware.dispense_height_mm == pytest.approx(0.5)
    assert plan.machine.flow_rates.aspirate_ul_s == pytest.approx(3.0)
    assert plan.machine.flow_rates.dispense_ul_s == pytest.approx(3.0)
    assert len(plan.deposits) == 16
    for action in plan.deposits:
        assert action.deposition.liquid_volume_ul == pytest.approx(5.0)
        assert action.deposition.pre_air_chase_ul == pytest.approx(0.0)
        assert action.deposition.trailing_air_gap_ul == pytest.approx(1.5)
        assert action.deposition.piston_dispense_ul == pytest.approx(6.5)
        assert action.deposition.push_out_ul == pytest.approx(3.0)
        assert action.deposition.blow_out is True


def test_latest_profile_forced_motion_simulation_consumes_resolved_setting(
    tmp_path, monkeypatch
):
    prepared = prepare_printing_request(_latest_validated_request())
    artifact = build_prepared_artifact(
        prepared,
        exercise_motion=True,
        output_dir=tmp_path,
    )

    # Use the simulator's structured run log so the assertion reaches the actual
    # commanded destination rather than merely finding 0.5 in embedded source.
    simulator_config = tmp_path / "opentrons-config"
    simulator_config.mkdir()
    monkeypatch.setenv("OT_API_CONFIG_DIR", str(simulator_config))
    from opentrons.simulate import simulate

    with Path(artifact.protocol_path).open("rb") as protocol_file:
        run_log, _ = simulate(
            protocol_file,
            custom_labware_paths=[str(REPO / "labware")],
        )

    paper_dispenses = [
        entry["payload"]["location"].point.z
        for entry in run_log
        if entry["payload"].get("text", "").startswith("Dispensing 6.5 uL into")
        and "Paper Print Surface 96" in entry["payload"]["text"]
    ]
    paper_bottom_z = json.loads(PAPER.read_text(encoding="utf-8"))["wells"]["A1"]["z"]

    assert artifact.protocol_dry_run is False
    assert len(paper_dispenses) == 16
    assert all(z == pytest.approx(paper_bottom_z + 0.5) for z in paper_dispenses)
    source = Path(artifact.protocol_path).read_text(encoding="utf-8")
    assert "'dispense_height_mm': 0.5" in source
    assert "well.bottom(z)" in source
