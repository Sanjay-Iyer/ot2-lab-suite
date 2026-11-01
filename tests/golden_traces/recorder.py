"""Exact ordered robot commands of protocol v19, for comparing any later engine against today's motion.

Two independent recordings of the same compiled config:

    motion_trace      runs the real protocol file on a recording fake OT-2. Fast; no Opentrons needed. Records every
                      value the protocol passes: volumes, wells, heights, flow rates, air gaps, push-out, touch-tip
                      geometry, delays, tips, labware slots and the pipette mount.
    analysis_trace    runs the same config, built by the real builder, through pinned `opentrons.cli analyze`. Slower.
                      Records what Opentrons itself computes: top-relative heights and default blow-out flow.

Both are software evidence only, and they freeze today's behavior whether or not it has been physically validated.
Neither models liquid, used tips, or real paper height. Opentrons' implicit plunger preparation at the well top
appears in neither.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from src.agents.dye_demo.model import LABWARE_DIR, REPO
from src.agents.dye_demo.redteam.fake_opentrons import FakeLabware, FakePipette, FakeProtocol, load_protocol_module

PINNED_PYTHON = REPO / ".venv" / "ot2-api-2.15-py310" / "python.exe"
DIGITS = 9


def _number(value: Any) -> float:
    return round(float(value), DIGITS)


def _place(location: Any) -> dict[str, Any]:
    well = location.well
    return {"labware": well.labware.load_name, "slot": str(well.labware.slot), "well": well.well_name,
            "reference": location.reference, "z_mm": _number(location.offset)}


class _FlowRates:
    """Pipette flow rates; each change is a recorded event, and every liquid command reports the rate in force. A rate
    no liquid command reports yet, such as blow_out, is still recorded when set."""

    def __init__(self, events: list[dict[str, Any]]):
        object.__setattr__(self, "_events", events)
        object.__setattr__(self, "aspirate", None)
        object.__setattr__(self, "dispense", None)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        self._events.append({"cmd": "set_flow_rate", "kind": name, "ul_s": _number(value)})


class TracingPipette(FakePipette):
    # Any other attribute a protocol sets (default_speed, well_bottom_clearance, ...) changes motion in ways this recorder
    # would not see, so setting one fails until the recorder is extended.
    _ATTRIBUTES = {"name", "log", "has_tip", "tip", "flow_rate", "mount", "events", "rack", "rack_slot"}

    def __init__(self, name: str, mount: str, events: list[dict[str, Any]], log: list[Any]):
        super().__init__(name, log)
        self.mount, self.events = mount, events
        self.rack, self.rack_slot = None, None
        self.flow_rate = _FlowRates(events)

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in self._ATTRIBUTES:
            raise AssertionError(f"a protocol set pipette.{name}; the golden trace recorder must record it")
        object.__setattr__(self, name, value)

    def _rate(self, kind: str) -> float | None:
        value = getattr(self.flow_rate, kind)
        return None if value is None else _number(value)

    def pick_up_tip(self, well: Any) -> None:
        super().pick_up_tip(well)
        self.rack, self.rack_slot = well.labware.load_name, str(well.labware.slot)
        self.events.append({"cmd": "pick_up_tip", "rack": self.rack, "slot": self.rack_slot, "tip": well.well_name})

    def drop_tip(self) -> None:
        tip = self.tip
        super().drop_tip()
        self.events.append({"cmd": "drop_tip", "tip": tip})

    def return_tip(self) -> None:
        tip = self.tip
        super().return_tip()
        self.events.append({"cmd": "return_tip", "rack": self.rack, "slot": self.rack_slot, "tip": tip})

    def aspirate(self, volume: float, location: Any) -> None:
        super().aspirate(volume, location)
        self.events.append({"cmd": "aspirate", "volume_ul": _number(volume), **_place(location),
                            "flow_ul_s": self._rate("aspirate"), "tip": self.tip})

    def dispense(self, volume: float, location: Any, push_out: float | None = None) -> None:
        super().dispense(volume, location, push_out=push_out)
        self.events.append({"cmd": "dispense", "volume_ul": _number(volume), **_place(location),
                            "flow_ul_s": self._rate("dispense"),
                            "push_out_ul": None if push_out is None else _number(push_out), "tip": self.tip})

    def blow_out(self, location: Any) -> None:
        super().blow_out(location)
        self.events.append({"cmd": "blow_out", **_place(location), "tip": self.tip})

    def air_gap(self, volume: float, height: float | None = None) -> None:
        super().air_gap(volume, height=height)
        self.events.append({"cmd": "air_gap", "volume_ul": _number(volume),
                            "height_mm": None if height is None else _number(height),
                            "flow_ul_s": self._rate("aspirate"), "tip": self.tip})

    def touch_tip(self, location: Any = None, radius: float = 1.0, v_offset: float = -1.0, speed: float = 60.0) -> None:
        super().touch_tip(location, radius=radius, v_offset=v_offset, speed=speed)
        self.events.append({"cmd": "touch_tip", "labware": location.labware.load_name,
                            "slot": str(location.labware.slot), "well": location.well_name, "radius": _number(radius),
                            "v_offset_mm": _number(v_offset), "speed_mm_s": _number(speed), "tip": self.tip})

    def mix(self, reps: int, volume: float, location: Any) -> None:
        raise AssertionError("protocol v19 does not call mix(); a new caller must extend this recorder")


class TracingProtocol(FakeProtocol):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def load_labware(self, load_name: str, slot: str, namespace: str | None = None, version: int | None = None) -> Any:
        self.events.append({"cmd": "load_labware", "load_name": load_name, "slot": str(slot),
                            "namespace": namespace, "version": version})
        return super().load_labware(load_name, slot, namespace=namespace, version=version)

    def load_instrument(self, name: str, mount: str, tip_racks: Any = None) -> TracingPipette:
        self.events.append({"cmd": "load_instrument", "name": name, "mount": mount,
                            "tip_racks": [rack.load_name for rack in tip_racks or []]})
        return TracingPipette(name, mount, self.events, self.log)

    def delay(self, seconds: float = 0, minutes: float = 0) -> None:
        super().delay(seconds=seconds, minutes=minutes)
        self.events.append({"cmd": "delay", "seconds": _number(float(seconds) + 60.0 * float(minutes))})


def motion_trace(config: dict[str, Any], *, module: Any = None) -> list[dict[str, Any]]:
    """Every command a protocol issues for this compiled config, in order, honoring the config's run_modes as the
    builder bakes them. `module` defaults to the current protocol v19; a future engine can pass its own. Comments are
    not motion."""
    module = module if module is not None else load_protocol_module()
    embedded = deepcopy(config)
    modes = embedded.pop("run_modes", None) or {}
    module.CONFIG = embedded
    module.DEFAULT_DRY_RUN = bool(modes.get("dry_run", False))
    module.DEFAULT_DO_DILUTION = bool(modes.get("do_dilution", True))
    module.DEFAULT_DO_PRINT = bool(modes.get("do_print", True))
    context = TracingProtocol()
    module.run(context)
    return context.events


def _labware_files(config: dict[str, Any]) -> list[Path]:
    files = []
    for spec in config["deck"].values():
        path = LABWARE_DIR / f"{spec.get('load_name')}.json"
        if path.is_file() and path not in files:
            files.append(path)
    return files


def _plain(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def normalize_analysis(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Every non-comment command of an `opentrons.cli analyze` result: all its parameters, with run-specific ids replaced
    by names, plus the absolute position Opentrons computed, so a changed labware definition shows as a difference."""
    labware_ids = {item["id"]: item["loadName"] for item in analysis.get("labware", [])}
    result = []
    for command in analysis["commands"]:
        kind, params = command["commandType"], dict(command.get("params") or {})
        if kind == "custom":
            continue
        entry: dict[str, Any] = {"cmd": kind}
        if kind == "loadLabware":
            location = params.pop("location", None) or {}
            entry.update(load_name=params.pop("loadName", None), slot=location.get("slotName"))
        if kind == "loadPipette":
            entry.update(name=params.pop("pipetteName", None))
        if params.get("labwareId") in labware_ids:
            entry["labware"] = labware_ids[params["labwareId"]]
        for key in ("labwareId", "pipetteId"):
            params.pop(key, None)
        location = params.pop("wellLocation", None)
        if location:
            offset = location.get("offset") or {}
            entry["origin"] = location.get("origin")
            entry["offset_mm"] = [_number(offset.get(axis, 0.0)) for axis in "xyz"]
        entry.update({key: _plain(value) for key, value in params.items() if value is not None})
        position = (command.get("result") or {}).get("position")
        if position:
            entry["position_mm"] = [_number(position.get(axis, 0.0)) for axis in "xyz"]
        result.append(entry)
    return result


