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

# Ensure root is in path for imports
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

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
    confirm_and_run_workflow, 
    show_full_config
)

rate_guard = RateLimitGuard(enabled=True)

# --- Dynamic Mock LLM ---
class MockToolCallingLLM(BaseChatModel):
    _bound_tools: list = []

    @property
    def _llm_type(self) -> str: return "mock-tool-calling"

    def bind_tools(self, tools: list, **kwargs: Any) -> "MockToolCallingLLM":
        clone = MockToolCallingLLM(); clone._bound_tools = tools; return clone

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        last_msg = messages[-1]
        
        # If the last message was a tool result, determine next step or finish
        if isinstance(last_msg, ToolMessage):
            # If we just loaded defaults, update it
            if last_msg.name == "load_workflow_defaults":
                msg = AIMessage(content="", tool_calls=[{
                    "id": f"mock_{uuid.uuid4().hex[:8]}",
                    "name": "update_workflow_config",
                    "args": {"updates": {"dilution": {"final_volume_ul": 250}}}
                }])
            # If we just updated, validate it
            elif last_msg.name == "update_workflow_config":
                msg = AIMessage(content="", tool_calls=[{
                    "id": f"mock_{uuid.uuid4().hex[:8]}",
                    "name": "validate_current_workflow",
                    "args": {}
                }])
            # If we just validated, and it passed, run it
            elif last_msg.name == "validate_current_workflow" and "VALIDATION PASSED" in last_msg.content:
                msg = AIMessage(content="", tool_calls=[{
                    "id": f"mock_{uuid.uuid4().hex[:8]}",
                    "name": "confirm_and_run_workflow",
                    "args": {}
                }])
            else:
                msg = AIMessage(content="[MockLLM] Workflow completed successfully.")
        else:
            # Initial User Request logic
            lower_content = last_msg.content.lower()
            if "dilution" in lower_content:
                msg = AIMessage(content="", tool_calls=[{
                    "id": f"mock_{uuid.uuid4().hex[:8]}",
                    "name": "load_workflow_defaults",
                    "args": {"workflow_type": "dilution"}
                }])
            elif "print" in lower_content:
                msg = AIMessage(content="", tool_calls=[{
                    "id": f"mock_{uuid.uuid4().hex[:8]}",
                    "name": "load_workflow_defaults",
                    "args": {"workflow_type": "printing"}
                }])
            else:
                msg = AIMessage(content="", tool_calls=[{
                    "id": f"mock_{uuid.uuid4().hex[:8]}",
                    "name": "list_available_workflows",
                    "args": {}
                }])
        return ChatResult(generations=[ChatGeneration(message=msg)])

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
        confirm_and_run_workflow, 
        show_full_config
    ]
    
    system_prompt = (
        "You are a Senior Laboratory Automation Engineer for the Opentrons OT-2.\n\n"
        "INTERACTION PROTOCOL:\n"
        "1. Identify the workflow type first (e.g., 'dilution', 'printing').\n"
        "2. ALWAYS load the default workflow config using 'load_workflow_defaults' before asking for parameters.\n"
        "3. Present the default config summary clearly to the user.\n"
        "4. Ask whether to 'use defaults' or 'update specific parameters'.\n"
        "5. If parameters are provided, merge them using 'update_workflow_config'.\n"
        "6. ALWAYS run 'validate_current_workflow' before attempting to run a protocol.\n"
        "7. VALIDATION FEEDBACK:\n"
        "   - ERRORS: Explain errors verbatim and suggest fixes. DO NOT proceed to run if errors exist.\n"
        "   - WARNINGS: Show warnings to the user. You may continue only if the user acknowledges them.\n"
        "8. EXECUTION: Only run 'confirm_and_run_workflow' after successful validation and user confirmation.\n\n"
        "IMPORTANT SAFETY MESSAGE:\n"
        "Local simulation only confirms that the Python protocol is executable. "
        "Physical feasibility must come from the constraint validation layer. "
        "Physical execution still requires labware, volume, and robot setup verification.\n\n"
        "PORTABILITY:\n"
        "- All paths are relative to the project root.\n"
        "- Simulations run locally using 'opentrons.simulate'."
    )
    
    return create_react_agent(model=llm, tools=tools, prompt=system_prompt)

if __name__ == "__main__":
    use_mock = "--mock" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--mock"]
    initial_input = " ".join(args) if args else None

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