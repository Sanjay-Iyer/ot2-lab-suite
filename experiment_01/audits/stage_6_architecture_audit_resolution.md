# Architecture Audit 6 — Runtime standard-printing skill

Stage 6 added `skills/standard-printing-experiment/SKILL.md` through the existing
runtime printing-skill loader. The skill teaches generalized configuration
procedure; it does not contain an experiment.

The skill covers when the workflow applies, the registered 20 mL vial-rack source
and 96-well preparation roles, liquid declarations, transfer/mix/direct/serial
dilution, repeated deposition, rests, controls and replicates, safe series tip
policy, explicit factor-1 source reuse, global product identities, validation,
scientist clarification, machine ownership, external approval, and the
simulation-only terminal state.

| Finding | Severity | Determination | Resolution |
|---|---|---|---|
| Legacy v9 skills could be selected by the generalized route | major | valid | Added exact `standard_experiment` routing and kept legacy selectors separate. |
| Skill did not state supported source roles or product identity rules | major | valid | Added registered-role, factor-1, and unique product-ID guidance. |
| Leakage test matched only exact phrases | minor | valid | Added semantic regex checks for dilution, drop, column, timing, and hash variants. |
| Agent could call the unrestricted skill loader after safe prompt injection | major | valid | Removed the generic loader from the dedicated agent; the one generalized skill is deterministically preloaded. |
| Template named a capability not exposed by the generalized agent | minor | valid | Updated it to the actual generalized capability-discovery path. |

The skill and agent surface contain no Experiment 1 materials, mappings, dilution
ladder, repeat count, timing, or expected fingerprints. Focused Stage 5–7 gate:
**17 passed**.

Verdict: **AUDIT CLEAN**.
