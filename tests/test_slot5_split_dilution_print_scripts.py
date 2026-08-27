"""Regression tests for separate slot-5 column-4 and column-3 prints."""
from pathlib import Path

import pytest

from src.printing.print_from_vial.builder import render_protocol_source
from src.printing.print_from_vial.loader import load_print_from_vial_config


REPO = Path(__file__).resolve().parents[1]
CONFIGS = {
    4: REPO
    / "configs"
    / "experiments"
    / "24_column11_dilutions_to_paper5_column4.yaml",
    3: REPO
    / "configs"
    / "experiments"
    / "25_column11_dilutions_to_paper5_column3.yaml",
}
ROWS = "ABCDEFGH"


@pytest.mark.parametrize(("column", "start_tip"), [(4, "C5"), (3, "C6")])
def test_split_script_maps_every_dilution_to_one_matching_paper_row(
    column, start_tip
):
    config, run_modes = load_print_from_vial_config(CONFIGS[column])

    assert run_modes == {"dry_run": False}
    assert config["deck"]["source"]["slot"] == 1
    assert config["deck"]["paper"]["slot"] == 5
    assert config["source"]["wells"] == [f"{row}11" for row in ROWS]
    assert config["print_groups"] == [
        {
            "source_well": f"{row}11",
            "targets": [f"{row}{column}"],
            "droplets": 1,
            "source_wells": {"paper": f"{row}11"},
        }
        for row in ROWS
    ]
    assert config["printing"]["droplet_volume_ul"] == 5.0
    assert config["tips"]["pipette_tip_reuse"] is True
    assert config["tips"]["print_tip"] == start_tip


def test_split_scripts_render_as_live_api_215_protocols():
    for config_path in CONFIGS.values():
        config, run_modes = load_print_from_vial_config(config_path)
        rendered = render_protocol_source(config, run_modes=run_modes)

        assert "DEFAULT_DRY_RUN = False" in rendered
        assert 'requirements = {"robotType": "OT-2", "apiLevel": "2.15"}' in rendered
        assert "'slot': 5" in rendered


def test_named_robot_runner_registers_both_split_scripts():
    import scripts.run_printing_experiment_robot as runner

    assert runner.WORKFLOWS["column11-paper5-column4"] == (
        "standard",
        "configs/experiments/24_column11_dilutions_to_paper5_column4.yaml",
    )
    assert runner.WORKFLOWS["column11-paper5-column3"] == (
        "standard",
        "configs/experiments/25_column11_dilutions_to_paper5_column3.yaml",
    )
