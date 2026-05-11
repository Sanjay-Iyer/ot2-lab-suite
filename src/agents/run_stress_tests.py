import os
import sys
import pathlib
import datetime

# Add current directory to path
sys.path.append(str(pathlib.Path(__file__).resolve().parent))

from main import create_opentrons_agent

# --- THE 5 STRESS TEST SCENARIOS ---
STRESS_TESTS = [
    {
        "name": "Volume Overflow",
        "prompt": "Configure a print where the droplet volume is 50 uL but we are using a P20 pipette. Run a simulation.",
        "note": "Should fail simulation or validation; agent should reduce volume."
    },
    {
        "name": "Out-of-Bounds",
        "prompt": "Tell the robot to print at coordinates X=450, Y=450 which is outside the deck, then simulate.",
        "note": "Should trigger a Gantry Out of Bounds error in the simulator."
    },
    {
        "name": "Logical Contradiction",
        "prompt": "I have 100 ug/mL of stock. I want you to prepare a 250 ug/mL dilution. Configure it.",
        "note": "Underlying config.py should fail because target > stock."
    },
    {
        "name": "Missing Dependency",
        "prompt": "Immediately run the size_mixing_protocol simulation. Do not run any configuration or dilution planners first.",
        "note": "Should fail because configs/dilution_plan.json won't exist."
    },
    {
        "name": "Type Error",
        "prompt": "Configure the experiment and set the stock concentration to 'fifty micrograms'.",
        "note": "LangChain schema validation should catch this or config.py will fail."
    }
]

BASIC_TASK_TESTS = [
    {
        "name": "Deck Layout Inquiry (Pre-Flight)",
        "prompt": "Before we run any protocols, what physical labware do I need to place on the OT-2 deck for the dilution_protocol, and what slots do they go in?",
        "note": "Agent should read the tool descriptions or script metadata and accurately list the 96-well plate, tip racks, and source tubes."
    },
    {
        "name": "Volume Sanity Check (Pre-Flight)",
        "prompt": "Configure a dilution plan targeting 50 ug/mL, but I want to prepare 50 mL of this concentration in a single well of the 96-well plate. Do not simulate.",
        "note": "A standard 96-well plate maxes out around 300 uL per well. The agent or config tool should immediately reject 50 mL as physically impossible for that labware."
    },
    {
        "name": "Standard Configuration (Happy Path)",
        "prompt": "Create a standard dilution plan for 3 concentrations from a 100 ug/mL stock, targeting 10, 20, and 50 ug/mL. Do not run the simulation, just save the config.",
        "note": "Validates the agent can cleanly execute the config tool and halt without automatically assuming it needs to simulate everything."
    },
    {
        "name": "Log Summarization (Post-Run)",
        "prompt": "Run a mock simulation of the dilution_protocol. Afterwards, parse the output log and tell me exactly how many times the P300 pipette picked up a new tip.",
        "note": "Forces the agent to carefully read the STDOUT from the simulation tool and extract specific post-run metrics rather than just saying 'It passed'."
    },
    {
        "name": "Pipeline Orchestration (End-to-End)",
        "prompt": "First, configure a dilution plan targeting a 25 ug/mL concentration. Once the config is saved, automatically run the mock simulation for the dilution_protocol and summarize the results.",
        "note": "Tests multi-step orchestration. The agent must successfully chain Tool 1 (Config) -> Tool 2 (Simulation) in a single reasoning loop."
    }
]

class Tee:
    def __init__(self, *files):
        self.files = files
    def write(self, obj):
        for f in self.files:
            f.write(obj)
            f.flush()
    def flush(self):
        for f in self.files:
            f.flush()

def run_benchmarks():
    use_mock = "--mock" in sys.argv
    
    # Setup Logging Directory
    log_dir = pathlib.Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"stress_test_{timestamp}.log"
    
    with open(log_file, "w") as f:
        # Redirect stdout to both console and file
        original_stdout = sys.stdout
        sys.stdout = Tee(sys.stdout, f)
        
        try:
            print(f"--- INITIALIZING FULL TEST SUITE (Mock Mode: {use_mock}) ---")
            print(f"Logging to: {log_file}")
            
            # The executor is now a compiled LangGraph agent
            executor = create_opentrons_agent(use_mock=use_mock)
            
            all_tests = STRESS_TESTS + BASIC_TASK_TESTS
            results = []
            
            for i, test in enumerate(all_tests, 1):
                print(f"\n[TEST {i}/{len(all_tests)}] {test['name']}")
                print(f"Prompt: {test['prompt']}")
                print("-" * 30)
                
                try:
                    # Modern LangChain v1.0 standard input schema uses a messages list
                    result = executor.invoke({"messages": [("user", test["prompt"])]})
                    
                    # Modern graph state returns a list of messages; we extract the text of the last AIMessage
                    final_output = result["messages"][-1].content
                    
                    print(f"\nFinal Agent Response:\n{final_output}")
                    results.append({"name": test["name"], "status": "PASSED"})
                except Exception as e:
                    print(f"\nExecution Error: {str(e)}")
                    results.append({"name": test["name"], "status": "FAILED", "error": str(e)})
                
                print("=" * 50)

            # --- FINAL SUMMARY REPORT ---
            print("\n\n" + "!" * 50)
            print("--- FINAL BENCHMARK REPORT ---")
            print("!" * 50)
            passed_count = 0
            for i, res in enumerate(results, 1):
                status = res["status"]
                if status == "PASSED": passed_count += 1
                print(f"[{i}] {res['name']:<35} : {status}")
            
            print("-" * 50)
            print(f"OVERALL SCORE: {passed_count}/{len(all_tests)}")
            print("!" * 50 + "\n")
            
        finally:
            sys.stdout = original_stdout

if __name__ == "__main__":
    run_benchmarks()