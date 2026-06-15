"""Throwaway check of the new waste-disposal resolvers. Deleted after running."""
import numpy as np
np.trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))  # opentrons sim shim
import importlib

m = importlib.import_module("src.protocols.plate_waste_disposal")

PLATE_COLS = [str(c) for c in range(1, 13)]
PLATE_ROWS = list("ABCDEFGH")
waste = m.CONFIG["waste"]

def check(label, got, expected):
    ok = got == expected
    print(f"[{'OK ' if ok else 'FAIL'}] {label}: {got!r}")
    assert ok, f"{label}: expected {expected!r}, got {got!r}"

# config default (source_columns: ["9"])
check("default (None)", m.resolve_source_columns(waste, None, PLATE_COLS), ["9"])
# runtime string overrides
check("'9,11'", m.resolve_source_columns(waste, "9,11", PLATE_COLS), ["9", "11"])
check("'9 11' whitespace", m.resolve_source_columns(waste, "9 11", PLATE_COLS), ["9", "11"])
check("'09' normalized", m.resolve_source_columns(waste, "09", PLATE_COLS), ["9"])
check("dedupe '9,9,11'", m.resolve_source_columns(waste, "9,9,11", PLATE_COLS), ["9", "11"])
check("'all'", m.resolve_source_columns(waste, "all", PLATE_COLS), PLATE_COLS)
check("'' falls back to config", m.resolve_source_columns(waste, "", PLATE_COLS), ["9"])
# legacy config fallback (source_column singular, no source_columns)
check("legacy source_column", m.resolve_source_columns({"source_column": "5"}, None, PLATE_COLS), ["5"])

# invalid inputs must raise
for bad in ("13", "abc", "0"):
    try:
        m.resolve_source_columns(waste, bad, PLATE_COLS)
        print(f"[FAIL] bad input {bad!r} did not raise")
        raise SystemExit(1)
    except ValueError as e:
        print(f"[OK ] bad input {bad!r} raised: {e}")

# waste vial override
check("vial default", m.resolve_waste_vial(waste, None), "A4")
check("vial override 'b2'", m.resolve_waste_vial(waste, "b2"), "B2")
check("vial blank -> default", m.resolve_waste_vial(waste, "  "), "A4")

# source wells: column-by-column, all rows
check(
    "wells '9,11'",
    m.resolve_source_wells(waste, ["9", "11"], PLATE_ROWS),
    [f"{r}9" for r in PLATE_ROWS] + [f"{r}11" for r in PLATE_ROWS],
)

# choices are well-formed and <=30 char display names
for ch in m._source_columns_choices() + m._waste_vial_choices():
    assert len(ch["display_name"]) <= 30, ch
print("[OK ] all choice display names <= 30 chars")

print("\nALL RESOLVER CHECKS PASSED")