def analysis_trace(config: dict[str, Any], *, python: Path = PINNED_PYTHON) -> dict[str, Any]:
    """Build the config with the real builder and analyze it with pinned Opentrons. Touches only temporary files."""
    from src.agents.dye_demo.redteam.simulate_states import BASE_PROTOCOL, _builder

    builder = _builder()
    built = deepcopy(config)
    run_modes = built.pop("run_modes", {})
    run_modes["dry_run"] = False
    built["protocol_version"] = int(built.get("protocol_version", 19))
    source = builder.build_source(BASE_PROTOCOL.read_text(encoding="utf-8"), built, run_modes)
    with tempfile.TemporaryDirectory(prefix="ot2-analysis-") as folder:
        root = Path(folder)
        protocol, output = root / "protocol.py", root / "analysis.json"
        protocol.write_text(source, encoding="utf-8", newline="\n")
        environment = dict(os.environ, OT_API_CONFIG_DIR=str(root / "opentrons"), PYTHONDONTWRITEBYTECODE="1")
        (root / "opentrons").mkdir()
        command = [str(python), "-B", "-m", "opentrons.cli", "analyze", "--json-output", str(output), str(protocol),
                   *[str(path) for path in _labware_files(built)]]
        finished = subprocess.run(command, cwd=folder, env=environment, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=300)
        if not output.is_file():
            raise RuntimeError(f"analysis produced no output (exit {finished.returncode}): {finished.stderr[-800:]}")
        data = json.loads(output.read_text(encoding="utf-8"))
    return {"errors": data.get("errors") or [], "commands": normalize_analysis(data)}


