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

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agents import vial_print_tools as vpt
from src.agents.vial_print_agent import parse_request


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
                               default_folds=default_folds)
    assert cfg["printing"]["droplet_volume_ul"] == 22.0
    assert cfg["printing"]["num_replicates"] == 3


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


def test_soft_validate_flags_tip_block_overlap(default_cfg, default_folds):
    """print_block_column inside single_tip_columns must be flagged (tip clobber)."""
    reserved = default_cfg["printing"]["print_block_column"]
    cfg, _ = vpt._apply_params(
        default_cfg,
        advanced_updates={"dilution": {"single_tip_columns": [reserved, 11]}},
        default_folds=default_folds)
    assert any("overlap" in p for p in vpt._soft_validate(cfg))


def test_parse_request_extracts_three_knobs():
    knobs = parse_request("set up 5 dilutions, 20 uL droplets, 3 replicates")
    assert knobs == {"num_dilutions": 5, "droplet_volume_ul": 20.0, "num_replicates": 3}


def test_parse_request_handles_micro_sign_and_no_unit():
    assert parse_request("7 dilutions").get("num_dilutions") == 7
    assert parse_request("15µl droplet").get("droplet_volume_ul") == 15.0


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

    upd = vpt.update_vial_print_params.invoke(
        {"num_dilutions": 4, "droplet_volume_ul": 18.0, "num_replicates": 2})
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
