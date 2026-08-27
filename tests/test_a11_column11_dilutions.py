from pathlib import Path

import pytest

from src.printing.dilution.builder import render_protocol_source
from src.printing.dilution.loader import load_dilution_config


REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs" / "experiments" / "19_a11_dilutions_into_column11.yaml"


def test_a11_is_the_stock_and_the_rest_of_column_11_is_the_destination():
    config, run_modes = load_dilution_config(CONFIG)

    assert run_modes == {"dry_run": False}
    assert config["stock"]["well"] == "A11"
    # Stock and destination are the same plate in the same slot; the executor
    # loads each distinct slot once, so this must resolve to one labware.
    assert config["deck"]["stock"] == config["deck"]["destination"]
    assert config["deck"]["stock"]["slot"] == 1
    assert config["diluent"]["well"] == "A1"
    assert config["deck"]["diluent"]["slot"] == 7
    assert config["destination"]["wells"] == [f"{row}11" for row in "BCDEFGH"]
    assert "A11" not in config["destination"]["wells"]


def test_dilution_plan_is_exact_and_within_p20_range():
    config, run_modes = load_dilution_config(CONFIG)

    dilution = config["dilution"]
    assert dilution["mode"] == "factors"
    assert dilution["factors"] == [2, 5, 10, 20, 30, 50, 100]
    assert dilution["total_volume_ul"] == 100
    assert len(dilution["factors"]) == len(config["destination"]["wells"])

    stock = [dilution["total_volume_ul"] / factor for factor in dilution["factors"]]
    water = [dilution["total_volume_ul"] - volume for volume in stock]
    assert stock == pytest.approx([50, 20, 10, 5, 100 / 30, 2, 1])
    assert sum(stock) == pytest.approx(91.3333, abs=1e-3)
    assert sum(water) == pytest.approx(608.6667, abs=1e-3)

    # Every pull, stock and water alike, must clear the P20 floor.
    assert min(stock) >= config["safety"]["p20_min_volume_ul"]
    assert min(water) >= config["safety"]["p20_min_volume_ul"]
    # And each finished well must fit the 350 uL BRAND well.
    assert dilution["total_volume_ul"] <= 350


def test_factor_mode_is_embedded_in_the_robot_protocol():
    config, run_modes = load_dilution_config(CONFIG)
    rendered = render_protocol_source(config, run_modes=run_modes)

    assert "'mode': 'factors'" in rendered
    assert "'factors': [2.0, 5.0, 10.0, 20.0, 30.0, 50.0, 100.0]" in rendered
    assert "'wells': ['B11', 'C11', 'D11', 'E11', 'F11', 'G11', 'H11']" in rendered
    assert "'well': 'A11'" in rendered
