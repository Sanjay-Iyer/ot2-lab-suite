"""
tests/test_vial_print.py
========================
Unit tests for the vial-dilution -> paper-print pipeline.

DESIGN PRINCIPLES (all T-1 through T-5 from the audit remediated)
-------------------------------------------------------------------
1. Test STRUCTURAL INVARIANTS, not specific config values. A valid reconfiguration
   (new column, different fold count, different plate) must not cause a test failure
   unless it genuinely violates a physical constraint.
2. Physical limits (max well volume, pipette max, column length) are read from the
   labware JSON or from the workflow YAML safety block — never hardcoded.
3. Factor resolution handles all modes (explicit, geometric, linear, log) without
   KeyError on non-explicit configs (T-1).
4. print_block_column is validated for OVERLAP with single_tip_columns, not for
   being exactly 1 (T-3).
5. Row lists are derived from labware JSON ordering, not from "ABCDEFGH" (T-5).
6. Geometry tests (T-4) carry docstrings explaining the physical meaning so that a
   test failure prompts investigation rather than a blind number change.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest
import yaml

# ── Paths ─────────────────────────────────────────────────────────────────────────
REPO          = Path(__file__).resolve().parent.parent
LABWARE_DIR   = REPO / "labware"
LABWARE_JSON  = LABWARE_DIR / "tuberack_3dprint_20ml_8vials_v2.json"
PLATE_JSON    = LABWARE_DIR / "corning_96_wellplate_360ul_custom.json"
WORKFLOW_YAML = REPO / "configs" / "workflows" / "defaults" / "vial_dilution_print.yaml"
PROTOCOL_BASE = REPO / "src" / "protocols" / "vial_dilution_print.py"

# P300 physical max (only used where we cannot load the YAML, as a last resort)
P300_MAX_UL   = 300.0


# ── Helpers ───────────────────────────────────────────────────────────────────────

def _resolve_factors(fc: dict) -> list:
    """Minimal resolve_factors that handles all modes without importing opentrons (T-1)."""
    mode = fc.get("mode", "explicit")
    if mode == "explicit":
        return [float(x) for x in fc["explicit"]]
    count = int(fc.get("count", 8))
    start = float(fc.get("start", 1))
    if mode == "geometric":
        step = float(fc.get("step_factor", 2))
        return [round(start * (step ** i), 4) for i in range(count)]
    end = float(fc.get("end", 50))
    if count == 1:
        return [start]
    if mode == "linear":
        return [round(start + (end - start) * i / (count - 1), 4) for i in range(count)]
    if mode == "log":
        lo, hi = math.log(start), math.log(end)
        return [round(math.exp(lo + (hi - lo) * i / (count - 1)), 4) for i in range(count)]
    raise ValueError(f"Unknown factors mode: {mode!r}")


def _labware_rows(lw_data: dict) -> list:
    """Derive unique row letters from a labware ordering array (T-5)."""
    ordering = lw_data.get("ordering", [])
    seen, rows = set(), []
    for col in ordering:
        for well_name in col:
            row = re.match(r"^([A-Z]+)", well_name)
            if row and row.group(1) not in seen:
                seen.add(row.group(1))
                rows.append(row.group(1))
    return rows


def _labware_rows_per_column(lw_data: dict) -> int:
    """Number of rows per column from the ordering array (T-5, no ABCDEFGH)."""
    ordering = lw_data.get("ordering", [])
    return len(ordering[0]) if ordering else 0


# ── Fixtures ──────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def labware() -> dict:
    assert LABWARE_JSON.exists(), f"Labware JSON not found: {LABWARE_JSON}"
    return json.loads(LABWARE_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def plate_data() -> dict | None:
    if not PLATE_JSON.exists():
        return None
    return json.loads(PLATE_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def workflow_cfg() -> dict:
    assert WORKFLOW_YAML.exists(), f"Workflow YAML not found: {WORKFLOW_YAML}"
    return yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8")) or {}


@pytest.fixture(scope="module")
def consts(workflow_cfg) -> dict:
    """Re-build the CONFIG dict from the YAML (same logic as the build script)."""
    cfg = dict(workflow_cfg)
    cfg.pop("run_modes", None)
    return {"CONFIG": cfg}


# ── Tube-rack labware JSON (geometry regression) ───────────────────────────────────

class TestV2Labware:
    """Geometry regression tests for tuberack_3dprint_20ml_8vials_v2.

    Purpose: detect if a re-print of the rack or an edit to the labware JSON
    has changed the physical geometry in a way that would shift tip alignment.
    If a test here fails, investigate the *physical* rack first; do not blindly
    update the expected value.
    """

    def test_v2_identity(self, labware):
        """Load name, namespace, version and display name must be stable."""
        assert labware["parameters"]["loadName"] == "tuberack_3dprint_20ml_8vials_v2"
        assert labware["metadata"]["displayName"] == "TubeRack_3Dprint_20ml_8vials_v2"
        assert labware["namespace"] == "custom_beta"
        assert labware["version"] == 1

    def test_v2_footprint(self, labware):
        """Footprint must match the SBS microplate standard (127 x 85 x 60 mm).

        The x/y footprint dictates deck slot registration. A changed footprint
        means the physical rack no longer fits in the deck slot correctly.
        """
        dims = labware["dimensions"]
        assert dims["xDimension"] == pytest.approx(127.0, abs=0.5), (
            "xDimension changed: SBS footprint required for deck registration")
        assert dims["yDimension"] == pytest.approx(85.0, abs=0.5), (
            "yDimension changed: SBS footprint required for deck registration")
        assert dims["zDimension"] == pytest.approx(60.0, abs=1.0), (
            "zDimension changed: tall enough for 20 mL vials")

    def test_v2_wells(self, labware):
        """Each vial: Ø28 mm bore × 55 mm depth, flat-bottom, 5.0 mm above adapter floor.

        These values set the pipette approach path. Diameter change -> tip misses vial bore.
        Depth change -> pipette bottoms out or doesn't reach liquid. z change -> tip hits
        the rack floor or hangs too high.
        """
        w = labware["wells"]["A1"]
        assert w.get("shape") == "circular", "Vial bore must be circular"
        assert w["diameter"] == pytest.approx(28.0, abs=0.5)
        assert w["depth"] == pytest.approx(55.0, abs=0.5)
        assert w["totalLiquidVolume"] == pytest.approx(20000, rel=0.05)
        assert w["z"] == pytest.approx(5.0, abs=0.5)

    def test_v2_well_spacing(self, labware):
        """Row spacing 34 mm, col spacing 31 mm.

        These govern the centre-to-centre distance between vials. A change here
        means the physical centres of the printed vials have shifted — re-measure
        and update both the JSON AND the safety config in the YAML.
        """
        ordering = labware.get("ordering", [])
        # Column 1 wells
        col1 = ordering[0] if ordering else []
        if len(col1) >= 2:
            y0 = labware["wells"][col1[0]]["y"]
            y1 = labware["wells"][col1[1]]["y"]
            row_spacing = abs(y0 - y1)
            assert row_spacing == pytest.approx(34.0, abs=0.5), (
                "Row (y) spacing changed: vial positions shifted in Y")
        # First well of columns 1 and 2
        if len(ordering) >= 2:
            x0 = labware["wells"][ordering[0][0]]["x"]
            x1 = labware["wells"][ordering[1][0]]["x"]
            col_spacing = abs(x1 - x0)
            assert col_spacing == pytest.approx(31.0, abs=0.5), (
                "Column (x) spacing changed: vial positions shifted in X")

    def test_v2_well_count(self, labware):
        """Rack must have exactly 8 wells (2 rows × 4 columns)."""
        assert len(labware["wells"]) == 8, (
            f"Expected 8 wells; got {len(labware['wells'])}. "
            "Verify rack geometry matches the v2 design.")

    def test_v2_well_rows(self, labware):
        """Row and column labels must follow the standard A-B / 1-4 naming (T-5).

        Derived from the ordering array, not from a hardcoded 'ABCDEFGH' string.
        """
        rows = _labware_rows(labware)
        assert len(rows) >= 2, "Expected at least 2 rows (A and B)"
        assert rows[0] == "A", "First row must be A"
        # Columns: derived from ordering column count
        n_cols = len(labware.get("ordering", []))
        assert n_cols >= 4, f"Expected at least 4 columns; got {n_cols}"

    def test_well_names_sorted_from_ordering(self, labware):
        """Wells declared in the ordering array must all exist in the wells dict (T-5)."""
        ordering = labware.get("ordering", [])
        all_well_names = {w for col in ordering for w in col}
        for name in all_well_names:
            assert name in labware["wells"], f"Ordering references well {name!r} not in wells dict"


# ── Config / dilution invariants ──────────────────────────────────────────────────

class TestDilutionInvariants:
    """Test physical bounds on the dilution plan — not specific config values (T-2)."""

    def test_dilution_volumes_within_bounds(self, consts, plate_data, workflow_cfg):
        """All stock + water volumes must be non-negative and within physical limits.

        Uses plate labware JSON for the well max (not a hardcoded 360 µL), and
        uses safety.pipette_max_volume_ul from YAML (not a hardcoded 300 µL).
        """
        cfg   = consts["CONFIG"]
        dil   = cfg["dilution"]
        safety = cfg.get("safety", {})
        total  = float(dil["total_volume_ul"])
        pip_max = float(safety.get("pipette_max_volume_ul", P300_MAX_UL))

        # Max well volume from labware JSON (authoritative); fallback to YAML if absent
        if plate_data is not None:
            well_max = float(min(
                w.get("totalLiquidVolume", float("inf"))
                for w in plate_data["wells"].values()
            ))
        else:
            well_max = float(safety.get("max_well_volume_ul", pip_max))

        assert total > 0, "total_volume_ul must be positive"
        assert total <= well_max, (
            f"total_volume_ul {total} µL exceeds plate well max {well_max} µL")

        factors = _resolve_factors(dil["factors"])
        assert len(factors) > 0, "factors must produce at least one dilution step"
        for fold in factors:
            stock = round(total / fold, 2)
            water = round(total - stock, 2)
            assert fold > 0,  f"Fold factor {fold} must be > 0"
            assert stock >= 0, f"Fold {fold}x: stock {stock} µL is negative"
            assert water >= 0, f"Fold {fold}x: water {water} µL is negative"
            assert stock <= pip_max, (
                f"Fold {fold}x: stock {stock} µL exceeds pipette max {pip_max} µL")

    def test_factor_count_fits_plate_column(self, consts, plate_data):
        """Number of dilution factors must not exceed the plate's row count per column.

        Row count is derived from the plate labware JSON ordering array (T-5),
        not from 8 or len("ABCDEFGH").
        """
        cfg    = consts["CONFIG"]
        dil    = cfg["dilution"]
        safety = cfg.get("safety", {})
        factors = _resolve_factors(dil["factors"])

        if plate_data is not None:
            max_rows = _labware_rows_per_column(plate_data)
        else:
            max_rows = int(safety.get("expected_plate_well_count", 96) // 12)

        assert len(factors) <= max_rows, (
            f"{len(factors)} factors but plate column only has {max_rows} rows "
            f"(from {'plate labware JSON' if plate_data else 'safety config'})")

    def test_all_factor_modes_resolve(self):
        """_resolve_factors must handle all documented modes without KeyError (T-1)."""
        explicit_fc   = {"mode": "explicit", "explicit": [1, 2, 5, 10]}
        geometric_fc  = {"mode": "geometric", "start": 1, "step_factor": 2, "count": 4}
        linear_fc     = {"mode": "linear", "start": 1, "end": 10, "count": 4}
        log_fc        = {"mode": "log", "start": 1, "end": 50, "count": 4}
        for fc in (explicit_fc, geometric_fc, linear_fc, log_fc):
            result = _resolve_factors(fc)
            assert len(result) > 0, f"Mode '{fc['mode']}' resolved empty list"
            assert all(f > 0 for f in result), f"Mode '{fc['mode']}' produced non-positive factor"


# ── Tip layout invariants ──────────────────────────────────────────────────────────

class TestTipLayoutInvariants:
    """Test structural correctness of the tip layout — not specific column numbers (T-3)."""

    def test_no_column_overlap(self, consts):
        """print_block_column must NOT appear in single_tip_columns (T-3).

        We do not assert that print_block_column IS a specific column like 1 — any
        valid non-overlapping column is acceptable.
        """
        cfg         = consts["CONFIG"]
        pr          = cfg["printing"]
        dil         = cfg["dilution"]
        print_col   = int(pr["print_block_column"])
        single_cols = [int(c) for c in dil["single_tip_columns"]]
        assert print_col not in single_cols, (
            f"print_block_column {print_col} overlaps single_tip_columns {single_cols}. "
            "Dilution tips would overwrite the 8-channel print block tips.")

    def test_tip_supply_sufficient(self, consts):
        """single_tip_columns must supply enough tips for water + all dilution wells.

        Rows-per-column is read from the YAML safety block (authoritative for the
        tiprack, which is standard Opentrons and not in the custom labware dir).
        """
        cfg         = consts["CONFIG"]
        pr          = cfg["printing"]
        dil         = cfg["dilution"]
        safety      = cfg.get("safety", {})
        print_col   = int(pr["print_block_column"])
        single_cols = [int(c) for c in dil["single_tip_columns"]]
        rows_per_col = int(safety.get("tiprack_rows_per_column", 8))

        factors   = _resolve_factors(dil["factors"])
        n_needed  = 1 + len(factors)   # 1 water tip + 1 per dilution well
        n_avail   = rows_per_col * len([c for c in single_cols if c != print_col])
        assert n_avail >= n_needed, (
            f"single_tip_columns {single_cols} (excluding print col {print_col}) provide "
            f"{n_avail} tips ({rows_per_col} rows × "
            f"{len([c for c in single_cols if c != print_col])} cols); "
            f"need {n_needed} (1 water + {len(factors)} per-well stock tips)")

    def test_capture_mid_rows_are_valid(self, consts, plate_data):
        """camera.capture_mid_rows entries must all be valid row letters for the plate.

        Validates the new schema (row letters only, not full well names like 'C1').
        """
        cfg       = consts["CONFIG"]
        cam       = cfg.get("camera", {})
        mid_rows  = cam.get("capture_mid_rows", [])
        if not mid_rows:
            pytest.skip("capture_mid_rows is empty — no mid-capture configured")

        if plate_data is not None:
            valid_rows = set(_labware_rows(plate_data))
        else:
            # Fallback: standard A-H
            valid_rows = set("ABCDEFGH")

        for row_letter in mid_rows:
            assert row_letter in valid_rows, (
                f"camera.capture_mid_rows entry '{row_letter}' is not a valid plate row. "
                f"Valid rows: {sorted(valid_rows)}. "
                "Note: store row letters only (e.g. 'C'), not full well names (e.g. 'C1').")

    def test_replicate_spacing_has_z_key(self, consts):
        """replicate_spacing_mm must include 'z' so the protocol can read it (H-10 fix)."""
        pr = consts["CONFIG"]["printing"]
        sp = pr.get("replicate_spacing_mm", {})
        assert "z" in sp, (
            "replicate_spacing_mm must include 'z: 0.0' for flat paper "
            "(non-zero z enables vertical replicate offsets in future).")


# ── Protocol source validation ─────────────────────────────────────────────────────

class TestProtocolSource:
    def test_api_level_supports_partial_return(self):
        """apiLevel must be >= 2.28 — return_tip() in partial (single-nozzle) mode
        was introduced in 2.28 and raises ProtocolCommandFailedError on older versions.
        """
        source = PROTOCOL_BASE.read_text(encoding="utf-8")
        m = re.search(r'"apiLevel"\s*:\s*"([\d.]+)"', source)
        assert m, "Could not parse apiLevel from protocol source"
        major, minor = (int(x) for x in m.group(1).split(".")[:2])
        assert (major, minor) >= (2, 28), (
            f"apiLevel {m.group(1)} < 2.28; return_tip() in single-nozzle mode "
            "requires apiLevel >= 2.28.")

    def test_no_hardcoded_rows_string(self):
        """Protocol source must NOT contain _ROWS = "ABCDEFGH" (H-2 regression guard).

        The protocol now derives row labels from labware.rows_by_name() at runtime.
        """
        source = PROTOCOL_BASE.read_text(encoding="utf-8")
        assert '_ROWS = "ABCDEFGH"' not in source, (
            '_ROWS = "ABCDEFGH" found in protocol — rows must be derived from '
            'labware.rows_by_name() to remain forward-compatible with non-standard labware.')

    def test_no_hardcoded_capture_mid_wells(self):
        """Protocol CONFIG must use 'capture_mid_rows' (row letters), not 'capture_mid_wells'
        (full well names). Full well names are column-coupled (H-1 regression guard).
        """
        source = PROTOCOL_BASE.read_text(encoding="utf-8")
        assert '"capture_mid_wells"' not in source, (
            '"capture_mid_wells" found in protocol CONFIG — this schema was replaced by '
            '"capture_mid_rows" (row letters only) so that changing destination_column '
            'automatically shifts camera captures to the correct wells.')
