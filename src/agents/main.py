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
    deploy_protocol_to_robot,
    execute_protocol_on_robot,
    validate_config,
    show_full_config
)

# Rate limiter is OFF by default. Pass --rate-limit to enable.
_rate_limit_enabled = "--rate-limit" in sys.argv
rate_guard = RateLimitGuard(enabled=_rate_limit_enabled)

# ... (MockToolCallingLLM logic unchanged) ...

# --- Agent Factory ---
def create_opentrons_agent(use_mock: bool = False):
    if use_mock:
        llm = MockToolCallingLLM()
    else:
        llm = Config.get_llm(temperature=0)

    tools = [
        list_available_workflows, 
        load_workflow_defaults, 
        update_workflow_config, 
        validate_current_workflow,
        generate_protocol,
        simulate_protocol,
        check_robot_connection,
        deploy_protocol_to_robot,
        execute_protocol_on_robot,
        validate_config,
        show_full_config
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
        "7. Before any physical execution, run 'check_robot_connection'.\n\n"
        "PHYSICAL EXECUTION SAFETY (STRICT):\n"
        "To run on the physical robot, you must follow this sequence:\n"
        "A. Ensure 'simulate_protocol' passed for the current protocol hash.\n"
        "B. Run 'check_robot_connection' to verify the instrument is online.\n"
        "C. Present a PRE-RUN SUMMARY to the user containing:\n"
        "   - Protocol Name & Hash (first 8 chars).\n"
        "   - Robot IP.\n"
        "   - Deck Layout Summary (labware in which slots).\n"
        "   - Pipette(s) and Mount(s).\n"
        "   - Estimated number of liquid transfers.\n"
        "D. MANDATORY CONFIRMATION: Ask the user to reply with exactly 'RUN ROBOT' to proceed.\n"
        "E. Only after the user says 'RUN ROBOT', call 'deploy_protocol_to_robot' followed by 'execute_protocol_on_robot'.\n\n"
        "IMPORTANT:\n"
        "- Use non-interactive SSH (BatchMode). If it fails, inform the user to check their SSH keys.\n"
        "- All paths are relative to the project root.\n"
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