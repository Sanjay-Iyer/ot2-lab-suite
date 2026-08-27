from pathlib import Path

import pytest

from src.printing.dilution.builder import render_protocol_source
from src.printing.dilution.loader import load_dilution_config


REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs" / "experiments" / "18_dye_column11_5x_to_100x.yaml"


def test_column11_dye_dilution_plan_is_exact_and_within_p20_range():
    config, run_modes = load_dilution_config(CONFIG)

    assert run_modes == {"dry_run": False}
    assert config["stock"]["well"] == "A2"
    assert config["diluent"]["well"] == "A1"
    assert config["destination"]["wells"] == [f"{row}11" for row in "ABCDEFGH"]

    dilution = config["dilution"]
    assert dilution["mode"] == "factors"
    assert dilution["factors"] == [5, 10, 15, 20, 30, 40, 50, 100]
    assert dilution["total_volume_ul"] == 100

    stock = [dilution["total_volume_ul"] / factor for factor in dilution["factors"]]
    water = [dilution["total_volume_ul"] - volume for volume in stock]
    assert stock == pytest.approx([20, 10, 100 / 15, 5, 100 / 30, 2.5, 2, 1])
    assert sum(stock) == pytest.approx(50.5)
    assert sum(water) == pytest.approx(749.5)
    assert min(stock) >= config["safety"]["p20_min_volume_ul"]


def test_factor_mode_is_embedded_in_the_robot_protocol():
    config, run_modes = load_dilution_config(CONFIG)
    rendered = render_protocol_source(config, run_modes=run_modes)

    assert "'mode': 'factors'" in rendered
    assert "'factors': [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 100.0]" in rendered
    assert "'wells': ['A11', 'B11', 'C11', 'D11', 'E11', 'F11', 'G11', 'H11']" in rendered
