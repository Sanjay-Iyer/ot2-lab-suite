"""
src/agents/vial_print_agent.py
==============================
Conversational AI driver for the flagship **vial-dilution-print** demo
(20 mL vials -> 96-well dilution series -> 8-channel paper print).

Talk to it in plain English to set the number of dilutions, droplet volume, and
replicates, then it builds, validates, and CV-checks a robot-ready protocol — and,
on the lab laptop only, runs it through the OT-2 HTTP API behind a RUN ROBOT
confirmation gate.

Run (from the repo root; use conda `llm` on the real robot laptop, `ai` on the
simulation laptop):

    # Conversational agent (simulation may use GOOGLE_API_KEY; live robot uses Vertex AI / gcloud ADC)
    python -m src.agents.vial_print_agent
    python -m src.agents.vial_print_agent "set up 5 dilutions, 20 uL droplets, 3 replicates"
    python -m src.agents.vial_print_agent --simulation-only "build and validate the default orange and blue workflow"

    # Deterministic offline pipeline — no LLM / API key (build -> validate -> CV)
    python -m src.agents.vial_print_agent --no-llm "5 dilutions, 20 uL droplets, 3 replicates"

Flags:
    --no-llm / --mock   Run the tool pipeline directly from a parsed request (no LLM).
    --simulation-only   LLM-driven build/validate/CV with robot tools unavailable.
    --rate-limit        Enable the 60 s rolling-window guard (Gemini free tier).
"""
from __future__ import annotations

import os
import platform
import re
import sys
import traceback
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

from src.utils.paths import AGENT_LOG_DIR, ensure_project_dirs

# Run as a module from the repo root.
if __name__ == "__main__" and not __package__:
    print("ERROR: run as a module from the project root:")
    print("       python -m src.agents.vial_print_agent")
    sys.exit(1)

ensure_project_dirs()

from src.agents.vial_print_tools import (
    load_vial_print_defaults,
    update_vial_print_params,
    preview_dilution_plan,
    show_vial_print_config,
    build_vial_print_protocol,
    validate_vial_print_matrix,
    verify_print_droplets_mock,
)

# Config-stage tools (safe on any machine).
CONFIG_TOOLS = [
    load_vial_print_defaults,
    update_vial_print_params,
    preview_dilution_plan,
    show_vial_print_config,
    build_vial_print_protocol,
    validate_vial_print_matrix,
    verify_print_droplets_mock,
]

SYSTEM_PROMPT = (
    "You are a Senior Laboratory Automation Engineer running the Opentrons OT-2 "
    "'vial dilution -> 8-channel paper print' demo. You drive the demo end to end "
    "from the user's natural language.\n\n"
    "WHAT THE DEMO DOES: draw water + food colouring from two 20 mL glass vials, "
    "build an 8-step (configurable) dilution series down one column of a 96-well "
    "plate, then pick up an 8-tip block and 'print' that column onto paper as "
    "simultaneous droplets. Tips are RETURNED, not trashed.\n\n"
    "THE FOUR KNOBS the user adjusts (map them with update_vial_print_params):\n"
    "  - number of dilutions (1-8): dilution wells = simultaneous droplets per print.\n"
    "  - droplet volume (uL): liquid dispensed per channel per replicate.\n"
    "  - number of replicates (>=1): how many times the column prints across paper.\n"
    "  - paper start column (1-12): first paper column used for printing.\n"
    "For anything else (fold strengths, total volume, mix reps, columns) use "
    "update_vial_print_params(advanced_updates=...).\n\n"
    "MANDATORY PIPELINE ORDER — never skip or reorder:\n"
    "  1. load_vial_print_defaults() first.\n"
    "  2. update_vial_print_params(...) for the user's requested changes.\n"
    "  3. preview_dilution_plan() and confirm the numbers look right.\n"
    "  4. build_vial_print_protocol() — MUST report 'SIMULATION OK'. If it fails, "
    "     read the error, fix the parameters, and rebuild. Do not proceed on failure.\n"
    "  5. validate_vial_print_matrix() — MUST report 'ALL CASES PASSED'.\n"
    "  6. verify_print_droplets_mock() — MUST report 'CV PASS'.\n\n"
    "PHYSICAL EXECUTION (lab laptop only — STRICT):\n"
    "  A. The live robot laptop must use Vertex AI / gcloud ADC auth "
    "     (LLM_PROVIDER=vertexai and GOOGLE_CLOUD_PROJECT in .env). "
    "     GOOGLE_API_KEY is only allowed for simulation-laptop testing.\n"
    "  B. Steps 4-6 must all have passed for the current protocol.\n"
    "  C. get_robot_hardware_status() to confirm the attached pipette matches "
    "     (expected p300_multi_gen2 on the right mount).\n"
    "  D. check_robot_http_api() to verify the robot server HTTP API is online.\n"
    "  E. Present a PRE-RUN SUMMARY: protocol path + SHA256, robot IP, deck layout "
    "     (vial rack slot 7, plate slot 4, paper slot 5, tips slot 9), pipette, "
    "     number of dilutions, droplets per print, replicates, droplet volume, "
    "     air gap, tip height, blow out, paper start column, and image pullback "
    "     (/data/vision/vial_dilution_print -> vision_runs/vial_dilution_print).\n"
    "  F. MANDATORY: ask the user to reply with exactly 'RUN ROBOT' to proceed.\n"
    "  G. Only after 'RUN ROBOT': call run_vial_print_robot_http("
    "confirmation='RUN ROBOT', live=True). This uses scripts/run_vial_print_robot.py "
    "and the OT-2 HTTP API; do not use deploy_protocol_to_robot() or "
    "execute_protocol_on_robot() for this workflow.\n\n"
    "SAFETY — non-negotiable:\n"
    "  - The robot handles GLASS vials. Never weaken the geometry pre-flight check, "
    "    widen tolerances, or switch to fallback labware to make something 'work'.\n"
    "  - Never claim a step passed unless the tool output literally said so "
    "    (SIMULATION OK / ALL CASES PASSED / CV PASS). A green exit code is not proof.\n"
    "  - Trust tool outputs over your own assumptions about parameter values.\n"
)

