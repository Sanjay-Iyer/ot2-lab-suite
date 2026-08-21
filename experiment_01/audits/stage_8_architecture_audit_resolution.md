# Architecture Audit 8 — Trusted approval and simulation workflow

Stage 8 composes the generalized agent, preloaded runtime skill, high-level tools,
strict job schema, deterministic resolver, human-readable review, trusted user
decision boundary, generic executor builder, and local OT-2 simulator. It stops at
`READY_FOR_EXECUTION`; it contains no live-run path.

The lifecycle is:

```text
scientist request -> dedicated agent -> generalized skill -> high-level tools
-> immutable proposed YAML -> PrintExperimentJobV1 -> ResolvedExperimentPlanV1
-> scientist review -> AWAITING_APPROVAL -> trusted USER APPROVAL
-> exact-plan generic executor -> local simulation -> READY_FOR_EXECUTION
```

| Finding | Severity | Determination | Resolution |
|---|---|---|---|
| Workflow state could swap the embedded config after preview | blocker | valid | Recompute config job identity and bind config, canonical YAML, persisted bytes/digest, validation, resolution, preview, approval, and simulation evidence. |
| Transitions used unvalidated `model_copy` | blocker | valid | Every transition serializes and fully revalidates state before and after updates. |
| Preview did not map controls/replicates to exact wells | major | valid | Added per-well condition, purpose, series-point/replicate index, explicit targets, and source/destination mapping. |
| Process-local HMAC could not survive a restart | major | valid | Require a protected persistent `OT2_STANDARD_EXPERIMENT_APPROVAL_KEY` of at least 32 bytes. |
| Plaintext bearer-record remediation could be forged | blocker | valid and superseded | Removed it; approval is now HMAC over exact job hash plus exact statement. The secret is absent from agent schemas and outputs. |

Tests cover preapproval stop, exact artifact binding, complete scientific review,
unreachable approval minting, rejection, preapproval and postapproval config swaps,
deserialization mismatch, forged seal rejection, missing-key failure, cross-process
verification, local simulation, and the terminal state.

Independent Stage 8 gate: **43 passed**. Broader Stage 2–8 gate: **142 passed**.

Verdict: **AUDIT CLEAN**.

Deployment must supply `OT2_STANDARD_EXPERIMENT_APPROVAL_KEY` through protected
trusted application configuration. Missing, malformed, or short keys fail closed.
