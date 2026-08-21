import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Optional
from dotenv import load_dotenv
from datetime import datetime

# Import centralized paths
from src.utils.paths import PROJECT_ROOT, AGENT_LOG_DIR, ensure_project_dirs

# Run this script using `python -m src.agents.main` from the project root.
if __name__ == "__main__" and not __package__:
    print("ERROR: This script must be run as a module from the project root.")
    print("Command: python -m src.agents.main")
    sys.exit(1)

from src.core.config import Config
from src.utils.limits_per_minute import RateLimitGuard

# Ensure directories exist
ensure_project_dirs()

from langgraph.prebuilt import create_react_agent
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.language_models.chat_models import BaseChatModel

# Tool Imports
from .tools import (
    list_available_workflows,
    load_workflow_defaults,
    update_workflow_config,
    validate_current_workflow,
    generate_protocol,
    simulate_protocol,
    check_robot_connection,
    validate_config,
    show_full_config,
    get_robot_hardware_status
)
from .labware_tools import (
    list_labware_configs,
    list_generated_labware,
    list_labware_presets,
    list_labware_families,
    describe_labware_config,
    create_labware_config,
    generate_labware_from_config,
    generate_custom_labware,
    derive_custom_labware,
    validate_labware_definition,
)

# Rate limiter is OFF by default. Pass --rate-limit to enable.
_rate_limit_enabled = "--rate-limit" in sys.argv
rate_guard = RateLimitGuard(enabled=_rate_limit_enabled)

