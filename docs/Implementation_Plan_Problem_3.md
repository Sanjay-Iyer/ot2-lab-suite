# Workflow & Execution Habits Refactoring Plan

## 1. Summary of Issues
- Scripts and tests use fragile `sys.path.append` hacks to resolve absolute imports.
- Direct execution (`python scripts/deploy.py`) is causing `ModuleNotFoundError` outside of the hacked environments.
- Network state confusion (Stale DNS/VPN) makes troubleshooting Gemini/Opentrons connectivity difficult.
- Lack of centralized documentation on how to properly invoke agents, tests, and scripts natively.

## 2. Proposed Changes

### Remove `sys.path` Hacks
Remove all manual `sys.path.append` modifications from:
- `tests/test_gemini.py`
- `src/printing/tools/dilution_planner.py`
- `src/agents/test_simulation_agent.py`
- `src/agents/run_stress_tests.py`
- `src/agents/main.py`
- `src/agents/check_models.py`
- `scripts/sync_robot.py`
- `scripts/deploy.py`
- `scripts/check_connectivity.py`

*(Note: These will all require `python -m` to be run natively from the project root).*

### Enhance Diagnostics in `check_connectivity.py`
- Extend script to explicitly print out troubleshooting advice for stale DNS, including Windows-specific `ipconfig /flushdns` and `Remove-Item Env:HTTP_PROXY` advice.
- Add `gcloud config get-value proxy/...` queries to provide a clear picture of Google CLI proxy assumptions.
- Check if `ROBOT_IP` is safely within `NO_PROXY` exceptions.

### Centralize and Fix Path Assumptions
- Ensure all relative config paths, output directories, and protocol staging dirs are anchored correctly to `PROJECT_ROOT`. (Currently well anchored in `src/utils/paths.py`, but will verify no `os.getcwd()` usages exist elsewhere).

### Standardize Documentation
- Provide standard powershell commands for starting the environment and running core routines in a new `docs/README_USAGE.md` (or append to existing docs):
  - Agent Launch: `python -m src.agents.main ...`
  - Tests: `python -m pytest tests`
  - Validation: `python -m scripts.audit_paths`
  - Diagnostics: `python -m scripts.check_connectivity`
- Add a "Stale DNS/VPN Troubleshooting" section as requested.

## 3. Verification Plan

### Automated Verification
- Run `python -m pytest tests` to ensure test execution still functions without `sys.path` hacks.
- Run `python -m scripts.check_connectivity` to review the new diagnostic output.
- Run `python -m scripts.audit_paths` to prove no pathing errors exist.
- Run `python -m src.agents.main "Configure a standard printing run and run a simulation"` to ensure the agent executes correctly via module invocation.
