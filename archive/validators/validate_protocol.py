#!/usr/bin/env python3
"""
scripts/validate_protocol.py
============================
Run src/protocols/verify_tuberack.py through the Opentrons simulator under every
run-mode combination and assert each behaves correctly.

WHY THIS EXISTS
---------------
`opentrons.simulate` exits with code 0 even when a protocol raises at RUNTIME (it
prints the error but still returns success). So a green exit code is NOT proof of a
good run. This script scans the simulator's actual OUTPUT TEXT for error markers and
for the expected operations, turning the run-mode matrix into a real pass/fail gate.

It exercises each case by rewriting the protocol's DEFAULT_* flag constants in a temp
copy (the Opentrons 9.x simulator has no CLI/Python hook to inject Runtime Parameter
values), then simulating that copy.

USAGE (from the env that has opentrons + numpy, e.g. conda env `ai`):
    python scripts/validate_protocol.py

Exits 0 only if every case passes; 1 otherwise.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO / "src" / "protocols" / "verify_tuberack.py"
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
    "dry":   (re.compile(r"(?m)^DEFAULT_DRY_RUN_TOP_ONLY\s*=.*$"), "DEFAULT_DRY_RUN_TOP_ONLY = {}"),
    "vials": (re.compile(r"(?m)^DEFAULT_VIALS_PRESENT\s*=.*$"),    "DEFAULT_VIALS_PRESENT    = {}"),
    "wall":  (re.compile(r"(?m)^DEFAULT_WALL_TOUCH_CHECK\s*=.*$"), "DEFAULT_WALL_TOUCH_CHECK = {}"),
    "wells": (re.compile(r'(?m)^DEFAULT_WELLS_TO_TEST\s*=.*$'),    'DEFAULT_WELLS_TO_TEST    = "{}"'),
}

# name, flags, expect_ok, must_contain, must_not_contain
CASES = [
    ("run1_top_only",         dict(dry=True,  vials=True,  wall=False, wells="all"),
     True,  ["Pre-flight validation passed", "Protocol Completed"], ["Touching tip", "Mixing"]),
    ("run2_empty_walltouch",  dict(dry=False, vials=False, wall=True,  wells="all"),
     True,  ["Touching tip", "Protocol Completed"],                 ["Mixing"]),
    ("run3_water_mix",        dict(dry=False, vials=True,  wall=False, wells="all"),
     True,  ["Mixing", "Protocol Completed"],                       ["Touching tip"]),
    ("subset_a1_only",        dict(dry=True,  vials=True,  wall=False, wells="a1"),
     True,  ["Testing Well: A1", "Protocol Completed"],             ["Testing Well: B1"]),
    ("subset_corners",        dict(dry=True,  vials=True,  wall=False, wells="corners"),
     True,  ["Testing Well: A4", "Testing Well: B4", "Protocol Completed"], ["Testing Well: A2"]),
    ("interlock_glass_probe", dict(dry=True,  vials=True,  wall=True,  wells="all"),
     False, ["PRE-FLIGHT VALIDATION FAILED"],                       ["Protocol Completed"]),
]


def _render(source: str, dry, vials, wall, wells) -> str:
    vals = {"dry": dry, "vials": vials, "wall": wall, "wells": wells}
    for key, (pattern, template) in _FLAG_SUBS.items():
        source = pattern.sub(template.format(vals[key]), source)
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
    for name, flags, expect_ok, must_contain, must_not in CASES:
        output = _simulate(_render(source, **flags))
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
