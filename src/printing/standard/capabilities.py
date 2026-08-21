"""Exact deterministic laboratory arithmetic for standard printing.

Every function here is pure, exact, and experiment-independent. Volumes are
computed with :class:`~fractions.Fraction` so a dilution cascade never accumulates
binary floating-point drift, and are converted to floats only at the boundary
where a resolved plan is serialized.

None of this arithmetic may be delegated to a language model: an incorrect
sub-transfer or diluent volume is a physical error on real hardware.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Iterable, Sequence


class CapabilityError(ValueError):
    """A requested laboratory operation is arithmetically or physically invalid."""


def as_fraction(value: Fraction | float | int | str) -> Fraction:
    """Convert to an exact rational without inheriting float representation error."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    return Fraction(str(value))


def split_transfer(
    total_ul: Fraction | float | int,
    *,
    maximum_ul: Fraction | float | int,
    minimum_ul: Fraction | float | int,
) -> tuple[Fraction, ...]:
    """Split one logical transfer into pipette-sized sub-transfers.

    Chunks are filled greedily to ``maximum_ul``. If that would leave a final
    chunk below the pipette's minimum, the last two chunks are rebalanced evenly
    so every sub-transfer stays inside the pipette's working range. The sum is
    exact.
    """
    total = as_fraction(total_ul)
    maximum = as_fraction(maximum_ul)
    minimum = as_fraction(minimum_ul)
    if maximum <= 0 or minimum <= 0:
        raise CapabilityError("pipette range must be positive")
    if minimum > maximum:
        raise CapabilityError("pipette minimum exceeds its maximum")
    if total <= 0:
        raise CapabilityError("transfer volume must be positive")
    if total < minimum:
        raise CapabilityError(
            f"transfer of {float(total):g} uL is below the pipette minimum "
            f"{float(minimum):g} uL"
        )

    chunks: list[Fraction] = []
    remaining = total
    while remaining > 0:
        chunk = min(remaining, maximum)
        chunks.append(chunk)
        remaining -= chunk

    if len(chunks) > 1 and chunks[-1] < minimum:
        merged = chunks[-2] + chunks[-1]
        half = merged / 2
        if half < minimum or half > maximum:
            raise CapabilityError(
                f"cannot split {float(total):g} uL into chunks within "
                f"[{float(minimum):g}, {float(maximum):g}] uL"
            )
        chunks[-2:] = [half, half]

    if sum(chunks, Fraction(0)) != total:
        raise CapabilityError("splitting changed the requested transfer volume")
    for chunk in chunks:
        if chunk < minimum or chunk > maximum:
            raise CapabilityError(
                f"sub-transfer {float(chunk):g} uL falls outside the pipette range"
            )
    return tuple(chunks)


def serial_cascade(
    *,
    target_usable_ul: Fraction | float | int,
    count: int,
    fold: int,
) -> dict[str, Any]:
    """Resolve a geometric serial dilution that retains the target in every well.

    The cascade is back-calculated from the last well. Each destination is filled
    so that, after its own downstream aliquot leaves, exactly
    ``target_usable_ul`` remains. This makes the required original stock a derived
    quantity rather than an unexplained constant, and it guarantees that no well
    is short of usable volume at printing time.
    """
    if count < 1:
        raise CapabilityError("a dilution series needs at least one point")
    if fold < 2:
        raise CapabilityError("a geometric dilution series needs a fold of at least 2")
    target = as_fraction(target_usable_ul)
    if target <= 0:
        raise CapabilityError("target usable volume must be positive")

    pre_transfer = [Fraction(0) for _ in range(count)]
    outgoing = [Fraction(0) for _ in range(count)]
    diluent = [Fraction(0) for _ in range(count)]
    pre_transfer[-1] = target
    for index in range(count - 2, -1, -1):
        outgoing[index] = pre_transfer[index + 1] / fold
        diluent[index + 1] = pre_transfer[index + 1] - outgoing[index]
        pre_transfer[index] = target + outgoing[index]

    retained = [pre_transfer[i] - outgoing[i] for i in range(count)]
    if any(value != target for value in retained):
        raise CapabilityError("serial cascade did not retain the requested usable volume")

    return {
        "count": count,
        "fold": fold,
        "factors": [fold**index for index in range(count)],
        "stock_allocation": pre_transfer[0],
        "total_diluent": sum(diluent, Fraction(0)),
        "pre_transfer": tuple(pre_transfer),
        "outgoing": tuple(outgoing),
        "diluent": tuple(diluent),
        "retained": tuple(retained),
    }


def direct_dilution_volumes(
    points: Iterable[tuple[Fraction | float | int, Fraction | float | int]],
) -> tuple[tuple[Fraction, Fraction], ...]:
    """Resolve independent ``(stock, diluent)`` pairs for ``(factor, total)`` points.

    Each point is prepared straight from the undiluted stock, so an error in one
    point cannot propagate into the next. A factor of 1 consumes no diluent.
    """
    resolved: list[tuple[Fraction, Fraction]] = []
    for raw_factor, raw_total in points:
        factor = as_fraction(raw_factor)
        total = as_fraction(raw_total)
        if factor < 1:
            raise CapabilityError("a dilution factor must be at least 1")
        if total <= 0:
            raise CapabilityError("a dilution total volume must be positive")
        stock = total / factor
        diluent = total - stock
        resolved.append((stock, diluent))
    return tuple(resolved)


def cumulative_consumption(
    entries: Sequence[tuple[str, Fraction]],
) -> dict[str, Fraction]:
    """Total exact consumption per liquid id, preserving declaration order."""
    totals: dict[str, Fraction] = {}
    for liquid_id, volume in entries:
        totals[liquid_id] = totals.get(liquid_id, Fraction(0)) + volume
    return totals
