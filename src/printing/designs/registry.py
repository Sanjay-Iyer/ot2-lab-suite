"""Minimal registry of functioning continuous-coordinate designs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from ..schemas import FourCloverPatch
from .four_clover import generate_four_clover_coordinates


@dataclass(frozen=True)
class DesignSpec:
    name: str
    description: str
    patch_model: type[BaseModel]
    generate: Callable[[dict[str, Any]], dict[str, Any]]


_DESIGNS = {
    "four_clover": DesignSpec(
        name="four_clover",
        description=(
            "Four droplets resolved as continuous XY offsets around one or more "
            "manual or generated-grid centers."
        ),
        patch_model=FourCloverPatch,
        generate=generate_four_clover_coordinates,
    )
}


def get_design(name: str) -> DesignSpec:
    try:
        return _DESIGNS[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown printing design {name!r}; available: {', '.join(sorted(_DESIGNS))}"
        ) from exc


def list_designs() -> tuple[DesignSpec, ...]:
    return tuple(_DESIGNS[name] for name in sorted(_DESIGNS))
