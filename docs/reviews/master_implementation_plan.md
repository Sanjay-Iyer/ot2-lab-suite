# OT-2 Lab Suite: Master Implementation Plan

**Project:** `C:\code\opentrons_home\ot2-lab-suite`  
**Target platform:** Opentrons OT-2  
**Primary application:** Agent NanoDrop  
**Status:** Proposed redesign — implementation not started  
**Execution model:** One coding agent/coordinator + three independent AI review agents + operator approval for physical validation

---

# 1. Project Objectives

Redesign the OT-2 repository into a unified automation platform that supports:

1. Natural-language experiment planning through LLMs and AI agents.
2. Manual experiment configuration through Python and YAML.
3. A graphical interface using the existing NiceGUI demonstration.
4. Independent dilution, serial dilution where supported, liquid transfer, mixing, paper printing, timed waits and multistep experimental workflows.
5. Custom laboratory configurations without creating a new Python script for every experiment.
6. Offline operation without internet access or an LLM.
7. Clear separation between HOME development and WORK instrument operation.
8. Validation of pipetting parameters, liquid volumes, tip usage, paper positioning and robot motions.
9. Independent Raman analysis with experimental data traceability.
10. A substantially smaller, documented and maintainable repository.

**Non-negotiable requirement:** The current working NanoDrop demonstration and its established liquid-handling behavior must remain available until the replacement has passed physical verification on the WORK instrument.

# 2. Four-Agent Development Organization

The development process will use four roles: one implementation coordinator and three independent reviewers.

## Implementation coordinator

Responsible for implementing the assigned task, running preliminary tests, preparing the code diff and producing the evidence package.

The coordinator must not approve its own work or change an agreed scientific method without recording the difference.

## Reviewer Agent A — Software Architecture and Integration

**Primary responsibility:** Verify the integrity of the software architecture.

For every task, check:

- Correct placement of files and modules.
- Dependency direction and module imports.
- Reuse of existing functionality rather than duplication.
- Backward compatibility with current NanoDrop behavior.
- Configuration ownership and schema consistency.
- Correct handling of HOME and WORK environments.
- Unnecessary complexity, duplicate state and redundant abstractions.
- Impacts on other scripts, protocols and repository components.

**Special authority:** Can block changes that introduce competing workflow engines or break existing application interfaces.

## Reviewer Agent B — OT-2 Hardware, Chemistry and Execution Safety

**Primary responsibility:** Verify the correctness of robot behavior and experimental operations.

For every task, check:

- Pipette and liquid-volume constraints.
- Aspiration and dispensing depths.
- Tip selection, reuse, contamination and disposal.
- Well capacities and liquid accounting.
- Printing heights and motion sequences.
- Labware geometry, deck locations and offsets.
- Timed waits, pauses and unattended operation.
- Physical-state bookkeeping.
- Handling of aborts, network failures and uncertain execution status.
- Differences between software simulation and physically validated behavior.

**Special authority:** Can block any change that modifies robot motion without adequate testing or evidence.

A simulation cannot substitute for physical validation.

## Reviewer Agent C — Testing, Reliability and User Workflow

**Primary responsibility:** Verify that the implementation works as requested.

For every task, check:

- Unit, integration and regression tests.
- Current NanoDrop GUI functionality.
- Offline and manual Python execution.
- AI-agent proposal handling.
- Negative tests, malformed inputs and adversarial cases.
- Error messages and failure recovery.
- Correct operation across HOME and WORK configurations.
- Documentation and copy-paste commands.
- Reproducibility of generated experiment artifacts.
- Whether the task's acceptance criteria are actually demonstrated.

**Special authority:** Can block changes that lack reproducible tests or break supported user workflows.

## Mandatory three-agent review cycle

Every individual task uses the same procedure.

**Round 1 — Independent reviews**

1. Coordinator completes one task on a dedicated branch.
2. Coordinator prepares a task-specific evidence package.
3. All three review agents receive the same code diff, task specification and test evidence.
4. Each reviewer independently inspects the work.
5. Each reviewer produces a written PASS, CHANGES REQUIRED or BLOCKED verdict.

**Round 2 — Cross-review**

6. Agent A reviews Agent C's findings and checks test coverage against the architecture.
7. Agent B reviews Agent A's findings and checks their physical-execution implications.
8. Agent C reviews Agent B's findings and checks that identified risks have executable verification procedures.
9. Reviewers report contradictions, missed issues and disagreements.
10. The coordinator resolves the findings and resubmits the revised diff.

**Round 3 — Final acceptance**

11. All three reviewers review the final revision.
12. Relevant tests are rerun after the last change.
13. Each reviewer issues a final verdict.
14. The task is accepted only after all three reviewers pass.
15. Changes requiring robot testing remain marked `WORK VALIDATION PENDING` even after three software reviews pass.

No reviewer should edit the implementation directly while reviewing. Review agents provide findings; the coordinator makes corrections.

### Standard task review record

Store each review under a proposed location such as:

`docs/reviews/<TASK_ID>/`

Each completed task should include:

- `task.md` — objective, scope, expected files and acceptance criteria.
- `implementation.md` — summary of code changes.
- `tests.txt` — executed commands and their actual results.
- `architecture_review.md` — Agent A.
- `hardware_review.md` — Agent B.
- `verification_review.md` — Agent C.
- `cross_review.md` — reviewer disagreements and resolutions.
- `final_verdict.md` — final status and unresolved WORK-only checks.

For efficiency, these may be consolidated into fewer files provided that all three independent reviews and the final decision remain traceable.

**Rule:** No phase advances with unresolved blocking defects in its required tasks.

---

# 3. Target Repository Architecture

The proposed repository should use one main package named `ot2lab`.

