"""Compile validated workflow-specific patches onto registered base configs.

This module is deliberately explicit. It does not expose a generic deep merge: every
writable key below corresponds to a field in an ``extra='forbid'`` Pydantic model.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .schemas import (
    CombinedOverlayPatch,
    ComplementaryLayerPatch,
    ComplementaryQuickPatch,
    FourCloverPatch,
    StandardWellGridPatch,
)


WorkflowPatch = (
    StandardWellGridPatch
    | ComplementaryLayerPatch
    | ComplementaryQuickPatch
    | CombinedOverlayPatch
    | FourCloverPatch
)


def _geometry_dict(model: Any) -> dict[str, Any]:
    return model.model_dump(exclude_none=True, exclude_unset=True)


def apply_workflow_patch(base_config: dict[str, Any], patch: WorkflowPatch) -> dict[str, Any]:
    """Return a patched copy while preserving hardware/source/config-owned fields."""
    config = deepcopy(base_config)
    if isinstance(patch, StandardWellGridPatch):
        target = config["print"]
        if patch.droplet_volume_ul is not None:
            target["volume_ul"] = patch.droplet_volume_ul
        if patch.replicate_columns is not None:
            target["replicate_columns"] = list(patch.replicate_columns)
        if patch.layers_by_row is not None:
            target["layers_by_row"] = dict(patch.layers_by_row)
        if patch.rest_minutes is not None:
            target["rest_minutes"] = patch.rest_minutes
        return config

    if isinstance(patch, ComplementaryLayerPatch):
        target = config["print"]
        if patch.droplet_volume_ul is not None:
            target["volume_ul"] = patch.droplet_volume_ul
        if patch.layers is not None:
            mode = str(target.get("layer_mode", "")).lower()
            target["layers"] = (
                {int(key): value for key, value in patch.layers.items()}
                if mode == "by_column"
                else {str(key).upper(): value for key, value in patch.layers.items()}
            )
        if patch.rest_minutes is not None:
            target["rest_minutes"] = patch.rest_minutes
        return config

    if isinstance(patch, ComplementaryQuickPatch):
        target = config["print"]
        if patch.droplet_volume_ul is not None:
            target["volume_ul"] = patch.droplet_volume_ul
        if patch.initial_layers is not None:
            target["initial_layers"] = patch.initial_layers
        if patch.extra_layers is not None:
            target["extra_layers"] = dict(patch.extra_layers)
        if patch.rest_minutes is not None:
            target["rest_minutes"] = patch.rest_minutes
        return config

    if isinstance(patch, CombinedOverlayPatch):
        if patch.droplet_volume_ul is not None:
            config["print"]["volume_ul"] = patch.droplet_volume_ul
        if patch.between_parts_delay_minutes is not None:
            config["between_parts_delay_minutes"] = patch.between_parts_delay_minutes
        return config

    if isinstance(patch, FourCloverPatch):
        printing = config["printing"]
        for field in (
            "droplet_volume_ul",
            "layers",
            "pre_air_chase_ul",
            "dispense_height_mm",
            "inter_drop_delay_s",
            "inter_layer_delay_s",
            "inter_clover_delay_s",
        ):
            value = getattr(patch, field)
            if value is not None:
                printing[field] = value
        if patch.order_mode is not None:
            config.setdefault("order", {})["mode"] = patch.order_mode

        destination = config["destination"]
        if patch.default_geometry is not None:
            destination["default_clover_geometry"] = _geometry_dict(patch.default_geometry)
        if patch.manual_centers is not None:
            destination["manual_clover_centers"] = [
                center.model_dump(exclude_none=True, exclude_unset=True)
                for center in patch.manual_centers
            ]
            destination["clover_grid"] = {"enabled": False}
        if patch.grid is not None:
            destination["clover_grid"] = {
                "enabled": True,
                **patch.grid.model_dump(exclude_none=True, exclude_unset=True),
            }
            destination["manual_clover_centers"] = []
        return config

    raise TypeError(f"unsupported printing patch model: {type(patch).__name__}")
