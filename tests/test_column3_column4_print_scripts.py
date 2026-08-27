"""Regression tests for the A10 and column-11 paper-print scripts."""
from pathlib import Path

from src.printing.print_from_vial.builder import render_protocol_source
from src.printing.print_from_vial.loader import load_print_from_vial_config


REPO = Path(__file__).resolve().parents[1]
A10_CONFIG = REPO / "configs" / "experiments" / "20_a10_to_paper_column3.yaml"
DILUTIONS_CONFIG = (
    REPO
    / "configs"
    / "experiments"
    / "21_column11_dilutions_to_paper_columns3_4.yaml"
)
ROWS = "ABCDEFGH"


def test_a10_prints_once_to_all_eight_rows_of_paper_column3():
    config, run_modes = load_print_from_vial_config(A10_CONFIG)

    assert run_modes == {"dry_run": False}
    assert config["deck"]["source"]["slot"] == 1
    assert config["deck"]["paper"]["slot"] == 11
    assert config["source"]["wells"] == ["A10"]
    assert config["printing"]["droplet_volume_ul"] == 5.0
    assert config["print_groups"] == [
        {
            "source_well": "A10",
            "targets": [f"{row}3" for row in ROWS],
            "droplets": 1,
        }
    ]
    assert config["tips"]["print_tip"] == "A4"


def test_column11_dilutions_map_by_row_to_both_paper_columns():
    config, run_modes = load_print_from_vial_config(DILUTIONS_CONFIG)

    assert run_modes == {"dry_run": False}
    assert config["deck"]["source"]["slot"] == 1
    assert config["deck"]["paper"]["slot"] == 11
    assert config["source"]["wells"] == [f"{row}11" for row in ROWS]
    assert config["printing"]["droplet_volume_ul"] == 5.0
    assert config["print_groups"] == [
        {
            "source_well": f"{row}11",
            "targets": [f"{row}3", f"{row}4"],
            "droplets": 1,
            "source_wells": {"paper": f"{row}11"},
        }
        for row in ROWS
    ]
    assert config["tips"]["pipette_tip_reuse"] is True
    assert config["tips"]["print_tip"] == "B4"


def test_both_scripts_render_as_live_api_215_protocols():
    for path in (A10_CONFIG, DILUTIONS_CONFIG):
        config, run_modes = load_print_from_vial_config(path)
        rendered = render_protocol_source(config, run_modes=run_modes)

        assert "DEFAULT_DRY_RUN = False" in rendered
        assert 'requirements = {"robotType": "OT-2", "apiLevel": "2.15"}' in rendered
        assert "'slot': 11" in rendered


def test_named_robot_runner_registers_both_scripts():
    import scripts.run_printing_experiment_robot as runner

    assert runner.WORKFLOWS["a10-paper-column3"] == (
        "standard",
        "configs/experiments/20_a10_to_paper_column3.yaml",
    )
    assert runner.WORKFLOWS["column11-paper-columns3-4"] == (
        "standard",
        "configs/experiments/21_column11_dilutions_to_paper_columns3_4.yaml",
    )
