import os
import sys
import pathlib
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# Add current directory to path so we can import main and tools
sys.path.append(str(pathlib.Path(__file__).resolve().parent))

from main import MockToolCallingLLM
from tools import run_mock_simulation, configure_printing_parameters

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
    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
    
    # 4. Run Test Prompt
    user_request = "Configure a print with 5 dilutions and then run a simulation of the dilution_protocol to check for errors."
    print(f"User Request: {user_request}\n")
    
    try:
        result = executor.invoke({"input": user_request})
        print("\n--- Test Result ---")
        print(result["output"])
    except Exception as e:
        print(f"\n--- Test Failed with Error ---\n{str(e)}")

if __name__ == "__main__":
    test_simulation_workflow()
