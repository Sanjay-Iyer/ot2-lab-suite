"""
tests/test_vial_print.py
========================
Opentrons-free unit checks for the vial-dilution -> paper-print demo:

  * the v2 vial-rack JSON geometry is what the protocol expects, and
  * the protocol's dilution plan is internally consistent (200 uL/well, every
    aspiration within the p300 range, column 1 of the tip rack reserved).

Constants are read from the protocol with ``ast`` (literal eval) so the test does
NOT import opentrons (which needs the numpy.trapz shim).
"""

import ast
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LABWARE_JSON = REPO / "labware" / "tuberack_3dprint_20ml_8vials_v2.json"
PROTOCOL = REPO / "src" / "protocols" / "vial_dilution_print.py"

P300_MAX_UL = 300.0


def _module_literals(path: Path) -> dict:
    """Return {name: value} for every module-level `NAME = <literal>` assignment."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                pass
    return out


@pytest.fixture(scope="module")
def labware() -> dict:
    return json.loads(LABWARE_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def consts() -> dict:
    return _module_literals(PROTOCOL)


# ── Labware geometry ─────────────────────────────────────────────────────────────

def test_v2_identity(labware):
    assert labware["parameters"]["loadName"] == "tuberack_3dprint_20ml_8vials_v2"
    assert labware["metadata"]["displayName"] == "TubeRack_3Dprint_20ml_8vials_v2"
    assert labware["namespace"] == "custom_beta"
    assert labware["version"] == 1


def test_v2_footprint(labware):
    dims = labware["dimensions"]
    assert dims["xDimension"] == 127.0
    assert dims["yDimension"] == 85.0
    assert dims["zDimension"] == 60.0


def test_v2_wells(labware):
    wells = labware["wells"]
    assert len(wells) == 8
    assert set(wells) == {"A1", "B1", "A2", "B2", "A3", "B3", "A4", "B4"}
    for w in wells.values():
        assert w["diameter"] == 28.0
        assert w["depth"] == 55.0
        assert w["totalLiquidVolume"] == 20000
        assert w["z"] == 5.0


def test_v2_spacing(labware):
    w = labware["wells"]
    assert round(w["A1"]["y"] - w["B1"]["y"], 2) == 34.0   # row spacing
    assert round(w["A2"]["x"] - w["A1"]["x"], 2) == 31.0   # col spacing


# ── Dilution plan consistency ────────────────────────────────────────────────────

def test_dilution_plan_volumes(consts):
    cfg = consts["CONFIG"]
    dil = cfg["dilution"]
    total = dil["total_volume_ul"]
    assert total == 200.0
    factors = dil["factors"]
    assert factors["mode"] == "explicit"
    folds = factors["explicit"]
    assert folds == [1, 2, 5, 10, 20, 30, 40, 50]
    col = str(dil["destination_column"])
    wells = [f"{r}{col}" for r, _f in zip("ABCDEFGH", folds)]
    assert wells[0] == "A1" and wells[-1] == "H1"
    for fold in folds:
        stock = round(total / fold, 2)
        water = round(total - stock, 2)
        assert 0 <= stock <= P300_MAX_UL, f"{fold}x: stock {stock} out of range"
        assert water >= 0, f"{fold}x: negative water {water}"


def test_tip_reservation(consts):
    """The single-channel dilution tip columns must not overlap the tip-rack column
    reserved for the 8-channel block print, and must supply enough tips."""
    cfg = consts["CONFIG"]
    pr = cfg["printing"]
    dil = cfg["dilution"]
    assert pr["print_block_column"] == 1
    single_cols = [int(c) for c in dil["single_tip_columns"]]
    assert pr["print_block_column"] not in single_cols, "print block column overlaps single-tip columns"
    n_wells = len(dil["factors"]["explicit"])
    n_single = len([f"{r}{c}" for c in single_cols for r in "ABCDEFGH"])
    assert n_single >= 1 + n_wells, "not enough single tips for water + per-well stock"


def test_api_level_supports_partial_return():
    """return_tip() in partial mode needs apiLevel >= 2.28."""
    text = PROTOCOL.read_text(encoding="utf-8")
    m = re.search(r'"apiLevel":\s*"(\d+)\.(\d+)"', text)
    assert m, "apiLevel not found in metadata"
    major, minor = int(m.group(1)), int(m.group(2))
    assert (major, minor) >= (2, 28), f"apiLevel {major}.{minor} < 2.28"
