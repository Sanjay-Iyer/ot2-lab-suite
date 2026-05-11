import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Optional
from dotenv import load_dotenv
from datetime import datetime

# Include 'printing_suite' in the path!
env_path = Path(__file__).resolve().parent.parent / "printing_suite" / "configs" / ".env"

print(f"DEBUG: Looking for .env file at -> {env_path}")
load_dotenv(env_path)

# --- NEW IMPORTS ---
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.language_models.chat_models import BaseChatModel

# Assuming these are available in your local tools.py
from tools import configure_printing_parameters, run_mock_simulation

# ─── Mock LLM for testing without an API key ────────────────────────
class MockToolCallingLLM(BaseChatModel):
    """
    A mock LLM that mimics tool-calling behavior so the full LangGraph
    agent pipeline can be tested locally with zero API keys.

    On the first call it inspects the user prompt and emits an AIMessage
    with a tool_call.  On the second call (after the tool result comes
    back) it returns a plain-text summary.
    """

    _bound_tools: list = []

    @property
    def _llm_type(self) -> str:
        return "mock-tool-calling"

    def bind_tools(self, tools: list, **kwargs: Any) -> "MockToolCallingLLM":
        """Return a copy of self with the given tools bound."""
        clone = MockToolCallingLLM()
        clone._bound_tools = tools
        return clone

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        has_tool_result = any(
            getattr(m, "type", None) == "tool" for m in messages
        )

        if has_tool_result:
            # --- Second pass: check for errors and self-correct ---
            tool_output = ""
            for m in messages:
                if getattr(m, "type", None) == "tool":
                    tool_output = m.content
            
            if "Failed" in tool_output or "below the minimum" in tool_output or "below 5" in tool_output:
                # SELF-CORRECTION LOGIC
                msg = AIMessage(
                    content="I detected an error in the previous configuration (volume too small). I will now self-correct by reducing the dilution factor.",
                    tool_calls=[{
                        "id": f"mock_fix_{uuid.uuid4().hex[:8]}",
                        "name": "configure_printing_parameters",
                        "args": {"stock_conc_ugml": 100.0, "dilutions": [1, 10, 20]} # Reduced from 100 to 20
                    }]
                )
            else:
                msg = AIMessage(
                    content=(
                        f"[MockLLM] The tool finished successfully. Here is the result:\n\n"
                        f"{tool_output}"
                    )
                )
        else:
            # --- First pass: decide which tool to call ---
            user_text = ""
            for m in messages:
                if getattr(m, "type", None) == "human":
                    user_text = m.content
                    break

            tool_name, tool_args = self._decide_tool_call(user_text)
            msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": f"mock_{uuid.uuid4().hex[:8]}",
                        "name": tool_name,
                        "args": tool_args,
                    }
                ],
            )

        return ChatResult(generations=[ChatGeneration(message=msg)])

    # ── simple heuristic to pick a tool from the user prompt ──
    def _decide_tool_call(self, user_text: str) -> tuple[str, dict]:
        text = user_text.lower()
        if "simulation" in text or "simulate" in text or "run" in text:
            protocol = "dilution_protocol"
            if "mixing" in text:
                protocol = "size_mixing_protocol"
            return "run_mock_simulation", {"protocol_name": protocol}

        # Check for layout inquiry
        if "layout" in text or "place" in text or "slot" in text:
            return "configure_printing_parameters", {}

        # Default: Try to configure
        conc_match = re.search(r"conc\S*\s*(\d+\.?\d*)", text)
        conc = float(conc_match.group(1)) if conc_match else 100.0
        
        dil_match = re.search(r"dilutions?\s*([\d,\s]+)", text)
        if dil_match:
            dilutions = [int(n.strip()) for n in re.split(r"[,\s]+", dil_match.group(1)) if n.strip()]
        else:
            dilutions = [1, 10, 100]

        return "configure_printing_parameters", {"stock_conc_ugml": conc, "dilutions": dilutions}


# ─── Agent factory ───────────────────────────────────────────────────
def create_opentrons_agent(use_mock: bool = False):
    """
    Creates an AI Agent that can simulate and deploy Opentrons protocols.

    Args:
        use_mock: If True, uses a local MockToolCallingLLM (no API key).
                  If False, uses Google Gemini via GOOGLE_API_KEY.
    """
    if use_mock:
        llm = MockToolCallingLLM()
    else:
        from langchain_google_genai import ChatGoogleGenerativeAI

        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key or api_key == "YOUR_GOOGLE_API_KEY_HERE":
            raise ValueError(
                "GOOGLE_API_KEY is not set. Update configs/.env with your API key, "
                "or run with --mock to test without one."
            )
        llm = ChatGoogleGenerativeAI(
            model=gemini_model,
            google_api_key=api_key,
            temperature=0,
        )

    # Define the tools
    tools = [configure_printing_parameters, run_mock_simulation]

    # Create the system prompt
    system_prompt = (
            "You are a laboratory automation assistant for the Opentrons OT-2. "
            "1. 1. When configuring dilutions, ALWAYS save the file as 'experiment_config.json' unless specified otherwise. "
            "2. If the user asks to simulate after configuring, use the 'dilution_protocol' by default. "
            "3. Use the output of the configuration tool as the input for the simulation tool. "
            "4. Always ignore deck calibration warnings during mock simulations."
        )

    # Build the modern agent using LangGraph's prebuilt react agent
    agent = create_react_agent(
            model=llm,
            tools=tools,
            prompt=system_prompt
        )

    return agent


if __name__ == "__main__":

    # Check for --mock flag
    use_mock = "--mock" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--mock"]

    # Get prompt from remaining args or use default
    if args:
        user_input = " ".join(args)
    else:
        user_input = "Run a simulation of configs/example_experiment.yaml and tell me if it succeeded."

    if use_mock:
        print("--- AI Agent Initialized (Mock LLM — no API key) ---")
    else:
        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        print(f"--- AI Agent Initialized (Google Gemini / {gemini_model}) ---")

    print(f"Goal: {user_input}\n")

    executor = create_opentrons_agent(use_mock=use_mock)

    # Run the agent using the modern 'messages' schema
    result = executor.invoke({"messages": [("user", user_input)]})

    print("\n--- Final Agent Response ---")
    
    # LangGraph returns a dictionary containing a 'messages' list. We extract the content of the final AI message.
    print(result["messages"][-1].content)