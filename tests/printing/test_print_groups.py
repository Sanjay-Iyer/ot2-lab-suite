"""
tests/test_print_groups.py
==========================
Tests for the unified print-group schema, resolver/validator, and the legacy
backward-compat migration (src/core/print_groups.py). Pure logic — no Opentrons.

Covers refactor requirements: P20-only, P300-only, mixed P20+P300, auto/explicit
selection in-group, invalid volumes, dilution/destination assignment validation,
tip reuse+return fields, and unchanged legacy behavior via auto-migration.
"""
import pytest

from src.core.print_groups import (
    ParsedPrintConfig,
    PrintConfigError,
    parse_print_config,
    resolve_and_validate,
)

BOTH_PIPETTES = [
    {"name": "p300_multi_gen2", "mount": "right", "single_start": "A1"},
    {"name": "p20_single_gen2", "mount": "left"},
]


def _cfg(groups, pipettes=None):
    return {"pipettes": pipettes if pipettes is not None else BOTH_PIPETTES, "print_groups": groups}


def _p20_group(**over):
    g = {
        "name": "fine", "volume_ul": 5, "pipette": "auto", "layout": "single_spot",
        "source": {"plate_column": "9", "wells": ["A9"]}, "replicates": 3,
        "destination": {"paper_start_column": 1},
        "tips": {"well": "H12", "reuse": True, "return": True},
    }
    g.update(over)
    return g


def _p300_group(**over):
    g = {
        "name": "coarse", "volume_ul": 30, "pipette": "auto", "layout": "column_8up",
        "source": {"plate_column": "11"}, "replicates": 3,
        "destination": {"paper_start_column": 4},
        "tips": {"block_column": 1, "reuse": True, "return": True},
    }
    g.update(over)
    return g


# ── selection within groups ───────────────────────────────────────────────────

def test_mixed_p20_and_p300_workflow():
    parsed = parse_print_config(_cfg([_p20_group(), _p300_group()]))
    choices, issues = resolve_and_validate(parsed)
    assert choices["fine"].name == "p20_single_gen2"
    assert choices["coarse"].name == "p300_multi_gen2"
    assert not [i for i in issues if i.severity == "error"]


def test_p20_only_workflow():
    parsed = parse_print_config(_cfg([_p20_group(), _p20_group(name="fine2", destination={"paper_start_column": 5})],
                                     pipettes=[{"name": "p20_single_gen2", "mount": "left"}]))
    choices, _ = resolve_and_validate(parsed)
    assert all(c.name == "p20_single_gen2" for c in choices.values())


def test_p300_only_workflow():
    parsed = parse_print_config(_cfg([_p300_group()],
                                     pipettes=[{"name": "p300_multi_gen2", "mount": "right"}]))
    choices, _ = resolve_and_validate(parsed)
    assert choices["coarse"].name == "p300_multi_gen2"


def test_explicit_pipette_in_group():
    parsed = parse_print_config(_cfg([_p300_group(pipette="p300_multi_gen2")]))
    choices, _ = resolve_and_validate(parsed)
    assert "explicitly" in choices["coarse"].reason


# ── invalid configs raise with actionable errors ──────────────────────────────

def test_invalid_volume_for_explicit_p20():
    parsed = parse_print_config(_cfg([_p20_group(volume_ul=30, pipette="p20_single_gen2")]))
    with pytest.raises(PrintConfigError, match="outside p20_single_gen2"):
        resolve_and_validate(parsed)


def test_p300_only_cannot_do_small_group():
    parsed = parse_print_config(_cfg([_p20_group(volume_ul=5)],
                                     pipettes=[{"name": "p300_multi_gen2", "mount": "right"}]))
    with pytest.raises(PrintConfigError, match="No mounted pipette"):
        resolve_and_validate(parsed)


def test_single_channel_cannot_use_column_8up():
    # p20 single-channel + column_8up layout is incompatible.
    parsed = parse_print_config(_cfg(
        [_p20_group(layout="column_8up", tips={"block_column": 1})],
        pipettes=[{"name": "p20_single_gen2", "mount": "left"}],
    ))
    with pytest.raises(PrintConfigError, match="single-spot|channels"):
        resolve_and_validate(parsed)


def test_duplicate_paper_destination_rejected():
    parsed = parse_print_config(_cfg([_p20_group(), _p300_group(destination={"paper_start_column": 2})]))
    # p20 occupies cols 1-3; p300 start=2 overlaps.
    with pytest.raises(PrintConfigError, match="duplicate destination"):
        resolve_and_validate(parsed)