```text
ot2-lab-suite/
│
├── README.md
├── AGENTS.md
├── pyproject.toml
├── .env.template
│
├── ot2lab/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   │
│   ├── application/
│   │   ├── session.py
│   │   ├── proposals.py
│   │   ├── history.py
│   │   └── run_gate.py
│   │
│   ├── workflow/
│   │   ├── models.py
│   │   ├── compiler.py
│   │   ├── validation.py
│   │   ├── resources.py
│   │   ├── placement.py
│   │   ├── references.py
│   │   └── methods/
│   │
│   ├── execution/
│   │   ├── emitter.py
│   │   ├── runtime.py
│   │   ├── simulation.py
│   │   ├── runner.py
│   │   └── robot_connection.py
│   │
│   ├── agent/
│   │   ├── conversation.py
│   │   ├── patches.py
│   │   ├── providers.py
│   │   └── skills.py
│   │
│   ├── gui/
│   │   ├── app.py
│   │   ├── adapter.py
│   │   └── labware_svg.py
│   │
│   └── labware/
│
├── configs/
│   ├── robot.yaml
│   ├── profiles/
│   │   ├── home.yaml
│   │   └── work.yaml
│   ├── machines/
│   ├── methods/
│   ├── setups/
│   ├── parameters/
│   └── workflows/
│       ├── demo/
│       ├── bringup/
│       ├── examples/
│       └── experiments/
│
├── skills/
├── labware/
├── scripts/
│   └── manual/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── simulation/
│   ├── gui/
│   ├── agent/
│   └── acceptance/
│
├── docs/
├── raman/
│
└── var/
    ├── builds/
    ├── runs/
    ├── history/
    ├── state/
    └── cache/
```

This is the target structure, not an instruction to move all files immediately.

Use temporary compatibility wrappers to preserve existing script entry points while the new package is introduced.

## Architectural rules

- The workflow compiler must not import NiceGUI, LLM providers or robot transport code.
- The AI agent must not directly issue robot commands.
- The GUI and CLI must use the same deterministic planning and validation functions.
- The OT-2 protocol must be standalone and not require the repository to be installed on the robot.
- Generated protocols must be tied to exact configurations and simulation results.
- Raman analysis remains independent.
- Machine-specific credentials and local settings remain outside Git.
- Avoid adding a database, distributed scheduler or generic agent framework unless a demonstrated requirement makes one necessary.

---

# Phase 0 — Preserve and Baseline the Existing Repository

**Goal:** Establish a recoverable, testable starting point before any redesign.

## Task 0.1 — Complete repository inventory

Inspect:

- `src/agents/dye_demo/`
- `src/printing/`
- `src/sers_engine/`
- `src/protocols/`
- `src/labware/`
- `scripts/`
- `configs/`
- `skills/`
- `tests/`
- `raman/`
- `vision/`
- Existing generated protocols and experiment records.

Record which components are actively called by the NanoDrop GUI, terminal interface and robot runner.

Produce a dependency map and classify files as active, shared, historical, generated or unknown.

**Acceptance:** Every proposed archival candidate has a documented role and dependency assessment.

## Task 0.2 — Protect all uncommitted work

Inspect Git status, including ignored and untracked files.

The previous audits identified substantial uncommitted October 1 development involving the semantic conversation router, compiler, patch engine and logical references.

Prepare a backup of those files before making changes.

With operator approval, establish a baseline branch, commit and tag.

Do not use destructive cleanup commands or rewrite Git history.

**Acceptance:** The original working state can be restored, including files that were previously untracked.

## Task 0.3 — Preserve WORK-specific information

Inventory the WORK laptop's:

- Robot configuration and identification.
- Conda environment and dependency versions.
- Vertex AI and gcloud settings.
- Proxy and network configuration.
- Labware definitions and offsets.
- Experiment records and generated protocols.
- Calibration and physical validation notes.

Back up relevant files without committing secrets.

**Acceptance:** A repository update cannot silently replace WORK settings with HOME defaults.

## Task 0.4 — Establish baseline tests

Run existing tests in a controlled environment.

Classify failures into:

- Existing defects.
- Outdated test expectations.
- Flaky tests.
- Environment-dependent failures.
- Missing simulator dependencies.
- Failures in the current semantic pipeline.

The earlier audits reported a mixture of passing and failing tests. Those results are historical baselines, not proof of the current checkout's status.

Remove routine model calls from test collection, particularly the existing Gemini connectivity test that runs on import.

**Acceptance:** A documented baseline test report exists, with known failures classified and no unintended model or hardware calls.

## Task 0.5 — Record golden execution traces

Use the current NanoDrop protocol and fake OT-2 infrastructure to capture the exact ordered robot commands for representative experiments.

Include:

- Default eight-dilution experiment.
- Dilution-only experiment.
- Print-only experiment.
- Changed dilution factors and volumes.
- Replicates and stacked drops.
- Modified source wells and paper positions.
- Different tip policies.
- Known experimental variations.

Record aspiration, dispensing, flow rates, heights, air gaps, blow-outs, touch-tip operations, dwell times and tip motions.

**Acceptance:** Reproducible baseline traces exist for comparing the new engine against the current implementation.

### Phase 0 completion gate

All three reviewers must confirm that the repository is recoverable, the active dependencies are documented and the baseline is sufficient to detect behavior changes.

---

# Phase 1 — Correct the Existing NanoDrop Execution Boundaries

**Goal:** Fix known issues in the current application before creating the new engine.

## Task 1.1 — Make proposal approval deterministic

Inspect:

`src/agents/dye_demo/session.py`

Modify the approval path so that an LLM interpretation alone cannot approve a pending proposal.

Require an explicit operator approval action tied to the exact proposal and its revision.

Ensure ordinary questions do not modify the approved plan.

Test ambiguous statements, including:

- "Looks good."
- "What would this change?"
- "Explain this proposal."
- "I agree with the reasoning, but change the volume."

**Acceptance:** No plan mutation occurs without a valid explicit approval action.

## Task 1.2 — Create a single authoritative run gate

Inspect all execution entry points, including the GUI Run button, terminal commands and session controller.

Create one run-readiness function that checks:

