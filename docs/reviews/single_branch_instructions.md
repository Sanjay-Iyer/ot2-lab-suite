# OT-2 Lab Suite — Autonomous Implementation and Three-Agent Review

## YOUR MISSION

You are taking over an ongoing Python laboratory automation repository redesign. Previous AI coding agents have already performed significant work, but their development was interrupted by rate limits and changes to the Git workflow.

**Your job is to recover their progress, consolidate it into one existing Git branch, and continue implementing the complete Master Implementation Plan.**

Do not start over. Do not develop another architecture proposal. Do not create additional development branches.

The user has already decided on the software architecture, the review process and the Git workflow.

Your primary responsibility is now **implementation, testing, independent technical review and incremental completion**.

---

# 1. Repository and Git Requirements

**Repository:**

`C:\code\opentrons_home\ot2-lab-suite`

**ONLY active development branch:**

`01_paper`

All new development must take place in this repository and on this existing branch.

Do not create:

- New `codex/task-*` branches.
- New feature branches.
- Separate branches for reviewing agents.
- New Git worktrees.
- Separate copies of the project for implementation.

Existing task branches and worktrees must be inspected and their changes recovered before they are retired.

Keep existing unrelated user branches untouched.

Only one AI agent may modify repository files. Other agents must review the same implementation without writing code.

Do not push to GitHub or other remotes.

## Master plan

Read the authoritative plan:

`docs/reviews/master_implementation_plan.md`

The replacement specification is titled:

**OT-2 Lab Suite — Master Implementation Plan (Single-Branch Edition)**

If the new single-branch version has not yet been installed in the repository, install the provided revised plan after protecting the previous document.

The revised plan supersedes older instructions to create one Git branch per task or phase.

---

# 2. Project Background

This repository controls an Opentrons OT-2 laboratory robot.

Its main active demonstration is **Agent NanoDrop**, an AI-assisted laboratory automation application.

The system currently includes:

- Gemini-based natural-language experiment interpretation.
- Structured experiment proposals and approval.
- Automated dye dilutions.
- Nanoparticle and dye printing on paper.
- Custom labware.
- A NiceGUI interface.
- Python experiment planning.
- Physical parameter validation.
- Simulation and OT-2 execution.
- Experiment history and saved plans.
- Separate Raman spectroscopy analysis.

Important current files include:

```text
scripts/ai_dye_demo_gui.py
scripts/ai_dye_demo.py

src/agents/dye_demo/
    session.py
    state.py
    conversation.py
    patches.py
    plan.py
    compiler.py
    validation.py
    placement.py
    references.py

src/agents/dye_demo/gui/
    app.py
    adapter.py
    labware_svg.py

src/protocols/printing/
    13_ai_agent_dilution_print_demo.py

scripts/build_vial_dilution_print.py
scripts/run_vial_print_robot.py

src/printing/
src/sers_engine/
src/labware/
configs/
skills/
experiment_history/
raman/
```

Verify actual file paths before editing.

The present NanoDrop architecture allows an LLM to interpret experimental instructions while deterministic Python validates and constructs robot operations.

**Preserve that separation. The LLM must never directly authorize robot execution or generate unrestricted physical robot commands.**

---

# 3. Final Architecture

The project should become one unified OT-2 automation application.

The intended execution architecture is:

```text
AI Natural Language       NiceGUI Manual       Python CLI
       |                       |                    |
       +-----------------------+--------------------+
                               |
                               v
                    Shared Application Layer
                               |
                               v
                     YAML Experiment Model
                               |
                               v
                    Deterministic Compiler
                               |
                               v
                  Validated Robot Operations
                               |
                               v
                   Simulation + Run Gate
                               |
                               v
                     Standalone OT-2 Protocol
```

The AI agent, GUI and manual Python interfaces must share the same planning and validation system.

## Target functionality

The final application must support:

