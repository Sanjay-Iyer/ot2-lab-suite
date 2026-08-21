# Architecture Audit 7 — Dedicated generalized Printing Agent

Stage 7 added a separate `create_standard_experiment_agent` factory. Its compact
role prompt says what the probabilistic layer owns and forbids protocol code,
action lists, calibration, and live commands. Procedural knowledge is injected
from the one generalized runtime skill.

The agent binds exactly seven high-level tools: capability discovery, create,
validate, resolve, inspect, approved local simulation, and structured issue
reporting. It cannot load legacy skills, compile legacy jobs, mint approval,
construct motion, or execute live.

| Finding | Severity | Determination | Resolution |
|---|---|---|---|
| Generalized prompt preloaded the right skill but exposed an unrestricted loader | major | valid | Removed the loader and changed prompt wording to use the preloaded skill. |
| Surface test used only a permissive subset assertion | moderate | valid | Test now asserts the exact seven-name allowlist. |
| Only one neutral agent request was exercised | readiness gap | valid | Added printing-only, transfer+print, and mix+control-replicate agent tests in addition to the neutral ladder preview. |

Independent verification found strict nested schemas, registered-profile
ownership, exact-job external approval, summary-only resolution, no low-level or
live tool, and no Experiment 1 leakage. Focused Stage 5–7 gate: **17 passed**.

Verdict: **AUDIT CLEAN**.