def well_depth_mm(load_name: str) -> float | None:
    """A labware's well depth from its definition, used to compare bottom-relative and top-relative heights."""
    wells = FakeLabware(load_name, "0").wells()
    return float(wells[0].depth) if wells and wells[0].depth is not None else None


FIXED_TRASH = "opentrons_1_trash_1100ml_fixed"


def expected_analysis(motion: list[dict[str, Any]], *, default_air_gap_height_mm: float = 5.0) -> list[dict[str, Any]]:
    """What the analysis should report for a motion trace, field by field. Blow-out flow rate is left to Opentrons."""
    expected: list[dict[str, Any]] = []
    last_well: tuple[str, str] | None = None

    def _top(event: dict[str, Any]) -> dict[str, Any]:
        depth = well_depth_mm(event["labware"])
        return {"labware": event["labware"], "wellName": event["well"], "origin": "top",
                "offset_mm": [0.0, 0.0, _number(event["z_mm"] - depth)]}

    for event in motion:
        kind = event["cmd"]
        if kind == "load_labware":
            expected.append({"cmd": "loadLabware", "load_name": event["load_name"], "slot": event["slot"]})
        elif kind == "load_instrument":
            expected.append({"cmd": "loadPipette", "name": event["name"], "mount": event["mount"]})
        elif kind == "aspirate":
            last_well = (event["labware"], event["well"])
            expected.append({"cmd": "aspirate", **_top(event), "volume": event["volume_ul"], "flowRate": event["flow_ul_s"]})
        elif kind == "dispense":
            last_well = (event["labware"], event["well"])
            item = {"cmd": "dispense", **_top(event), "volume": event["volume_ul"], "flowRate": event["flow_ul_s"]}
            if event["push_out_ul"] is not None:
                item["pushOut"] = event["push_out_ul"]
            expected.append(item)
        elif kind == "air_gap":
            height = event["height_mm"] if event["height_mm"] is not None else default_air_gap_height_mm
            where = {"labware": last_well[0], "wellName": last_well[1], "origin": "top",
                     "offset_mm": [0.0, 0.0, _number(height)]}
            expected.append({"cmd": "moveToWell", **where})
            expected.append({"cmd": "aspirate", **where, "volume": event["volume_ul"], "flowRate": event["flow_ul_s"]})
        elif kind == "blow_out":
            expected.append({"cmd": "blowout", **_top(event)})
        elif kind == "touch_tip":
            expected.append({"cmd": "touchTip", "labware": event["labware"], "wellName": event["well"], "origin": "top",
                             "offset_mm": [0.0, 0.0, event["v_offset_mm"]], "radius": event["radius"],
                             "speed": event["speed_mm_s"]})
        elif kind == "delay":
            expected.append({"cmd": "waitForDuration", "seconds": event["seconds"]})
        elif kind == "pick_up_tip":
            expected.append({"cmd": "pickUpTip", "labware": event["rack"], "wellName": event["tip"]})
        elif kind == "drop_tip":
            expected.append({"cmd": "dropTip", "labware": FIXED_TRASH, "wellName": "A1"})
        elif kind == "return_tip":
            expected.append({"cmd": "dropTip", "labware": event["rack"], "wellName": event["tip"]})
    return expected


_PREDICTED = {
    "loadLabware": {"load_name", "slot"},
    "loadPipette": {"name", "mount"},
    "aspirate": {"labware", "wellName", "origin", "offset_mm", "volume", "flowRate"},
    "moveToWell": {"labware", "wellName", "origin", "offset_mm"},
    "dispense": {"labware", "wellName", "origin", "offset_mm", "volume", "flowRate", "pushOut"},
    "blowout": {"labware", "wellName", "origin", "offset_mm"},
    "touchTip": {"labware", "wellName", "origin", "offset_mm", "radius", "speed"},
    "waitForDuration": {"seconds"},
    "pickUpTip": {"labware", "wellName"},
    "dropTip": {"labware", "wellName"},
}


def comparable_analysis(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Analysis commands reduced to the fields expected_analysis predicts. A command type nobody predicted stays, whole,
    so it shows as a difference. Fields Opentrons adds by default stay in the stored trace for later comparison."""
    result = []
    for command in commands:
        kind = command["cmd"]
        if kind == "home":
            continue
        fields = _PREDICTED.get(kind)
        result.append(dict(command) if fields is None else
                      {"cmd": kind, **{key: value for key, value in command.items() if key in fields}})
    return result
