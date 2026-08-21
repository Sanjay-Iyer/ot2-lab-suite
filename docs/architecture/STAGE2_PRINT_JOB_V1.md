# Stage 2: scientist-facing `PrintJobV1`

Stage 2 adds a strict boundary between scientific intent and resolved physical
execution. It does not change either proven printing protocol.

```text
validated labware definition
             |
             | stable reference
             v
         PrintJobV1
             |
             | deterministic job compiler
             v
   existing typed workflow patch
             |
             | existing validation + v9/v12 resolver
             v
    ResolvedPrintPlanV1
```

## Schema ownership

`PrintJobV1` owns logical material identity, a stable substrate reference,
scientific target/design selection, liquid volume, layers, replicate meaning,
and scientifically relevant ordering intent. The substrate reference contains
the load name, namespace, version, validated definition SHA-256, and registered
template ID. It never copies the 96 well definitions or their coordinates.

The trusted existing workflow configuration continues to own source deck slot
and well, pipette and mount, tip strategy, flow rates, aspiration/dispense
heights, pre-air chase, trailing air gap, piston displacement, push-out,
blow-out, protocol/API versions, and safety capacity. `ResolvedPrintPlanV1`
records those values only after deterministic resolution.

## V1 scope

- `well_selection`: Cartesian rows and replicate columns with per-row layers,
  compiled through `plate_well_direct_v9`.
- `four_clover`: the existing symmetric or explicit four-point geometry and
  reference-well center-offset placement convention, compiled through the
  trusted v12 four-clover profile.

Layers are repeated depositions at the same targets. Standard replicate columns
are distinct experimental placements. Clover centers are distinct design
instances. These concepts are represented independently and their declared
counts must agree with the selected placements.

## Temporary Stage 2 profile coupling

`MachineProfileV1` does not exist yet. The compiler therefore selects one
trusted existing profile per proven family and requires the job's logical
material and substrate to match that profile:

- v9 golden profile: material `sample`, substrate `paper_print_96_flat`.
- v12 air-chase profile: material `BP`, substrate `paper_print_96_flat`.

This keeps `sample` and `BP` logical in the job while source slot/well and all
liquid-handling implementation values remain downstream. A later machine
profile/material registry should replace this narrow compatibility check.

## Identity and evidence chain

`job_id` is the SHA-256 of canonical scientific content. Human labels,
description, and inspection metadata are not identity-bearing. The compiled
plan stores that digest in `provenance.source_job_sha256`.

The plan's established `plan_id` remains the hash of the resolved physical plan.
The new cross-artifact link is excluded from that calculation so the existing
Stage 1 plan identities remain unchanged:

```text
standard: a36c314184c15eb94eb8a8cb2ccf7a492405f4cfe66d1a4a12bdb9cd64bbad0a
clover:   664c92e97743239aadb677566c673a0e8d63fa3b8fb5e34a86f16da7c7695ab7
```

## Deliberately deferred

Stage 2 does not connect the Printing Agent to `PrintJobV1`, change runtime
printing skills, add broad agent tools, implement `MachineProfileV1`, modify the
Labware Specialist, generate free-form protocol Python, or alter robot execution.
