"""
tests/test_vial_print_agent.py
==============================
Offline tests for the conversational vial-dilution-print agent layer
(src/agents/vial_print_tools.py + src/agents/vial_print_agent.py).

No live LLM and no robot connection. Two tiers:

  * FAST (pure):  parameter-knob mapping — asserts derived from the default YAML,
    not hardcoded literals (AI_AGENTS_SKILLS_OVERVIEW Rule 3), so a benign config
    edit can't silently break a gate.
  * SLOW (integration): the real build -> validate(--config) -> CV pipeline, driven
    through the tool wrappers. Requires the conda `ai` env (opentrons, cv2). Run:
        conda run -n ai python -m pytest tests/test_vial_print_agent.py -q
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agents import vial_print_tools as vpt
from src.agents.vial_print_agent import (
    get_vial_print_tools,
    parse_request,
    _log_block,
    _message_text,
    _sanitize_chat_history,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────────

@pytest.fixture()
def default_cfg() -> dict:
    return yaml.safe_load(vpt.DEFAULT_YAML.read_text(encoding="utf-8")) or {}


@pytest.fixture()
def default_folds(default_cfg) -> list:
    return vpt._resolve_factors(default_cfg["dilution"]["factors"])


# ════════════════════════════════════════════════════════════════════════════════
# FAST — parameter mapping (pure, no subprocess)
# ════════════════════════════════════════════════════════════════════════════════

def test_num_dilutions_slices_canonical_folds_and_syncs_cv(default_cfg, default_folds):
    """N dilutions -> first N canonical folds, and cv.expected_droplets tracks N."""
    n = 5
    assert n <= len(default_folds)                      # precondition derived from YAML
    cfg, warns = vpt._apply_params(default_cfg, num_dilutions=n,
                                   default_folds=default_folds)
    got = vpt._resolve_factors(cfg["dilution"]["factors"])
    assert got == default_folds[:n]                     # invariant, not a literal
    assert vpt._num_dilutions(cfg) == n
    assert cfg["cv"]["expected_droplets"] == n          # CV --expect stays in sync
    assert warns == []


def test_droplet_volume_and_replicates_map_to_yaml_keys(default_cfg, default_folds):
    cfg, _ = vpt._apply_params(default_cfg, droplet_volume_ul=22.0, num_replicates=3,
                               paper_start_column=4,
                               default_folds=default_folds)
    assert cfg["printing"]["droplet_volume_ul"] == 22.0
    assert cfg["printing"]["num_replicates"] == 3
    assert cfg["printing"]["paper_start_column"] == 4


def test_num_dilutions_above_max_is_clamped_with_warning(default_cfg, default_folds):
    cfg, warns = vpt._apply_params(default_cfg, num_dilutions=99,
                                   default_folds=default_folds)
    assert vpt._num_dilutions(cfg) == vpt.MAX_DILUTIONS
    assert any("range" in w for w in warns)


def test_advanced_explicit_folds_resyncs_cv_count(default_cfg, default_folds):
    cfg, _ = vpt._apply_params(
        default_cfg,
        advanced_updates={"dilution": {"factors": {"mode": "explicit",
                                                   "explicit": [1, 3, 9, 27]}}},
        default_folds=default_folds)
    assert vpt._num_dilutions(cfg) == 4
    assert cfg["cv"]["expected_droplets"] == 4          # auto re-synced


def test_apply_params_does_not_mutate_input(default_cfg, default_folds):
    before = vpt._num_dilutions(default_cfg)
    vpt._apply_params(default_cfg, num_dilutions=3, default_folds=default_folds)
    assert vpt._num_dilutions(default_cfg) == before     # pure: original untouched


def test_default_config_passes_soft_validation(default_cfg):
    assert vpt._soft_validate(default_cfg) == []


def test_default_config_prints_orange_and_blue_series(default_cfg):
    """The flagship default uses two independent dye series: orange, then blue."""
    series = default_cfg.get("color_series", [])
    assert [item["name"] for item in series] == ["orange", "blue"]
    assert series[0]["dye_vial"] == default_cfg["sources"]["orange_dye_vial"]
    assert series[1]["dye_vial"] == default_cfg["sources"]["blue_dye_vial"]
    assert series[0]["destination_column"] != series[1]["destination_column"]
    assert series[0]["print_block_column"] != series[1]["print_block_column"]
    assert series[0]["paper_start_column"] < series[1]["paper_start_column"]


def test_agent_summary_exposes_dual_color_series(default_cfg):
    summary = vpt._summary(default_cfg)

    assert "orange vial A3 -> plate column 11" in summary
    assert "blue vial A2 -> plate column 9" in summary
    assert "paper columns 1-3" in summary
    assert "paper columns 4-6" in summary


def test_preview_dilution_plan_exposes_dual_color_wells(default_cfg):
    vpt._WORKING = default_cfg
    try:
        plan = vpt.preview_dilution_plan.invoke({})
    finally:
        vpt._WORKING = None

    assert "orange series: dye vial A3 -> plate column 11" in plan
    assert "blue series: dye vial A2 -> plate column 9" in plan
    assert "A11: 1x" in plan
    assert "A9: 1x" in plan


def test_replicate_knob_updates_each_color_series(default_cfg, default_folds):
    cfg, _ = vpt._apply_params(
        default_cfg,
        num_replicates=2,
        default_folds=default_folds,
    )

    assert cfg["printing"]["num_replicates"] == 2
    assert [item["num_replicates"] for item in cfg["color_series"]] == [2, 2]


def test_color_plate_column_knobs_update_named_series(default_cfg, default_folds):
    cfg, _ = vpt._apply_params(
        default_cfg,
        orange_plate_column=12,
        blue_plate_column=8,
        default_folds=default_folds,
    )
    by_name = {item["name"]: item for item in cfg["color_series"]}

    assert by_name["orange"]["destination_column"] == "12"
    assert by_name["blue"]["destination_column"] == "8"


def test_save_and_load_vial_print_template(default_cfg, default_folds):
    name = "pytest_orange12_blue8_template"
    path = vpt.TEMPLATE_DIR / f"{name}.yaml"
    cfg, _ = vpt._apply_params(
        default_cfg,
        orange_plate_column=12,
        blue_plate_column=8,
        default_folds=default_folds,
    )
    try:
        if path.exists():
            path.unlink()
        vpt._WORKING = copy.deepcopy(cfg)
        save_out = vpt.save_vial_print_template.invoke({"name": name})
        assert "Template saved" in save_out
        assert path.exists()

        vpt._WORKING = None
        load_out = vpt.load_vial_print_template.invoke({"name": name})
        assert "Template loaded" in load_out
        loaded = {item["name"]: item for item in vpt.get_working_config()["color_series"]}
        assert loaded["orange"]["destination_column"] == "12"
        assert loaded["blue"]["destination_column"] == "8"
    finally:
        vpt._WORKING = None
        if path.exists():
            path.unlink()


def test_simulation_only_agent_toolset_excludes_robot_tools():
    names = {tool.name for tool in get_vial_print_tools(simulation_only=True)}
    assert "build_vial_print_protocol" in names
    assert "validate_vial_print_matrix" in names
    assert "verify_print_droplets_mock" in names
    assert "get_robot_hardware_status" not in names
    assert "check_robot_http_api" not in names
    assert "run_vial_print_robot_http" not in names


def test_soft_validate_flags_tip_block_overlap(default_cfg, default_folds):
    """print_block_column inside single_tip_columns must be flagged (tip clobber)."""
    reserved = default_cfg["printing"]["print_block_column"]
    cfg, _ = vpt._apply_params(
        default_cfg,
        advanced_updates={"dilution": {"single_tip_columns": [reserved, 11]}},
        default_folds=default_folds)
    assert any("overlap" in p for p in vpt._soft_validate(cfg))


def test_parse_request_extracts_three_knobs():
    knobs = parse_request(
        "set up 5 dilutions, 20 uL droplets, 3 replicates, paper column 1, "
        "orange column 12, blue column 9"
    )
    assert knobs == {
        "num_dilutions": 5,
        "droplet_volume_ul": 20.0,
        "num_replicates": 3,
        "paper_start_column": 1,
        "orange_plate_column": 12,
        "blue_plate_column": 9,
    }


def test_parse_request_does_not_treat_color_column_as_paper_column():
    knobs = parse_request("use orange column 12 and blue column 9")

    assert knobs == {
        "orange_plate_column": 12,
        "blue_plate_column": 9,
    }


def test_parse_request_handles_micro_sign_and_no_unit():
    assert parse_request("7 dilutions").get("num_dilutions") == 7
    assert parse_request("15µl droplet").get("droplet_volume_ul") == 15.0


def test_message_text_extracts_provider_text_blocks():
    content = [{"type": "text", "text": "hello"}, {"content": " world"}]
    assert _message_text(content) == "hello world"


def test_sanitize_chat_history_drops_empty_and_non_chat_messages():
    class FakeMessage:
        def __init__(self, role, content):
            self.type = role
            self.content = content

    history = [
        ("user", "Show the plan"),
        ("assistant", ""),
        FakeMessage("tool", "internal result"),
        FakeMessage("ai", [{"type": "text", "text": "Plan ready"}]),
        ("user", "   "),
    ]

    assert _sanitize_chat_history(history) == [
        ("user", "Show the plan"),
        ("assistant", "Plan ready"),
    ]


def test_log_block_appends_readable_log():
    path = REPO_ROOT / "robot_data" / "data" / "logs" / "agents" / "test_log_block.log"
    try:
        if path.exists():
            path.unlink()
        _log_block(path, "TEST BLOCK", ["alpha", "beta"])

        text = path.read_text(encoding="utf-8")
        assert "TEST BLOCK" in text
        assert "alpha" in text
        assert "beta" in text
    finally:
        if path.exists():
            path.unlink()


# ════════════════════════════════════════════════════════════════════════════════
# SLOW — full offline pipeline through the tool wrappers (needs conda `ai`)
# ════════════════════════════════════════════════════════════════════════════════

def _opentrons_available() -> bool:
    return (vpt.BUILD_SCRIPT.exists() and vpt.VALIDATE_SCRIPT.exists()
            and vpt.CV_SCRIPT.exists())


@pytest.mark.skipif(not _opentrons_available(), reason="pipeline scripts missing")
def test_offline_pipeline_build_validate_cv():
    """End-to-end: load -> update -> build -> validate(--config) -> CV, all green.

    Exercises the new validator --config passthrough by building from a custom user
    YAML (4 dilutions) and confirming the matrix validates against THOSE params.
    """
    assert "Defaults loaded" in vpt.load_vial_print_defaults.invoke({})

    # 30 uL: a valid P300 volume. (Sub-20 uL droplets like the old 18 uL are now
    # correctly REJECTED for a P300-only setup by the shared pipette-selection
    # validator — small volumes must use the P20. See src/core/pipette_selection.py.)
    upd = vpt.update_vial_print_params.invoke(
        {"num_dilutions": 4, "droplet_volume_ul": 30.0, "num_replicates": 2})
    assert "updated" in upd.lower()

    build_out = vpt.build_vial_print_protocol.invoke({})
    assert "SIMULATION OK" in build_out, build_out

    # A user YAML was written (committed default untouched) and a PASS recorded.
    user_yaml = vpt.get_last_user_yaml()
    assert user_yaml is not None and user_yaml.exists()
    assert user_yaml.parent.name == "user"

    val_out = vpt.validate_vial_print_matrix.invoke({})
    assert "ALL CASES PASSED" in val_out, val_out

    cv_out = vpt.verify_print_droplets_mock.invoke({})
    assert "CV PASS" in cv_out, cv_out


@pytest.mark.skipif(not _opentrons_available(), reason="pipeline scripts missing")
def test_user_yaml_matches_working_config():
    """The built user YAML reflects the conversational edits (4 dilutions)."""
    vpt.load_vial_print_defaults.invoke({})
    vpt.update_vial_print_params.invoke({"num_dilutions": 4})
    vpt.build_vial_print_protocol.invoke({})
    written = yaml.safe_load(vpt.get_last_user_yaml().read_text(encoding="utf-8"))
    assert vpt._num_dilutions(written) == 4
    assert written["cv"]["expected_droplets"] == 4
