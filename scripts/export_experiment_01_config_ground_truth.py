"""Export the Experiment 01 CONFIG ground truth and its comparison to the static one.

Run from the repository root:

    C:\\Users\\iyer95\\miniconda3\\envs\\ai\\python.exe scripts/export_experiment_01_config_ground_truth.py

This writes only under ``experiment_01/ground_truth/`` and ``experiment_01/comparison/``.
It never contacts a robot; pass ``--simulate`` to additionally build and locally
simulate the trusted executor carrying the resolved plan.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.printing.standard import builder, equivalence  # noqa: E402
from src.printing.standard.loader import load_experiment_job  # noqa: E402
from src.printing.standard.resolver import resolve_experiment_job  # noqa: E402
from src.printing.standard.review import render_plan_review  # noqa: E402


CONFIG_REFERENCE = "configs/experiments/01_printing_standard.yaml"
GROUND_TRUTH = REPO / "experiment_01" / "ground_truth"
COMPARISON = REPO / "experiment_01" / "comparison"
STATIC_TRACE = GROUND_TRUTH / "static_canonical_trace.json"


def _write_json(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.write_text(text, encoding="utf-8")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="also build the trusted executor and run a local OT-2 simulation",
    )
    args = parser.parse_args()

    job = load_experiment_job(CONFIG_REFERENCE)
    plan = resolve_experiment_job(job)
    static_plan = json.loads(STATIC_TRACE.read_text(encoding="utf-8"))

    _write_json(GROUND_TRUTH / "config_job.json", job.model_dump(mode="json"))
    _write_json(GROUND_TRUTH / "config_resolved_plan.json", plan.model_dump(mode="json"))
    _write_json(
        GROUND_TRUTH / "config_physical_trace.json", equivalence.physical_trace(plan)
    )
    _write_json(GROUND_TRUTH / "config_setup_trace.json", equivalence.setup_trace(plan))
    (GROUND_TRUTH / "config_review.txt").write_text(
        render_plan_review(plan, job) + "\n", encoding="utf-8"
    )

    hashes = {
        "config_reference": CONFIG_REFERENCE,
        "machine_profile": "configs/machines/ot2_standard_printing_p20_v1.yaml",
        "job_sha256": job.job_id,
        "plan_canonical_sha256": plan.plan_id,
        "plan_physical_sha256": equivalence.physical_sha256(plan),
        "plan_setup_sha256": equivalence.setup_sha256(plan),
        "plan_structural_sha256": equivalence.structural_sha256(plan),
        "static_physical_sha256": equivalence.physical_sha256(static_plan),
        "static_setup_sha256": equivalence.setup_sha256(static_plan),
        "static_structural_sha256": equivalence.structural_sha256(static_plan),
    }
    _write_json(GROUND_TRUTH / "config_hashes.json", hashes)

    report = equivalence.compare_plans(
        static_plan, plan, left_label="static", right_label="config"
    )
    _write_json(COMPARISON / "static_vs_config.json", report)

    lines = [
        "# Experiment 01 - static vs config ground truth",
        "",
        f"static actions : {report['left_action_count']}",
        f"config actions : {report['right_action_count']}",
        f"physical match : {report['physical_match']}",
        f"setup match    : {report['setup_match']}",
        f"execution match: {report['execution_match']}",
        f"structural match: {report['structural_match']}",
        "",
        f"static physical SHA-256  : {report['left_physical_sha256']}",
        f"config physical SHA-256  : {report['right_physical_sha256']}",
        f"static setup SHA-256     : {report['left_setup_sha256']}",
        f"config setup SHA-256     : {report['right_setup_sha256']}",
        f"static structural SHA-256: {report['left_structural_sha256']}",
        f"config structural SHA-256: {report['right_structural_sha256']}",
        "",
    ]
    if report["physical_differences"]:
        lines.append("## Physical differences")
        lines.extend(f"- {line}" for line in report["physical_differences"])
    else:
        lines.append("No physical differences.")
    (COMPARISON / "static_vs_config.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    print(f"job_sha256            : {job.job_id}")
    print(f"plan canonical sha256 : {plan.plan_id}")
    print(f"plan physical sha256  : {hashes['plan_physical_sha256']}")
    print(f"static physical sha256: {hashes['static_physical_sha256']}")
    print(f"physical match        : {report['physical_match']}")
    print(f"setup match           : {report['setup_match']}")
    print(f"execution match       : {report['execution_match']}")
    print(f"structural match      : {report['structural_match']}")
    print(f"actions               : {report['right_action_count']}")

    if args.simulate:
        artifact = builder.build_standard_protocol(plan)
        passed, run_log, text = builder.simulate_standard_protocol(
            artifact.protocol_path, expected_sha256=artifact.protocol_sha256
        )
        deposits = sum(
            1
            for entry in run_log
            if entry["payload"].get("text", "").startswith("Dispensing 6.5 uL into")
            and "Paper Print Surface" in entry["payload"]["text"]
        )
        print(f"simulated artifact    : {artifact.protocol_path}")
        print(f"artifact sha256       : {artifact.protocol_sha256}")
        print(f"simulation            : {'PASS' if passed else 'FAIL'}")
        print(f"paper deposits        : {deposits}")
        print(f"final comment         : {text.splitlines()[-1]}")

    return 0 if report["execution_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
