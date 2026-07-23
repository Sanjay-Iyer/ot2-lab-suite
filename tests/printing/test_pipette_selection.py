"""
tests/test_pipette_selection.py
===============================
Unit tests for the centralized pipette-selection service
(src/core/pipette_selection.py). Pure logic — no Opentrons/robot dependency — so
these run under the plain interpreter as well as the `ai` conda env.

Covers refactor requirements:
  * automatic pipette selection
  * explicit pipette selection
  * invalid volume for P20
  * invalid volume for P300
  * requested pipette not mounted
  * P20-only feasibility / P300-only feasibility
  * incompatible multichannel layout
  * low-accuracy (below recommended min) warning / dead-zone handling
"""
import pytest

from src.core.pipette_selection import (
    MountedPipette,
    PipetteSelectionError,
    assert_layout_supported,
    load_pipette_specs,
    parse_mounted_pipettes,
    select_pipette,
)

SPECS = load_pipette_specs()


def _mounted(*names_and_mounts):
    return [MountedPipette(n, m, SPECS[n]) for n, m in names_and_mounts]


BOTH = _mounted(("p300_multi_gen2", "right"), ("p20_single_gen2", "left"))
P300_ONLY = _mounted(("p300_multi_gen2", "right"))
P20_ONLY = _mounted(("p20_single_gen2", "left"))


# ── auto selection ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("vol", [1, 5, 10, 20])
def test_auto_selects_p20_for_small_volumes(vol):
    choice = select_pipette(vol, BOTH)
    assert choice.name == "p20_single_gen2"


@pytest.mark.parametrize("vol", [30, 35, 100, 300])
def test_auto_selects_p300_for_large_volumes(vol):
    choice = select_pipette(vol, BOTH)
    assert choice.name == "p300_multi_gen2"


def test_auto_deadzone_volume_uses_p300_with_accuracy_warning():
    # 25 µL is above the P20 max (20) so only the P300 covers it, but it's below
    # the P300's recommended min (30) -> selected with a warning, not an error.
    choice = select_pipette(25, BOTH)
    assert choice.name == "p300_multi_gen2"
    assert any("recommended minimum" in w for w in choice.warnings)


# ── explicit selection ────────────────────────────────────────────────────────

def test_explicit_pipette_respected_in_range():
    choice = select_pipette(10, BOTH, explicit="p20_single_gen2")
    assert choice.name == "p20_single_gen2"
    assert "explicitly" in choice.reason


def test_explicit_overrides_auto_preference():
    # 10 µL would auto-pick P20, but the P300 is explicitly forced and in-range.
    choice = select_pipette(30, BOTH, explicit="p300_multi_gen2")
    assert choice.name == "p300_multi_gen2"


def test_auto_keyword_is_treated_as_auto():
    assert select_pipette(5, BOTH, explicit="auto").name == "p20_single_gen2"


# ── invalid volumes ───────────────────────────────────────────────────────────

def test_invalid_volume_for_p20_explicit():
    with pytest.raises(PipetteSelectionError, match="outside p20_single_gen2"):
        select_pipette(30, BOTH, explicit="p20_single_gen2")


@pytest.mark.parametrize("vol", [5, 400])
def test_invalid_volume_for_p300_explicit(vol):
    with pytest.raises(PipetteSelectionError, match="outside p300_multi_gen2"):
        select_pipette(vol, BOTH, explicit="p300_multi_gen2")


def test_zero_or_negative_volume_rejected():
    with pytest.raises(PipetteSelectionError):
        select_pipette(0, BOTH)


# ── not mounted ───────────────────────────────────────────────────────────────

def test_requested_pipette_not_mounted():
    with pytest.raises(PipetteSelectionError, match="not mounted"):
        select_pipette(50, P300_ONLY, explicit="p20_single_gen2")


def test_auto_p300_only_cannot_do_small_volume():
    with pytest.raises(PipetteSelectionError, match="No mounted pipette"):
        select_pipette(5, P300_ONLY)


def test_auto_p20_only_cannot_do_large_volume():
    with pytest.raises(PipetteSelectionError, match="No mounted pipette"):
        select_pipette(200, P20_ONLY)


def test_no_pipettes_mounted():
    with pytest.raises(PipetteSelectionError):
        select_pipette(10, [])


# ── mounted-set parsing ───────────────────────────────────────────────────────

def test_parse_new_multi_pipette_form():
    cfg = {"pipettes": [
        {"name": "p300_multi_gen2", "mount": "right"},
        {"name": "p20_single_gen2", "mount": "left"},
    ]}
    mounted = parse_mounted_pipettes(cfg, SPECS)
    assert {m.name for m in mounted} == {"p300_multi_gen2", "p20_single_gen2"}


def test_parse_legacy_single_pipette_form():
    cfg = {"pipette": {"name": "p300_multi_gen2", "mount": "right"}}
    mounted = parse_mounted_pipettes(cfg, SPECS)
    assert len(mounted) == 1 and mounted[0].name == "p300_multi_gen2"


def test_parse_rejects_shared_mount():
    cfg = {"pipettes": [
        {"name": "p300_multi_gen2", "mount": "right"},
        {"name": "p20_single_gen2", "mount": "right"},
    ]}
    with pytest.raises(PipetteSelectionError, match="both"):
        parse_mounted_pipettes(cfg, SPECS)


def test_parse_rejects_unknown_pipette():
    with pytest.raises(PipetteSelectionError, match="Unknown pipette"):
        parse_mounted_pipettes({"pipette": {"name": "p9000", "mount": "left"}}, SPECS)


# ── multichannel layout guard ─────────────────────────────────────────────────

def test_single_channel_cannot_hit_multiple_wells():
    with pytest.raises(PipetteSelectionError, match="single-spot"):
        assert_layout_supported(SPECS["p20_single_gen2"], wells_per_dispense=8)


def test_multichannel_supports_full_column():
    assert_layout_supported(SPECS["p300_multi_gen2"], wells_per_dispense=8)  # no raise


def test_single_channel_single_well_ok():
    assert_layout_supported(SPECS["p20_single_gen2"], wells_per_dispense=1)  # no raise


# ── constraints file is the source of truth ───────────────────────────────────

def test_p300_multi_present_in_constraints_file():
    # Regression: p300_multi_gen2 was historically missing from the YAML.
    specs = load_pipette_specs()
    assert "p300_multi_gen2" in specs
    assert specs["p300_multi_gen2"].channels == 8
