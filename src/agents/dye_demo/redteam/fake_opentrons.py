"""A recording fake of the Opentrons ProtocolContext, for running protocol v19 without opentrons.

Used by tests/test_ai_dye_demo_protocol.py and by the red-team harness, which checks that
the protocol generated from a conversation's final state moves exactly as its plan says
(no dilution steps when dilution is off, one printed row per dilution, and so on).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.agents.dye_demo.model import LABWARE_DIR, REPO

PROTOCOL = REPO / "src" / "protocols" / "printing" / "13_ai_agent_dilution_print_demo.py"


class FakeLocation:
    def __init__(self, well, reference, offset):
        self.well, self.reference, self.offset = well, reference, float(offset)

    def key(self):
        return self.well.labware.load_name, self.well.well_name, self.reference, self.offset


class FakeWell:
    def __init__(self, labware, name, spec):
        self.labware, self.well_name = labware, name
        self.diameter, self.depth = spec.get("diameter"), spec.get("depth")

    def top(self, z=0.0):
        return FakeLocation(self, "top", z)

    def bottom(self, z=0.0):
        return FakeLocation(self, "bottom", z)


class FakeLabware:
    def __init__(self, load_name, slot):
        self.load_name, self.slot = load_name, slot
        path = LABWARE_DIR / f"{load_name}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            ordering, specs = data["ordering"], data["wells"]
        else:
            ordering = [[f"{row}{column}" for row in "ABCDEFGH"] for column in range(1, 13)]
            specs = {}
        self._ordering = ordering
        self._wells = {name: FakeWell(self, name, specs.get(name, {})) for column in ordering for name in column}

    def wells(self):
        return [self._wells[name] for column in self._ordering for name in column]

    def wells_by_name(self):
        return dict(self._wells)

    def columns(self):
        return [[self._wells[name] for name in column] for column in self._ordering]

    def __getitem__(self, name):
        return self._wells[name]


class FakePipette:
    def __init__(self, name, log):
        self.name, self.log = name, log
        self.has_tip, self.tip = False, None
        self.flow_rate = types.SimpleNamespace(aspirate=None, dispense=None)

    def pick_up_tip(self, well):
        assert not self.has_tip, "picked up a tip while holding one"
        self.has_tip, self.tip = True, well.well_name
        self.log.append(("pick_up_tip", well.well_name))

    def drop_tip(self):
        assert self.has_tip
        self.has_tip = False
        self.log.append(("drop_tip", self.tip))

    def return_tip(self):
        assert self.has_tip
        self.has_tip = False
        self.log.append(("return_tip", self.tip))

    def aspirate(self, volume, location):
        assert self.has_tip, "aspirated without a tip"
        self.log.append(("aspirate", float(volume), location.key(), self.tip))

    def dispense(self, volume, location, push_out=None):
        self.log.append(("dispense", float(volume), location.key(), push_out))

    def blow_out(self, location):
        self.log.append(("blow_out", location.key()))

    def air_gap(self, volume, height=None):
        self.log.append(("air_gap", float(volume), height))

    def mix(self, reps, volume, location):
        assert self.has_tip
        self.log.append(("mix", reps, float(volume), location.key()))


class FakeProtocol:
    def __init__(self):
        self.log, self.comments, self.loaded = [], [], []

    def load_labware(self, load_name, slot, namespace=None, version=None):
        self.loaded.append((load_name, slot))
        return FakeLabware(load_name, slot)

    def load_instrument(self, name, mount, tip_racks=None):
        return FakePipette(name, self.log)

    def comment(self, text):
        self.comments.append(text)

    def delay(self, seconds=0, minutes=0):
        self.log.append(("delay", float(seconds)))


_MODULE: Any = None


def load_protocol_module(path: Path = PROTOCOL) -> Any:
    """Import the protocol file with a stub `opentrons` module (it only uses it for a type hint)."""
    global _MODULE
    if _MODULE is not None and path == PROTOCOL:
        return _MODULE
    stub = types.ModuleType("opentrons")
    stub.protocol_api = types.SimpleNamespace(ProtocolContext=object)
    saved = sys.modules.get("opentrons")
    sys.modules["opentrons"] = stub
    try:
        spec = importlib.util.spec_from_file_location("ai_demo_protocol_v19", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            sys.modules.pop("opentrons", None)
        else:
            sys.modules["opentrons"] = saved
    if path == PROTOCOL:
        _MODULE = module
    return module


def run_protocol(module: Any, config: dict[str, Any], *, dry_run: bool = False) -> FakeProtocol:
    embedded = deepcopy(config)
    embedded.pop("run_modes", None)          # the builder bakes run modes into flags
    module.CONFIG = embedded
    module.DEFAULT_DRY_RUN = dry_run
    module.DEFAULT_DO_DILUTION = True
    module.DEFAULT_DO_PRINT = True
    context = FakeProtocol()
    module.run(context)
    return context
