#!/usr/bin/env python3
"""Export immutable paper artifacts from the static Experiment 01 protocol."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np


np.trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
PROTOCOL = REPO / "src" / "protocols" / "printing" / "01_printing_standard_ground_truth.py"
DEFAULT_OUTPUT = REPO / "experiment_01" / "ground_truth"


def _load_protocol():
    spec = importlib.util.spec_from_file_location("experiment_01_static_export", PROTOCOL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import static ground truth: {PROTOCOL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def export(output_dir: Path) -> dict[str, object]:
    module = _load_protocol()
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = module.build_ground_truth_plan()
    canonical_sha = module.ground_truth_sha256()
    protocol_sha = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()

    trace_path = output_dir / "static_canonical_trace.json"
    hash_path = output_dir / "static_canonical_sha256.txt"
    reference_path = output_dir / "static_protocol_reference.json"
    trace_path.write_text(module.ground_truth_artifact_json(), encoding="utf-8", newline="\n")
    hash_path.write_text(f"{canonical_sha}\n", encoding="utf-8", newline="\n")
    reference_path.write_text(
        json.dumps(
            {
                "protocol_path": PROTOCOL.relative_to(REPO).as_posix(),
                "protocol_sha256": protocol_sha,
                "canonical_trace_path": trace_path.relative_to(REPO).as_posix(),
                "canonical_trace_sha256": canonical_sha,
                "action_count": plan["totals"]["action_count"],
                "print_count": plan["totals"]["print_count"],
                "status": "FROZEN_AFTER_STAGE_1_ARCHITECTURE_AUDIT",
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "canonical_sha256": canonical_sha,
        "protocol_sha256": protocol_sha,
        "action_count": plan["totals"]["action_count"],
        "print_count": plan["totals"]["print_count"],
        "paths": [str(trace_path), str(hash_path), str(reference_path)],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(export(args.output_dir.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
