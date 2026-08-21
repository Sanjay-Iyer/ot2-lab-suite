# ResolvedPrintPlanV1 (Stage 1)

`ResolvedPrintPlanV1` is the canonical, deterministic boundary between an already
resolved printing experiment and a future protocol/execution adapter. Stage 1 only
projects the trusted v9 and v12 implementations into this shared data model. Neither
protocol consumes the plan, and no physical execution path has changed.

## Implemented path

```text
CURRENT EXECUTION
-----------------
Standard v9 configuration ──→ existing v9 protocol
Clover v12 configuration  ──→ existing v12 protocol

PARALLEL STAGE 1 REPRESENTATION
-------------------------------
Standard v9 request/config
  └─→ registered request/config resolution
      └─→ v9 _layer_plan()
          └─→ plate-well adapter
              └─→ ResolvedPrintPlanV1

Clover v12 request/config
  └─→ registered request/config resolution
      └─→ registered four-clover adapter
          └─→ v12 _resolve_clovers() + _print_order()
              └─→ ResolvedPrintPlanV1
```

The v9 adapter mirrors the protocol's existing layer/row/column loop because v9 has
no standalone deposit-order helper. Golden normalization and exact-motion simulation
tests guard that small projection against drift. The clover adapter directly reuses
the registered production geometry adapter, which delegates to the protocol's current
geometry and ordering functions.

## Schema boundary

`ResolvedPrintPlanV1` contains:

- `schema_version`, `plan_id`, and `workflow_id` for stable identity;
- provenance hashes/references for the resolved config, source request, registered
  config, source protocol family, builder version, and deterministic adapter;
- resolved machine configuration: OT-2/API, pipette and capacity, mount, source
  definitions, deck slots, labware, calibrated heights, tip strategy, and flow rates;
- one ordered `deposits` list of `DepositInstructionV1` objects;
- workflow-level timing and order mode;
- validated totals for deposits, liquid, air, piston displacement, delays, sources,
  layers, replicates, and clovers.

Each `DepositInstructionV1` contains a continuous sequence index, source and
destination-labware references, a discriminated destination, physical deposition
components, scientific provenance, and exact before/after timing.

Well destinations retain the named well, row, column, and both paper-local and OT-2
deck coordinates. Clover destinations retain the reference well and its coordinates,
center translation, resolved paper/deck center, D1-D4 point offset, and final
paper/deck coordinate. Well-grid and four-clover provenance are discriminated so
meaningless clover fields are not forced onto standard deposits.

The deposition model deliberately keeps these concepts separate:

```text
pre-air chase + liquid + trailing anti-drip air = piston dispense
```

Push-out and blow-out remain independent fields and are not folded into that sum.

## Canonical identity and validation

Canonical serialization uses UTF-8 JSON with recursive string keys, sorted mapping
keys, no insignificant whitespace, and non-finite numbers forbidden. `plan_id` is the
lowercase SHA-256 of the complete meaningful plan payload excluding `plan_id` itself,
which avoids a circular hash. Parsing verifies that the stored identity still matches.

Independent plan validation rejects unknown fields, empty deposits, discontinuous
sequence or layer indexes, bad well/design-point enums, nonpositive liquid, negative
air/timing, inconsistent piston displacement, pipette-capacity violations, missing or
inconsistent coordinates, unknown source/destination references, inconsistent totals,
and identity tampering.

Stable examples live at:

- `tests/fixtures/printing/standard_resolved_plan.json`
- `tests/fixtures/printing/clover_resolved_plan.json`

## Scientific and machine configuration

Deposit destinations, volumes, layers, replicate/design membership, geometry, and
ordering are scientific/experimental facts. Pipette model and capacity, mount, deck
slots, labware identities, source-well mapping, aspiration/parking/dispense heights,
tip handling, and flow rates are machine facts. Stage 1 records both because the plan
must describe exact current behavior, but keeps machine facts under a distinct
`machine` object so a future `MachineProfileV1` can own them without redesigning the
deposit abstraction.

## Future path — not implemented

```text
Natural language
↓
PrintJobV1
↓
deterministic compiler
↓
ResolvedPrintPlanV1
↓
plan validation
↓
protocol generation
↓
simulation
↓
approval / handoff
↓
execution
```

Stage 1 does not implement `PrintJobV1`, a general geometry engine,
`MachineProfileV1`, handoff/approval manifests, plan-consuming protocol generation,
or live execution changes.

## Paper significance

The plan demonstrates that named-well printing and continuous clover geometry can be
normalized into one deterministic representation of physical laboratory actions. It
creates a reviewable and hashable boundary: AI/scientific reasoning may eventually
produce high-level jobs, while laboratory execution receives only deterministic,
validated instructions.
