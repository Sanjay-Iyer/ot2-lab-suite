# Architecture Audit 2 — Independent findings and resolution

Independent read-only review found no persisted prior Audit 2 report, so every
finding was reconstructed from the implementation and reproduced before changes.
All commands ran on the simulation laptop; no robot connection or live command was
used.

| Finding | Severity | Determination | Resolution |
|---|---|---|---|
| Inline or arbitrary machine profiles bypass laboratory ownership | blocker | valid | Experiment YAML now requires a profile directly under `configs/machines`; inline machines are rejected. |
| `per_target` grouped by row only | blocker | valid Experiment 1 layout leakage | Full target identifiers now define tip groups and operation ids. |
| Transfer source could disagree with `liquid_id` | major | valid | Explicit sources must equal the authoritative liquid location. |
| Mix location could disagree with `liquid_id` | major | valid | Requested location must equal the authoritative liquid location. |
| Transfer could target substrate/tiprack | major | valid/partial | Transfers are restricted to source/preparation labware; substrates require `print`. |
| One explicit direct product relaxed every collision | major | valid | Collision permission is per product. |
| Transfer result could alias an existing liquid | major | valid | Destination aliquots require a new, unbound result id. |
| Explicit series reuse was over-broad | major | valid | Only an explicit factor-1 product equal to the source id may reuse identity. |
| Tip demand was not checked before motion | major | valid | Resolver compares demand with definition-derived rack capacity; executor rechecks loaded capacity. |
| Nonzero pre-air chase was counted but not performed | major | valid | V1 rejects nonzero pre-air until the resolved action and executor support it. |
| Machine-profile comments exposed Experiment 1 context | minor | partially valid | Provenance now describes a generic laboratory-validated operating point. |

Series printing also now fails closed unless `tip_policy: per_target`, preventing
different prepared liquids from sharing a tip. Positive and adversarial tests cover
serial and direct factor-1 identity reuse, unrelated collisions, same-row targets,
and a 97-tip request.

Verification: 76 focused Stage 2 tests passed. Experiment 1 still resolves to 187
actions and 61 tips. Its physical SHA-256 remains
`3ce809a8133a95207da62fce7bea44977cf4b134490478559c903b6b77e77313`.

Verdict: **AUDIT CLEAN**.