SIMULATION_ONLY_PROMPT = (
    "\n\nSIMULATION-ONLY MODE:\n"
    "  - The current session is for build, simulation, validation, and mock CV only.\n"
    "  - Do not attempt robot hardware checks, HTTP API checks, uploads, or live runs.\n"
    "  - Finish after build_vial_print_protocol(), validate_vial_print_matrix(), "
    "and verify_print_droplets_mock() pass.\n"
)

_SCHEMA_WARNING_FRAGMENT = "Key 'additionalProperties' is not supported in schema, ignoring"
_MAX_REPL_HISTORY_MESSAGES = 16
_ERROR_LOG_FILE = AGENT_LOG_DIR / "vial_print_errors.log"


class _SchemaWarningFilter:
    """Forward stream writes except for Vertex's noisy tool-schema warning."""

    def __init__(self, wrapped):
        self._wrapped = wrapped
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if _SCHEMA_WARNING_FRAGMENT not in line:
                self._wrapped.write(line + "\n")
        return len(text)

    def flush(self) -> None:
        if self._buffer and _SCHEMA_WARNING_FRAGMENT not in self._buffer:
            self._wrapped.write(self._buffer)
        self._buffer = ""
        self._wrapped.flush()


@contextmanager
def _suppress_schema_warning():
    """Silence Vertex schema noise unless explicitly disabled for debugging."""
    if os.getenv("OT2_SHOW_SCHEMA_WARNINGS", "").strip().lower() in {"1", "true", "yes"}:
        yield
        return
    with redirect_stdout(_SchemaWarningFilter(sys.stdout)), redirect_stderr(_SchemaWarningFilter(sys.stderr)):
        yield


def _message_text(message: Any) -> str:
    """Extract plain text from a LangChain message or provider content block."""
    content = getattr(message, "content", message)
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return str(content).strip()


def _message_role(message: Any) -> str | None:
    """Normalize a message role to the tuple format expected by LangChain."""
    role = getattr(message, "type", None) or getattr(message, "role", None)
    if role == "human":
        return "user"
    if role == "ai":
        return "assistant"
    if role in {"user", "assistant"}:
        return role
    return None


def _sanitize_chat_history(history: list[Any]) -> list[tuple[str, str]]:
    """Keep only non-empty user/assistant text turns for the next REPL call.

    Vertex rejects content entries that serialize with no parts. LangGraph tool
    turns can include empty AI messages, tool messages, or provider-specific
    blocks, so the interactive loop stores a clean transcript between turns.
    """
    clean: list[tuple[str, str]] = []
    for message in history:
        if isinstance(message, tuple) and len(message) >= 2:
            role = str(message[0])
            text = _message_text(message[1])
        else:
            role = _message_role(message)
            text = _message_text(message)
        if role not in {"user", "assistant"} or not text:
            continue
        clean.append((role, text))
    return clean[-_MAX_REPL_HISTORY_MESSAGES:]


def _agent_error_message(exc: Exception) -> str:
    """Human-readable error for provider hiccups without a Python traceback."""
    text = str(exc)
    if "must include at least one parts field" in text:
        return (
            "The Vertex AI request was rejected because the conversation history "
            "contained an empty message. I kept the robot stopped; restart the "
            "agent or retry the last prompt after pulling the history-sanitizing fix."
        )
    if "Remote end closed connection" in text or "Connection aborted" in text:
        return (
            "The Vertex AI connection was closed before a response came back. "
            "The robot was not started. Retry the prompt, preferably with "
            "--rate-limit if you are doing a long demo."
        )
    return (
        "The LLM call failed before the agent could continue. The robot was not "
        "started. Retry the prompt or restart the agent if the connection looks stale."
    )


