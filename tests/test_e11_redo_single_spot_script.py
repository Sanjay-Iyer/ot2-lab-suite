"""Regression tests for the single-spot E1 redo on the white paper in slot 5."""
from pathlib import Path

from src.printing.print_from_vial.builder import render_protocol_source
from src.printing.print_from_vial.loader import load_print_from_vial_config


REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs" / "experiments" / "28_dye_e11_redo_paper5_e1.yaml"


def test_redo_prints_exactly_one_droplet_from_e11_into_e1_on_slot_5():
    config, run_modes = load_print_from_vial_config(CONFIG)

    assert run_modes == {"dry_run": False}
    assert config["deck"]["source"]["slot"] == 1
    assert config["deck"]["paper"]["slot"] == 5
    assert config["source"]["wells"] == ["E11"]
    assert config["print_groups"] == [
        {
            "source_well": "E11",
            "targets": ["E1"],
            "droplets": 1,
            "source_wells": {"paper": "E11"},
        }
    ]
    assert config["printing"]["droplet_volume_ul"] == 5.0


def test_redo_reuses_the_script_27_dye_tip():
    config, _ = load_print_from_vial_config(CONFIG)

    assert config["tips"]["pipette_tip_reuse"] is True
    assert config["tips"]["print_tip"] == "B1"
    assert config["tips"]["return_tips"] is True


def test_redo_renders_as_a_live_api_215_protocol():
    config, run_modes = load_print_from_vial_config(CONFIG)
    rendered = render_protocol_source(config, run_modes=run_modes)

    assert "DEFAULT_DRY_RUN = False" in rendered
    assert 'requirements = {"robotType": "OT-2", "apiLevel": "2.15"}' in rendered
    assert "'slot': 5" in rendered
    assert "'slot': 11" not in rendered


def test_named_robot_runner_registers_the_redo_script():
    import scripts.run_printing_experiment_robot as runner

    assert runner.WORKFLOWS["dye-e11-redo-paper5-e1"] == (
        "standard",
        "configs/experiments/28_dye_e11_redo_paper5_e1.yaml",
    )