- Valid and approved workflow.
- No unresolved safety question or blocking proposal.
- Valid current configuration revision.
- Valid compiled operations.
- Successful required simulation.
- Correct machine role.
- Valid robot identity for physical execution.
- Explicit physical-run authorization.
- No unresolved prior robot execution state.

The CLI and GUI must invoke this same function.

**Acceptance:** An execution blocker cannot be bypassed by choosing a different interface.

## Task 1.3 — Enforce semantic compilation

Inspect:

- `src/agents/dye_demo/compiler.py`
- `scripts/build_vial_dilution_print.py`
- `src/protocols/printing/13_ai_agent_dilution_print_demo.py`

Ensure logical print-source selections, placement rules and semantic fields are fully resolved before producing robot execution data.

A standalone builder must either compile semantic inputs correctly or explicitly refuse them.

**Acceptance:** A workflow requesting selected dilutions at specified paper coordinates cannot silently produce the older default print layout.

## Task 1.4 — Eliminate mutable execution artifacts

Replace reliance on shared `_latest.py` artifacts as the authoritative upload target.

Generate a unique protocol artifact for each run.

Bind together:

- Resolved workflow.
- Exact operation list.
- Protocol file.
- Custom labware definitions.
- Simulator result.
- Hashes and run identity.

**Acceptance:** The exact protocol that passed simulation is the one selected for upload.

## Task 1.5 — Repair production-path test coverage

Update the red-team and conversation regression tools to exercise the semantic pipeline used by the current model-backed NanoDrop launchers.

Add tests for:

- Incorrect model classification.
- Approval without authorization.
- Stale proposals.
- Questions containing edit instructions.
- Invalid structured patches.
- Uncompiled semantic fields.
- Conflicting concurrent edits.
- Pending safety questions.

**Acceptance:** Production-path tests cover the previously identified approval and compilation defects.

## Task 1.6 — Regression-test existing NanoDrop behavior

Run GUI, terminal, compiler, protocol and fake-robot tests.

Compare command traces with the Phase 0 baseline.

Do not combine these fixes with a broader code relocation.

**Acceptance:** Known boundary defects are corrected without unapproved changes to liquid-handling motions.

### Phase 1 completion gate

Agents A, B and C must approve all six tasks. The current NanoDrop demo must remain usable before any architectural migration begins.

---

# Phase 2 — Establish the Unified Python Application

**Goal:** Provide one clean software interface without disturbing existing robot behavior.

## Task 2.1 — Create the `ot2lab` package

Create the package structure and public entry points.

Use a root-level installable Python package to support:

`python -m ot2lab`

Keep existing NanoDrop launchers available as compatibility wrappers.

Move code incrementally rather than relocating the entire repository at once.

**Acceptance:** Existing launches continue working and the new package imports successfully.

## Task 2.2 — Create a shared application interface

Expose functions for:

- Loading workflows.
- Resolving configuration.
- Validating workflows.
- Compiling operations.
- Preparing simulation.
- Preparing robot execution.
- Retrieving run status.

Reuse NanoDrop's existing controller and adapter functionality where practical.

Avoid introducing a large service framework before it is needed.

**Acceptance:** The GUI and manual commands can call the same planning functions.

## Task 2.3 — Implement the manual command-line interface

Create these target commands:

```powershell
python -m ot2lab doctor
python -m ot2lab check <workflow.yaml>
python -m ot2lab plan <workflow.yaml>
python -m ot2lab simulate <workflow.yaml>
python -m ot2lab params
python -m ot2lab params --pending
python -m ot2lab gui --simulate --no-model
```

For physical execution, use an explicit command such as:

```powershell
python -m ot2lab run <workflow.yaml> --live
```

The `--live` flag must not override HOME restrictions.

**Acceptance:** The CLI provides consistent command behavior and meaningful failures without importing an LLM.

## Task 2.4 — Separate HOME and WORK environments

Preserve:

**HOME**

- Conda `ai`.
- Gemini API-key authentication where enabled.
- Software development and simulation.
- No physical robot execution.

**WORK**

- Conda `llm`.
- Vertex AI through gcloud ADC.
- Simulation and instrument execution.
- Manual operation without any cloud model.

Use versioned station examples and machine-local settings ignored by Git.

Missing local role information must default to a non-live configuration.

Prevent WORK from silently falling back to HOME API-key authentication.

**Acceptance:** Tests demonstrate that HOME cannot execute a live robot run and WORK can use manual operation without an LLM.

## Task 2.5 — Separate core and optional dependencies

Place the application dependencies in `pyproject.toml`.

Keep NiceGUI and LLM providers optional for core planning and robot operations.

Preserve a separately pinned simulator environment for the deployed OT-2 software and Protocol API 2.15 compatibility.

Do not introduce an Opentrons software upgrade during this migration.

**Acceptance:** Planning and offline simulation can run without importing optional GUI or cloud SDKs.

## Task 2.6 — Preserve standalone manual hardware checks

Retain or rebuild a minimal set of independent manual tools for:

- Tip pickup and return.
- A single dilution or liquid transfer.
- A single paper-printing operation.

Keep these tools independent of the main AI and workflow application so they remain useful for instrument troubleshooting.

**Acceptance:** Basic instrument checks remain possible even if the main application has a software defect.

### Phase 2 completion gate

Reviewers confirm one coherent application interface, offline usability, safe workstation separation and compatibility with existing launchers.

---

# Phase 3 — Consolidate YAML Configuration and Parameter Validation

**Goal:** Make experimental settings easy to change without scattering values across Python modules.

## Task 3.1 — Assign configuration ownership

Create separate configuration responsibilities:

| Configuration | Responsibility |
|---|---|
| Machine | Pipette capabilities and robot/API compatibility |
| Method | Aspiration, dispense, mixing and printing behavior |
| Setup | Deck positions and labware |
| Workflow | Experimental liquids and ordered steps |
| Parameters | Allowed settings, units, bounds and validation evidence |
| Station | HOME/WORK paths, connectivity and provider settings |
| Secrets | Credentials outside version control |

