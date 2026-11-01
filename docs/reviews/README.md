# OT-2 redesign implementation records

The [master plan](master_implementation_plan.md) applies the operator's
[single-branch instructions](single_branch_instructions.md) to the existing
detailed tasks. The [original plan](master_implementation_plan.original.md) is
preserved verbatim from its pre-amendment working file. Architecture/scientific
requirements are unchanged; task status is recorded here and in final verdicts.

Source attachment SHA-256:
`0309dc59cea394fce44d5559ce06c02e35cfd9cb3fafcf69b51f6b57f9c2935b`.

Development continues automatically on **`01_paper` only**, with independent
Python/laboratory reviews. This is HOME, simulation-only; no physical robot
execution, remote push, credential change or deployment promotion is authorized.

| Task | Scope | Status |
|---|---|---|
| [R0](task-R0/task.md) | Recover branches/worktrees into one development history | Implemented; final recovery review pending |
| [0.1](task-0.1/task.md) | Repository inventory and dependency/retention assessment | See [final verdict](task-0.1/final_verdict.md) |
| [0.2](task-0.2/task.md) | Protect working state; approved baseline/checkpoint | PASS; both reviewers, checkpoint a2f8dba |
| [0.3](task-0.3/task.md) | Preserve WORK-specific information | Local PASS/PASS; WORK VALIDATION PENDING |
| [0.4](task-0.4/task.md) | Controlled application test baseline | Recovered candidate; historical 2227/25/5 reconciled; in-place runner/reviews pending |
| [0.5](task-0.5/task.md) | Golden motion traces | Recovered 29 traces; stale packet/dry-run comparison require correction and reviews |
| 1.1 | Deterministic approval | Commit1838b3a preserved as inactive patch; review pending |
| 1.2 | Unified run gate | Exact dirty candidate preserved as inactive patch; review pending |
| 1.3 onward | Remaining numbered master tasks | Not started; continue in dependency order |

Task0.1 used the original three-reviewer process. From Task0.2 the operator's
[standing authorization/process amendment](PROCESS.md) requires the coordinator
plus independent Python and laboratory reviewers, cross-review, correction, and
final verdicts, with automatic continuation. A software PASS never establishes
physical validation. No archive move is authorized by an inventory classification.
