# Task 0.1 — Repository inventory

## Objective and source

Implement Task 0.1 of the [master plan](../master_implementation_plan.md): inspect
the current repository, record the GUI/terminal/robot-runner dependencies,
classify files, and assess proposed archival candidates without moving anything.

The operator selected **Begin Batch A implementation** in this chat. Task branch:
`codex/task-0-1-repository-inventory`. Starting branch: `01_paper`.
Starting HEAD: `1a4fd19a6b42c99bef639ca432a8b3d89a39ff37` (`before big update`,
2026-10-30). Git status was clean before adding these review records. The
previously untracked semantic modules/skills/tests are now in that commit; do not
repeat the older audit's claim that they remain uncommitted.

## Scope and expected files

Only `docs/reviews/` may change in this task. It contains the preserved master
plan, task specification, inventory generator/verifier, CSV/JSON evidence,
human-readable dependency/retention assessment, verification log, and review
records. No runtime source, protocol, config, skill, labware, data, credential,
environment, or existing guide is edited.

Inventory scope: tracked files and nonignored untracked files at inspection time,
excluding `docs/reviews/`. Ignored/local outputs and settings are listed by path
only. They are not content-inspected, backed up, proven unused, or approved for
deletion. Git internals and installed environments are not dependency-scanned.

## Acceptance criteria

1. Every in-scope file has a role, active/shared/historical/generated/unknown
   classification, Git state, read status, size/hash when safe to read, and
   retention assessment.
2. The active NanoDrop GUI, terminal and subprocess/robot paths are mapped,
   including runtime skills, YAML defaults, labware, pinned simulator and outputs.
3. Parallel general-printing, SERS, labware and Raman systems are distinguished.
4. Dynamic registries/catalogues and static-scan limitations are documented;
   zero imports never means safe to archive.
5. Each proposed candidate has dependencies, replacement conditions and a
   retention decision. Nothing is moved or declared physically obsolete.
6. Evidence is reproducible without repository imports, cloud/robot contact,
   builds, simulations or application test collection.
7. Independent reviewers A/B/C inspect the same packet, cross-review, and issue
   final verdicts after corrections. HOME evidence does not qualify WORK methods.

## Explicitly deferred

Backup/checkpoint authorization (0.2), actual WORK settings verification (0.3),
application tests (0.4), golden traces (0.5), all boundary fixes and redesign.
This task may record existing concerns but does not claim to reproduce/fix them.