Preserve the existing `configs/` directory naming.

**Acceptance:** Every active setting has one authoritative owner.

## Task 3.2 — Build strict, versioned YAML schemas

Use a declared schema version.

Validate required fields, units, allowed values and references.

Reject unknown configuration fields that would otherwise be silently ignored.

Create migration support for existing NanoDrop configurations and saved experiments.

**Acceptance:** Invalid YAML fails with a clear, actionable error rather than silently falling back to defaults.

## Task 3.3 — Centralize physical parameter constraints

Create a parameter catalog and reusable validation functions.

Distinguish:

1. Hardware limits.
2. Method-specific validated operating ranges.
3. Whole-experiment constraints.

Include limits for:

- Pipette and transfer volumes.
- Air gaps.
- Aspiration and dispense depths.
- Flow rates.
- Mixing parameters.
- Paper release heights.
- Tip policies.
- Well and vial capacities.
- Delay durations.
- Printing positions and occupancy.

Do not rely on a single numerical range when the validity of a setting depends on other parameters.

**Acceptance:** GUI, CLI and AI proposals use the same authoritative validation rules.

## Task 3.4 — Track physical validation evidence

Record validated values separately from theoretical or software-accepted limits.

Investigate the previously identified discrepancies involving:

- Paper printing heights of 0.5 mm versus 1.1 mm.
- Plate aspiration heights of 0.2 mm versus 0.3 mm.
- Any labware offsets not reflected in the existing runner.

Do not arbitrarily declare one value correct.

Allow appropriate exploratory settings within hard constraints, but mark unvalidated values clearly and require operator review.

**Acceptance:** The system reports which settings still require WORK verification.

## Task 3.5 — Add suggestions and configuration overrides

If a value is invalid, show:

- The requested value.
- The applicable constraint.
- Why the request is invalid.
- A suggested correction, when a valid alternative is known.
- Any effects on other parameters.

Add optional command-line trial overrides such as:

```powershell
python -m ot2lab plan experiment.yaml --set print_1.drop_volume_ul=3
```

Only explicitly allowed fields may be overridden.

A suggestion must never automatically change the approved workflow.

**Acceptance:** Parameter changes are transparent, repeatable and reversible.

### Phase 3 completion gate

All three reviewers verify schema behavior, configuration consistency, physical bounds and preservation of validated method settings.

---

# Phase 4 — Build the Canonical Multi-Step Workflow Engine

**Goal:** Replace task-specific script combinations with one composable experiment definition.

## Task 4.1 — Consolidate existing planning components

Evaluate and reuse:

- NanoDrop `plan.py`.
- NanoDrop `compiler.py`.
- NanoDrop `placement.py`.
- NanoDrop `references.py`.
- Existing printing experiment schemas.
- Standard resolver and builder components.
- SERS step models and depth calculations.

Select one canonical implementation for each responsibility.

Do not maintain parallel planners that independently calculate the same physical operations.

**Acceptance:** The resulting architecture has one authoritative compilation path.

## Task 4.2 — Define the initial step types

Implement structured steps with stable identifiers.

Initial types:

- `transfer`
- `dilute_series`
- `mix`
- `print`
- `wait`

Then extend to:

- `pause`
- Finite `repeat`
- Parameter `sweep`

Distinguish independent stock dilution from serial dilution.

Do not interpret arbitrary Python expressions inside YAML.

**Acceptance:** Each step has a validated schema, a compiler implementation and tests.

## Task 4.3 — Implement ordered workflow compilation

Convert the requested experiment into a fully resolved ordered sequence.

For each step, determine:

- Required source and destination wells.
- Liquid volumes.
- Exact aspiration and dispense locations.
- Pipette and tip usage.
- Printing coordinates.
- Timing requirements.
- State changes.

Resolve all logical references before generating robot code.

**Acceptance:** The compiled execution sequence contains no unresolved scientific instructions.

## Task 4.4 — Implement whole-sequence resource accounting

Track resources throughout the entire experiment:

- Stock volume.
- Diluent volume.
- Prepared well contents.
- Remaining sample volumes.
- Tip availability and contamination history.
- Paper occupancy.
- Repeated or stacked droplets.
- Wait and pause requirements.

Do not reset accounting between steps or repeated cycles.

For example, preparing a dilution once and printing it twice must not be treated as preparing two separate dilutions.

**Acceptance:** The compiler catches resource exhaustion and conflicting operations before robot execution.

## Task 4.5 — Support flexible paper-printing patterns

Implement:

- Individual coordinate selection.
- Rows and columns.
- Multiple paper substrates.
- Replicate positions.
- Stacked droplets.
- Explicit overprinting.
- Source-specific droplet numbers.
- Different release heights by position.
- Touch-down and hold parameters where validated.

Preserve existing specialized printing methods until equivalent behavior has been demonstrated.

**Acceptance:** The configuration system can represent the established NanoDrop and specialized paper-printing experiments.

## Task 4.6 — Implement timed waits and pauses

Support ordinary waits inside robot protocols.

Ensure pipette tips and liquids are handled appropriately before long delays.

A paused experiment should preserve its run identity and distinguish waiting for a timer from waiting for operator action.

Verify pause and resume behavior against the installed Opentrons software before enabling it for physical execution.

**Acceptance:** Timed experiments do not depend on the GUI or model service remaining connected.

## Task 4.7 — Add finite repeats and parameter sweeps

Allow users to repeat a sequence a declared number of times.

Expand repeats into a finite, inspectable sequence before execution.

Support experiment-specific sweeps such as:

- Printing height.
- Droplet volume.
- Number of droplets.
- Dilution factor.
- Paper coordinate.
- Time interval.

Every iteration must have clearly identified destinations and resource usage.

**Acceptance:** The compiler can preview the complete expanded experiment with no hidden or indefinite operations.

