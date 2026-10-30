# Task 1.1 — Deterministic proposal approval

An LLM interpretation alone must not approve a pending proposal. Require an explicit operator
action tied to the exact proposal and its revision, and ensure ordinary questions do not modify
the approved plan. Test ambiguous statements, including "Looks good.", "What would this change?",
"Explain this proposal." and "I agree with the reasoning, but change the volume."

Scope: `src/agents/dye_demo/session.py`, `conversation.py` (router prompt text),
`gui/adapter.py`, `gui/app.py`, and a new test file. No robot motion, protocol or physical
parameter changes.

Acceptance: no plan mutation occurs without a valid explicit approval action.
Physical qualification is not affected by this task.
