"""Shared liquid-source selection for the simple printing workflows.

Both standard printing (01_print_from_vial) and clover printing
(02_printing_four_clover) draw liquid the same way -- the only difference is the
print geometry. This module owns the one question they share: WHICH labware, in
WHICH slot, and WHICH well(s) does the liquid come from.

    source:
      type: vial_rack        # 20 mL custom rack, default slot 7
      wells: [A1]

    source:
      type: well_plate       # BRAND Ref. 781662 96-well plate, default slot 1
      wells: [A1, B4]

`slot` and `aspirate_height_mm` may be overridden per experiment; everything else
comes from the registered defaults below so an experiment YAML never restates
labware identity. No dilution, no liquid preparation -- this only says where the
already-loaded liquid is.
"""
from __future__ import annotations

from typing import Any


class SourceConfigError(ValueError):
    """The source block of an experiment configuration is not usable."""


#: Registered source labware. Keyed by the `type` an experiment YAML selects.
SOURCE_TYPES: dict[str, dict[str, Any]] = {
    "vial_rack": {
        "load_name": "tuberack_3dprint_20ml_8vials_v2",
        "namespace": "custom_beta",
        "version": 1,
        "default_slot": 7,
        # 4.0 mm above the bottom of a 55 mm deep vial: the physically validated
        # aspiration height already in real use.
        "default_aspirate_height_mm": 4.0,
        "well_depth_mm": 55.0,
        "description": "Custom 3D-printed 20 mL vial rack (A1-A4 / B1-B4)",
    },
    "corning_plate": {
        "load_name": "corning_96_wellplate_360ul_custom",
        "namespace": "custom_beta",
        "version": 1,
        "default_slot": 4,
        "default_aspirate_height_mm": 1.0,
        "well_depth_mm": 10.67,
        "description": "Existing custom Corning 96-well plate (360 uL)",
    },
    "well_plate": {
        "load_name": "brand_96_wellplate_350ul_flat_781662",
        "namespace": "custom_beta",
        "version": 1,
        # Slot 1: the BRAND plate lives here permanently, so it never has to be
        # swapped with the Corning plate in slot 4.
        "default_slot": 1,
        # 10.65 mm deep flat-bottom well: stay low so a small working volume is
        # still reachable, but clear of the bottom.
        "default_aspirate_height_mm": 1.0,
        "well_depth_mm": 10.65,
        "description": "BRAND Ref. 781662 96-well flat-bottom plate (350 uL)",
    },
}


def resolve_source(source: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve an experiment `source:` block into deck + well information.

    Returns a mapping with:
      ``deck_spec``          -- {slot, load_name, namespace, version} for load_labware
      ``wells``              -- ordered list of upper-case source well names
      ``aspirate_height_mm`` -- height above the well bottom to aspirate at
      ``type``/``material``/``loaded_volume_ul``/``minimum_remaining_ul``
    """
    if not isinstance(source, dict):
        raise SourceConfigError("source must be a mapping")

    source_type = str(source.get("type", "vial_rack"))
    spec = SOURCE_TYPES.get(source_type)
    if spec is None:
        raise SourceConfigError(
            f"unknown source.type {source_type!r}; registered types: "
            f"{', '.join(sorted(SOURCE_TYPES))}"
        )

    # Accept `wells: [A1, A2]` or a single `well: A1` for backward compatibility.
    wells = source.get("wells")
    if wells is None:
        single = source.get("well")
        wells = [single] if single else []
    if isinstance(wells, str):
        wells = [wells]
    wells = [str(w).upper() for w in wells]
    if not wells:
        raise SourceConfigError("source must declare at least one well")
    duplicates = sorted({w for w in wells if wells.count(w) > 1})
    if duplicates:
        raise SourceConfigError(f"source wells must be unique; repeated: {duplicates}")

    slot = int(source.get("slot", spec["default_slot"]))
    if not (1 <= slot <= 11):
        raise SourceConfigError(f"source.slot must be 1-11, got {slot}")

    aspirate_height = float(
        source.get("aspirate_height_mm", spec["default_aspirate_height_mm"])
    )
    if not (0 < aspirate_height < spec["well_depth_mm"]):
        raise SourceConfigError(
            f"source.aspirate_height_mm must be > 0 and < {spec['well_depth_mm']:g} mm "
            f"for {source_type}, got {aspirate_height:g}"
        )

    # If the labware load name is stated explicitly it must match the registered
    # one -- an experiment may not silently substitute different hardware.
    declared = source.get("labware")
    if declared and str(declared) != spec["load_name"]:
        raise SourceConfigError(
            f"source.labware {declared!r} does not match the registered labware for "
            f"type {source_type!r} ({spec['load_name']})"
        )

    return {
        "type": source_type,
        "deck_spec": {
            "slot": slot,
            "load_name": spec["load_name"],
            "namespace": spec["namespace"],
            "version": spec["version"],
        },
        "wells": wells,
        "aspirate_height_mm": aspirate_height,
        "material": str(source.get("material", "unlabeled liquid")),
        "loaded_volume_ul": float(source.get("loaded_volume_ul", 0.0)),
        "minimum_remaining_ul": float(source.get("minimum_remaining_ul", 0.0)),
    }