## Task 4.8 — Reproduce representative historical experiments

Use existing experimental workflows as acceptance cases.

Include:

- Standard NanoDrop dilution and printing.
- Nanoparticle layer followed by a timed wait and dye overprinting.
- Multiple paper substrates.
- Experiments with unequal drops per position.
- Experiments that continue printing onto previously prepared paper.
- Height-ladder experiments.
- Extended time-course printing.

Use the repository's existing experiment records to define expected behavior.

**Acceptance:** Every selected historical workflow can be expressed through the new configuration model without writing a new dedicated protocol.

### Phase 4 completion gate

The three reviewers verify that the new engine reuses existing logic, preserves relevant experimental semantics and correctly accounts for all operations.

---

# Phase 5 — Protocol Generation, Simulation and Reliable Robot Execution

**Goal:** Produce deterministic, reproducible robot protocols and handle execution failures correctly.

## Task 5.1 — Define the atomic execution representation

Introduce one resolved operation representation covering the required pipette and robot commands.

Include operations such as:

- Pick up tip.
- Aspirate.
- Air gap.
- Dispense.
- Blow out.
- Touch tip.
- Move.
- Wait.
- Pause.
- Drop or return tip.

Each operation must specify the exact position, volume and method parameters required for execution.

**Acceptance:** The robot runtime does not need to reinterpret dilution factors, printing intent or placement rules.

## Task 5.2 — Preserve NanoDrop motion compatibility

Create a compatibility method such as:

`nanodrop_current_v1`

Preserve the current:

- Transfer ordering.
- Volume splitting.
- Vial aspiration method.
- Plate dispensing and mixing behavior.
- Tip handling.
- Blow-outs.
- Touch-tip motions.
- Paper-release motions.
- Relevant dwell and flow-rate parameters.

Compare complete command traces with the existing protocol.

**Acceptance:** All unexplained differences are resolved before physical migration.

## Task 5.3 — Generate immutable standalone protocols

Create one protocol per run with a unique run identity.

Include exact compiled operations and required labware information.

Generate a manifest containing:

- Workflow identity and version.
- Git commit and working-tree status.
- Resolved configuration hash.
- Protocol hash.
- Labware hashes.
- Simulator result.
- Machine profile.
- Method profile.
- Operator and run identity.

Never select a mutable latest-generated file as the authoritative execution target.

**Acceptance:** The uploaded artifact is identical to the simulated artifact.

## Task 5.4 — Improve simulation and regression tests

Run generated protocols through:

- The recording fake OT-2.
- The pinned Opentrons simulator compatible with the deployed instrument.
- Existing relevant protocol regression tests.

Compare the full motion trace, not just final liquid volumes.

Include flow rates, air gaps, tip handling, heights, time delays and supported dispense options.

Unsupported required robot commands must fail rather than silently falling back to different physical behavior.

**Acceptance:** New and reference implementations satisfy the required trace-parity tests.

## Task 5.5 — Implement explicit execution states

Use a documented lifecycle such as:

```text
DRAFT
  -> VALIDATED
  -> BUILT
  -> SIMULATED
  -> READY_FOR_WORK_REVIEW
  -> APPROVED_FOR_LIVE
  -> RUNNING
  -> SUCCEEDED / FAILED / STOPPED / OUTCOME_UNKNOWN
```

A software failure, lost network connection or missing response must not automatically be interpreted as a stopped robot.

**Acceptance:** Every run has an explicit and recoverable status.

## Task 5.6 — Implement reconnection and stop handling

Persist the robot run ID before starting execution.

Provide commands to inspect and reconnect to an existing run.

Distinguish:

- Stop requested.
- Stop acknowledged.
- Robot confirmed stopped.
- Communication lost.
- Outcome unknown.

Do not automatically replay a partially completed pipetting experiment.

**Acceptance:** A network interruption cannot silently cause duplicate experiment execution.

## Task 5.7 — Implement physical-state records

Maintain a local record of:

- Used tips.
- Estimated well contents and volumes.
- Printed paper positions.
- Executed operations.
- Run outcome.
- Operator-confirmed material changes.

Differentiate predicted state from physically verified state.

Loading a saved experiment must not automatically restore a claim that the required liquids, paper and tips are physically present.

**Acceptance:** Subsequent runs use valid current-state assumptions and require reconciliation where state is uncertain.

### Phase 5 completion gate

Agents A, B and C must approve simulation, provenance, execution boundaries and failure handling.

Physical equivalence remains pending until WORK commissioning.

---

# Phase 6 — Expand NiceGUI and AI-Agent Functionality

**Goal:** Make the new workflow engine accessible through the existing successful GUI and natural-language interface.

## Task 6.1 — Preserve the current NanoDrop GUI

Maintain:

- Chat interface.
- Current and proposed plan.
- Apply and Discard controls.
- Labware diagrams.
- Plate and paper visualizations.
- Run confirmation.
- Stop controls.
- Experiment reset.
- Experiment history.

Move GUI files only after functional regression tests are available.

**Acceptance:** The existing demonstration remains operational.

## Task 6.2 — Add a visual workflow timeline

Display each experiment step in order.

For example:

```text
1. Prepare dye dilutions
2. Print nanoparticles
3. Wait 30 minutes
4. Print dye onto existing spots
5. Repeat on the next paper column
```

Allow each step to expand into detailed well, volume, tip and printing information.

**Acceptance:** A scientist can understand the complete experiment before execution.

## Task 6.3 — Support GUI operation without AI

Enable users to:

- Load YAML workflows.
- Modify approved parameters.
- Select labware and coordinates.
- Inspect validation errors.
- Preview compiled operations.
- Simulate experiments.
- Execute on WORK with authorization.

The absence of Gemini or Vertex credentials must not disable manual workflow planning.

**Acceptance:** The GUI is useful without an internet connection.

## Task 6.4 — Extend agent patch operations

Enable the AI agent to propose:

