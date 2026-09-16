"""Tests for SVG labware visualizers."""
from __future__ import annotations

from pathlib import Path
from src.agents.dye_demo.gui.labware_svg import (
    render_paper_svg,
    render_plate_svg,
    render_tuberack_svg,
)
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config, set_path


def test_plate_svg_case_1():
    config = load_config(DEFAULT_CONFIG)
    set_path(config, "dilution.plate_column", "11")
    set_path(config, "dilution.start_row", "A")
    set_path(config, "dilution.factors", [1, 2, 4, 8, 16, 32, 64, 128])
    svg = render_plate_svg(config)
    assert "96-Well Plate — Slot 4" in svg
    assert 'fill="#22c55e"' in svg
    # Well A11 should be green
    assert '<circle cx="212" cy="48" r="6.5" fill="#22c55e"' in svg


def test_paper_svg_case_2():
    config = load_config(DEFAULT_CONFIG)
    set_path(config, "print.enabled", True)
    set_path(config, "print.paper_start_column", 1)
    set_path(config, "print.replicates", 3)
    set_path(config, "print.droplet_volume_ul", 5.0)
    set_path(config, "dilution.start_row", "A")
    set_path(config, "dilution.factors", [1, 2, 4])  # rows A, B, C
    svg = render_paper_svg(config)
    assert "Paper Substrate — Slot 5" in svg
    assert "1 drop / position" in svg or "drops / position" in svg
    assert 'fill="#22c55e"' in svg


def test_paper_svg_case_3():
    config = load_config(DEFAULT_CONFIG)
    set_path(config, "print.enabled", True)
    set_path(config, "print.paper_start_column", 4)
    set_path(config, "print.replicates", 2)  # cols 4, 5
    set_path(config, "print.droplet_volume_ul", 5.0)
    set_path(config, "dilution.start_row", "A")
    set_path(config, "dilution.factors", [1, 2])  # rows A, B
    svg = render_paper_svg(config)
    assert "Paper Substrate — Slot 5" in svg
    # Positions A4, A5, B4, B5 active
    assert 'Position A4' in svg
    assert 'Position A5' in svg


def test_paper_svg_case_4_no_printing():
    config = load_config(DEFAULT_CONFIG)
    set_path(config, "print.enabled", False)
    svg = render_paper_svg(config)
    assert "Paper Substrate — Slot 5" in svg
    assert "No printing planned" in svg
    assert 'fill="#22c55e"' not in svg


def test_tuberack_svg_case_5():
    config = load_config(DEFAULT_CONFIG)
    set_path(config, "dilution.enabled", True)
    set_path(config, "materials.dye.vial", "A1")
    set_path(config, "materials.water.vial", "A2")
    svg = render_tuberack_svg(config)
    assert "8-Vial Rack — Slot 7" in svg
    assert 'fill="#22c55e"' in svg
    assert "Vial A1" in svg
    assert "Vial A2" in svg


def test_tuberack_svg_print_only():
    config = load_config(DEFAULT_CONFIG)
    set_path(config, "dilution.enabled", False)
    set_path(config, "print.enabled", True)
    svg = render_tuberack_svg(config)
    assert "8-Vial Rack — Slot 7" in svg
    assert 'fill="#22c55e"' not in svg
    assert "Vial A1 (Unused)" in svg

