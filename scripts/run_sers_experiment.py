#!/usr/bin/env python3
"""Run a SERS experiment from a YAML config. Offline simulation is the default.

This is the deterministic path: no LLM, no agent, no conversation. It accepts
both config formats.

  sers-experiment-intent/v1   the experiment contract the agent also writes
                              (configs/experiments/*.yaml) - resolved here
  sers-experiment/v1          a hand-written low-level execution config
                              (configs/sers_cv_titration_demo.yaml)

    python scripts/run_sers_experiment.py --config configs/experiments/sers_exp1_np_cv.yaml
    python scripts/run_sers_experiment.py --config <path> --plan       # review only
    python scripts/run_sers_experiment.py --config <path> --live --confirm-robot-ready

--live uploads the emitted protocol over the robot's HTTP API and plays it.
Every gate in src/sers_engine/execution.py must pass first.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.sers_engine.agent_tools import execute_sers_workflow  # noqa: E402
from src.sers_engine.intent import INTENT_SCHEMA_VERSION  # noqa: E402
from src.sers_engine.provenance import close_session, create_session  # noqa: E402
from src.sers_engine.provenance.models import Event, sha256_path  # noqa: E402
from src.sers_engine.schema import SERSConfigError, config_as_dict, load_experiment_config  # noqa: E402
from src.sers_engine.state import ExperimentSession  # noqa: E402
from src.sers_engine.summary import render_review_plan  # noqa: E402

DEFAULT_CONFIG = REPO_ROOT / "configs" / "experiments" / "sers_exp1_np_cv.yaml"
LOG = logging.getLogger("sers_engine")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SERSConfigError(f"experiment config not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SERSConfigError(f"experiment config must be a YAML mapping: {path}")
    return payload


def _open_record(args: argparse.Namespace, config_path: Path, mode: str):
    """Start the provenance record for a run that has no conversation behind it.

    The deterministic path deserves the same reconstructable record as the agent
    path: the config that went in, the plan it resolved to, what was validated
    and simulated, and what the robot did. There is simply no chat to log.
    """
    record = create_session(
        label=config_path.stem,
        mode=mode,
        input_config=str(config_path),
        input_config_sha256=sha256_path(config_path),
    )
    try:
        (record.directory / "input_config.yaml").write_text(
            config_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    except OSError as exc:
        record._degrade(f"could not copy the input config: {exc}")
    record.log_event(
        Event.MANUAL_CONFIG_EXECUTION,
        details={
            "config": str(config_path),
            "config_sha256": sha256_path(config_path),
            "command": [sys.executable, *sys.argv],
            "mode": "live" if args.live else "simulate",
            "note": "manual config execution; no language model was involved",
        },
    )
    return record


def _run_intent_config(args: argparse.Namespace, payload: dict[str, Any], record) -> int:
    session = ExperimentSession.create(payload)
    report = session.resolve_and_validate()
    for warning in report.warnings:
        LOG.warning("%s", warning)
    if not report.ok:
        for error in report.errors:
            LOG.error("%s", error)
        return 1

    print(render_review_plan(session.resolved))
    if args.plan:
        return 0

    # The manual path stands in for the conversational plan approval: running
    # this command with a config in hand IS the operator reviewing the plan.
    session.approve_plan()
    simulation = session.simulate()
    print()
    print(f"SIMULATION {simulation.status.upper()}")
    print(f"  commands        {simulation.command_count}")
    print(f"  deposits        {simulation.deposits}")
    print(f"  tips            {simulation.tips_used}/{simulation.tips_required}")
    print(f"  printed         {simulation.printed_volume_ul:g} uL")
    print(f"  resolved hash   {simulation.resolved_hash}")
    for warning in simulation.depth_warnings:
        print(f"  depth warning   {warning}")
    for error in simulation.errors:
        print(f"  ERROR           {error}")
    directory = session.write_snapshot("simulated" if simulation.passed else "simulation_failed")
    print(f"  snapshot        {directory}")
    print(f"  record          {record.directory}")
    if not simulation.passed:
        return 1

    if not args.live:
        return 0

    if not args.confirm_robot_ready:
        LOG.error("--live requires --confirm-robot-ready")
        return 1

    from src.sers_engine.execution import execute_live, preflight

    # Running this command with --live --confirm-robot-ready IS the operator's
    # authorization, so that is what goes on file as the approval text.
    session.approve_live_execution(
        f"operator ran {Path(sys.argv[0]).name} --live --confirm-robot-ready "
        f"for {args.config}"
    )
    check = preflight(session, args.robot_ip)
    print()
    print("PREFLIGHT")
    for gate in check.gates:
        print(f"  [{'ok ' if gate.passed else 'BLOCK'}] {gate.name}: {gate.detail}")
    if not check.ready:
        for item in check.hardware_confirmations:
            print(f"  needs human hardware confirmation: {item}")
        LOG.error("live execution blocked: %s", ", ".join(check.blocking))
        return 1

    result = execute_live(session, robot_host=args.robot_ip, poll_seconds=args.poll_seconds)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "succeeded" else 1


def _run_legacy_config(args: argparse.Namespace, record) -> int:
    config = load_experiment_config(args.config)
    LOG.info("Validated legacy experiment %s", config.experiment_name)
    record.describe(experiment_name=config.experiment_name)
    if args.live:
        LOG.error(
            "--live is not supported for the legacy sers-experiment/v1 format; "
            "port the config to sers-experiment-intent/v1 (see "
            "configs/experiments/sers_exp1_np_cv.yaml) so it goes through the "
            "guarded execution gates"
        )
        return 1
    result = execute_sers_workflow(config_as_dict(config), live=False)
    print(json.dumps(result, indent=2, sort_keys=True))
    record.log_event(
        Event.SIMULATION_PASSED if result.get("status") == "completed" else Event.SIMULATION_FAILED,
        config_hash=result.get("config_hash"),
        details={"format": "sers-experiment/v1 (legacy)", "result": result},
    )
    record._write_json(record.directory / "simulation_report.json", result)
    print(f"  record  {record.directory}")
    return 0 if result.get("status") == "completed" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default=str(DEFAULT_CONFIG), help=f"Experiment YAML (default: {DEFAULT_CONFIG})"
    )
    parser.add_argument(
        "--plan", action="store_true", help="Print the resolved plan and stop; no simulation."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--simulate", action="store_true", help="Virtual hardware (the default).")
    mode.add_argument(
        "--live",
        action="store_true",
        help="Run on the physical OT-2 through the guarded execution gates.",
    )
    parser.add_argument(
        "--confirm-robot-ready",
        action="store_true",
        help="Confirm the deck is physically ready; required with --live.",
    )
    parser.add_argument("--robot-ip", default=None, help="Robot host or IP (default: discover).")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s"
    )
    config_path = Path(args.config).expanduser().resolve()
    record = None
    status = "error"
    try:
        payload = _read_yaml(config_path)
        intent = payload.get("schema_version") == INTENT_SCHEMA_VERSION
        record = _open_record(args, config_path, "manual" if intent else "manual_legacy")
        code = (
            _run_intent_config(args, payload, record)
            if intent
            else _run_legacy_config(args, record)
        )
        status = "complete" if code == 0 else "failed"
        return code
    except Exception as exc:
        LOG.error("%s: %s", type(exc).__name__, exc)
        if record is not None:
            record.log_event(
                "RUN_ERROR", details={"error": f"{type(exc).__name__}: {exc}"}
            )
        if args.verbose:
            raise
        return 1
    finally:
        if record is not None:
            close_session(record, status=status)


if __name__ == "__main__":
    raise SystemExit(main())