- Adding steps.
- Deleting steps.
- Modifying step parameters.
- Reordering steps.
- Changing dilution factors.
- Changing printing patterns.
- Changing timing intervals.
- Selecting existing prepared liquids.

Identify steps by stable IDs rather than fragile list positions.

The AI may propose changes but must not directly edit machine constraints, authorize a run or generate arbitrary executable robot Python.

**Acceptance:** All model-generated modifications pass the same deterministic validation as manual edits.

## Task 6.5 — Update AI skills and adversarial testing

Extend the existing `nanodrop-*` runtime skills for multi-step experiments.

Test requests involving:

- Ambiguous dilution instructions.
- Impossible pipetting volumes.
- Invalid labware positions.
- Conflicting print coordinates.
- Missing reagents.
- Unavailable tips.
- Incorrect or missing timing.
- Unauthorized approvals.
- Attempts to bypass validation.

Run the red team against the current production conversation path.

**Acceptance:** AI flexibility does not bypass approved experimental constraints or operator decisions.

## Task 6.6 — Preserve experiment memory

Keep saved experiment definitions, revisions and useful parameter history.

Separate saved scientific plans from physical instrument state.

Store sufficient provenance to reproduce a previous experiment and determine which configuration actually executed.

**Acceptance:** Saved experiments can be recalled and modified without making incorrect assumptions about the current robot setup.

### Phase 6 completion gate

All three reviewers verify the GUI, AI proposal boundaries, offline operation and multistep planning experience.

---

# Phase 7 — WORK Instrument Commissioning

**Goal:** Verify the migrated engine on the physical OT-2 before replacing the existing protocol.

## Task 7.1 — Confirm instrument configuration

On WORK, verify:

- Robot identity.
- Software and Protocol API compatibility.
- Mounted pipettes.
- Deck positions.
- Labware definitions.
- Tip racks.
- Labware offsets.
- Relevant physical heights.
- Robot connectivity and stop behavior.

Record actual observations rather than copying HOME assumptions.

**Acceptance:** The physical setup matches the execution artifact's declared assumptions.

## Task 7.2 — Execute simple bring-up workflows

Use staged tests:

1. Tip pickup and return.
2. One transfer or dilution using an appropriate test liquid.
3. One printing operation.
4. A small multi-position printing experiment.
5. A complete dilution-and-print experiment.

Check the actual robot motions, transfer behavior and labware clearance.

**Acceptance:** Basic operations pass physical tests before more complex workflows are attempted.

## Task 7.3 — Test complex experiments incrementally

Physically verify selected advanced workflows:

- Dilution followed by printing.
- Nanoparticle printing followed by dye overprinting.
- Timed waits.
- Printing on multiple paper substrates.
- Different validated droplet volumes.
- Different printing heights.
- Replicates and stacked droplets.
- Continued printing using previously prepared liquids.

Use experiment-appropriate observations to document physical performance.

**Acceptance:** Required workflow families have recorded physical validation evidence.

## Task 7.4 — Verify operational recovery

Test appropriate non-destructive failure scenarios, including:

- GUI closure.
- Loss of optional cloud-model access.
- Controlled connection interruption.
- Stop requests.
- Run-status reconnection.
- Reconciliation after an interrupted experiment.

Do not test hazardous failure conditions by deliberately creating uncontrolled robot motion.

**Acceptance:** A communication or application failure cannot be mistaken for confirmed physical completion.

## Task 7.5 — Promote the new engine

Only after required physical tests pass:

- Make the new workflow engine the default.
- Preserve a tested rollback release.
- Record the exact software and method versions.
- Keep historical protocol artifacts for reproducibility.
- Update user-facing commands.

**Acceptance:** The replacement executes the required experiments without losing validated functionality.

### Phase 7 completion gate

Software reviews are necessary but not sufficient.

All three reviewers must approve the recorded evidence, and the operator must confirm physical acceptance before promotion.

---

# Phase 8 — Repository Cleanup, Raman Separation and Documentation

**Goal:** Retire redundant code without losing scientific methods, historical data or reproducibility.

## Task 8.1 — Classify obsolete workflow families

Reassess:

- Older NanoDrop implementations.
- Numbered printing protocols.
- Older chat-agent entry points.
- Superseded dilution builders.
- Older SERS planning components.
- Duplicate skills.
- Historical generated protocols.
- Unused scripts and test fixtures.

Use current dependency checks and verified replacement coverage.

Do not assume older means unused.

**Acceptance:** Every archival candidate has an identified replacement or explicit retention reason.

## Task 8.2 — Archive superseded code safely

Create an archive outside the active checkout.

Preserve original paths and produce a manifest containing:

- Original filename.
- Source commit.
- Checksum.
- Reason for archival.
- Replacement.
- Restoration procedure.

Archive related source, tests, configurations and documentation together.

Do not rewrite Git history or delete original scientific records without preservation.

**Acceptance:** Archived workflow families remain recoverable.

## Task 8.3 — Keep Raman independent

Preserve the existing Raman analysis system and its numerical behavior.

Provide separate:

- README.
- Analysis commands.
- Configuration documentation.
- Tests.
- Dependencies.
- Data-retention policy.

Create a standard run-layout record mapping the OT-2 paper position to the intended printed sample.

Raman scan IDs and acquired spectra remain separate until actual measurement data can be linked.

**Acceptance:** Raman analysis works independently, while experiment records provide a reliable future integration point.

## Task 8.4 — Consolidate user documentation

Produce concise operational guides:

- Root `README.md`.
- HOME setup.
- WORK setup.
- Python and CLI commands.
- Custom workflow YAML.
- AI-agent usage.
- NiceGUI usage.
- Instrument validation.
- Raman analysis.

Each command should be copied from tested examples, not merely described.

Separate historical audit reports from active operating instructions.

**Acceptance:** A user can find the correct entry point without navigating multiple generations of documentation.

## Task 8.5 — Implement continuous regression testing

Add automated checks for:

