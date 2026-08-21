# Architecture Audit 3 — Independent findings and resolution

The independent review confirmed that static A and config B are separate plan
builders and that B uses strict schema validation, but found gaps in the equivalence
gate. All verification was simulation-only.

| Finding | Severity | Determination | Resolution |
|---|---|---|---|
| Machine ownership was conventional, not enforced | major | valid | Fixed by Audit 2 registered-profile enforcement. |
| Physical hash ignored initial-liquid setup | major | valid | Added canonical setup trace/hash; `execution_match` requires setup and actions. |
| Structural trace erased chemical identity | blocker for A vs C | valid | Reclassified as topology-only; production success never depends on it. |
| `5` and `5.0` could compare equal but hash differently | major | valid | Physical numeric fields are normalized before equality and hashing; invariant is asserted. |
| Physical trace was mislabeled canonical | moderate | valid | Artifact is now `config_physical_trace.json`; setup has its own artifact. |
| Stage 3 could compare against stored A without checking A drift | moderate | valid | The required gate runs static and config ground-truth suites together. |
| Bare action lists could claim execution equivalence | major | valid | Execution comparison requires complete plans with `initial_liquids`. |
| Setup declaration order caused false mismatches | moderate | valid | Setup records are canonically sorted by physical location and liquid id. |

Current result: 187 actions on each side; physical, setup, and execution matches are
all true. Physical SHA-256 remains
`3ce809a8133a95207da62fce7bea44977cf4b134490478559c903b6b77e77313`;
setup SHA-256 is
`5ca13fa16f2e4302f109bb69518e4593955465d3ddeca48d1619ecdd1bf02c34`.
The canonical config-plan identity legitimately changed to
`6f2c68045a956b67cd004f3115368691ee631ff0714e80261298a9d9d70b39f3`
because nonphysical operation and tip-group labels now use full targets.

Forced-motion simulation passed with 64 deposits and 320 uL printed. The combined
static/config gate contains 23 tests.

Verdict: **AUDIT CLEAN**.