def _log_block(path: Path, title: str, lines: list[str]) -> None:
    """Append a readable block to an agent log file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n[{stamp}] {title}\n")
        for line in lines:
            f.write(f"{line}\n")
        f.write("=" * 80 + "\n")


def _history_debug(history: list[tuple[str, str]], max_chars: int = 600) -> list[str]:
    """Compact transcript preview for error triage."""
    lines = []
    for idx, (role, text) in enumerate(history[-6:], start=max(1, len(history) - 5)):
        one_line = " ".join(text.split())
        if len(one_line) > max_chars:
            one_line = one_line[:max_chars] + "...[truncated]"
        lines.append(f"history[{idx}] {role}: {one_line}")
    return lines


def _log_session_start(log_file: Path, *, auth_summary: str, rate_limited: bool,
                       simulation_only: bool) -> None:
    _log_block(log_file, "SESSION START", [
        f"host={platform.node()}",
        f"python={sys.executable}",
        f"cwd={os.getcwd()}",
        f"mode={'simulation-only' if simulation_only else 'robot-capable'}",
        f"rate_limited={rate_limited}",
        "auth_summary:",
        auth_summary,
    ])


def _log_turn_start(log_file: Path, *, user_input: str,
                    candidate_history: list[tuple[str, str]]) -> None:
    _log_block(log_file, "TURN START", [
        f"user_input={user_input}",
        f"history_messages={len(candidate_history)}",
        *_history_debug(candidate_history),
    ])


def _log_turn_success(log_file: Path, *, clean: str,
                      chat_history: list[tuple[str, str]], result: Any) -> None:
    _log_block(log_file, "TURN SUCCESS", [
        f"history_messages={len(chat_history)}",
        f"agent_text={clean}",
        f"full_debug_trace={result}",
    ])


def _log_agent_error(log_file: Path, *, user_input: str, clean_error: str,
                     exc: Exception, candidate_history: list[tuple[str, str]],
                     auth_summary: str, simulation_only: bool) -> None:
    lines = [
        f"user_input={user_input}",
        f"mode={'simulation-only' if simulation_only else 'robot-capable'}",
        f"history_messages={len(candidate_history)}",
        f"exception_type={type(exc).__name__}",
        f"exception={exc!r}",
        f"user_facing_error={clean_error}",
        "auth_summary:",
        auth_summary,
        "recent_history:",
        *_history_debug(candidate_history),
        "traceback:",
        traceback.format_exc(),
    ]
    _log_block(log_file, "TURN ERROR", lines)
    _log_block(_ERROR_LOG_FILE, "VIAL PRINT AGENT ERROR", [
        f"session_log={log_file}",
        *lines,
    ])


# ── Deterministic offline path (no LLM) ───────────────────────────────────────────

def parse_request(text: str) -> dict:
    """Extract the three knobs from a free-text request. Convenience only; the LLM
    path handles richer phrasing."""
    t = (text or "").lower()
    out: dict = {}
    m = re.search(r"(\d+)\s*dilution", t)
    if m:
        out["num_dilutions"] = int(m.group(1))
    m = re.search(r"(\d+)\s*replicate", t)
    if m:
        out["num_replicates"] = int(m.group(1))
    m = re.search(r"(?:paper\s*)?(?:start\s*)?column\s*(\d+)", t)
    if m:
        out["paper_start_column"] = int(m.group(1))
    # droplet volume: a number directly followed by a microlitre unit.
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:u\s*l|µl|ul|microlit\w*)\b", t)
    if m:
        out["droplet_volume_ul"] = float(m.group(1))
    return out


def run_scripted(request: str) -> int:
    """Run load -> update -> preview -> build -> validate -> CV directly, no LLM.

    Returns 0 only if build/validate/CV all pass. Used for offline verification and
    CI (no GOOGLE_API_KEY required).
    """
    knobs = parse_request(request)
    print("=== Vial-print agent (offline / no-LLM) ===")
    print(f"Parsed knobs: {knobs or '(none — using defaults)'}\n")

    print(load_vial_print_defaults.invoke({}))
    if knobs:
        print("\n" + update_vial_print_params.invoke(knobs))
    print("\n" + preview_dilution_plan.invoke({}))

    print("\n--- build ---")
    build_out = build_vial_print_protocol.invoke({})
    print(build_out)
    if "SIMULATION OK" not in build_out:
        print("\nFAILED at build. Stopping.")
        return 1

    print("\n--- validate ---")
    val_out = validate_vial_print_matrix.invoke({})
    print(val_out)
    if "ALL CASES PASSED" not in val_out:
        print("\nFAILED at validate. Stopping.")
        return 1

    print("\n--- CV ---")
    cv_out = verify_print_droplets_mock.invoke({})
    print(cv_out)
    if "CV PASS" not in cv_out:
        print("\nFAILED at CV. Stopping.")
        return 1

    print("\n=== ALL OFFLINE GATES PASSED ===")
    print("HTTP robot launch is lab-laptop only (run the live agent there, say RUN ROBOT).")
    return 0


# ── Live conversational agent (Gemini) ────────────────────────────────────────────

def get_vial_print_tools(simulation_only: bool = False):
    """Return the tool set for the current vial-print agent mode."""
    if simulation_only:
        return list(CONFIG_TOOLS)

    from src.agents.tools import get_robot_hardware_status
    from src.agents.robot_http_tools import (
        check_robot_http_api,
        list_robot_http_protocols,
        run_vial_print_robot_http,
    )

    robot_tools = [
        list_robot_http_protocols,
        get_robot_hardware_status,
        check_robot_http_api,
        run_vial_print_robot_http,
    ]
    return list(CONFIG_TOOLS) + robot_tools


def create_vial_print_agent(use_mock: bool = False, simulation_only: bool = False):
    """Build the LangGraph ReAct agent. Robot tools are imported lazily so the
    offline path never needs them."""
    from langgraph.prebuilt import create_react_agent
    from src.core.config import Config

    llm = Config.get_llm(temperature=0)
    prompt = SYSTEM_PROMPT + (SIMULATION_ONLY_PROMPT if simulation_only else "")
    return create_react_agent(
        model=llm,
        tools=get_vial_print_tools(simulation_only=simulation_only),
        prompt=prompt,
    )


def _repl(initial_input: str | None, rate_limited: bool, simulation_only: bool) -> None:
    from src.core.config import Config
    from src.utils.limits_per_minute import RateLimitGuard

    rate_guard = RateLimitGuard(enabled=rate_limited)
    executor = create_vial_print_agent(simulation_only=simulation_only)
    chat_history: list = []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = AGENT_LOG_DIR / f"vial_print_session_{timestamp}.log"
    auth_summary = Config.describe_llm_auth()
    print("--- Vial-Print AI Agent (Gemini) ---")
    print(auth_summary)
    if simulation_only:
        print("Mode: simulation-only (robot tools unavailable)")
    print(f"Logging to: {log_file}")
    print(f"Error log: {_ERROR_LOG_FILE}")
    print("Try: 'set up 5 dilutions, 20 uL droplets, 3 replicates' then 'build and validate'.")
    _log_session_start(
        log_file,
        auth_summary=auth_summary,
        rate_limited=rate_limited,
        simulation_only=simulation_only,
    )

    while True:
        try:
            user_input = initial_input if initial_input else input("\n[USER]: ")
            initial_input = None
        except (KeyboardInterrupt, EOFError):
            break
        if user_input.lower() in ("exit", "quit", "q"):
            break
        if not user_input.strip():
            continue

        candidate_history = _sanitize_chat_history(chat_history + [("user", user_input)])
        _log_turn_start(
            log_file,
            user_input=user_input,
            candidate_history=candidate_history,
        )
        try:
            with _suppress_schema_warning():
                result = rate_guard.invoke_with_limit(executor, {"messages": candidate_history})
        except Exception as exc:
            clean_error = _agent_error_message(exc)
            print(f"\n[AGENT ERROR]: {clean_error}")
            _log_agent_error(
                log_file,
                user_input=user_input,
                clean_error=clean_error,
                exc=exc,
                candidate_history=candidate_history,
                auth_summary=auth_summary,
                simulation_only=simulation_only,
            )
            chat_history = _sanitize_chat_history(chat_history)
            continue

        final_msg = result["messages"][-1]
        clean = _message_text(final_msg)
        if not clean:
            clean = (
                "The tool call completed, but the model returned no final text. "
                "Please ask me to summarize the last result."
            )
        chat_history = _sanitize_chat_history(candidate_history + [("assistant", clean)])
        print(f"\n[AGENT]: {clean}")
        _log_turn_success(
            log_file,
            clean=clean,
            chat_history=chat_history,
            result=result,
        )


def main() -> int:
    argv = sys.argv[1:]
    no_llm = ("--no-llm" in argv) or ("--mock" in argv)
    rate_limited = "--rate-limit" in argv
    simulation_only = "--simulation-only" in argv
    args = [
        a for a in argv
        if a not in ("--no-llm", "--mock", "--rate-limit", "--simulation-only")
    ]
    request = " ".join(args) if args else None

    if no_llm:
        return run_scripted(request or "")

    _repl(request, rate_limited, simulation_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
