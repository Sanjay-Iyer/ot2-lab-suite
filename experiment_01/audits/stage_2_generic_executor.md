# Stage 2 - Structured schema, deterministic resolver, generic executor

Recorded on 2026-08-20 on the HOME simulation laptop. No robot connection, upload,
HTTP run, SSH run, or `--live` command was used at any point.

## What Stage 2 had to provide

Stage 1 froze an independently hand-written reference for Experiment 01. Stage 2
builds the **reusable** half of the architecture: a strict structured schema for
experimental intent, a deterministic resolver from that intent to canonical
physical actions, and one trusted OT-2 executor that performs those actions and
nothing else.

The hard constraint is that none of it may know Experiment 01.

## Schema decision: extend, or add a sibling family?

Stage 0 recorded the pre-existing `PrintJobV1` / `ResolvedPrintPlanV1` pair as
"extend additively or introduce a compatible standard-workflow intent model".
Re-inspecting them at implementation time settles the question:

- `src/printing/schemas/jobs.py:173` fixes `materials` at
  `min_length=1, max_length=1`, so a job describes exactly **one** material.
- `PrintJobV1` has a single `deposition: DepositionIntentV1` - one material, one
  volume - and its patterns are `well_selection` or `four_clover`.
- `src/printing/schemas/plans.py` resolves to `deposits: list[DepositInstructionV1]`
  only. There is no transfer, no mix, no delay, and no notion of a liquid produced
  during the run.
- `PlanProvenanceV1` pins `source_protocol_family` to
  `plate_well_direct_v9 | four_clover_v12`, both of which are frozen, golden-tested
  behaviours.

Experiment 01 needs four declared liquids, sixteen prepared intermediates, ordered
preparation, mixing, repeated deposition, and rests. Widening `PrintJobV1` to carry
that would change the identity of every existing golden job and plan fixture, for no
benefit to the design-printing families that use it.

**Decision: add a sibling schema family, reuse everything else.** The new models live
beside the old ones in the same package, use the same `extra="forbid"`, frozen,
`allow_inf_nan=False` conventions, and share `src/printing/canonical.py` for
canonical bytes and SHA-256. The old pair is untouched.

| Paper role | This workflow | Pre-existing design-printing workflow |
|---|---|---|
| Scientist-facing job schema | `PrintExperimentJobV1` | `PrintJobV1` |
| Canonical resolved plan | `ResolvedExperimentPlanV1` | `ResolvedPrintPlanV1` |
| Trusted executor | `01_printing_standard.py` | `09_plate_well_direct_...v9.py`, `12_four_clover...v12.py` |

## Files added

| File | Purpose |
|---|---|
| `src/printing/schemas/experiments.py` | `PrintExperimentJobV1`, `ResolvedExperimentPlanV1`, and the canonical action models (`LOAD_LABWARE`, `LOAD_PIPETTE`, `TRANSFER`, `MIX`, `PRINT`, `DELAY`). |
| `src/printing/standard/capabilities.py` | Exact `Fraction` arithmetic: `split_transfer`, `serial_cascade`, `direct_dilution_volumes`. |
| `src/printing/standard/resolver.py` | `PrintExperimentJobV1 -> ResolvedExperimentPlanV1`, plus every semantic and physical validation. |
| `src/printing/standard/loader.py` | Strict YAML parsing and machine-profile resolution. |
| `src/printing/standard/equivalence.py` | Canonical / physical / structural traces, SHA-256 fingerprints, semantic diff. |
| `src/printing/standard/review.py` | Human-readable pre-approval rendering. |
| `src/printing/standard/builder.py` | Injects one approved plan into the trusted executor; local simulation; artifact hashing. |
| `src/protocols/printing/01_printing_standard.py` | The generic trusted OT-2 executor. |
| `configs/machines/ot2_standard_printing_p20_v1.yaml` | Laboratory-owned machine profile. |

## The experiment vocabulary

`experiment.procedure` is one ordered list of discriminated steps:

```text
serial_dilution | direct_dilution | transfer | mix | print | delay
```

There is no "column" concept, no fixed row count, and no default liquid. A print
step declares its source (`kind: series` or `kind: liquid`), its targets, its droplet
volume, how many repeats, how long to rest after each pass, whether to mix before
aspirating, and its tip policy. Repeated deposition and rests therefore fall out of
configuration rather than a special case:

- `repeats: 1, delay_after_pass_s: 0` -> one pass, no rest;
- `repeats: 1, delay_after_pass_s: 300` -> one pass, then a five-minute rest;
- `repeats: 3, delay_after_pass_s: 300` -> pass, rest, pass, rest, pass, rest.

Column 1 and Column 2 of Experiment 01 are the first and third of those, using the
same rule. Neither is a branch in the executor.

## Machine profile separation

`configs/machines/ot2_standard_printing_p20_v1.yaml` holds deck slots, labware
identities, calibrated aspiration heights, well diameters, and the physically
validated droplet release profile. An experiment configuration references it by
path; the loader expands the reference before validation so the hashed job is
identical either way.

This is the mechanism that stops an agent inventing safety-critical geometry. The
0.5 mm standoff, the 0.2 mm plate aspiration height, the 1.5 uL trailing air gap,
the 3.0 uL push-out, and the 3.0 uL/s flow rates are laboratory facts recorded once,
with their provenance in comments.

## Deterministic arithmetic

`split_transfer` fills greedily to the pipette maximum, then rebalances the last two
chunks evenly if the greedy tail would fall below the pipette minimum. For every
Experiment 01 volume this degenerates to plain greedy splitting, which is what the
frozen static reference does; for a volume such as 20.5 uL it returns
`(10.25, 10.25)` instead of an unpipettable `(20, 0.5)`. Every chunk is asserted to
be inside the pipette range and the sum is asserted exact, in `Fraction` arithmetic.

`serial_cascade` back-calculates from the last well so that the required original
stock is derived rather than assumed. Eight twofold points retaining 30 uL each need
59.765625 uL of stock - close to the ~60 uL the scientific brief allocates, with no
sub-microlitre transfer anywhere.

## What the resolver refuses

Fail-closed checks, all raising `ExperimentResolutionError` before anything is built:

- unknown labware role, or a well that does not exist in that labware definition;
- aspirating from a substrate, or printing onto something that is not a substrate;
- a liquid that is neither declared nor produced by an earlier step;
- a derived preparation product whose name collides with an existing liquid;
- assigning two different liquids to the same well;
- a droplet below the pipette minimum, or droplet-plus-air above its maximum;
- a mix volume outside the pipette range;
- a transfer that cannot be split into in-range chunks;
- a series whose length does not match its print targets;
- an aspiration or mix from a nominally dry location, replayed action by action
  against well cross-sections and the configured aspiration heights;
- a declared source finishing below its reserved minimum volume;
- a second deposit onto a substrate well with no intervening drying rest, unless
  `experiment.policies.require_drying_delay_between_deposits` is explicitly disabled.

Structural validation - well patterns, positive volumes, integer repeats,
non-negative delays, unique ids, unknown keys - is enforced by the pydantic models
themselves, so an invalid configuration cannot even be constructed.

## The generic executor

`src/protocols/printing/01_printing_standard.py` contains no experiment values. Its
`run()` walks the embedded plan and performs each action exactly as written. Before
moving, `verify_plan()` rejects a plan with broken sequence numbering, an unsupported
action, totals that do not describe the action list, a labware role that is never
loaded, an unknown height reference, or a volume outside the loaded pipette's range.

The base file ships a placeholder plan - one 5 uL drop of a liquid called
`placeholder_liquid` from a plate well onto substrate A1 - so the trusted file
simulates standalone. It shares no scientific content with Experiment 01.

The physical release sequence (aspirate, trailing air gap, single piston dispense of
liquid plus air with push-out, blow-out, post-dispense dwell) and the tip discipline
(one tip per group, tip dropped before every rest) match the frozen static reference
action for action.

## Verification

```text
tests/test_standard_printing_capabilities.py   11 passed
tests/test_standard_printing_schemas.py        36 passed
tests/test_standard_printing_resolver.py       29 passed

76 passed total
```

All resolver tests use experiments deliberately unrelated to Experiment 01,
including varied dilution geometries, single- and multi-drop rows, vial and plate
sources, delays on and off, mixing on and off, same-row targets, identity-collision
attacks, and an over-capacity tip request.

Regression baseline after Stage 2, all passing:

```text
tests/test_print_jobs_v1.py tests/test_resolved_print_plans.py
tests/test_printing_schemas.py tests/test_printing_registry.py
tests/test_printing_golden_baselines.py tests/test_printing_tools.py
tests/test_printing_skills.py tests/test_printing_agent_print_jobs.py
tests/test_printing_experiment_workflow.py
tests/test_experiment_01_geometry_baseline.py
tests/test_experiment_01_static_ground_truth.py

165 passed
```
