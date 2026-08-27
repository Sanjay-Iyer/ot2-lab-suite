from pathlib import Path

import pytest

from src.printing.dilution.builder import render_protocol_source
from src.printing.dilution.loader import load_dilution_config


REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs" / "experiments" / "19_a11_dilutions_into_column11.yaml"

# BRAND 781662: 6.94 mm circular flat-bottom well, 37.83 uL per mm of depth.
UL_PER_MM = 37.83
# A11 is loaded as 120 uL dye + 120 uL water and is never topped up by this run.
A11_LOADED_UL = 240.0


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


def test_every_well_is_filled_to_300_ul():
    config, _ = load_dilution_config(CONFIG)

    dilution = config["dilution"]
    assert dilution["mode"] == "factors"
    assert dilution["total_volume_ul"] == 300
    assert len(dilution["factors"]) == len(config["destination"]["wells"])
    # 300 uL must fit the 350 uL BRAND well, and stand clear of the 10.65 mm rim.
    assert dilution["total_volume_ul"] <= 350
    assert dilution["total_volume_ul"] / UL_PER_MM < 10.65


def test_stock_draw_fits_inside_what_a11_can_actually_give_up():
    config, _ = load_dilution_config(CONFIG)

    dilution = config["dilution"]
    stock = [dilution["total_volume_ul"] / f for f in dilution["factors"]]
    water = [dilution["total_volume_ul"] - v for v in stock]

    assert dilution["factors"] == [5, 10, 20, 30, 50, 100, 200]
    # Round stock volumes: nothing depends on hitting a fractional target.
    assert stock == pytest.approx([60, 30, 15, 10, 6, 3, 1.5])
    assert sum(stock) == pytest.approx(125.5)
    assert sum(water) == pytest.approx(1974.5)

    # The tip aspirates at a fixed height, so the bottom of A11 is unreachable.
    unreachable = config["stock"]["aspirate_height_mm"] * UL_PER_MM
    budget = A11_LOADED_UL - unreachable
    assert budget == pytest.approx(221.1, abs=0.1)
    assert sum(stock) < budget, "the run would drain A11 dry partway through"
    # A wide margin, because the 120 + 120 loading is only approximate.
    assert budget - sum(stock) > 90


def test_every_pull_clears_the_p20_floor_and_ceiling():
    config, _ = load_dilution_config(CONFIG)

    dilution = config["dilution"]
    stock = [dilution["total_volume_ul"] / f for f in dilution["factors"]]
    water = [dilution["total_volume_ul"] - v for v in stock]
    floor = config["safety"]["p20_min_volume_ul"]

    assert min(stock) == pytest.approx(1.5)
    assert min(stock) >= floor
    assert min(water) >= floor
    # A chunk plus its trailing air gap must still fit the pipette.
    chunk = dilution["transfer_chunk_ul"]
    assert chunk + config["liquid_handling"]["air_gap_ul"] <= config["safety"][
        "p20_max_volume_ul"
    ]
    # No transfer may end on a chunk below the P20 floor.
    for volume in stock + water:
        remainder = volume % chunk
        assert remainder == 0 or remainder >= floor


def test_mix_height_stays_submerged_in_the_finished_well():
    config, _ = load_dilution_config(CONFIG)

    # The executor mixes at dispense_height_mm, so it must sit below the
    # surface of the finished 300 uL or the mix would aspirate air.
    surface_mm = config["dilution"]["total_volume_ul"] / UL_PER_MM
    assert surface_mm == pytest.approx(7.93, abs=0.02)
    assert config["destination"]["dispense_height_mm"] < surface_mm


def test_factor_mode_is_embedded_in_the_robot_protocol():
    config, run_modes = load_dilution_config(CONFIG)
    rendered = render_protocol_source(config, run_modes=run_modes)

    assert "'mode': 'factors'" in rendered
    assert "'factors': [5.0, 10.0, 20.0, 30.0, 50.0, 100.0, 200.0]" in rendered
    assert "'total_volume_ul': 300.0" in rendered
    assert "'wells': ['B11', 'C11', 'D11', 'E11', 'F11', 'G11', 'H11']" in rendered
    assert "'well': 'A11'" in rendered
