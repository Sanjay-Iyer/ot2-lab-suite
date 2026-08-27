from pathlib import Path

import pytest

from src.printing.dilution.builder import render_protocol_source
from src.printing.dilution.loader import load_dilution_config


REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs" / "experiments" / "20_consolidate_b11_c11_into_a11.yaml"

# BRAND 781662: 6.94 mm circular flat-bottom well, 37.83 uL per mm of depth.
UL_PER_MM = 37.83


def test_both_source_wells_and_the_destination_are_the_same_slot_1_plate():
    config, run_modes = load_dilution_config(CONFIG)

    assert run_modes == {"dry_run": False}
    # C11 rides in the diluent role; it is a second source, not a diluent.
    assert config["stock"]["well"] == "B11"
    assert config["diluent"]["well"] == "C11"
    assert config["destination"]["wells"] == ["A11"]
    decks = [config["deck"][role] for role in ("stock", "diluent", "destination")]
    assert all(deck == decks[0] for deck in decks)
    assert decks[0]["slot"] == 1


def test_one_hundred_microlitres_is_requested_from_each_source_well():
    config, _ = load_dilution_config(CONFIG)

    dilution = config["dilution"]
    assert dilution["mode"] == "single"
    assert dilution["stock_volume_ul"] == 100      # B11
    assert dilution["diluent_volume_ul"] == 100    # C11

    # 200 uL lands in one well, which must fit the 350 uL BRAND capacity.
    assert dilution["stock_volume_ul"] + dilution["diluent_volume_ul"] <= 350

    # Every pull must clear the P20 floor and ceiling.
    chunk = dilution["transfer_chunk_ul"]
    assert chunk + config["liquid_handling"]["air_gap_ul"] <= config["safety"][
        "p20_max_volume_ul"
    ]
    remainder = dilution["stock_volume_ul"] % chunk
    assert remainder == 0 or remainder >= config["safety"]["p20_min_volume_ul"]


def test_aspirate_height_stays_at_the_validated_floor():
    config, _ = load_dilution_config(CONFIG)

    # 0.5 mm is the lowest height this repo treats as safe over a flat well
    # floor. It leaves ~18.9 uL unreachable, so ~81 of the 100 uL comes out.
    for role in ("stock", "diluent"):
        assert config[role]["aspirate_height_mm"] == pytest.approx(0.5)
    unreachable = 0.5 * UL_PER_MM
    assert unreachable == pytest.approx(18.9, abs=0.1)
    assert 100 - unreachable == pytest.approx(81.1, abs=0.1)


def test_transfer_is_embedded_in_the_robot_protocol():
    config, run_modes = load_dilution_config(CONFIG)
    rendered = render_protocol_source(config, run_modes=run_modes)

    assert "'mode': 'single'" in rendered
    assert "'stock_volume_ul': 100.0" in rendered
    assert "'diluent_volume_ul': 100.0" in rendered
    assert "'wells': ['A11']" in rendered
    assert "DEFAULT_DRY_RUN = False" in rendered
