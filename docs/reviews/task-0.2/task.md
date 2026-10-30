# Task 0.2 — Protect the working state

The operator authorized local baseline/checkpoints and then issued standing
authorization to implement the full plan with two independent reviewers. See
`../PROCESS.md`; it supersedes the original routine-approval/four-agent process.

Baseline branch `codex/pre-redesign-baseline` and tag
`codex-pre-redesign-20261030` point to original source commit
`1a4fd19a6b42c99bef639ca432a8b3d89a39ff37`. Reviewed inventory records are committed
as `f8cfc5d`. Current task branch: `codex/task-0-2-preservation`.

Scope: preserve all committed history and actual readable working files,
including untracked and ignored scientific data, user configs, HOME settings and
installed simulator environments, before substantial runtime modifications.
Only inaccessible disposable-cache paths may be excluded, explicitly recorded.
Any inaccessible non-cache project data is a blocker, not silently skipped.

Acceptance: a private snapshot outside the checkout, complete Git bundle,
per-file digest manifest, independently checked archive and extracted-file
hashes, and verified local Git recovery; preservation/recovery procedures and
both independent reviews. No pushes, source deletion, credential changes,
runtime behavior edits, or hardware execution. Actual WORK information remains
Task0.3 WORK VALIDATION PENDING.
