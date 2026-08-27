"""Load a general dilution config, flattened for the 04 executor.

Source, diluent and destination all use the shared labware registry in
src/printing/source_config.py, so any of the three registered labware types can
play any role without touching the deterministic Python.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from ..config import resolve_repo_path
from ..source_config import SOURCE_TYPES, SourceConfigError, resolve_source
from ..standard.loader import ExperimentJobLoadError, load_machine_profile


class DilutionLoadError(ValueError):
    """A dilution configuration file is not valid."""


#: Accept either `type: vial_rack` or a literal `labware:` load name.
_BY_LOAD_NAME = {spec["load_name"]: name for name, spec in SOURCE_TYPES.items()}


def _resolve_role(block: dict[str, Any], label: str, *, wells_key: str) -> dict[str, Any]:
    """Resolve one source/diluent/destination block into deck + wells."""
    block = dict(block or {})
    if not block.get("type"):
        declared = block.get("labware")
        if declared:
            source_type = _BY_LOAD_NAME.get(str(declared))
            if source_type is None:
                raise DilutionLoadError(
                    f"{label}.labware {declared!r} is not a registered labware; "
                    f"known: {', '.join(sorted(_BY_LOAD_NAME))}"
                )
            block["type"] = source_type
        else:
            raise DilutionLoadError(f"{label} must declare a labware or type")
    # resolve_source accepts `wells` or a single `well`.
    if wells_key == "wells" and "wells" not in block and "well" in block:
        block["wells"] = [block["well"]]
    try:
        return resolve_source(block)
    except SourceConfigError as exc:
        raise DilutionLoadError(f"{label}: {exc}") from exc


def load_dilution_config(reference: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(config, run_modes)`` ready for the 04 executor's CONFIG block."""
    path = resolve_repo_path(reference)
    if not path.is_file():
        raise DilutionLoadError(f"dilution config not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise DilutionLoadError(f"dilution config must be a mapping: {path}")
    loaded = deepcopy(loaded)

    machine_profile_ref = loaded.pop(
        "machine_profile", "configs/machines/ot2_standard_printing_p20_v1.yaml"
    )
    try:
        machine = load_machine_profile(machine_profile_ref)
    except ExperimentJobLoadError as exc:
        raise DilutionLoadError(str(exc)) from exc
    labware_by_role = {item["role"]: item for item in machine.get("labware", [])}
    try:
        tiprack = labware_by_role["tiprack"]
        pipette = machine["pipette"]
        release = machine["print_release"]
    except KeyError as exc:
        raise DilutionLoadError(
            f"machine profile {machine_profile_ref} is missing {exc}"
        ) from exc

    protocol_label = loaded.pop("protocol_label", "general_dilution")
    run_modes = loaded.pop("run_modes", {}) or {}
    tip_reuse = loaded.pop("pipette_tip_reuse", None)
    source = loaded.pop("source", {}) or {}
    diluent = loaded.pop("diluent", {}) or {}
    destination = loaded.pop("destination", {}) or {}
    dilution = loaded.pop("dilution", {}) or {}
    tips = loaded.pop("tips", {}) or {}
    liquid_handling = loaded.pop("liquid_handling", {}) or {}
    if loaded:
        raise DilutionLoadError(f"{path} has unexpected top-level key(s): {sorted(loaded)}")

    resolved_source = _resolve_role(source, "source", wells_key="wells")
    resolved_diluent = _resolve_role(diluent, "diluent", wells_key="wells")
    resolved_dest = _resolve_role(destination, "destination", wells_key="wells")

    if tip_reuse is None:
        tip_reuse = tips.get("pipette_tip_reuse", True)

    raw_factors = dilution.get("factors", []) or []
    if not isinstance(raw_factors, list):
        raise DilutionLoadError("dilution.factors must be a list")

    config: dict[str, Any] = {
        "protocol_label": str(protocol_label),
        "deck": {
            "stock": resolved_source["deck_spec"],
            "diluent": resolved_diluent["deck_spec"],
            "destination": resolved_dest["deck_spec"],
            "tiprack_p20": {
                "slot": int(tiprack["slot"]),
                "load_name": tiprack["load_name"],
            },
        },
        "pipette": {"name": pipette["name"], "mount": pipette["mount"]},
        "stock": {
            "well": resolved_source["wells"][0],
            "material": resolved_source["material"],
            "aspirate_height_mm": resolved_source["aspirate_height_mm"],
        },
        "diluent": {
            "well": resolved_diluent["wells"][0],
            "material": resolved_diluent["material"],
            "aspirate_height_mm": resolved_diluent["aspirate_height_mm"],
        },
        "destination": {
            "wells": resolved_dest["wells"],
            "dispense_height_mm": float(destination.get("dispense_height_mm", 2.0)),
            "aspirate_height_mm": resolved_dest["aspirate_height_mm"],
        },
        "dilution": {
            "mode": str(dilution.get("mode", "single")).lower(),
            "stock_volume_ul": float(dilution.get("stock_volume_ul", 20.0)),
            "diluent_volume_ul": float(dilution.get("diluent_volume_ul", 80.0)),
            "transfer_volume_ul": float(dilution.get("transfer_volume_ul", 20.0)),
            "factors": [float(value) for value in raw_factors],
            "total_volume_ul": float(dilution.get("total_volume_ul", 100.0)),
            "transfer_chunk_ul": float(
                dilution.get("transfer_chunk_ul", pipette["maximum_volume_ul"])
            ),
            "mix_cycles": int(dilution.get("mix_cycles", 5)),
            "mix_volume_ul": float(dilution.get("mix_volume_ul", 15.0)),
        },
        "liquid_handling": {
            "air_gap_ul": float(
                liquid_handling.get("air_gap_ul", release.get("trailing_air_gap_ul", 1.5))
            ),
            "air_gap_height_mm": float(
                liquid_handling.get(
                    "air_gap_height_mm", release.get("air_gap_height_mm", 5.0)
                )
            ),
        },
        "tips": {
            "start_tip": str(tips.get("start_tip", "A1")).upper(),
            "return_tips": bool(tips.get("return_tips", True)),
            "pipette_tip_reuse": bool(tip_reuse),
        },
        "flow_rates": {"p20": pipette.get("flow_rates", {})},
        "safety": {
            "p20_min_volume_ul": float(pipette["minimum_volume_ul"]),
            "p20_max_volume_ul": float(pipette["maximum_volume_ul"]),
        },
    }
    return config, run_modes
