# Architecture Audit 4 — Independent findings and resolution

An independent reviewer searched the template, parser, schema, resolver, machine
profile, executor, generic tests, defaults, comments, examples, and discoverable
skills. The pre-existing Stage 4 note was implementation evidence, not an
independent audit. Verification remained simulation-only.

| Finding | Severity | Determination | Resolution |
|---|---|---|---|
| Series printing defaulted to one contaminating tip | blocker | valid | Series sources now require explicit `per_target`; omission, `per_step`, and `per_pass` fail schema validation. |
| `per_target` actually grouped by row | blocker | valid | Full target identifiers now control tip changes; A1/A2 regression proves separation. |
| Leakage scan covered only the template | major | valid | An explicit blind-context allowlist now scans template, machine profile, schema, capabilities, loader, resolver, review, executor, and generic tests. |
| Generic tests contained Experiment 1 answer combinations | major | valid | Replaced with unrelated dilution, volume, repeat, and timing examples. |
| Required generality matrix was only partial | major | valid | Added/asserted one-drop, transfer+print, mix+print, direct/serial dilution+print, single/vial/plate sources, multi-drop, delay on/off, and mixing on/off cases. |
| Legacy v9 skill/defaults could contaminate a future blind context | major readiness risk | partially valid | They remain frozen legacy artifacts and are excluded from the generalized blind-context allowlist; Stage 6 must provide a separately routed experiment skill. |
| Template said it must never run although tests simulate it | minor | valid | Wording now identifies a simulation-safe worked example that is not physically approved. |

The worked template resolves and simulates as 25 actions: 7 transfers, 6 mixes,
6 prints, 1 delay, and 8 tips. The Stage 2 plus template suite passes 92 tests.

Verdict: **AUDIT CLEAN**, with the legacy-skill routing requirement carried forward
explicitly to Stage 6.