1. Independent stock dilutions.
2. Serial dilution when explicitly specified and supported.
3. Liquid transfers.
4. Mixing.
5. Nanoparticle printing.
6. Dye printing.
7. Multiple paper substrates.
8. Replicates and stacked droplets.
9. Intentional overprinting.
10. Timed waits.
11. Operator pauses.
12. Finite repeated sequences.
13. Experimental parameter sweeps.
14. Dynamic tip and liquid-volume accounting.
15. Offline Python operation.
16. AI-assisted construction and editing of complete experiments.
17. Reproducible, immutable robot protocols.
18. Experiment history and physical-state records.

The repository should eventually have one main package named `ot2lab`.

**Reuse the existing NanoDrop, printing and SERS components rather than creating another independent workflow engine.**

Keep Raman independent.

---

# 4. Important: Previous LLM Progress

The prior agents have already worked on the redesign.

The following progress has been reported, but you must verify it against the actual repository.

| Task | Previously reported status |
|---|---|
| 0.1 — Repository inventory | Completed and committed |
| 0.2 — Baseline preservation | Completed and committed |
| 0.3 — WORK settings protection | Completed and committed |
| 0.4 — Baseline testing | Tests completed; classification and reviews may be pending |
| 0.5 — Golden motion traces | Implementation and tests reportedly passing; final review uncertain |
| 1.1 — Deterministic approval | Separate development branch/worktree exists; completion uncertain |
| 1.2 — Unified run gate | Separate development branch/worktree exists; completion uncertain |

### Task 0.4 reported test results

The previous agent reported:

- 2,225 tests passed.
- 25 tests failed.
- 5 tests skipped.

It identified environment-sensitive authentication failures and flaky GUI tests among the failures.

Do not assume all failures are genuine regressions or that they have already been resolved.

Classify the remaining failures using actual test evidence.

### Task 0.5 reported progress

The previous agent reported:

- 20 experimental plans compiled.
- 67 golden-motion-trace checks passed.
- Comparisons were made using pinned Opentrons analysis.

Verify the generated fixtures, recorded commands, simulator version and independent reviewer verdicts.

Do not repeat completed work unnecessarily.

### Previously created task branches

The earlier agent created:

```text
codex/pre-redesign-baseline
codex/task-0-1-repository-inventory
codex/task-0-2-preservation
codex/task-0-3-work-settings
codex/task-0-4-baseline-tests
codex/task-1-1-deterministic-approval
codex/task-1-2-run-gate
```

The earlier Git output showed that Tasks 1.1 and 1.2 were checked out in separate worktrees.

**These branches may contain valuable work that is not present on `01_paper`.**

Do not delete them or assume their changes are already integrated.

---

# 5. FIRST TASK — R0: Recover and Consolidate Everything into `01_paper`

This is your immediate priority.

Do not start new feature development until the current work has been safely recovered.

## Task R0.1 — Inspect Git and task history

From the repository, inspect:

```powershell
git status --short --branch --untracked-files=all
git branch -vv
git worktree list --porcelain
git tag --list
git log --all --graph --decorate --oneline -40
```

Also inspect all existing task branches and worktree changes.

Read:

```text
docs/reviews/README.md
docs/reviews/master_implementation_plan.md
docs/reviews/task-0.1/
docs/reviews/task-0.2/
docs/reviews/task-0.3/
docs/reviews/task-0.4/
docs/reviews/task-0.5/
docs/reviews/task-1.1/
docs/reviews/task-1.2/
```

Only assume these directories exist after verifying them.

Determine:

- Which tasks are completed.
- Which tasks are reviewed.
- Which tasks remain incomplete.
- Which task branches contain commits.
- Which worktrees contain uncommitted changes.
- Which changes are already present in `01_paper`.
- Which changes must be transferred.

## Task R0.2 — Preserve existing changes

Before any checkout, merge, cherry-pick or file movement:

1. Back up the current `01_paper` state.
2. Preserve all relevant uncommitted and untracked files.
3. Inspect and protect changes in every existing task worktree.
4. Protect NanoDrop configurations and experimental records.
5. Keep machine-specific credentials secure.
6. Create a recoverable archive outside the repository where needed.
7. Verify that the backup actually contains the protected material.

A Git tag does not preserve uncommitted or untracked files.

Do not use `git reset --hard` or destructive cleanup commands.

## Task R0.3 — Consolidate safely

Use `01_paper` as the one active development branch.

Recover accepted changes in task-dependency order.

Inspect each prior task branch before choosing whether to cherry-pick, apply a patch or manually reconcile the changes.

Do not blindly merge whole branches.

Avoid duplicate changes.

For incomplete tasks, preserve the useful implementation but do not mark them accepted until their tests and reviews pass.

Run focused regression tests after each integration.

## Task R0.4 — Verify the result

After consolidation, verify:

- `01_paper` contains the accepted work.
- Unfinished tasks are clearly identified.
- Review records remain accessible.
- Original NanoDrop functionality is preserved.
- Tests still execute.
- No work was lost.
- No new branch or worktree was created.
- Nothing was pushed to a remote.

Do not remove historical branches or worktrees until their contents are fully preserved and they are no longer in use.

**R0 acceptance:** All recoverable work has been safely consolidated into one active development history on `01_paper`.

---

# 6. Required Three-AI-Agent Team

Use three AI agents throughout development.

## Agent 1 — Main Implementation Coordinator

You are the main coding agent.

You are the ONLY agent authorized to:

- Edit implementation files.
- Modify tests.
- Update configurations.
- Update review documentation.
- Integrate existing development work.
- Create local commits on `01_paper`.

You must implement the tasks in the Master Implementation Plan and coordinate both reviewers.

## Agent 2 — Senior Python Software Engineer

Launch a separate independent reviewer specializing in Python software engineering.

This reviewer must inspect:

- Python correctness.
- Package architecture.
- Dependencies and imports.
- Code reuse.
- Backward compatibility.
- Configuration schemas.
- State management.
- Test quality.
- Error handling.
- Maintainability.
- Complexity and unnecessary abstractions.

The reviewer must identify specific defects and required corrections.

**Read-only access. No editing, branches, worktrees or commits.**

## Agent 3 — Laboratory Automation and OT-2 Expert

Launch a separate independent reviewer specializing in laboratory automation and Opentrons instruments.

This reviewer must inspect:

- Pipette compatibility.
- Liquid volumes.
- Aspiration and dispense depths.
- Tip pickup, reuse and contamination.
- Air gaps.
- Flow rates.
- Blow-outs.
- Touch-tip behavior.
- Paper printing heights.
- Printing positions.
- Labware geometry.
- Timed waits.
- Physical robot-motion compatibility.
- Simulation evidence.
- Execution failure recovery.
- Scientific reproducibility.

The reviewer must distinguish simulated behavior from physically verified behavior.

**Read-only access. No editing, branches, worktrees, commits or physical robot operations.**

If your agent tool automatically creates branches or worktrees, do not use that mode. Instead, provide reviewers with immutable code diffs, source snapshots and test reports.

If genuine independent subagents are unavailable, do not fabricate their review results.

---

# 7. Mandatory Review Cycle for EVERY Task

For each task:

**Step 1 — Implement**

Agent 1 implements the requested task on `01_paper`.

**Step 2 — Test**

Agent 1 runs relevant tests and saves exact results.

**Step 3 — Independent review**

Agent 2 independently reviews the implementation.

Agent 3 independently reviews the implementation.

Neither reviewer should simply accept the main agent's summary.

**Step 4 — Cross-review**

Agent 2 checks Agent 3's findings for overlooked software consequences.

Agent 3 checks Agent 2's findings for physical or scientific consequences.

**Step 5 — Correct**

Agent 1 fixes all blocking issues.

**Step 6 — Re-test**

Rerun affected tests after changes.

**Step 7 — Final approval**

Both reviewers independently issue:

- PASS
- CHANGES REQUIRED
- BLOCKED

Resolve disagreements through evidence and tests.

**Step 8 — Commit**

After both reviewers approve, commit the accepted change directly to `01_paper`.

**Step 9 — Continue**

Immediately proceed to the next eligible task.

Do not request routine user approval.

Store the task evidence and reviews under:

`docs/reviews/task-X.Y/`

Every task must have traceable implementation, testing, independent-review and final-verdict records.

---

# 8. NEXT TASKS — Complete the Existing Baseline

After R0, resume at the first incomplete task.

## Task 0.4 — Baseline Test Verification

- Verify the existing test-runner corrections.
- Confirm simulator tests are included.
- Confirm the NiceGUI photo assets are present.
- Classify all remaining test failures.
- Separate genuine application defects from environment failures and obsolete expectations.
- Do not spend excessive time fixing unrelated historical code.
- Record the baseline.
- Obtain both independent reviewer approvals.

**Done when:** The test baseline is reproducible and properly classified.

## Task 0.5 — Golden Robot-Motion Traces

- Inspect the existing trace recorder.
- Verify all 20 reported plans.
- Verify the 67 reported checks.
- Include exact aspiration and dispense heights, volumes, flow rates, tip motions, air gaps, dwell times and relevant specialized operations.
- Preserve current Protocol 13 as the reference implementation.
- Store reproducible trace fixtures.
- Obtain both independent reviewer approvals.

**Done when:** Reliable motion traces exist for detecting behavioral drift during refactoring.

---

# 9. PHASE 1 — Fix NanoDrop Execution Problems

Once baseline tasks pass, complete these tasks.

## Task 1.1 — Deterministic Approval

Inspect and reuse any existing work from the prior Task 1.1 branch.

Ensure a model-generated `approve` classification cannot independently apply a proposal.

Require explicit operator approval tied to the correct proposal and revision.

Test ambiguous messages, questions, stale proposals and incorrect classifications.

## Task 1.2 — Unified Run Gate

Inspect and reuse prior Task 1.2 work.

Ensure GUI, terminal and other execution entry points use one authoritative readiness check.

Verify that safety blockers cannot be bypassed through alternative execution commands.

## Task 1.3 — Semantic Compilation

Ensure semantic dilution selections, source references and printing coordinates are compiled into exact executable operations.

Reject uncompiled semantic fields.

## Task 1.4 — Immutable Protocol Artifacts

Ensure the robot runner selects the exact protocol artifact that passed simulation.

Eliminate dependence on mutable `_latest.py` files for execution.

## Task 1.5 — Production-Path Testing

Update the red-team harness and regression tests to exercise the current semantic conversation implementation.

Ensure routine pytest collection does not trigger real cloud-model requests.

## Task 1.6 — NanoDrop Regression Verification

Run targeted GUI, CLI, compiler and robot simulation tests.

Compare complete golden motion traces.

**Phase 1 acceptance:** Existing known execution-boundary problems are corrected without unintended changes to physical methods.

---

# 10. PHASE 2 — Unified Python Application

Implement the new `ot2lab` package incrementally.

Tasks:

1. Create package entry points.
2. Expose shared planning and validation functions.
3. Implement a consistent manual CLI.
4. Separate HOME and WORK environments.
5. Make AI and NiceGUI optional dependencies.
6. Preserve standalone manual hardware diagnostic scripts.

Target commands include:

```powershell
python -m ot2lab doctor
python -m ot2lab check experiment.yaml
python -m ot2lab plan experiment.yaml
python -m ot2lab simulate experiment.yaml
python -m ot2lab gui --simulate --no-model
```

These are future target commands; do not document them as working until implemented and tested.

Manual operation must work without Gemini, Vertex AI or internet connectivity.

The existing NanoDrop GUI must remain functional.

---

# 11. PHASE 3 — Unified Configuration and Parameter Validation

Implement:

