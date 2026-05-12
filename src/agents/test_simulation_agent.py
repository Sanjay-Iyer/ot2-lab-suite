import os
import sys
import pathlib
from langgraph.prebuilt import create_react_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from src.agents.main import MockToolCallingLLM
# tools.py no longer has run_mock_simulation, but we update the import to be absolute
try:
    from src.agents.tools import run_mock_simulation, configure_printing_parameters
except ImportError:
    pass

def test_simulation_workflow():
    print("--- Initializing High-Fidelity Simulation Test ---")
    
    # 1. Setup Mock LLM and Tools
    llm = MockToolCallingLLM()
    tools = [configure_printing_parameters, run_mock_simulation]
    
    # 2. Create Prompt
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an Opentrons Expert. Always simulate protocols using the run_mock_simulation tool before finishing. "
                   "If you need to configure parameters first, use configure_printing_parameters."),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    
    # 3. Initialize Agent
    agent = create_react_agent(llm, tools)
    
    # 4. Run Test Prompt
    user_request = "Configure a print with 5 dilutions and then run a simulation of the dilution_protocol to check for errors."
    print(f"User Request: {user_request}\n")
    
    try:
        result = agent.invoke({"messages": [("user", user_request)]})
        print("\n--- Test Result ---")
        print(result["output"])
    except Exception as e:
        print(f"\n--- Test Failed with Error ---\n{str(e)}")

if __name__ == "__main__":
    test_simulation_workflow()
