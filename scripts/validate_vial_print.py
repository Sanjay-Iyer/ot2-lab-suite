#!/usr/bin/env python3
"""
scripts/validate_vial_print.py
==============================
Run the generated (or base) vial_dilution_print protocol through the Opentrons
simulator under several run modes and assert each behaves correctly.

WHY THIS EXISTS
---------------
`opentrons.simulate` exits 0 even when a protocol raises at RUNTIME (it prints the
error but returns success). A green exit code is NOT proof of a good run. This
script scans the simulator's actual OUTPUT TEXT for error markers AND for the
expected operations, turning the run-mode matrix into a real pass/fail gate.

TARGET FILE PRIORITY
--------------------
  1. src/protocols/generated/vial_dilution_print_latest.py  (deployed artifact)
  2. src/protocols/vial_dilution_print.py                   (base template, fallback)

Always validate the generated file when it exists — that is what actually runs on
the robot. Fall back to the base template if no generated file is present yet.

MUST-CONTAIN STRINGS
--------------------
Generated dynamically from the workflow YAML so that changing destination_column or
the factor list never silently breaks the validator.

USAGE (from the env that has opentrons + numpy, e.g. conda env `ai`):
    python scripts/validate_vial_print.py

Exits 0 only if every case passes; 1 otherwise.
"""
from __future__ import annotations

import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO      = Path(__file__).resolve().parent.parent
LABWARE   = REPO / "labware"

# Target the generated (deployed) artifact; fall back to the base template.
_GENERATED = REPO / "src" / "protocols" / "generated" / "vial_dilution_print_latest.py"
_BASE      = REPO / "src" / "protocols" / "vial_dilution_print.py"
PROTOCOL   = _GENERATED if _GENERATED.exists() else _BASE

# Workflow YAML: source of truth for well names and fold values used in must_contain
_WORKFLOW_YAML = REPO / "configs" / "workflows" / "defaults" / "vial_dilution_print.yaml"

# numpy.trapz shim + opentrons CLI entrypoint (mirrors simulate_protocol.py).
_SHIM = (
    "import numpy as np; "
    "np.trapz = getattr(np, 'trapezoid', np.trapz if hasattr(np, 'trapz') else None); "
    "from opentrons.simulate import main; main()"
)

# Tightened error regex — genuine Python/Opentrons failure markers only.
# "not allowed" removed because it appears in valid Opentrons warning messages and
# some user protocol.comment() calls, causing false-positive failures.
_ERROR_RE = re.compile(
    r"Traceback \(most recent call last\)|RuntimeError|LabwareNotFoundError|"
    r"ProtocolCommandFailedError|InvalidProtocolData|KeyError|AttributeError",
    re.IGNORECASE,
)

# DEFAULT_* flag lines we rewrite per case (anchored, whitespace-tolerant).
_FLAG_SUBS = {
    "dry":      (re.compile(r"(?m)^DEFAULT_DRY_RUN\s*=.*$"),     "DEFAULT_DRY_RUN     = {}"),
    "dilution": (re.compile(r"(?m)^DEFAULT_DO_DILUTION\s*=.*$"), "DEFAULT_DO_DILUTION = {}"),
    "print":    (re.compile(r"(?m)^DEFAULT_DO_PRINT\s*=.*$"),    "DEFAULT_DO_PRINT    = {}"),
}

# Wrong-labware injection: desync the pre-flight EXPECTED rack name from the loaded
# one so pre-flight aborts on a geometry/identity mismatch.
# Two patterns: pprint.pformat() in the generated file produces single-quoted Python
# dict repr; the base template uses double-quoted string literals.
_BADCFG_RE = re.compile(
    r"""(['"])expected_tuberack_load_name\1\s*:\s*(['"])tuberack_3dprint_20ml_8vials_v2\2"""
)
_BADCFG_SUB = lambda m: (  # noqa: E731
    f"{m.group(1)}expected_tuberack_load_name{m.group(1)}: "
    f"{m.group(2)}tuberack_3dprint_20ml_8vials_MISMATCH{m.group(2)}"
)


# ── Dynamic factor resolver (no opentrons import) ─────────────────────────────────

def _resolve_factors(fc: dict) -> list:
    """Minimal local copy of the protocol's resolve_factors for must_contain generation."""
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
    lo, hi = math.log(start), math.log(end)
    return [round(math.exp(lo + (hi - lo) * i / (count - 1)), 4) for i in range(count)]


