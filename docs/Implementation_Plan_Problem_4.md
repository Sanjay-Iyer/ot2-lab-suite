# Implementation Plan: Code Maintenance & Dependencies (Problem 4)

## 1. Summary of Findings
- **Current Versions:** `langgraph (1.1.10)`, `langgraph-prebuilt (1.0.13)`, `langchain (1.2.18)`, `langchain-google-genai (4.2.2)`.
- **Deprecations/Imports:** The codebase currently uses `from langgraph.prebuilt import create_react_agent`. Based on the installed `langgraph` v1.1.10, this is the **correct, modern, and supported** method for creating tool-calling agents. I will *not* downgrade this to `langchain.agents.create_agent` per the guardrail, as that would be a regression.
- **Hardcoded Configuration Drift:** 
  - `src/printing/tools/config.py` contained a hardcoded link-local fallback.
  - `src/core/config.py` defaults to `gemini-1.5-flash` instead of the newer `gemini-2.5-flash-lite`.

## 2. Proposed Changes

### Configuration Hardening
#### [MODIFY] `src/printing/tools/config.py`
- Remove the hardcoded robot IP string.
- Refactor to pull from `os.getenv("ROBOT_IP")` or delegate entirely to `src.core.config.Config.ROBOT_IP` to centralize the network definition.

#### [MODIFY] `src/core/config.py`
- Update default model to `gemini-2.5-flash-lite` if `GEMINI_MODEL` is missing.

#### [MODIFY] `.env.template`
- Overhaul to exactly match the requested schema with clear sections for Gemini, Proxy, Robot, and Local paths.

### Dependency Hygiene & Testing
#### [NEW] `tests/test_config.py`
- Add tests to ensure `Config` accurately respects environment variables (`ROBOT_IP`, `GEMINI_MODEL`, `NO_PROXY`).

#### [NEW] `tests/test_dependency_hygiene.py`
- Add AST-based checks (similar to `preflight.py`) to statically audit the `src/agents/` directory, asserting that deprecated `langchain.agents` legacy imports are forbidden, and ensuring modern `langgraph.prebuilt` is used.

## 3. Verification Plan
After making the changes, I will run the following to verify:
```powershell
python -m pytest tests/test_config.py tests/test_dependency_hygiene.py
python -m scripts.audit_paths
```