- Unit tests.
- Workflow compilation.
- Golden robot-motion traces.
- Schema validation.
- Package import boundaries.
- Offline operation.
- AI proposal boundaries.
- Generated artifact provenance.
- GUI regressions where supported.

Keep hardware and live-model tests opt-in.

Tests should never unexpectedly start the robot, call a paid model or modify tracked files.

**Acceptance:** Future code changes receive automated warnings when they break previously validated functionality.

## Task 8.6 — Complete the final repository audit

The three reviewers independently inspect the complete result.

Confirm:

- No active dependency on archived code.
- No duplicated authoritative planning engine.
- No unapproved physical-motion changes.
- No unprotected experimental records.
- No accidental secret exposure.
- No obsolete documentation presented as current.
- All core manual commands function without the LLM.
- The NanoDrop GUI remains operational.
- Physical validation records identify the active method versions.

**Acceptance:** The final application is smaller, documented, reproducible and suitable for continued development.

---

# 4. Required End-to-End Acceptance Experiments

The redesign is not complete until these scenarios are supported.

| Scenario | Expected behavior |
|---|---|
| Standard NanoDrop | Prepare independent dilutions and print onto paper |
| Dilution only | Prepare samples without printing |
| Print only | Print from already available, explicitly declared liquids |
| Multiple steps | Dilute, print, wait, dilute again and print again |
| Repeated printing | Reuse remaining prepared liquid with correct volume accounting |
| Nanoparticle layering | Print nanoparticles, wait and overprint dye |
| Multiple papers | Allocate coordinates across separate substrates |
| Replicates | Print separate replicate spots |
| Stacked drops | Deposit multiple droplets onto intentionally shared positions |
| Parameter sweep | Vary controlled parameters between positions |
| Long wait | Preserve correct tip handling, timing and run state |
| Offline operation | Plan and simulate with no model or internet |
| Invalid AI request | Return a validation explanation without executing |
| Lost connection | Reconnect to the existing run without duplicate execution |
| Stopped experiment | Mark uncertain physical state and prevent unsafe continuation |
| Saved experiment | Restore the workflow without claiming physical materials are present |

Each scenario requires compilation tests, resource-accounting checks and relevant simulation coverage. Scenarios involving new physical methods require WORK qualification.

---

# 5. Example Target Workflow

The system should eventually accept a configuration similar to the following.

**Illustrative future YAML — not an existing executable configuration.**

```yaml
schema_version: ot2-workflow/v1

name: nanoparticle_dye_overprint

machine_profile: ot2_p20
method_profile: nanodrop_current_v1
setup: standard_paper_printing

liquids:
  dye:
    location: vial_rack:A2
    loaded_volume_ul: 4000

  water:
    location: vial_rack:A1
    loaded_volume_ul: 4000

  nanoparticles:
    location: dilution_plate:A9
    loaded_volume_ul: 300

steps:

  - id: prepare_dye
    type: dilute_series
    source: dye
    diluent: water
    factors: [2, 5, 10]
    final_volume_ul: 150
    destinations:
      - dilution_plate:A11
      - dilution_plate:B11
      - dilution_plate:C11

  - id: print_nanoparticles
    type: print
    sources:
      - nanoparticles
    destinations:
      - paper:A1
      - paper:B1
      - paper:C1
    drop_volume_ul: 5

  - id: drying_interval
    type: wait
    duration_s: 1800

  - id: print_dye
    type: print
    sources:
      output_of: prepare_dye
    destinations:
      - paper:A1
      - paper:B1
      - paper:C1
    overprint: true
    drop_volume_ul: 5
```

The compiler would determine exact transfers, well references, tip handling and robot motions before simulation.

All associated volumes, method settings and physical constraints would still require validation.

---

# 6. AI Reviewer Prompts

The following prompts should be used as separate subagent instructions. Each reviewer receives the current task specification, relevant source files, implementation diff, acceptance criteria and test results.

## Agent A — Architecture Review Prompt

> You are the independent Software Architecture and Integration Reviewer for the OT-2 Lab Suite.
>
> Review the assigned task for correctness, compatibility, dependency integrity, configuration consistency and maintainability.
>
> Treat the current NanoDrop demonstration as a working system whose behavior must be preserved unless an explicitly approved change is documented.
>
> Identify duplicate implementations, hidden import dependencies, architectural violations, unnecessary abstractions and impacts on existing workflows.
>
> Do not modify implementation files.
>
> Produce a report containing:
> 1. Task ID and files examined.
> 2. Requirements satisfied.
> 3. Defects, with severity and file locations.
> 4. Missing tests or integration evidence.
> 5. Concrete required corrections.
> 6. PASS, CHANGES REQUIRED or BLOCKED.
>
> Review Agent C's findings in the cross-review round, identifying any missing architecture or integration tests.
>
> Do not approve work merely because the code executes or existing tests pass.

## Agent B — OT-2 Hardware and Scientific Review Prompt

> You are the independent OT-2 Hardware, Liquid Handling and Scientific Execution Reviewer.
>
> Review the assigned task for physical correctness and preservation of established laboratory methods.
>
> Check pipette compatibility, liquid volumes, aspiration and dispense depths, tip contamination, well capacities, labware coordinates, printing heights, timing, run recovery and resource accounting.
>
> Compare new robot operations with the established NanoDrop behavior wherever applicable.
>
> Clearly distinguish software evidence from physical validation.
>
> Do not modify implementation files or issue robot commands.
>
> Produce a report containing:
> 1. Task ID and physical operations affected.
> 2. Physical constraints checked.
> 3. Any changed robot motion or laboratory assumption.
> 4. Missing simulation or physical evidence.
> 5. Required corrections.
> 6. PASS, CHANGES REQUIRED or BLOCKED.
> 7. Whether WORK validation remains pending.
>
> Review Agent A's findings in the cross-review round, checking whether architectural changes alter physical execution.
>
> Never infer that a motion is physically validated merely because it passes simulation.

