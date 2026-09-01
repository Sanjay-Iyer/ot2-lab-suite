from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from scripts.ai_dye_demo import (
    DEFAULT_CONFIG,
    _extract_json,
    _sync_derived_fields,
    _validate_ai_update,
    render_plan,
)


@pytest.fixture()
def config():
    return yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))


def test_basic_template_is_single_dye(config):
    assert "color_series" not in config
    assert config["sources"]["food_coloring_vial"] == "A2"
    assert config["dilution"]["factors"]["explicit"] == [1, 2, 5, 10]


def test_update_syncs_print_source_and_cv_count(config):
    updated = _validate_ai_update(config, {
        "dilution": {
            "destination_column": "8",
            "factors": {"mode": "explicit", "explicit": [1, 2, 4]},
        },
        "printing": {"droplet_volume_ul": 25.0, "num_replicates": 2},
    })
    assert updated["printing"]["source_column"] == "8"
    assert updated["cv"]["expected_droplets"] == 3


def test_llm_cannot_edit_hardware_or_release_controls(config):
    with pytest.raises(ValueError, match="blocked"):
        _validate_ai_update(config, {"pipette": {"mount": "left"}})
    with pytest.raises(ValueError, match="air_gap_ul"):
        _validate_ai_update(config, {"printing": {"air_gap_ul": 0}})


def test_invalid_deck_collision_is_rejected(config):
    with pytest.raises(ValueError, match="deck slots must be distinct"):
        _validate_ai_update(config, {"deck": {"plate": {"slot": 7}}})


def test_plan_states_no_robot_contact(config):
    text = render_plan(
        config, simulate=True, config_path=Path("configs/workflows/user/example.yaml")
    )
    assert "SIMULATION" in text
    assert "no robot contact" in text
    assert "A9" in text and "D9" in text
    assert "total drops  : 4" in text


def test_extract_json_and_sync(config):
    value = _extract_json(
        '```json\n{"updates":{"printing":{"num_replicates":2}}}\n```'
    )
    assert value["updates"]["printing"]["num_replicates"] == 2
    with pytest.raises(ValueError, match="updates"):
        _extract_json('{"explanation":"nothing"}')
    edited = deepcopy(config)
    edited["color_series"] = [{"name": "unexpected"}]
    _sync_derived_fields(edited)
    assert "color_series" not in edited
