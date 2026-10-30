"""Manual connectivity probe; module import and pytest collection are offline.

Explicit network opt-in: python tests/test_gemini.py --connect
"""
from __future__ import annotations


def main() -> int:
    import argparse
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true", help="Send one model request")
    if not parser.parse_args().connect:
        parser.error("The manual model probe requires --connect")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.core.config import Config
    from src.utils.limits_per_minute import RateLimitGuard
    from langchain_core.messages import HumanMessage
    
    rate_guard = RateLimitGuard(enabled=True)
    
    # Initialize LLM via Factory
    llm = Config.get_llm(temperature=0)
    
    # Using the guard's wrapper to protect the call
    response = rate_guard.invoke_with_limit(
        llm, 
        [HumanMessage(content="Hello! Please reply with 'Gemini is online and limited!' and nothing else.")]
    )
    
    print(response.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
