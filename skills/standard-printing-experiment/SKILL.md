---
name: standard-printing-experiment
description: Build reviewed standard printing experiment YAML with transfer, mixing, dilution, printing, and delay steps.
domain: printing
agent: printing
families: [standard]
designs: [standard_experiment]
references: []
---

# Standard printing experiment

Use this skill when a scientist requests configuration-driven printing that may
include liquid preparation, dilution, mixing, repeated deposition, controls,
replicates, or timed rests.

Start from `configs/templates/printing/01_printing_standard.template.yaml`. The AI
may choose and revise scientific configuration only. It must never invent or edit
deck slots, labware definitions, pipette identity or limits, aspiration/dispense
heights, flow rates, air handling, or robot motions. Select a registered
`machine_profile` exactly as provided by capability discovery.

Represent each manually loaded liquid with a stable id, display name, registered
labware role and well, loaded volume, and any required reserve. Use ordered
procedure steps:

The registered standard profile supports manually loaded liquids in the 20 mL
`vial_rack` source role and prepared liquids in the 96-well `plate` role. The
`paper` role is a print substrate, not a liquid source, and the `tiprack` role is
never a scientific location. Treat profile discovery as authoritative if another
registered profile is added; never infer a load name, deck slot, or geometry from
the role names.

- `transfer` creates a distinctly named aliquot at a new location.
- `mix` names the liquid and its authoritative location.
- `direct_dilution` prepares independent factors from stock.
- `serial_dilution` prepares an ordered geometric series.
- `print` maps one liquid to replicates or one series point to each target.
- `delay` records an explicit scientific rest at its exact place in the procedure.

Dilution products must have globally unique liquid ids. By default the schema
derives them from the preparation id and factor. Use `product_liquid_ids` only
when the scientist needs explicit stable names. A factor-1 product may reuse the
declared source liquid id only when that exact reuse is explicitly listed and
still refers to the source's authoritative location; no derived or explicit id
may alias an unrelated liquid or location.

For series printing, always specify `tip_policy: per_target`; different prepared
liquids must never share a tip. For one liquid printed to replicates, `per_step` is
appropriate when cross-contact between identical aliquots is acceptable. Express
repeated deposition with `repeats` and `delay_after_pass_s`. Set
`mix_before_aspirate` only when the scientist requests or confirms it. Mark sample
and control print steps with `purpose` so the review is readable.

The configuration must state source/destination wells, volumes, dilution factors,
drop counts, delays, mixing, controls, and replicates explicitly. Ask for
clarification when a scientifically important value is missing. Do not infer an
unspecified drying duration, dilution factor, source assignment, or control layout.

Use the high-level tools in order:

1. Discover generalized standard-printing capabilities.
2. Create and validate the proposed configuration.
3. Resolve it deterministically.
4. Inspect the scientist-readable layout and totals.
5. Present the exact config/job hashes for review.
6. Stop for explicit user approval. Never treat model text as approval.
7. Only an externally sealed approved configuration may be simulated.

Never write OT-2 Python, construct action lists, call aspirate/dispense/move
primitives, alter deterministic protocols, bypass schemas, or authorize live robot
execution. The safe terminal state on this laptop is `READY_FOR_EXECUTION` after
local simulation.