class MockToolCallingLLM(BaseChatModel):
    """Deprecated compatibility model for the historical ``--mock`` flag.

    The former implementation was removed but callers still imported the symbol,
    leaving the generic agent's mock path as a NameError. It now fails closed with
    a deterministic migration message and never calls a tool, network, or robot.
    Production Printing Agent routing is tested with injected fake models instead.
    """

    @property
    def _llm_type(self) -> str:
        return "deprecated-ot2-mock"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = AIMessage(
            content=(
                "The generic --mock agent is deprecated. Use the deterministic "
                "printing CLI or the production Printing Agent tests with an "
                "injected fake tool-calling model. No tools were called."
            )
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

# --- Agent Factory ---
def create_opentrons_agent(use_mock: bool = False):
    if use_mock:
        llm = MockToolCallingLLM()
    else:
        llm = Config.get_llm(temperature=0)

    tools = [
        # Local planning/simulation tools. Live deploy/execute functions remain
        # available to manual code paths but are intentionally not AI-facing.
        list_available_workflows,
        load_workflow_defaults,
        update_workflow_config,
        validate_current_workflow,
        generate_protocol,
        simulate_protocol,
        check_robot_connection,
        validate_config,
        show_full_config,
        get_robot_hardware_status,
        # Labware creation tools
        list_labware_configs,
        list_generated_labware,
        list_labware_presets,
        list_labware_families,
        describe_labware_config,
        create_labware_config,
        generate_labware_from_config,
        generate_custom_labware,
        derive_custom_labware,
        validate_labware_definition,
    ]
    
    system_prompt = (
        "You are a Senior Laboratory Automation Engineer for the Opentrons OT-2.\n\n"
        "INTERACTION PROTOCOL:\n"
        "1. Identify the workflow type first (e.g., 'dilution').\n"
        "2. Load defaults using 'load_workflow_defaults'.\n"
        "3. Update parameters using 'update_workflow_config' if requested.\n"
        "4. ALWAYS run 'validate_current_workflow' before generating a protocol.\n"
        "5. Generate the protocol using 'generate_protocol'.\n"
        "6. ALWAYS run 'simulate_protocol' on the generated file and verify it PASSES.\n"
        "7. Stop after local simulation and report the exact artifact SHA256.\n\n"
        "PHYSICAL EXECUTION HANDOFF (STRICT):\n"
        "Agent tools cannot deploy or execute protocols on the physical robot. "
        "For later work-laptop execution, present a HANDOFF SUMMARY containing:\n"
        "   - Protocol Name & SHA256 Hash (from tool output).\n"
        "   - Deck Layout Summary (labware in which slots).\n"
        "   - Configured pipette(s) and mount(s).\n"
        "   - Estimated number of liquid transfers.\n"
        "Then stop. A human operator must follow the documented manual work-laptop "
        "procedure and confirm physical readiness outside this agent.\n\n"
        "IMPORTANT:\n"
        "- All paths are relative to the project root.\n"
        "- TRUST TOOLS OVER MEMORY: If a tool output (like show_full_config or generate_protocol) says a pipette is 'p300_multi_gen2', DO NOT report it as 'p300_single_gen2' in your chat, even if you thought it was single-channel earlier.\n\n"
        "LABWARE CREATION PROTOCOL:\n"
        "When the user asks you to create, define, or generate custom labware:\n"
        "0. Call list_labware_families() to see what can actually be built. Never offer a labware "
        "family that is not in that list.\n"
        "1. Decide which path fits the request:\n"
        "   a. BASED ON AN EXISTING LABWARE ('another one like X', 'same as my paper template but ...') "
        "→ derive_custom_labware(). Use mode='copy' when only the name/metadata changes, and "
        "mode='regenerate' when the user asked for different physical geometry.\n"
        "   b. FULLY SPECIFIED NEW LABWARE → generate_custom_labware() with the complete parameters.\n"
        "   c. A STANDARD PLATE TYPE the user wants as a starting point → list_labware_presets(), then "
        "create_labware_config(), then generate_labware_from_config().\n"
        "2. NEVER invent a physical dimension. Well diameter, depth, spacing, offsets and the outer "
        "footprint come from the user, from a named template, or from a preset. If a dimension is "
        "missing and no template or preset supplies it, say exactly which values you need and ask — "
        "do not choose a plausible number to make generation succeed. A wrong dimension here is a tip "
        "driven into glass.\n"
        "3. load_name MUST be lowercase letters, digits, underscores, or dots only — no spaces, hyphens, "
        "or capitals. display_name can be any human-readable string. The output filename always follows "
        "load_name.\n"
        "4. You never write well coordinates. The tools compute all of them from rows/cols/offsets/"
        "spacing. Do not attempt to list x/y values yourself.\n"
        "5. Show the user the parameters (describe_labware_config(), or the summary the tool returns) "
        "and confirm before generating when anything was inferred.\n"
        "6. Report the validation line the tool returns verbatim — schema/geometry/json/opentrons. If a "
        "layer FAILED, no file was written; fix the parameters rather than retrying unchanged.\n"
        "7. NEVER pass overwrite=True unless the user explicitly asked to replace an existing definition. "
        "The default refusal protects labware the robot is already calibrated against.\n"
        "8. Local validation is not physical verification. Never tell the user a definition has been "
        "checked on the OT-2 unless it actually has.\n"
        "If the user only wants to view or list existing labware, use list_labware_configs(), "
        "list_generated_labware(), describe_labware_config(), or validate_labware_definition().\n"
    )
    
    return create_react_agent(model=llm, tools=tools, prompt=system_prompt)

if __name__ == "__main__":
    use_mock = "--mock" in sys.argv
    rate_limited = "--rate-limit" in sys.argv
    args = [a for a in sys.argv[1:] if a not in ("--mock", "--rate-limit")]
    initial_input = " ".join(args) if args else None

    # Update the module-level guard based on CLI flag
    rate_guard.enabled = rate_limited

    executor = create_opentrons_agent(use_mock=use_mock)
    chat_history = []
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = AGENT_LOG_DIR / f"agent_session_{timestamp}.log"

    print(f"--- AI Agent Initialized ({'Mock' if use_mock else 'Gemini'}) ---")
    print(f"Logging to: {log_file}")

    while True:
        try:
            user_input = initial_input if initial_input else input("\n[USER]: ")
            initial_input = None 
        except (KeyboardInterrupt, EOFError): break

        if user_input.lower() in ["exit", "quit", "q"]: break

        chat_history.append(("user", user_input))
        result = rate_guard.invoke_with_limit(executor, {"messages": chat_history})
        
        final_msg = result["messages"][-1]
        chat_history.append(final_msg)

        if isinstance(final_msg.content, list):
            clean_text = "".join([p.get("text", "") for p in final_msg.content if isinstance(p, dict)])
        else:
            clean_text = final_msg.content
        
        print(f"\n[AGENT]: {clean_text}")

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().strftime('%H:%M:%S')}] USER: {user_input}\n")
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] AGENT: {clean_text}\n")
            f.write(f"--- FULL DEBUG TRACE ---\n{str(result)}\n" + "="*50 + "\n")