## Agent C — Testing and Reliability Review Prompt

> You are the independent Testing, Reliability and User Workflow Reviewer for the OT-2 Lab Suite.
>
> Review the assigned task against its specified requirements, acceptance criteria and actual test evidence.
>
> Evaluate unit tests, integration tests, regression coverage, current semantic AI routing, NiceGUI behavior, manual CLI execution and HOME/WORK separation.
>
> Examine invalid inputs, interrupted runs, malformed proposals, stale state, ambiguous approvals and failure recovery.
>
> Do not modify implementation files or call the physical robot.
>
> Produce a report containing:
> 1. Task ID and functionality tested.
> 2. Test commands and actual observed results.
> 3. Missing positive and negative test cases.
> 4. Reproducibility problems.
> 5. User-facing workflow failures.
> 6. Concrete required corrections.
> 7. PASS, CHANGES REQUIRED or BLOCKED.
>
> Review Agent B's findings in the cross-review round, ensuring that physical-risk scenarios have appropriate verification plans.
>
> Do not approve a task without evidence that its acceptance criteria were met.

---

# 7. Coordinator Prompt for Running the Three-Agent Process

This prompt can be supplied to the primary coding agent at the start of a phase.

> You are the Implementation Coordinator for the OT-2 Lab Suite redesign.
>
> Work from the Master Implementation Plan and implement one numbered task at a time.
>
> Before editing, inspect the actual current repository and verify that the paths, functions and assumptions in the task specification remain accurate.
>
> For each task:
>
> 1. Identify the exact source files involved.
> 2. Describe the existing behavior.
> 3. Define the minimal necessary implementation change.
> 4. Identify possible effects on the current NanoDrop demonstration.
> 5. Implement the task in an isolated change set.
> 6. Run relevant offline and simulation tests.
> 7. Save the resulting diff and test evidence.
> 8. Launch three independent reviewer agents: Architecture, OT-2 Hardware and Scientific Execution, and Testing and Reliability.
> 9. Require all three to independently inspect the same evidence.
> 10. Cross-review their findings using the designated review relationships.
> 11. Resolve all blocking and required corrections.
> 12. Repeat affected tests and obtain final verdicts.
> 13. Record unresolved physical validation tasks separately.
> 14. Present the completion report before proceeding to the next numbered task.
>
> Do not substitute a single model's three opinions for three independent agent reviews. If independent subagents are unavailable, explicitly report this limitation and leave the review requirement pending.
>
> Never claim tests passed unless they actually ran successfully.
>
> Do not start hardware experiments automatically.
>
> Do not alter WORK machine configurations or scientifically validated physical parameters without authorization.
>
> Do not archive files until replacement functionality is confirmed.
>
> Preserve compatibility with the current working NanoDrop demonstration.
>
> Treat physical validation as an explicit WORK-only milestone.
>
> A task is completed only when its implementation, acceptance tests and three review records are available.

---

# 8. Project-Level Completion Criteria

The redesign is considered complete only when the following conditions are satisfied:

- [ ] The original NanoDrop functionality has been preserved or demonstrably replaced.
- [ ] One authoritative workflow engine exists.
- [ ] YAML can define multistep experiments without new protocol scripts.
- [ ] Dilution, printing, mixing, transfers and waits are supported.
- [ ] GUI, AI and manual commands use the same compiler and validators.
- [ ] AI approval cannot authorize robot execution by itself.
- [ ] HOME and WORK environments remain correctly separated.
- [ ] Manual planning and execution paths do not require an LLM.
- [ ] Machine and method parameters have clear ownership and validation evidence.
- [ ] Generated protocols are immutable and tied to simulation results.
- [ ] Software execution state and physical state are tracked appropriately.
- [ ] Interrupted runs do not automatically restart.
- [ ] Historical experimental workflows have appropriate replacement coverage.
- [ ] Physical methods are qualified on WORK before promotion.
- [ ] Raman remains independent and scientific data are preserved.
- [ ] Superseded scripts and agents are archived with recovery instructions.
- [ ] Documentation matches real, tested commands.
- [ ] Regression testing runs without unintended hardware or cloud calls.
- [ ] Every completed implementation task has three independent reviewer approvals.

# 9. Recommended First Implementation Batch

Begin with these tasks only:

**Batch A — Preserve and stabilize the current system**

1. Task 0.1 — Repository inventory.
2. Task 0.2 — Protect uncommitted work.
3. Task 0.3 — Preserve WORK-specific information.
4. Task 0.4 — Baseline tests.
5. Task 0.5 — Golden motion traces.
6. Task 1.1 — Deterministic approval.
7. Task 1.2 — Unified run gate.
8. Task 1.3 — Semantic compilation enforcement.

Complete all three-agent reviews before proceeding.

Do not begin aggressive directory relocation or archive operations during Batch A.

**Batch B — Establish the new application interface**

Proceed through the remaining Phase 1 tasks, then Phase 2.

**Batch C — Introduce reusable experiment definitions**

Complete Phases 3 and 4 before modifying the physical robot protocol.

**Batch D — Verify execution and extend intelligent automation**

Complete Phases 5 and 6, followed by WORK qualification.

**Batch E — Consolidate the repository**

Only then complete repository cleanup, Raman separation, documentation and final release verification.

---

# Final Engineering Principle

The intended end state is not simply an AI agent that writes OT-2 scripts.

It is a laboratory automation application in which **scientists describe or configure experiments, a shared deterministic compiler constructs and validates the execution sequence, and the OT-2 performs the approved physical operations.**

LLMs and AI agents provide flexibility in experiment construction. YAML provides reproducibility. Python provides deterministic control. Independent testing and physical validation provide confidence that the intended experimental method is actually preserved.

The three-reviewer process is designed to ensure that progress in one area—such as conversational flexibility or software consolidation—does not silently compromise scientific behavior, physical execution or the usability of the existing demonstration.

**Implementation begins by protecting the system that already works, not by replacing it.**