"""Deterministic coordinate generation for the rectangular_grid family.

Expectations are derived from the known-working labware JSON, not hardcoded
twice: the boundary tests read `labware/paper_print_96_flat.json` and assert the
generator reproduces whatever is in it.
"""

import pytest

from src.labware.builder import read_labware_json
from src.labware.geometry import (
    column_major_ordering,
    flatten_ordering,
    generate_rectangular_grid,
    row_letters,
)
from src.utils.paths import LABWARE_OUTPUT_DIR

PAPER_JSON = LABWARE_OUTPUT_DIR / "paper_print_96_flat.json"

# The baseline grid, as read off the reference definition.
PAPER_GRID = dict(
    rows=8, columns=12,
    origin_x_mm=14.38, origin_y_mm=74.24,
    spacing_x_mm=9.0, spacing_y_mm=9.0,
)


def _paper_positions():
    return generate_rectangular_grid(**PAPER_GRID)


def _by_name(positions):
    return {p.name: p for p in positions}


# ── row letters ───────────────────────────────────────────────────

def test_row_letters_basic():
    assert row_letters(8) == list("ABCDEFGH")


def test_row_letters_past_z():
    letters = row_letters(28)
    assert letters[25] == "Z"
    assert letters[26] == "AA"
    assert letters[27] == "AB"


def test_row_letters_rejects_zero():
    with pytest.raises(ValueError):
        row_letters(0)


# ── grid generation ───────────────────────────────────────────────

def test_grid_generates_expected_count():
    assert len(_paper_positions()) == 8 * 12


def test_grid_names_are_unique():
    names = [p.name for p in _paper_positions()]
    assert len(set(names)) == len(names)


def test_grid_coordinates_are_unique():
    coords = [(p.x, p.y) for p in _paper_positions()]
    assert len(set(coords)) == len(coords)


@pytest.mark.parametrize("well", ["A1", "A12", "H1", "H12"])
def test_boundary_wells_match_reference_definition(well):
    """The four corners must reproduce the known-working paper labware."""
    reference = read_labware_json(PAPER_JSON)["wells"][well]
    position = _by_name(_paper_positions())[well]
    assert position.x == pytest.approx(reference["x"], abs=1e-9)
    assert position.y == pytest.approx(reference["y"], abs=1e-9)


@pytest.mark.parametrize("well", ["B1", "D6", "E7", "C9", "G4", "F11"])
def test_internal_wells_match_reference_definition(well):
    reference = read_labware_json(PAPER_JSON)["wells"][well]
    position = _by_name(_paper_positions())[well]
    assert position.x == pytest.approx(reference["x"], abs=1e-9)
    assert position.y == pytest.approx(reference["y"], abs=1e-9)


def test_every_well_matches_reference_definition():
    reference = read_labware_json(PAPER_JSON)["wells"]
    for position in _paper_positions():
        assert (position.x, position.y) == (
            reference[position.name]["x"],
            reference[position.name]["y"],
        ), f"{position.name} drifted from the reference definition"


def test_row_a_is_the_back_row():
    """y must DECREASE from row A to row H — the OT-2 convention."""
    positions = _by_name(_paper_positions())
    ys = [positions[f"{letter}1"].y for letter in "ABCDEFGH"]
    assert ys == sorted(ys, reverse=True)
    assert positions["A1"].y > positions["H1"].y


def test_column_1_is_leftmost():
    """x must INCREASE from column 1 to column 12."""
    positions = _by_name(_paper_positions())
    xs = [positions[f"A{c}"].x for c in range(1, 13)]
    assert xs == sorted(xs)


def test_spacing_is_honoured_exactly():
    positions = _by_name(_paper_positions())
    assert positions["A2"].x - positions["A1"].x == pytest.approx(9.0)
    assert positions["A1"].y - positions["B1"].y == pytest.approx(9.0)


def test_coordinates_are_rounded_not_drifting():
    """Binary float error must not leak into the definition (14.38+9*5)."""
    positions = _by_name(_paper_positions())
    assert positions["A6"].x == 59.38
    assert positions["D1"].y == 47.24


def test_single_row_and_single_column_grids():
    trough = generate_rectangular_grid(1, 12, 13.94, 42.74, 9.0, 0.0)
    assert len(trough) == 12
    assert {p.name for p in trough} == {f"A{c}" for c in range(1, 13)}
    assert len({p.y for p in trough}) == 1

    strip = generate_rectangular_grid(8, 1, 14.38, 74.24, 0.0, 9.0)
    assert len(strip) == 8
    assert len({p.x for p in strip}) == 1


@pytest.mark.parametrize("rows,columns", [(0, 12), (8, 0), (-1, 4)])
def test_grid_rejects_non_positive_dimensions(rows, columns):
    with pytest.raises(ValueError):
        generate_rectangular_grid(rows, columns, 10.0, 10.0, 9.0, 9.0)


# ── ordering ──────────────────────────────────────────────────────

def test_ordering_is_column_major():
    ordering = column_major_ordering(_paper_positions())
    assert len(ordering) == 12
    assert all(len(column) == 8 for column in ordering)
    assert ordering[0] == ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"]
    assert ordering[-1] == ["A12", "B12", "C12", "D12", "E12", "F12", "G12", "H12"]


def test_ordering_matches_reference_definition():
    assert column_major_ordering(_paper_positions()) == read_labware_json(PAPER_JSON)["ordering"]


def test_flatten_ordering_preserves_column_major_order():
    flat = flatten_ordering(column_major_ordering(_paper_positions()))
    assert flat[:3] == ["A1", "B1", "C1"]
    assert flat[8] == "A2"
    assert len(flat) == 96
