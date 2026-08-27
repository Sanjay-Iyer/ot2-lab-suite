#!/usr/bin/env python3
"""Conversational SERS experiment agent.

    python scripts/sers_agent.py                     # full session
    python scripts/sers_agent.py --simulation-only   # no robot tools at all
    python scripts/sers_agent.py --check             # verify LLM auth and exit
    python scripts/sers_agent.py "make 30x and 50x NP dilutions..."

Talk to it in plain English. It proposes a workflow, you revise it, it
simulates, and only after two explicit approvals can it start the robot.

The deterministic path never needs this script: scripts/run_sers_experiment.py
runs a hand-written YAML config with no LLM involved.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.sers_engine.agent.graph import SERSExperimentAgent, make_default_agent  # noqa: E402
from src.sers_engine.provenance import close_session, create_session  # noqa: E402
from src.sers_engine.state import REGISTRY  # noqa: E402

# Tool results whose text the user should see verbatim rather than paraphrased.
VERBATIM_TOOLS = {"summarize_experiment", "create_experiment", "update_experiment"}

BANNER = """\
SERS Experiment Agent
  Describe an experiment in plain English. Ask for changes and it will patch the
  current experiment rather than starting over.
  Everything you say, every tool call, every revision and every approval is
  written to this session's provenance directory as it happens.
  Commands:  /plan  /state  /session  /snapshot  /help  /quit
"""


def _show_tool_output(agent: SERSExperimentAgent, seen: int) -> int:
    """Print any new plan text the tools produced. Returns the new cursor."""
    transcript = agent.tool_transcript()
    for message in transcript[seen:]:
        if message["name"] not in VERBATIM_TOOLS:
            continue
        try:
            payload = json.loads(message["content"]) if isinstance(message["content"], str) else message["content"]
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("plan"):
            print()
            print(payload["plan"])
        for error in payload.get("validation", {}).get("errors", []) or []:
            print(f"  ERROR   {error}")
        for warning in payload.get("validation", {}).get("warnings", []) or []:
            print(f"  warning {warning}")
        if payload.get("error"):
            print(f"  ERROR   {payload['error']}")
    return len(transcript)


def _confirm_robot_call(pending: list[dict]) -> bool:
    print()
    print("  " + "=" * 68)
    print("  HUMAN APPROVAL REQUIRED - the agent wants to call:")
    for call in pending:
        print(f"    {call['name']}({json.dumps(call['args'])})")
    print("  " + "=" * 68)
    answer = input("  Allow this call? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _print_state(agent: SERSExperimentAgent) -> None:
    try:
        session = REGISTRY.get()
    except Exception:
        print("  (no experiment yet)")
        return
    snapshot = session.snapshot()
    print(f"  experiment      {snapshot.experiment_name}  [{snapshot.experiment_id}]")
    print(f"  status          {snapshot.status.value}   revision {snapshot.revision}")
    print(f"  config hash     {snapshot.config_hash}")
    print(f"  resolved hash   {snapshot.resolved_hash}")
    print(f"  simulated hash  {snapshot.simulated_hash}")
    print(f"  plan approved   {snapshot.plan_approved}")
    print(f"  live approved   {snapshot.live_execution_approved}")
    if snapshot.robot_run_id:
        print(f"  robot run       {snapshot.robot_run_id} ({snapshot.robot_run_status})")
    if snapshot.last_change:
        print(f"  last change     {snapshot.last_change}")


def _handle_command(command: str, agent: SERSExperimentAgent) -> bool:
    """Return True if the command was handled locally."""
    if command in {"/quit", "/exit"}:
        raise SystemExit(0)
    if command == "/help":
        print(BANNER)
        return True
    if command == "/state":
        _print_state(agent)
        return True
    if command == "/plan":
        from src.sers_engine.summary import render_review_plan

        try:
            session = REGISTRY.get()
        except Exception:
            print("  (no experiment yet)")
            return True
        if session.resolved is None:
            session.resolve_and_validate()
        if session.resolved is None:
            print("  (the experiment does not resolve yet)")
            for error in session.validation.errors if session.validation else []:
                print(f"  ERROR   {error}")
            return True
        print(render_review_plan(session.resolved))
        return True
    if command == "/session":
        record = agent.provenance
        if record is None:
            print("  (this session is not being recorded)")
            return True
        record.write_manifest()
        print(f"  session id      {record.session_id}")
        print(f"  record          {record.directory}")
        print(f"  export with     python scripts/export_sers_run.py --session {record.session_id}")
        if record.degraded:
            for reason in record.degraded_reasons:
                print(f"  INCOMPLETE      {reason}")
        return True
    if command == "/snapshot":
        try:
            session = REGISTRY.get()
        except Exception:
            print("  (no experiment yet)")
            return True
        directory = session.write_snapshot("manual")
        print(f"  saved {directory}")
        if agent.provenance is not None:
            agent.provenance.write_manifest()
            print(f"  record {agent.provenance.directory}")
        return True
    return False


def converse(agent: SERSExperimentAgent, message: str, seen: int) -> int:
    result = agent.send(message)
    seen = _show_tool_output(agent, seen)

    while result["interrupted"]:
        if _confirm_robot_call(result["pending_tools"]):
            result = agent.resume()
        else:
            result = agent.refuse_pending_tool("the operator declined at the terminal")
        seen = _show_tool_output(agent, seen)

    if result["reply"]:
        print()
        print(f"Agent > {result['reply']}")
    return seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="*", help="Send one message and exit.")
    parser.add_argument(
        "--simulation-only",
        action="store_true",
        help="Build the agent without any robot tools; simulation only.",
    )
    parser.add_argument("--check", action="store_true", help="Verify LLM auth and exit.")
    parser.add_argument("--thread", default="sers-session", help="Conversation thread id.")
    args = parser.parse_args(argv)

    from src.core.config import Config

    if args.check:
        print(Config.describe_llm_auth())
        try:
            Config.get_llm(temperature=0, max_retries=1).invoke("Reply with exactly: SERS_OK")
        except Exception as exc:
            print(f"LLM auth FAILED: {exc}")
            return 1
        print("LLM auth OK.")
        return 0

    # Open the scientific record before the agent exists, so the very first
    # thing the researcher says is already being written down.
    record = create_session(label="agent", mode="agent", thread_id=args.thread)

    try:
        agent = make_default_agent(
            thread_id=args.thread,
            allow_robot_tools=not args.simulation_only,
            provenance=record,
        )
    except Exception as exc:
        close_session(record, status="agent_start_failed")
        print(f"Could not start the agent: {type(exc).__name__}: {exc}")
        print("Check LLM auth with: python scripts/sers_agent.py --check")
        return 1

    if args.message:
        try:
            converse(agent, " ".join(args.message), 0)
        finally:
            close_session(record, status="single_message")
            print(f"\n  record  {record.directory}")
        return 0

    print(BANNER)
    print(f"  record: {record.directory}")
    if args.simulation_only:
        print("  (simulation-only: robot tools are not loaded)")
    print()
    seen = 0
    try:
        while True:
            try:
                line = input("SERS Agent > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line:
                continue
            if line.startswith("/"):
                if _handle_command(line, agent):
                    continue
                print(f"  unknown command {line}; try /help")
                continue
            try:
                seen = converse(agent, line, seen)
            except SystemExit:
                raise
            except Exception as exc:
                print(f"  ERROR   {type(exc).__name__}: {exc}")
    finally:
        close_session(record, status="closed")
        print(f"  record  {record.directory}")
        print(f"  export  python scripts/export_sers_run.py --session {record.session_id}")


if __name__ == "__main__":
    raise SystemExit(main())
