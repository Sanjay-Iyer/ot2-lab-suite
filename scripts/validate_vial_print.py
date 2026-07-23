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
  2. src/protocols/printing/01_vial_dilution_paper_print.py (base template, fallback)

Always validate the generated file when it exists — that is what actually runs on
the robot. Fall back to the base template if no generated file is present yet.

MUST-CONTAIN STRINGS
--------------------
Generated dynamically from the workflow YAML so that changing destination_column or
the factor list never silently breaks the validator.

USAGE (from the env that has opentrons + numpy, e.g. conda env `ai`):
    python scripts/validate_vial_print.py
    python scripts/validate_vial_print.py --config configs/workflows/user/my_run.yaml

Pass --config with the same YAML used to build the generated protocol so a custom
factor list / destination column is validated against its own expectations rather
than the committed default's. Exits 0 only if every case passes; 1 otherwise.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPO      = Path(__file__).resolve().parent.parent
LABWARE   = REPO / "labware"

# Target the generated (deployed) artifact; fall back to the base template. Each
# protocol version has its own generated basename + base template, selected by the
# config's `protocol_version` (v2 = P20-assisted dilution).
_GENERATED_DIR = REPO / "src" / "protocols" / "generated"
_PRINTING_DIR  = REPO / "src" / "protocols" / "printing"
_VERSION_FILES: dict[int, tuple[Path, Path]] = {
    1: (_GENERATED_DIR / "vial_dilution_print_latest.py",
        _PRINTING_DIR / "01_vial_dilution_paper_print.py"),
    2: (_GENERATED_DIR / "vial_dilution_print_v2_latest.py",
        _PRINTING_DIR / "02_vial_dilution_paper_print_p20_dilution.py"),
    3: (_GENERATED_DIR / "vial_dilution_print_v3_latest.py",
        _PRINTING_DIR / "03_vial_dilution_paper_print_v3.py"),
}

V3_SIMULATOR_VERSION = "7.0.2"
V3_SIMULATOR_PYTHON = (
    REPO / ".venv" / "ot2-api-2.15-py310" / "python.exe"
)


def _resolve_protocol(version: int) -> tuple[Path, Path]:
    """(target, base) for a protocol version; unknown versions fall back to v1."""
    return _VERSION_FILES.get(version, _VERSION_FILES[1])

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
    r"ProtocolCommandFailedError|InvalidProtocolData|FileNotFoundError|"
    r"KeyError|AttributeError",
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

def _normalize(workflow_cfg: dict) -> dict:
    """Return the internal CONFIG shape (dilution + color_series) for any workflow YAML.

    New-schema configs express the plan as `dilution_plan:`/`series:`; the assertions
    below are written against the internal `dilution:`/`color_series:` shape. Reuse the
    authoritative normalizer (src/core/workflow_config.py) rather than re-deriving it,
    and fall back to the raw dict for legacy configs or if normalization fails.
    """
    if not any(k in workflow_cfg for k in ("materials", "dilution_plan", "labware")):
        return workflow_cfg
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    try:
        from src.core.workflow_config import normalize_and_validate
        return normalize_and_validate(workflow_cfg, strict=False).config
    except Exception as exc:  # noqa: BLE001 - validator failures are the builder's job
        print(f"WARNING: could not normalize the workflow YAML ({exc}); "
              f"deriving assertions from the raw dict.")
        return workflow_cfg


def _protocol_api_level(source: str) -> tuple[int, int] | None:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if not ({"requirements", "metadata"} & set(names)):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
        raw = value.get("apiLevel") if isinstance(value, dict) else None
        if raw:
            major, minor = str(raw).split(".", 1)
            return int(major), int(minor)
    return None


