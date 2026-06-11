#!/usr/bin/env python3
"""
scripts/build_plate_waste_disposal.py
=====================================
Turn configs/workflows/defaults/plate_waste_disposal.yaml into a robot-ready copy of
src/protocols/plate_waste_disposal.py with the YAML embedded as the CONFIG dict, then
simulate it. This is how the YAML reaches the OT-2 (the robot can't read the repo).

  conda activate ai
  python scripts/build_plate_waste_disposal.py
  python scripts/build_plate_waste_disposal.py --config my.yaml --no-sim

Pipeline: load YAML -> embed CONFIG (between the markers in the base protocol) +
write run_modes into the DEFAULT_* flags -> generate into src/protocols/generated/
-> simulate (scans output text for errors, because opentrons.simulate exits 0 even
when a protocol raises at runtime).
"""
from __future__ import annotations

import argparse
import pprint
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO          = Path(__file__).resolve().parent.parent
BASE_PROTOCOL = REPO / "src" / "protocols" / "plate_waste_disposal.py"
GENERATED_DIR = REPO / "src" / "protocols" / "generated"
LABWARE       = REPO / "labware"
DEFAULT_CONFIG = REPO / "configs" / "workflows" / "defaults" / "plate_waste_disposal.yaml"

START_SENTINEL = "# >>> CONFIG START >>>"
END_SENTINEL   = "# <<< CONFIG END <<<"

_SHIM = (
    "import numpy as np; "
    "np.trapz = getattr(np, 'trapezoid', np.trapz if hasattr(np, 'trapz') else None); "
    "from opentrons.simulate import main; main()"
)
_ERROR_RE = re.compile(
    r"Traceback \(most recent call last\)|RuntimeError|LabwareNotFoundError|"
    r"ProtocolCommandFailedError|InvalidProtocolData|KeyError|AttributeError",
    re.IGNORECASE,
)

_FLAG_SUBS = {
    "dry_run": (re.compile(r"(?m)^DEFAULT_DRY_RUN\s*=.*$"), "DEFAULT_DRY_RUN = {}"),
}


def build_source(base_text: str, cfg: dict, run_modes: dict) -> str:
    out = base_text
    for key, (pattern, template) in _FLAG_SUBS.items():
        if key in run_modes:
            out = pattern.sub(template.format(bool(run_modes[key])), out)

    i_start    = out.index(START_SENTINEL)
    line_start = out.rfind("\n", 0, i_start) + 1
    i_end      = out.index(END_SENTINEL)
    line_end   = out.index("\n", i_end)

    config_repr = pprint.pformat(cfg, indent=2, sort_dicts=False, width=100)
    new_region  = (
        f"{START_SENTINEL} (auto-generated from YAML; edit the YAML, not this file)\n"
        f"CONFIG = {config_repr}\n"
        f"{END_SENTINEL}"
    )
    return out[:line_start] + new_region + out[line_end:]


def simulate(path: Path) -> tuple:
    proc = subprocess.run(
        [sys.executable, "-c", _SHIM, "-L", str(LABWARE), path.name],
        cwd=str(path.parent), capture_output=True, text=True,
    )
    output = proc.stdout + "\n" + proc.stderr
    errors = [ln.strip() for ln in output.splitlines() if _ERROR_RE.search(ln)]
    return (proc.returncode == 0 and not errors), output


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build + simulate the plate-waste-disposal protocol from YAML.")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to the YAML config.")
    ap.add_argument("--no-sim", action="store_true", help="Generate only; skip simulation.")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"ERROR: config not found: {cfg_path}")
        return 1
    full      = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    run_modes = full.pop("run_modes", {})   # not part of CONFIG
    config    = full

    base_text = BASE_PROTOCOL.read_text(encoding="utf-8")
    generated = build_source(base_text, config, run_modes)

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    run_path    = GENERATED_DIR / f"plate_waste_disposal_run_{ts}.py"
    latest_path = GENERATED_DIR / "plate_waste_disposal_latest.py"
    for dest in (run_path, latest_path):
        dest.write_text(generated, encoding="utf-8")
    print(f"Generated: {run_path.relative_to(REPO)}")
    print(f"Generated: {latest_path.relative_to(REPO)}")

    if args.no_sim:
        return 0

    ok, output = simulate(run_path)
    tail = "\n".join(line for line in output.splitlines()
                     if any(k in line for k in ("Pre-flight", "Plan:", "Emptying",
                                                "Waste removal", "Completed ===", "WARNING")))
    print("\n--- simulation key lines ---")
    print(tail)
    print("--- end ---")
    if ok:
        print("\nSIMULATION OK")
        return 0
    print("\nSIMULATION FAILED")
    print(output[-2000:])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
