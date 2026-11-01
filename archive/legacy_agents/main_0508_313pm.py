import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Optional
from dotenv import load_dotenv
from datetime import datetime

# 1. Setup Environment
env_path = Path(__file__).resolve().parent.parent / "printing_suite" / "configs" / ".env"
print(f"DEBUG: Looking for .env file at -> {env_path}")
load_dotenv(env_path)

from langgraph.prebuilt import create_react_agent
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.language_models.chat_models import BaseChatModel

# Local Tool Imports
from tools import configure_printing_parameters, run_mock_simulation

# --- Mock LLM for testing without an API key ---
class MockToolCallingLLM(BaseChatModel):
    _bound_tools: list = []

    @property
    def _llm_type(self) -> str:
        return "mock-tool-calling"

    def bind_tools(self, tools: list, **kwargs: Any) -> "MockToolCallingLLM":
        clone = MockToolCallingLLM()
        clone._bound_tools = tools
        return clone

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        has_tool_result = any(getattr(m, "type", None) == "tool" for m in messages)
        if has_tool_result:
            tool_output = next((m.content for m in messages if getattr(m, "type", None) == "tool"), "")
            if any(x in tool_output for x in ["Failed", "below the minimum", "below 5"]):
                msg = AIMessage(
                    content="I detected an error in the previous configuration. I will now self-correct.",
                    tool_calls=[{
                        "id": f"mock_fix_{uuid.uuid4().hex[:8]}",
                        "name": "configure_printing_parameters",
                        "args": {"stock_conc_ugml": 100.0, "dilutions": [1, 10, 20]}
                    }]
                )
            else:
                msg = AIMessage(content=f"[MockLLM] Success:\n\n{tool_output}")
        else:
            user_text = next((m.content for m in messages if getattr(m, "type", None) == "human"), "")
            tool_name, tool_args = self._decide_tool_call(user_text)
            msg = AIMessage(content="", tool_calls=[{"id": f"mock_{uuid.uuid4().hex[:8]}", "name": tool_name, "args": tool_args}])
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _decide_tool_call(self, user_text: str) -> tuple[str, dict]:
        text = user_text.lower()
        if any(x in text for x in ["simulation", "simulate", "run"]):
            protocol = "size_mixing_protocol" if "mixing" in text else "dilution_protocol"
            return "run_mock_simulation", {"protocol_name": protocol}
        return "configure_printing_parameters", {"stock_conc_ugml": 100.0, "dilutions": [1, 10, 100]}

# --- Agent Factory ---
def create_opentrons_agent(use_mock: bool = False):
    if use_mock:
        llm = MockToolCallingLLM()
    else:
        from langchain_google_genai import ChatGoogleGenerativeAI
        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        llm = ChatGoogleGenerativeAI(model=gemini_model, google_api_key=os.getenv("GOOGLE_API_KEY"), temperature=0)

    tools = [configure_printing_parameters, run_mock_simulation]
    system_prompt = (
        "You are a proactive laboratory automation assistant for the Opentrons OT-2. "
        "1. When configuring dilutions for the 'dilution_protocol', ALWAYS save the file as 'dilution_plan.json'. This is the specific filename the Python protocol expects to load."
        "2. CRITICAL: If a calculation (like volume) violates hardware limits (e.g., < 2.0 uL for P300), "
        "   DO NOT simply fail. Instead, calculate a viable alternative (e.g., increasing final volume) "
        "   and propose this solution to the user."
        "3. WAIT FOR VERIFICATION: If you propose a solution, ask the user for confirmation. "
        "   Do not call the 'run_mock_simulation' tool until the user explicitly says 'Yes', 'Proceed', or 'Confirm'."
        "4. Once confirmed, proceed with the simulation."
    )
    return create_react_agent(model=llm, tools=tools, prompt=system_prompt)

# --- Execution ---
if __name__ == "__main__":
    use_mock = "--mock" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--mock"]
    initial_input = " ".join(args) if args else None

    executor = create_opentrons_agent(use_mock=use_mock)
    
    # We keep track of the conversation history here
    chat_history = []
    
    print(f"--- AI Agent Initialized ({'Mock' if use_mock else 'Gemini'}) ---")
    
    # Start the loop
    while True:
        if initial_input:
            user_input = initial_input
            initial_input = None # Only use the command line arg once
        else:
            user_input = input("\n[USER]: ")
        
        if user_input.lower() in ["exit", "quit", "q"]:
            print("Exiting...")
            break

        # Run the agent
        chat_history.append(("user", user_input))
        result = executor.invoke({"messages": chat_history})
        
        # Extract response
        final_msg = result["messages"][-1]
        chat_history.append(final_msg)
        
        # --- NEW CLEAN TERMINAL PRINTING ---
        # This checks if the response is a list (with metadata) or just a string
        if isinstance(final_msg.content, list):
            # Extract only the 'text' parts and ignore the 'extras/signature'
            clean_text = "".join([part.get("text", "") for part in final_msg.content if isinstance(part, dict)])
        else:
            clean_text = final_msg.content
            
        print(f"\n[AGENT]: {clean_text}")
        # -----------------------------------

        # --- LOGGING --- 
        # (This still uses 'str(result)', so your log file still gets the signatures)
    # --- LOGGING LOGIC (Now safely inside __main__) ---
    log_dir = r"C:\Code\opentrons_home\versions\0508_opentrons\opentrons\opentrons\printing_suite\logs"
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"agent_run_{timestamp}.log")

    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"TIMESTAMP: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"PROMPT: {user_input}\n")
        f.write("="*50 + "\n")
        # We save the full 'result' object so you can see tool calls too!
        f.write(f"FULL AGENT TRACE:\n{str(result)}\n") 
    
    print(f"\n[LOG SAVED]: {log_file}")