import os
import sys
from pathlib import Path

# Test execution should use `python -m pytest tests`


try:
    from src.core.config import Config
    from src.utils.limits_per_minute import RateLimitGuard
    from langchain_core.messages import HumanMessage
    
    print("--- Environment Check ---")
    print(f"Using Model: {Config.GEMINI_MODEL}")
    
    # Initialize Rate Limit Guard (Testing mode)
    rate_guard = RateLimitGuard(enabled=True)
    
    # Initialize LLM via Factory
    print("Initializing Resilient LLM Factory...")
    llm = Config.get_llm(temperature=0)
    
    print("\n--- Sending Rate-Limited Test Prompt ---")
    
    # Using the guard's wrapper to protect the call
    response = rate_guard.invoke_with_limit(
        llm, 
        [HumanMessage(content="Hello! Please reply with 'Gemini is online and limited!' and nothing else.")]
    )
    
    print(f"\n[RESPONSE]: {response.content}")
    
except Exception as e:
    print(f"\n[ERROR]: {str(e)}")
    sys.exit(1)