1. Clear configuration ownership.
2. Versioned YAML schemas.
3. Explicit parameter units.
4. Hardware limits.
5. Method-specific limits.
6. Whole-workflow resource constraints.
7. Validated versus unvalidated parameter records.
8. Consistent GUI, CLI and AI validation.
9. Actionable parameter correction suggestions.
10. Allowlisted experimental overrides.

Preserve current physical parameters unless a change is scientifically justified and documented.

Pay special attention to previously identified printing-height, aspiration-height and labware-offset discrepancies.

Do not silently clamp experimental values.

---

# 12. PHASE 4 — Multistep Workflow Engine

Build one composable experimental planning system.

Initial step types:

```text
transfer
dilute_series
mix
print
wait
```

Then extend to:

```text
pause
repeat
parameter_sweep
```

Each step requires:

- Stable identifier.
- Schema.
- Compiler.
- Validation.
- Resource accounting.
- Tests.

Support complex workflows such as:

```text
Prepare dilutions
    ↓
Print nanoparticles
    ↓
Wait 30 minutes
    ↓
Print dye on the same positions
    ↓
Change parameters
    ↓
Repeat on another paper column
```

Track liquid consumption, well capacities, tip usage, contamination history, printing positions and timing across the entire experiment.

Do not reset resource accounting between steps.

Reuse the existing NanoDrop and general printing code instead of creating a third parallel planning engine.

---

# 13. PHASE 5 — Deterministic Protocol Generation

Implement:

1. Exact atomic robot operations.
2. A compatibility method preserving current NanoDrop motions.
3. Immutable, standalone generated protocols.
4. Protocol and labware hashes.
5. Pinned Opentrons simulation.
6. Execution-state tracking.
7. Safe stop and reconnection handling.
8. Persistent predicted versus confirmed physical state.
9. Failure and uncertainty reporting.

The simulator must use the exact artifact selected for upload.

Do not automatically replay partially completed operations after a network failure.

A disconnected robot is not necessarily a stopped robot.

Preserve existing Protocol 13 until its replacement passes the required verification.

---

# 14. PHASE 6 — NiceGUI and AI-Agent Improvements

Preserve the existing Agent NanoDrop demonstration.

Extend it with:

- Ordered workflow timeline.
- Expandable step details.
- Manual YAML workflow loading.
- Offline parameter editing.
- Workflow validation.
- Simulation previews.
- Dynamic multistep AI proposals.
- Insert, modify, delete and reorder operations.
- Experiment history and versioning.

The AI agent may propose changes but must not approve its own proposals or bypass physical-execution authorization.

The GUI must remain usable without AI credentials.

---

# 15. PHASE 7 — Physical WORK Validation

Do not execute this phase autonomously on the robot.

Document procedures for verifying:

- Real robot configuration.
- Pipette mounting.
- Labware positions.
- Printing heights.
- Aspirate depths.
- Physical transfer and dispensing behavior.
- Timed workflows.
- Multi-position printing.
- Stop and reconnection behavior.
- New versus legacy protocol motion.

Mark physical testing as:

`WORK VALIDATION PENDING`

Continue unrelated software work where possible.

Do not promote the replacement protocol as physically validated until the operator performs and accepts the instrument tests.

---

# 16. PHASE 8 — Repository Cleanup and Documentation

After replacement functionality is verified:

1. Reassess the 14 previous retirement candidates.
2. Identify obsolete scripts and agents.
3. Archive complete superseded workflow families.
4. Preserve historical experiment evidence.
5. Keep Raman separate.
6. Preserve spectroscopy data and calibration records.
7. Consolidate documentation.
8. Add automated regression checks.
9. Complete final independent architecture and laboratory automation reviews.

Do not archive or delete working methods simply because they are old.

Do not delete experimental data.

The repository should ultimately be smaller and easier to use, while retaining reproducibility and access to historical methods.

---

# 17. HOME/WORK ENVIRONMENT RULES

### HOME

- Conda environment `ai`.
- Gemini API-key path when enabled.
- Development and simulation.
- No physical robot operation.