# ── Dynamic must-contain generation ──────────────────────────────────────────────

def _build_cases(workflow_cfg: dict) -> list:
    """Build the CASES list from the workflow YAML so that changing destination_column
    or the factor list never silently breaks the validator."""
    dil     = workflow_cfg.get("dilution", {})
    col     = str(dil.get("destination_column", "1"))
    fc      = dil.get("factors", {})
    factors = _resolve_factors(fc)

    if not factors:
        factors = [1.0, 50.0]   # degenerate fallback — validate() would have caught this

    top_fold = int(factors[0])   if factors[0] == int(factors[0])   else factors[0]
    bot_fold = int(factors[-1])  if factors[-1] == int(factors[-1]) else factors[-1]

    # Row letters for top and bottom dilution wells, derived from factor count
    top_row = "A"
    bot_row = chr(ord("A") + len(factors) - 1)   # 8 factors -> 'H'
    top_well = f"{top_row}{col}"
    bot_well = f"{bot_row}{col}"

    return [
        # name, flags, bad_labware, expect_ok, must_contain, must_not_contain
        ("full_run",
         dict(dry=False, dilution=True, print=True), False, True,
         ["Pre-flight validation passed",
          f"Diluting well {top_well} to {top_fold:g}x",
          f"Diluting well {bot_well} to {bot_fold:g}x",
          "Nozzle layout: SINGLE", "Nozzle layout: ALL",
          "8-channel block pickup", "Printing 8 droplets onto paper",
          "Returned the 8 print tips", "Demo Completed ==="],
         ["PRE-FLIGHT VALIDATION FAILED", "Completed (dry run)"]),

        ("dry_run",
         dict(dry=True, dilution=True, print=True), False, True,
         ["Pre-flight validation passed", "DRY RUN", "Completed (dry run)"],
         ["Diluting well", "Printing 8 droplets onto paper"]),

        ("dilution_only",
         dict(dry=False, dilution=True, print=False), False, True,
         [f"Diluting well {top_well} to {top_fold:g}x",
          "Dilution series complete", "Demo Completed ==="],
         ["Printing 8 droplets onto paper", "8-channel block pickup"]),

        ("print_only",
         dict(dry=False, dilution=False, print=True), False, True,
         ["8-channel block pickup", "Printing 8 droplets onto paper", "Demo Completed ==="],
         ["Diluting well"]),

        ("wrong_labware",
         dict(dry=False, dilution=True, print=True), True, False,
         ["PRE-FLIGHT VALIDATION FAILED",
          # The injected bad name appears in the 'expected' slot of the pre-flight error.
          # Confirming this string is in output verifies:
          #   (a) the injection successfully modified the CONFIG, and
          #   (b) the pre-flight identity check fired on the specific name mismatch.
          "tuberack_3dprint_20ml_8vials_MISMATCH"],
         ["Demo Completed ==="]),
    ]


def _render(source: str, flags: dict, bad_labware: bool) -> str:
    for key, (pattern, template) in _FLAG_SUBS.items():
        source = pattern.sub(template.format(flags[key]), source)
    if bad_labware:
        source = _BADCFG_RE.sub(_BADCFG_SUB, source)
    return source


def _simulate(source: str) -> str:
    """Write source to a temp .py (utf-8, no BOM) and simulate it; return combined output."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "case.py"
        f.write_text(source, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-c", _SHIM, "-L", str(LABWARE), f.name],
            cwd=d, capture_output=True, text=True,
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
    # ── Announce which file is being validated ────────────────────────────────────
    if PROTOCOL == _GENERATED:
        print(f"Validating GENERATED protocol: {PROTOCOL.relative_to(REPO)}")
    else:
        print(
            f"WARNING: Generated protocol not found at "
            f"{_GENERATED.relative_to(REPO)}; "
            f"falling back to base template: {PROTOCOL.relative_to(REPO)}\n"
            f"  Run 'python scripts/build_vial_dilution_print.py' first."
        )

    if not PROTOCOL.exists():
        print(f"ERROR: protocol not found: {PROTOCOL}")
        return 1

    # ── Load workflow YAML for dynamic case generation ────────────────────────────
    if not _WORKFLOW_YAML.exists():
        print(f"ERROR: workflow YAML not found: {_WORKFLOW_YAML}")
        return 1
    workflow_cfg = yaml.safe_load(_WORKFLOW_YAML.read_text(encoding="utf-8")) or {}

    CASES = _build_cases(workflow_cfg)
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
