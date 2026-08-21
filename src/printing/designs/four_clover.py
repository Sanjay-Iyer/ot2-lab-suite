"""Compatibility adapter for the production four-clover geometry engine."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from ..config import REPO_ROOT


_PROTOCOL = REPO_ROOT / "src" / "protocols" / "printing" / "12_four_clover_paper_print.py"
_PAPER = REPO_ROOT / "labware" / "paper_print_96_flat.json"


def _fresh_module():
    spec = importlib.util.spec_from_file_location("four_clover_design_adapter", _PROTOCOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load four-clover protocol: {_PROTOCOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _paper_wells() -> dict[str, dict[str, Any]]:
    return json.loads(_PAPER.read_text(encoding="utf-8"))["wells"]


def generate_four_clover_coordinates(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve the exact coordinates/order used by the production protocol."""
    module = _fresh_module()
    module.CONFIG = config
    wells = _paper_wells()

    def well_xy(name: str) -> tuple[float, float]:
        well = wells[str(name).upper()]
        return float(well["x"]), float(well["y"])

    clovers = module._resolve_clovers(well_xy)
    order_mode, plan = module._print_order(clovers)
    return {
        "design_name": "four_clover",
        "order_mode": order_mode,
        "clovers": clovers,
        "plan": [
            {
                "clover": clover["name"],
                "layer": layer,
                "droplet": droplet,
                "absolute": list(clover["droplets"][droplet]["absolute"]),
            }
            for clover, layer, droplet in plan
        ],
    }