### WORK

- Conda environment `llm`.
- Vertex AI through gcloud ADC when enabled.
- Manual operation without LLM credentials.
- Physical OT-2 execution only after explicit operator authorization.

The previous documentation reported robot software 7.0.2 and Protocol API 2.15. Verify these before depending on them.

Do not upgrade the robot software as part of this redesign.

Do not overwrite WORK-specific configuration with HOME defaults.

---

# 18. Rules for Autonomous Progress

I authorize you to continue routine software development without asking for repeated approval.

You may:

- Read and inspect code.
- Consolidate protected development changes.
- Refactor source files.
- Add tests.
- Update YAML.
- Run offline tests.
- Run local simulations.
- Create local commits on `01_paper`.
- Run independent AI reviews.
- Resolve review findings.
- Continue to the next task.

Do not:

- Push to a remote.
- Create new branches or worktrees.
- Rewrite Git history.
- Force-reset the repository.
- Execute physical robot commands.
- Overwrite credentials.
- Delete irreplaceable scientific data.
- Silently alter validated physical methods.
- Claim simulation proves physical performance.
- Fabricate reviewer decisions or test results.

If a step requires unavailable authorization, hardware or external resources, document that specific blocker and proceed with safe independent work.

Do not repeatedly ask for confirmation of routine programming decisions.

---

# 19. Required Progress Reporting

At the end of each accepted task, provide a concise report:

```text
TASK:
Task identifier and description

BRANCH:
01_paper

IMPLEMENTED:
Files and functionality changed

TESTS:
Exact commands, passed/failed/skipped results

PYTHON REVIEWER:
PASS / CHANGES REQUIRED / BLOCKED

LAB AUTOMATION REVIEWER:
PASS / CHANGES REQUIRED / BLOCKED

CROSS-REVIEW:
Issues identified and resolved

COMMIT:
Local commit hash, if accepted

WORK VALIDATION:
None required / Pending

NEXT TASK:
Next numbered task
```

Store detailed reports under `docs/reviews/`.

Do not spend excessive time producing redundant narratives.

The priority is implementing and verifying software.

---

# 20. Your Immediate Execution Order

**Begin with these exact steps:**

1. Inspect the repository and existing Git worktrees.
2. Read the revised single-branch Master Implementation Plan.
3. Recover and back up unfinished work.
4. Determine which task changes are already on `01_paper`.
5. Consolidate accepted work and preserve incomplete work safely.
6. Stop using separate task branches and worktrees for new development.
7. Verify Task 0.4 test classification and reviewer verdicts.
8. Verify Task 0.5 golden motion traces and reviewer verdicts.
9. Inspect and reuse the existing Task 1.1 approval implementation.
10. Inspect and reuse the existing Task 1.2 unified run-gate implementation.
11. Complete the remaining Phase 1 tasks.
12. Continue sequentially through the rest of the revised Master Implementation Plan.
13. For every task, obtain both independent reviews and cross-review before acceptance.
14. Commit accepted work directly to `01_paper`.
15. Continue automatically without routine user intervention.

**Do not create another master plan. Do not start over. Do not create additional branches.**

## Final Requirement

The completed project should have:

**ONE repository:** `ot2-lab-suite`

**ONE active development branch:** `01_paper`

**THREE AI agents:**
- One Python implementation coordinator.
- One independent Python engineering reviewer.
- One independent OT-2 laboratory automation reviewer.

**ONE deterministic workflow engine**, shared by AI, NiceGUI and manual Python workflows.

**ONE validated robot-execution pathway**, with immutable artifacts and clear physical authorization.

**ONE coherent development history**, preserving the current NanoDrop demonstration and established experimental methods.

Your first action is to inspect and recover the current work. Your second priority is to finish the incomplete baseline and execution-boundary tasks.

Then continue implementing the project automatically, one reviewed task at a time, until all software-only tasks are complete or a genuine blocker prevents further safe progress.