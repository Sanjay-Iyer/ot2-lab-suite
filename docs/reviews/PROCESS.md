# Implementation process amendment — single branch, 2026-10-30

The [latest operator instructions](single_branch_instructions.md) supersede the
old branching workflow, four-agent process and routine checkpoint approvals.
All development/local commits use existing `01_paper` in this repository.
Create no new branches, worktrees or project copies. Existing directories/refs
remain evidence until safely recovered. Only the coordinator edits files.

From Task 0.2 forward there are three roles: implementation coordinator, an
independent Senior Python Software Engineer, and an independent Laboratory
Automation/OT-2 Expert. Both reviewers inspect each completed task, cross-review
each other's findings, and independently recheck corrections before acceptance.
Reviewers report findings; the coordinator changes implementation and records.

Standing authorization covers local checkpoints on `01_paper`, code/docs,
refactoring, offline tests/simulations and subsequent numbered tasks. Do not ask
routine permission to continue. Preserve recoverable committed, uncommitted,
untracked and relevant ignored project files before substantial changes.

No remote push, history rewrite, permanent scientific-data deletion, credential
alteration, physical robot execution, or deployed-protocol promotion is authorized.
WORK-only checks are documented as WORK VALIDATION PENDING; continue independent
software work. Physical qualification cannot be established by simulation.

Each task record includes scope/files, actual checks/results, both independent
reviews, cross-review/corrections, final verdict, remaining WORK checks and next
task. Task 0.1's already accepted three-reviewer record remains historical.
