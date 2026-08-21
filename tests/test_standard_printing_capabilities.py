"""Stage 2 tests for the exact deterministic laboratory arithmetic."""

from __future__ import annotations

from fractions import Fraction

import pytest

from src.printing.standard.capabilities import (
    CapabilityError,
    direct_dilution_volumes,
    serial_cascade,
    split_transfer,
)


P20 = {"maximum_ul": 20.0, "minimum_ul": 1.0}


def test_split_transfer_is_greedy_and_exact():
    assert split_transfer(15.0, **P20) == (Fraction(15),)
    assert split_transfer(20.0, **P20) == (Fraction(20),)
    chunks = split_transfer("59.765625", **P20)
    assert chunks == (Fraction(20), Fraction(20), Fraction("19.765625"))
    assert sum(chunks) == Fraction("59.765625")


def test_split_transfer_rebalances_a_sub_minimum_remainder():
    """A greedy split may strand a final chunk below the pipette minimum."""
    chunks = split_transfer(20.5, **P20)

    assert chunks == (Fraction("10.25"), Fraction("10.25"))
    assert sum(chunks) == Fraction("20.5")
    assert all(1.0 <= float(chunk) <= 20.0 for chunk in chunks)


def test_split_transfer_rebalances_only_the_tail():
    chunks = split_transfer(40.5, **P20)

    assert chunks == (Fraction(20), Fraction("10.25"), Fraction("10.25"))
    assert sum(chunks) == Fraction("40.5")


def test_split_transfer_rejects_volumes_the_pipette_cannot_handle():
    with pytest.raises(CapabilityError, match="below the pipette minimum"):
        split_transfer(0.4, **P20)
    with pytest.raises(CapabilityError, match="must be positive"):
        split_transfer(0.0, **P20)
    with pytest.raises(CapabilityError, match="must be positive"):
        split_transfer(5.0, maximum_ul=0.0, minimum_ul=1.0)


def test_split_transfer_has_no_floating_point_drift():
    total = Fraction("29.765625")
    for _ in range(200):
        assert sum(split_transfer(total, **P20)) == total


def test_serial_cascade_retains_the_target_in_every_well():
    cascade = serial_cascade(target_usable_ul=28, count=4, fold=3)

    assert cascade["factors"] == [1, 3, 9, 27]
    assert all(value == Fraction(28) for value in cascade["retained"])
    assert cascade["stock_allocation"] == Fraction(1120, 27)
    assert cascade["outgoing"][-1] == 0
    assert cascade["diluent"][0] == 0


def test_serial_cascade_generalizes_across_fold_and_point_counts():
    cascade = serial_cascade(target_usable_ul=25, count=3, fold=5)

    assert cascade["factors"] == [1, 5, 25]
    assert all(value == Fraction(25) for value in cascade["retained"])
    # every downstream aliquot is exactly one fold-step of the next well's volume
    assert cascade["outgoing"][0] * 5 == cascade["pre_transfer"][1]
    assert cascade["outgoing"][1] * 5 == cascade["pre_transfer"][2]


def test_serial_cascade_conserves_volume():
    for count in (1, 2, 5, 7, 11):
        cascade = serial_cascade(target_usable_ul=29, count=count, fold=3)
        for index in range(count):
            incoming = (
                cascade["outgoing"][index - 1] if index else cascade["stock_allocation"]
            )
            assert (
                incoming + cascade["diluent"][index] == cascade["pre_transfer"][index]
            )


def test_serial_cascade_rejects_impossible_requests():
    with pytest.raises(CapabilityError):
        serial_cascade(target_usable_ul=30, count=0, fold=2)
    with pytest.raises(CapabilityError):
        serial_cascade(target_usable_ul=30, count=4, fold=1)
    with pytest.raises(CapabilityError):
        serial_cascade(target_usable_ul=0, count=4, fold=2)


def test_direct_dilution_volumes_are_independent_per_point():
    resolved = direct_dilution_volumes([(1, 30), (2, 30), (10, 50)])

    assert resolved[0] == (Fraction(30), Fraction(0))
    assert resolved[1] == (Fraction(15), Fraction(15))
    assert resolved[2] == (Fraction(5), Fraction(45))
    for stock, diluent in resolved:
        assert stock > 0 and diluent >= 0


def test_direct_dilution_rejects_invalid_points():
    with pytest.raises(CapabilityError):
        direct_dilution_volumes([(0.5, 30)])
    with pytest.raises(CapabilityError):
        direct_dilution_volumes([(2, 0)])
