"""Record the exact robot operation list without touching Opentrons.

The engine in :mod:`sers_engine.orchestrator` is the only implementation of what
the robot does.  Rather than writing a second one for the uploaded protocol,
this module runs that same engine against stand-in labware and a stand-in
pipette, and records every call symbolically:

    pick up tip -> aspirate 18 uL from vial_rack:A2 bottom(4.0) -> air gap ...

The emitted protocol is then a small interpreter over that list, so simulation
and physical execution cannot drift apart.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schema import ExperimentConfig, REPO_ROOT, SERSConfigError


class _Point:
    """A symbolic position: a well plus a reference and offset."""

    __slots__ = ("labware", "well", "reference", "z")

    def __init__(self, labware: str, well: str, reference: str, z: float) -> None:
        self.labware = labware
        self.well = well
        self.reference = reference
        self.z = float(z)

    def as_dict(self) -> dict[str, Any]:
        return {
            "labware": self.labware,
            "well": self.well,
            "reference": self.reference,
            "z": round(self.z, 4),
        }


class _Well:
    __slots__ = ("labware", "name", "diameter", "length", "width", "depth", "max_volume")

    def __init__(self, labware: str, name: str, definition: dict[str, Any]) -> None:
        self.labware = labware
        self.name = name
        self.diameter = definition.get("diameter")
        self.length = definition.get("xDimension")
        self.width = definition.get("yDimension")
        self.depth = definition.get("depth")
        self.max_volume = definition.get("totalLiquidVolume", 0)

    def bottom(self, z: float = 0.0) -> _Point:
        return _Point(self.labware, self.name, "bottom", z)

    def top(self, z: float = 0.0) -> _Point:
        return _Point(self.labware, self.name, "top", z)


class _Labware:
    def __init__(self, role: str, definition: dict[str, Any]) -> None:
        self.role = role
        self._wells = {
            name: _Well(role, name, spec)
            for name, spec in definition.get("wells", {}).items()
        }

    def wells_by_name(self) -> dict[str, _Well]:
        return self._wells

    def wells(self) -> list[_Well]:
        return list(self._wells.values())

    def __getitem__(self, name: str) -> _Well:
        return self._wells[name]


class _FlowRate:
    def __init__(self) -> None:
        self.aspirate = 0.0
        self.dispense = 0.0


class RecordingPipette:
    """Mimics the InstrumentContext surface the SERS engine actually uses."""

    def __init__(self, operations: list[dict[str, Any]]) -> None:
        self._ops = operations
        self.has_tip = False
        self.flow_rate = _FlowRate()

    def pick_up_tip(self, tip: _Well) -> None:
        self._ops.append({"op": "pick_up_tip", "labware": tip.labware, "well": tip.name})
        self.has_tip = True

    def drop_tip(self) -> None:
        self._ops.append({"op": "drop_tip"})
        self.has_tip = False

    def return_tip(self) -> None:
        self._ops.append({"op": "return_tip"})
        self.has_tip = False

    def aspirate(self, volume: float, location: _Point) -> None:
        self._ops.append({"op": "aspirate", "volume": round(float(volume), 4), **location.as_dict()})

    def dispense(self, volume: float, location: _Point, push_out: float | None = None) -> None:
        entry: dict[str, Any] = {
            "op": "dispense",
            "volume": round(float(volume), 4),
            **location.as_dict(),
        }
        if push_out is not None:
            entry["push_out"] = float(push_out)
        self._ops.append(entry)

    def air_gap(self, volume: float, height: float = 5.0) -> None:
        self._ops.append(
            {"op": "air_gap", "volume": round(float(volume), 4), "height": float(height)}
        )

    def blow_out(self, location: _Point) -> None:
        self._ops.append({"op": "blow_out", **location.as_dict()})

    def mix(self, repetitions: int, volume: float, location: _Point) -> None:
        self._ops.append(
            {
                "op": "mix",
                "repetitions": int(repetitions),
                "volume": round(float(volume), 4),
                **location.as_dict(),
            }
        )

    def move_to(self, location: _Point) -> None:
        self._ops.append({"op": "move_to", **location.as_dict()})

    def touch_tip(self, well: _Well) -> None:
        self._ops.append({"op": "touch_tip", "labware": well.labware, "well": well.name})


class RecordingProtocol:
    """Mimics the ProtocolContext surface the SERS engine actually uses."""

    def __init__(self, labware_by_role: dict[str, _Labware]) -> None:
        self.operations: list[dict[str, Any]] = []
        self.comments: list[str] = []
        self._labware = labware_by_role
        self._pipette = RecordingPipette(self.operations)

    def comment(self, text: str) -> None:
        self.comments.append(text)
        self.operations.append({"op": "comment", "text": text})

    def delay(self, seconds: float = 0.0, minutes: float = 0.0) -> None:
        total = float(seconds) + float(minutes) * 60.0
        self.operations.append({"op": "delay", "seconds": round(total, 4)})

    def home(self) -> None:
        self.operations.append({"op": "home"})

    @property
    def pipette(self) -> RecordingPipette:
        return self._pipette


_ROWS = "ABCDEFGH"


def _standard_96_grid() -> dict[str, Any]:
    """Well names for a stock 96-position labware, column-major like Opentrons."""
    return {
        "wells": {
            f"{row}{column}": {}
            for column in range(1, 13)
            for row in _ROWS
        }
    }


def _definition_for(spec: Any) -> dict[str, Any]:
    if not spec.definition_path:
        # Stock Opentrons labware carries no repo-local JSON. Tip racks are the
        # only such labware here and the engine uses nothing but their well
        # names, so a standard grid is sufficient and needs no robot import.
        if spec.kind == "tiprack" and "96" in spec.load_name:
            return _standard_96_grid()
        raise SERSConfigError(
            f"labware {spec.load_name!r} has no definition_path, so its geometry cannot "
            "be recorded offline"
        )
    path = Path(spec.definition_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.is_file():
        raise SERSConfigError(f"labware definition not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def record_operations(config: ExperimentConfig) -> list[dict[str, Any]]:
    """Run the real engine against stand-ins and return the flat operation list."""
    from .orchestrator import TipTracker, run_workflow_steps

    labware_by_role: dict[str, _Labware] = {}
    for role, spec in {**config.deck_layout.labware, **config.deck_layout.tip_racks}.items():
        labware_by_role[role] = _Labware(role, _definition_for(spec))

    protocol = RecordingProtocol(labware_by_role)
    pipette = protocol.pipette
    pipette.flow_rate.aspirate = config.pipette.aspirate_flow_rate_ul_s
    pipette.flow_rate.dispense = config.pipette.dispense_flow_rate_ul_s

    tip_tracker = TipTracker(
        protocol,
        {role: labware_by_role[role] for role in config.deck_layout.tip_racks},
        config.pipette.tip_rack_roles,
        config.tips.start_tip,
        config.tips.return_tips,
        config.tips_required,
    )
    run_workflow_steps(protocol, pipette, config, labware_by_role, tip_tracker)
    return protocol.operations