def _fetch_robot_max_api(robot_ip: str) -> tuple[int, int]:
    url = f"http://{robot_ip}:31950/health"
    request = urllib.request.Request(url, headers={"opentrons-version": "*"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not fetch robot health from {url}: {exc}") from exc
    raw = payload.get("maximum_protocol_api_version")
    if not (
        isinstance(raw, list)
        and len(raw) == 2
        and all(isinstance(value, int) for value in raw)
    ):
        raise RuntimeError(
            "robot /health response lacks maximum_protocol_api_version: "
            + json.dumps(payload)
        )
    return int(raw[0]), int(raw[1])


def _v3_static_problems(source: str, workflow_cfg: dict) -> list[str]:
    """Check v3 source/config invariants that simulation alone cannot prove."""
    problems: list[str] = []
    if "configure_" + "nozzle_layout" in source:
        problems.append("source contains a partial-nozzle configuration call")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"source does not parse: {exc}"]

    forbidden = {"SINGLE", "PARTIAL_COLUMN", "COLUMN", "ROW", "ALL"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = {alias.name for alias in node.names}
            bad = names & forbidden
            if bad:
                problems.append(
                    "source imports nozzle-layout constants: "
                    + ", ".join(sorted(bad))
                )
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "p300":
            continue
        arguments = " ".join(
            ast.get_source_segment(source, arg) or "" for arg in node.args
        )
        arguments += " " + " ".join(
            ast.get_source_segment(source, kw.value) or "" for kw in node.keywords
        )
        if "tuberack" in arguments:
            problems.append(
                f"P300 command {node.func.attr} targets the slot-7 tuberack"
            )

    config = _normalize(workflow_cfg)
    dilution = config.get("dilution", {})
    factors = _resolve_factors(dilution.get("factors", {}))
    total = float(dilution.get("total_volume_ul", 0.0))
    dead = float(dilution.get("dead_volume_ul", 0.0))
    groups = config.get("print_groups", [])
    consumed = sum(float(group.get("volume_ul", 0.0)) for group in groups)
    if total > 340.0:
        problems.append(f"mixing-plate well total {total:g} uL exceeds 340 uL")
    if total + 0.01 < consumed + dead:
        problems.append(
            f"V={total:g} uL is below print consumption + dead volume "
            f"({consumed + dead:g} uL)"
        )
    for factor in factors:
        stock = total / factor
        water = total - stock
        if abs((stock + water) - total) > 0.1:
            problems.append(
                f"{factor:g}x stock + water differs from V by more than 0.1 uL"
            )
    for group in groups:
        if group.get("pipette") != "p20_single_gen2":
            problems.append(f"{group.get('name')}: print pipette is not P20")
        if float(group.get("volume_ul", 0.0)) > 20.0:
            problems.append(f"{group.get('name')}: P20 print volume exceeds 20 uL")
        group_tips = group.get("tips", {})
        if (
            group_tips.get("strategy") != "per_source_row"
            or group_tips.get("map_ref") != "print_by_row"
        ):
            problems.append(
                f"{group.get('name')}: print group does not reference print_by_row"
            )

    rack_name = config.get("deck", {}).get("tuberack", {}).get("load_name", "")
    rack_path = LABWARE / f"{rack_name}.json"
    try:
        rack = json.loads(rack_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"cannot read tuberack geometry {rack_path}: {exc}")
    else:
        declared = float(rack.get("dimensions", {}).get("zDimension", 0.0))
        well_top = max(
            (
                float(well.get("z", 0.0)) + float(well.get("depth", 0.0))
                for well in rack.get("wells", {}).values()
            ),
            default=0.0,
        )
        clearance = float(
            config.get("safety", {}).get("p300_travel_clearance_mm", 0.0)
        )
        if declared + 0.5 < well_top:
            problems.append(
                f"tuberack zDimension {declared:g} mm is below well top {well_top:g} mm"
            )
        if declared + clearance <= well_top:
            problems.append("P300 travel envelope does not clear the slot-7 rack")
    return problems


def _v3_simulator_problem(python_executable: str) -> str | None:
    check = subprocess.run(
        [
            python_executable,
            "-c",
            "import importlib.metadata; "
            "print(importlib.metadata.version('opentrons'))",
        ],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        return (
            f"{python_executable} cannot load opentrons package metadata; install "
            "requirements-ot2-api-2.15.txt in the isolated simulator environment"
        )
    installed = check.stdout.strip().splitlines()[-1]
    if installed != V3_SIMULATOR_VERSION:
        return (
            f"opentrons=={V3_SIMULATOR_VERSION} is required for v3 validation; "
            f"{python_executable} reports {installed}"
        )
    return None


def _build_cases(workflow_cfg: dict) -> list:
    """Build the CASES list from the workflow YAML so that changing destination_column
    or the factor list never silently breaks the validator."""
    workflow_cfg = _normalize(workflow_cfg)
    dil     = workflow_cfg.get("dilution", {})
    pr      = workflow_cfg.get("printing", {})
    col     = str(dil.get("destination_column", "1"))
    fc      = dil.get("factors", {})
    factors = _resolve_factors(fc)

    if not factors:
        factors = [1.0, 50.0]   # degenerate fallback — validate() would have caught this

    top_fold = int(factors[0])   if factors[0] == int(factors[0])   else factors[0]

    color_series = [
        item for item in workflow_cfg.get("color_series", [])
        if item.get("enabled", True)
    ]
    if not color_series:
        color_series = [{
            "name": "dye",
            "destination_column": col,
            "setup_tip": dil.get("setup_tip", ""),
            "print_block_column": pr.get("print_block_column", 1),
            "paper_start_column": pr.get("paper_start_column", 1),
            "num_replicates": pr.get("num_replicates", 1),
        }]

    top_row = "A"
    top_wells = [
        f"{top_row}{series.get('destination_column', col)}"
        for series in color_series
    ]
    stock_setup_done = [
        f"{str(series.get('name', 'dye')).lower()} stock transfers done"
        for series in color_series
    ]
    series_columns = [
        f"{str(series.get('name', 'dye')).lower()}={series.get('destination_column', col)}"
        for series in color_series
    ]
    # The end-of-print summary lists PRINT GROUP names, which only coincide with the
    # series names for legacy configs (where each series migrates to one group).
    series_prints = [
        f"{g['name']} paper columns"
        for g in workflow_cfg.get("print_groups", []) if g.get("name")
    ] or [
        f"{str(series.get('name', 'dye')).lower()} paper columns"
        for series in color_series
    ]

    # Layout-specific motion assertions. A P20-only config has no column_8up group and
    # therefore never emits the 8-tip strings; asserting them unconditionally made any
    # single-channel config unpassable. Derive them from the layouts actually present.
    groups  = workflow_cfg.get("print_groups", [])
    has_8up = (not groups) or any(g.get("layout", "column_8up") == "column_8up"
                                  for g in groups)
    has_spot = any(g.get("layout") == "single_spot" for g in groups)
    print_motion = []
    if has_8up:
        print_motion += ["picked 8 tips from column", "8 droplets -> paper column",
                         "returned 8-tip block"]
    if has_spot:
        print_motion += ["single droplet", "returned P20 tip"]
    # The "no printing happened" assertion for dilution-only / dry runs.
    no_print_marker = "8 droplets -> paper column" if has_8up else "single droplet"

    return [
        # name, flags, bad_labware, expect_ok, must_contain, must_not_contain
        ("full_run",
         dict(dry=False, dilution=True, print=True), False, True,
          ["Pre-flight validation passed",
           "One-tip setup: picked tip",
           "Water setup transfers done",
           *[f"Diluting well {well} to {top_fold:g}x" for well in top_wells],
           *stock_setup_done,
           *print_motion,
           *series_prints,
           "Paper print complete:",
           "Demo Completed ==="],
         ["PRE-FLIGHT VALIDATION FAILED", "Completed (dry run)"]),

        ("dry_run",
         dict(dry=True, dilution=True, print=True), False, True,
         ["Pre-flight validation passed", "DRY RUN", "Completed (dry run)"],
         ["Diluting well", no_print_marker]),

        ("dilution_only",
         dict(dry=False, dilution=True, print=False), False, True,
         ["One-tip setup: picked tip",
          "Water setup transfers done",
          *[f"Diluting well {well} to {top_fold:g}x" for well in top_wells],
          *stock_setup_done,
          "Mixed source column",
          "Dilution series complete",
          *series_columns,
          "Demo Completed ==="],
         [no_print_marker]),

        ("print_only",
         dict(dry=False, dilution=False, print=True), False, True,
         [*print_motion, "Paper print complete:", "Demo Completed ==="],
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


def _build_v3_cases() -> list:
    return [
        (
            "full_run",
            dict(dry=False, dilution=True, print=True),
            False,
            True,
            [
                "Pre-flight validation passed",
                "P20 water setup tip",
                "Water setup transfers done",
                "P20 stock setup tip",
                "Diluting well A11 to 1x",
                "bp stock transfers done",
                "P300 mixed column 11 with eight tips",
                "P20 print row A tip",
                "P20 print: row A, 20.00 uL -> paper column 1",
                "Paper print complete: 32 spots",
                "Paper Print V3 Completed ===",
            ],
            ["PRE-FLIGHT VALIDATION FAILED", "Completed (dry run)"],
        ),
        (
            "dry_run",
            dict(dry=True, dilution=True, print=True),
            False,
            True,
            [
                "Pre-flight validation passed",
                "DRY RUN: pre-flight only",
                "Paper Print V3 Completed (dry run)",
            ],
            ["P20 transfer:", "P20 print:"],
        ),
        (
            "dilution_only",
            dict(dry=False, dilution=True, print=False),
            False,
            True,
            [
                "P20 water setup tip",
                "P20 stock setup tip",
                "Diluting well A11 to 1x",
                "P300 mixed column 11 with eight tips",
                "Dilution series complete",
                "Paper Print V3 Completed ===",
            ],
            ["P20 print:"],
        ),
        (
            "print_only",
            dict(dry=False, dilution=False, print=True),
            False,
            True,
            [
                "P20 print row A tip",
                "P20 print: row A, 20.00 uL -> paper column 1",
                "Paper print complete: 32 spots",
                "Paper Print V3 Completed ===",
            ],
            ["P20 transfer:", "P300 mixed column"],
        ),
        (
            "wrong_labware",
            dict(dry=False, dilution=True, print=True),
            True,
            False,
            [
                "PRE-FLIGHT VALIDATION FAILED",
                "tuberack_3dprint_20ml_8vials_MISMATCH",
            ],
            ["Paper Print V3 Completed ==="],
        ),
    ]


def _expected_v3_dilution_cycles(workflow_cfg: dict) -> int:
    config = _normalize(workflow_cfg)
    dilution = config["dilution"]
    total = float(dilution["total_volume_ul"])
    maximum = float(dilution["max_transfer_ul"])
    factors = _resolve_factors(dilution["factors"])

    def cycles(volume: float) -> int:
        if volume <= 0.01:
            return 0
        return int(math.ceil((volume - 0.01) / maximum))

    return sum(
        cycles(total / factor) + cycles(total - total / factor)
        for factor in factors
    )


def _v3_dynamic_problems(
    output: str, case_name: str, workflow_cfg: dict
) -> list[str]:
    problems: list[str] = []
    dilution_volumes = [
        float(value)
        for value in re.findall(r"P20 transfer:\s*([0-9]+(?:\.[0-9]+)?)\s*uL", output)
    ]
    print_volumes = [
        float(value)
        for value in re.findall(
            r"P20 print:\s*row [A-H],\s*([0-9]+(?:\.[0-9]+)?)\s*uL",
            output,
        )
    ]
    too_large = [volume for volume in dilution_volumes + print_volumes if volume > 20.0]
    if too_large:
        problems.append(f"P20 command volume exceeds 20 uL: {too_large[0]:g}")

    if case_name in ("full_run", "dilution_only"):
        expected = _expected_v3_dilution_cycles(workflow_cfg)
        if len(dilution_volumes) != expected:
            problems.append(
                f"expected {expected} P20 dilution round trips, "
                f"observed {len(dilution_volumes)}"
            )
    if case_name in ("full_run", "print_only") and len(print_volumes) != 32:
        problems.append(
            f"expected 32 P20 paper transfers, observed {len(print_volumes)}"
        )
    return problems


def _render(source: str, flags: dict, bad_labware: bool) -> str:
    for key, (pattern, template) in _FLAG_SUBS.items():
        source = pattern.sub(template.format(flags[key]), source)
    if bad_labware:
        source = _BADCFG_RE.sub(_BADCFG_SUB, source)
    return source


def _simulate(source: str, python_executable: str | None = None) -> str:
    """Write source to a temp .py (utf-8, no BOM) and simulate it; return combined output."""
    python_executable = python_executable or sys.executable
    simulator_config = REPO / ".test_tmp" / "opentrons-simulator"
    simulator_config.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["OT_API_CONFIG_DIR"] = str(simulator_config)
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "case.py"
        f.write_text(source, encoding="utf-8")
        proc = subprocess.run(
            [python_executable, "-c", _SHIM, "-L", str(LABWARE), f.name],
            cwd=d, capture_output=True, text=True, env=env,
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
    ap = argparse.ArgumentParser(
        description="Run the generated vial_dilution_print protocol through the "
                    "Opentrons simulator under a 5-case run-mode matrix.")
    ap.add_argument(
        "--config", default=str(_WORKFLOW_YAML),
        help="Workflow YAML the must-contain assertions are derived from "
             "(default: the committed default). Pass the same user config used to "
             "build the generated protocol so a custom factor list / destination "
             "column validates against its own expectations, not the default's.")
    ap.add_argument(
        "--protocol-version", type=int, default=None, choices=sorted(_VERSION_FILES),
        help="Which generated protocol to validate. Default: the config's "
             "'protocol_version' key, else 1.")
    ap.add_argument(
        "--robot-ip", default=os.environ.get("ROBOT_IP"),
        help="Robot IP used to fetch /health for v3 API compatibility validation.",
    )
    ap.add_argument(
        "--simulator-python",
        default=None,
        help="Isolated Python executable containing opentrons==7.0.2. Default: "
             "OT2_API_2_15_PYTHON, then the current interpreter.",
    )
    args = ap.parse_args()
    workflow_yaml = Path(args.config)

    # ── Load workflow YAML for dynamic case generation ────────────────────────────
    if not workflow_yaml.exists():
        print(f"ERROR: workflow YAML not found: {workflow_yaml}")
        return 1
    if workflow_yaml != _WORKFLOW_YAML:
        print(f"Deriving case assertions from: {workflow_yaml}")
    workflow_cfg = yaml.safe_load(workflow_yaml.read_text(encoding="utf-8")) or {}

    # ── Pick the protocol version the config was built for ────────────────────────
    version = args.protocol_version or int(workflow_cfg.pop("protocol_version", 1))
    generated, base = _resolve_protocol(version)
    PROTOCOL = generated if generated.exists() else base
    print(f"Protocol version: v{version}")

    # ── Announce which file is being validated ────────────────────────────────────
    if PROTOCOL == generated:
        print(f"Validating GENERATED protocol: {PROTOCOL.relative_to(REPO)}")
    else:
        print(
            f"WARNING: Generated protocol not found at "
            f"{generated.relative_to(REPO)}; "
            f"falling back to base template: {PROTOCOL.relative_to(REPO)}\n"
            f"  Run 'python scripts/build_vial_dilution_print.py "
            f"--config {workflow_yaml}' first."
        )

    if not PROTOCOL.exists():
        print(f"ERROR: protocol not found: {PROTOCOL}")
        return 1

    source = PROTOCOL.read_text(encoding="utf-8")
    simulator_python = (
        args.simulator_python
        or os.environ.get("OT2_API_2_15_PYTHON")
        or (str(V3_SIMULATOR_PYTHON) if V3_SIMULATOR_PYTHON.exists() else None)
        or sys.executable
    )
    if version == 3:
        simulator_problem = _v3_simulator_problem(simulator_python)
        if simulator_problem:
            print(f"SIMULATION ENVIRONMENT INVALID: {simulator_problem}")
            return 1
        static_problems = _v3_static_problems(source, workflow_cfg)
        if static_problems:
            print("V3 STATIC VALIDATION FAILED:")
            for problem in static_problems:
                print(f"  - {problem}")
            return 1
        if not args.robot_ip:
            print(
                "V3 ROBOT API VALIDATION FAILED: --robot-ip (or ROBOT_IP) is "
                "required so maximum_protocol_api_version can be fetched from /health."
            )
            return 1
        try:
            robot_max = _fetch_robot_max_api(args.robot_ip)
        except RuntimeError as exc:
            print(f"V3 ROBOT API VALIDATION FAILED: {exc}")
            return 1
        emitted = _protocol_api_level(source)
        if emitted != robot_max:
            print(
                "V3 ROBOT API VALIDATION FAILED: emitted apiLevel "
                f"{emitted} does not equal robot maximum {robot_max}."
            )
            return 1
        print(
            f"Robot API compatibility passed: emitted {emitted[0]}.{emitted[1]} "
            f"equals /health maximum."
        )
        CASES = _build_v3_cases()
    else:
        CASES = _build_cases(workflow_cfg)
    print(f"Validating {PROTOCOL.relative_to(REPO)} across {len(CASES)} run modes\n")

    all_pass = True
    for name, flags, bad_labware, expect_ok, must_contain, must_not in CASES:
        output = _simulate(
            _render(source, flags, bad_labware),
            simulator_python if version == 3 else sys.executable,
        )
        problems = _evaluate(output, expect_ok, must_contain, must_not)
        if version == 3 and expect_ok:
            problems.extend(_v3_dynamic_problems(output, name, workflow_cfg))
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