def test_missing_tip_for_single_spot():
    parsed = parse_print_config(_cfg([_p20_group(tips={"reuse": True, "return": True})]))
    with pytest.raises(PrintConfigError, match="requires tips.well"):
        resolve_and_validate(parsed)


def test_missing_source_rejected_by_schema():
    with pytest.raises(Exception):  # pydantic ValidationError
        parse_print_config(_cfg([_p20_group(source={})]))


# ── tip reuse / return fields preserved ───────────────────────────────────────

def test_tip_return_field_parsed():
    parsed = parse_print_config(_cfg([_p20_group(tips={"well": "H12", "reuse": True, "return": False})]))
    assert parsed.groups[0].tips.return_tip is False
    assert parsed.groups[0].tips.reuse is True


# ── backward-compat migration ─────────────────────────────────────────────────

LEGACY = {
    "pipette": {"name": "p300_multi_gen2", "mount": "right", "single_start": "A1"},
    "printing": {"droplet_volume_ul": 20.0, "source_column": "9", "num_replicates": 3,
                 "paper_start_column": 1, "print_block_column": 1, "blow_out": True,
                 "replicate_spacing_mm": {"x": 9.0, "y": 0.0, "z": 0.0}},
    "color_series": [
        {"name": "orange", "destination_column": "11", "print_block_column": 1,
         "paper_start_column": 1, "num_replicates": 3},
        {"name": "blue", "destination_column": "9", "print_block_column": 2,
         "paper_start_column": 4, "num_replicates": 3},
    ],
    "tips": {"return_tips": True},
}


def test_legacy_config_auto_migrates():
    parsed = parse_print_config(LEGACY)
    assert {g.name for g in parsed.groups} == {"orange", "blue"}
    assert all(g.layout == "column_8up" and g.pipette == "p300_multi_gen2" for g in parsed.groups)
    assert parsed.migration_notes  # note recorded
    choices, issues = resolve_and_validate(parsed)
    assert choices["orange"].name == "p300_multi_gen2"
    assert not [i for i in issues if i.severity == "error"]


def test_mixing_new_and_legacy_rejected():
    bad = dict(LEGACY)
    bad["print_groups"] = [_p20_group()]
    with pytest.raises(PrintConfigError, match="mixes new 'print_groups'"):
        parse_print_config(bad)


def test_legacy_flat_printing_only_migrates_to_one_group():
    cfg = {"pipette": {"name": "p300_multi_gen2", "mount": "right"},
           "printing": {"droplet_volume_ul": 20.0, "source_column": "9", "num_replicates": 3,
                        "paper_start_column": 1, "print_block_column": 1}}
    parsed = parse_print_config(cfg)
    assert len(parsed.groups) == 1 and parsed.groups[0].name == "print"


# ── stacked droplets (droplets_per_spot) + mix_before ─────────────────────────

def test_droplets_per_spot_defaults_to_one():
    parsed = parse_print_config(_cfg([_p20_group()]))
    g = parsed.groups[0]
    assert g.droplets_per_spot == 1
    assert g.mix_before is True
    assert g.volume_per_source_well_ul() == 5 * 3  # volume * replicates * 1 droplet


def test_droplets_per_spot_multiplies_source_demand():
    parsed = parse_print_config(_cfg([_p20_group(droplets_per_spot=3)]))
    g = parsed.groups[0]
    assert g.droplets_per_spot == 3
    assert g.volume_per_source_well_ul() == 5 * 3 * 3
    # Stacking droplets does not change which paper columns are occupied.
    assert g.paper_columns() == [1, 2, 3]


def test_droplets_per_spot_must_be_at_least_one():
    with pytest.raises(Exception):
        parse_print_config(_cfg([_p20_group(droplets_per_spot=0)]))


def test_stacked_droplets_warn_when_they_exceed_the_source_well():
    # 5 uL x 3 replicates x 10 droplets = 150 uL drawn from a 100 uL well.
    parsed = parse_print_config(_cfg([_p20_group(droplets_per_spot=10)]))
    _choices, issues = resolve_and_validate(parsed, dilution_config={"total_volume_ul": 100})
    assert any(i.severity == "warning" and "150" in i.message for i in issues)


def test_mix_before_can_be_disabled_per_group():
    parsed = parse_print_config(_cfg([_p300_group(), _p20_group(mix_before=False)]))
    by_name = {g.name: g for g in parsed.groups}
    assert by_name["coarse"].mix_before is True
    assert by_name["fine"].mix_before is False
