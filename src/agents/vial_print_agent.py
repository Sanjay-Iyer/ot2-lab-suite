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

    # Deterministic offline pipeline — no LLM / API key (build -> validate -> CV)
    python -m src.agents.vial_print_agent --no-llm "5 dilutions, 20 uL droplets, 3 replicates"

Flags:
    --no-llm / --mock   Run the tool pipeline directly from a parsed request (no LLM).
    --rate-limit        Enable the 60 s rolling-window guard (Gemini free tier).
"""
from __future__ import annotations

import re
import sys
from datetime import datetime

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

def create_vial_print_agent(use_mock: bool = False):
    """Build the LangGraph ReAct agent. Robot tools are imported lazily so the
    offline path never needs them."""
    from langgraph.prebuilt import create_react_agent
    from src.core.config import Config
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
    llm = Config.get_llm(temperature=0)
    return create_react_agent(model=llm, tools=CONFIG_TOOLS + robot_tools, prompt=SYSTEM_PROMPT)


def _repl(initial_input: str | None, rate_limited: bool) -> None:
    from src.utils.limits_per_minute import RateLimitGuard

    rate_guard = RateLimitGuard(enabled=rate_limited)
    executor = create_vial_print_agent()
    chat_history: list = []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = AGENT_LOG_DIR / f"vial_print_session_{timestamp}.log"
    print("--- Vial-Print AI Agent (Gemini) ---")
    print(f"Logging to: {log_file}")
    print("Try: 'set up 5 dilutions, 20 uL droplets, 3 replicates' then 'build and validate'.")

    while True:
        try:
            user_input = initial_input if initial_input else input("\n[USER]: ")
            initial_input = None
        except (KeyboardInterrupt, EOFError):
            break
        if user_input.lower() in ("exit", "quit", "q"):
            break

        chat_history.append(("user", user_input))
        result = rate_guard.invoke_with_limit(executor, {"messages": chat_history})
        final_msg = result["messages"][-1]
        chat_history.append(final_msg)

        if isinstance(final_msg.content, list):
            clean = "".join(p.get("text", "") for p in final_msg.content if isinstance(p, dict))
        else:
            clean = final_msg.content
        print(f"\n[AGENT]: {clean}")

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().strftime('%H:%M:%S')}] USER: {user_input}\n")
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] AGENT: {clean}\n")
            f.write(f"--- FULL DEBUG TRACE ---\n{result}\n" + "=" * 50 + "\n")


def main() -> int:
    argv = sys.argv[1:]
    no_llm = ("--no-llm" in argv) or ("--mock" in argv)
    rate_limited = "--rate-limit" in argv
    args = [a for a in argv if a not in ("--no-llm", "--mock", "--rate-limit")]
    request = " ".join(args) if args else None

    if no_llm:
        return run_scripted(request or "")

    _repl(request, rate_limited)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
