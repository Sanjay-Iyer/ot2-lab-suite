"""
src/agents/check_llm_auth.py
============================
Small smoke test for the configured LLM auth path.

Run from the repo root:

    python -m src.agents.check_llm_auth

If LLM_PROVIDER=vertexai, this uses Vertex AI through gcloud ADC. If
LLM_PROVIDER=api-key, this uses GOOGLE_API_KEY.
"""
from __future__ import annotations

import sys

from src.core.config import Config


def _message_text(message) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    return str(content)


def main() -> int:
    print(Config.describe_llm_auth())
    print("Running LLM auth smoke test...")

    try:
        llm = Config.get_llm(temperature=0, max_retries=1)
        response = llm.invoke("Reply with exactly: LLM_AUTH_OK")
    except Exception as exc:
        print(f"LLM auth check FAILED: {exc}")
        return 1

    text = _message_text(response).strip()
    if "LLM_AUTH_OK" in text:
        print("LLM auth check PASSED.")
        return 0

    print("LLM auth check WARNING: provider responded, but not with the expected token.")
    print(f"Response: {text[:500]}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
