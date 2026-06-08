#!/usr/bin/env python3
"""
scripts/validate_vial_print.py
==============================
Run src/protocols/vial_dilution_print.py through the Opentrons simulator under
several run modes and assert each behaves correctly.

WHY THIS EXISTS
---------------
`opentrons.simulate` exits 0 even when a protocol raises at RUNTIME (it prints the
error but returns success). A green exit code is NOT proof of a good run. This
script scans the simulator's actual OUTPUT TEXT for error markers AND for the
expected operations, turning the run-mode matrix into a real pass/fail gate.

It rewrites the protocol's DEFAULT_* flag constants (and, for the wrong-labware
case, the TUBERACK_LOADNAME constant) in a temp copy, then simulates that copy.

USAGE (from the env that has opentrons + numpy, e.g. conda env `ai`):
    python scripts/validate_vial_print.py

Exits 0 only if every case passes; 1 otherwise.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO / "src" / "protocols" / "vial_dilution_print.py"
LABWARE = REPO / "labware"

# numpy.trapz shim + opentrons CLI entrypoint (mirrors simulate_protocol.py).
_SHIM = (
    "import numpy as np; "
    "np.trapz = getattr(np, 'trapezoid', np.trapz if hasattr(np, 'trapz') else None); "
    "from opentrons.simulate import main; main()"
)

# Markers that indicate a runtime failure in the simulator output.
_ERROR_RE = re.compile(r"Error|Traceback|Exception|not allowed", re.IGNORECASE)

# DEFAULT_* flag lines we rewrite per case (anchored, whitespace-tolerant).
_FLAG_SUBS = {
    "dry":      (re.compile(r"(?m)^DEFAULT_DRY_RUN\s*=.*$"),     "DEFAULT_DRY_RUN     = {}"),
    "dilution": (re.compile(r"(?m)^DEFAULT_DO_DILUTION\s*=.*$"), "DEFAULT_DO_DILUTION = {}"),
    "print":    (re.compile(r"(?m)^DEFAULT_DO_PRINT\s*=.*$"),    "DEFAULT_DO_PRINT    = {}"),
}
# Wrong-labware injection: load the v1 rack (deck.tuberack.load_name) while
# pre-flight still expects v2 (safety.expected_tuberack_load_name) -> mismatch abort.
_LOADNAME_RE = re.compile(r'"load_name":\s*"tuberack_3dprint_20ml_8vials_v2"')
_V1_LOADNAME = '"load_name": "tuberack_3dprint_20ml_8vials_v1"'

# name, flags, bad_labware, expect_ok, must_contain, must_not_contain
CASES = [
    ("full_run", dict(dry=False, dilution=True, print=True), False,
     True,
     ["Pre-flight validation passed",
      "Diluting well A1 to 1x", "Diluting well H1 to 50x",
      "Nozzle layout: SINGLE", "Nozzle layout: ALL",
      "8-channel block pickup", "Printing 8 droplets onto paper",
      "Returned the 8 print tips", "Demo Completed ==="],
     ["PRE-FLIGHT VALIDATION FAILED", "Completed (dry run)"]),

    ("dry_run", dict(dry=True, dilution=True, print=True), False,
     True,
     ["Pre-flight validation passed", "DRY RUN", "Completed (dry run)"],
     ["Diluting well", "Printing 8 droplets onto paper"]),

    ("dilution_only", dict(dry=False, dilution=True, print=False), False,
     True,
     ["Diluting well A1 to 1x", "Dilution series complete", "Demo Completed ==="],
     ["Printing 8 droplets onto paper", "8-channel block pickup"]),

    ("print_only", dict(dry=False, dilution=False, print=True), False,
     True,
     ["8-channel block pickup", "Printing 8 droplets onto paper", "Demo Completed ==="],
     ["Diluting well"]),

    ("wrong_labware", dict(dry=False, dilution=True, print=True), True,
     False,
     ["PRE-FLIGHT VALIDATION FAILED", "!= expected 'tuberack_3dprint_20ml_8vials_v2'"],
     ["Demo Completed ==="]),
]


def _render(source: str, flags: dict, bad_labware: bool) -> str:
    for key, (pattern, template) in _FLAG_SUBS.items():
        source = pattern.sub(template.format(flags[key]), source)
    if bad_labware:
        source = _LOADNAME_RE.sub(_V1_LOADNAME, source)
    return source


def _simulate(source: str) -> str:
    """Write source to a temp .py (utf-8, no BOM) and simulate it; return combined output."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "case.py"
        f.write_text(source, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-c", _SHIM, "-L", str(LABWARE), f.name],
            cwd=d,
            capture_output=True,
            text=True,
        )
        return proc.stdout + "\n" + proc.stderr


def _evaluate(output, expect_ok, must_contain, must_not_contain) -> list:
    problems = []
    error_lines = [ln.strip() for ln in output.splitlines() if _ERROR_RE.search(ln)]
    if expect_ok and error_lines:
        problems.append(f"unexpected error: {error_lines[0]}")
    if not expect_ok and not error_lines:
        problems.append("expected an error/abort, but none was found")
    for s in must_contain:
        if s not in output:
            problems.append(f"missing expected text: {s!r}")
    for s in must_not_contain:
        if s in output:
            problems.append(f"found forbidden text: {s!r}")
    return problems


def main() -> int:
    if not PROTOCOL.exists():
        print(f"ERROR: protocol not found: {PROTOCOL}")
        return 1
    source = PROTOCOL.read_text(encoding="utf-8")
    print(f"Validating {PROTOCOL.relative_to(REPO)} across {len(CASES)} run modes\n")

    all_pass = True
    for name, flags, bad_labware, expect_ok, must_contain, must_not in CASES:
        output = _simulate(_render(source, flags, bad_labware))
        problems = _evaluate(output, expect_ok, must_contain, must_not)
        if problems:
            all_pass = False
            print(f"[FAIL] {name}")
            for p in problems:
                print(f"       - {p}")
        else:
            print(f"[PASS] {name}")

    print()
    if all_pass:
        print("ALL CASES PASSED")
        return 0
    print("SOME CASES FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
