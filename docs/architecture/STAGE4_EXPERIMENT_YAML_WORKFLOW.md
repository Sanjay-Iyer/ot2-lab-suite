# Stage 4: experiment YAML and approval workflow

## Architectural rule

The AI configures deterministic laboratory automation. It does not generate or
rewrite robot-motion Python for each experiment.

The scientist-facing approval chain is:

```text
natural-language request
  -> registered printing skill
  -> deterministic template instantiation
  -> versioned PrintingExperimentConfigV1 YAML
  -> strict PrintJobV1 scientific validation
  -> scientist review and explicit approval of the config SHA-256
  -> ResolvedPrintPlanV1
  -> existing v9 or v12 Python builder
  -> exact-artifact local simulation
  -> READY_FOR_EXECUTION
```

No Stage 4 function contacts an OT-2. `READY_FOR_EXECUTION` is the terminal state.

## Existing architecture audit

Before Stage 4, the repository already had four strong deterministic layers:

- `configs/printing/*.yaml` contained complete runtime configuration for historical
  and current protocol families, including v9 exact-well and v12 four-clover paths.
- `PrintJobV1` captured sealed scientific intent without deck, pipette, tip, air, or
  coordinate fields.
- `job_compiler.py` adapted a job into a registered workflow patch, and
  `ResolvedPrintPlanV1` captured the exact physical deposits.
- `artifacts.py` reused `build_vial_dilution_print.py` to build and locally simulate
  hashed Python artifacts.

The missing layer was a persistent, human-readable experiment artifact and a state
machine enforcing review and approval before job-to-plan compilation.

## Configuration hierarchy

### A. Capability template

`configs/templates/printing/*.yaml` selects a registered workflow family, trusted
defaults, a scientist-facing layout type, and bounded capacity. A template is never
edited when a new experiment is created.

### B. Scientific experiment YAML

`configs/experiments/*_vN.yaml` is the first-class experiment artifact. It owns:

- experiment name, description, version, template, and parent config hash;
- logical substrate and material references;
- droplet volume;
- scientific conditions, repeated-drop meaning, and replicate wells; or
- four-clover geometry and logical centers;
- scientific ordering intent.

It deliberately omits coordinates, source wells, deck slots, pipettes, tips, air
handling, calibration, and robot address.

### C. Resolved runtime configuration

The existing registered workflow YAML plus a strict workflow patch produces a
canonical resolved runtime snapshot. This layer retains proven v9/v12 parameters and
is hashed by the builder.

### D. Machine configuration

Existing printing workflow configuration continues to own deck slots, source mapping,
pipettes, mounts, tips, flow rates, and safety limits. `MachineProfileV1` is not part
of Stage 4.

## Standard experiment YAML schema

```yaml
schema_version: printing-experiment/v1
experiment:
  name: Nanoparticle droplet-number series
  description: 1, 2, and 3 drops in triplicate.
  version: 1
  template_id: standard_paper_printing/v1
workflow:
  family: standard
  name: plate_well_direct_v9
substrate:
  labware_id: paper_print_96_flat
material:
  material_id: sample
  display_name: nanoparticle_A
printing:
  droplet_volume_ul: 5.0
  layout:
    kind: well_conditions
    conditions:
    - name: 1_drop
      drops_per_position: 1
      wells: [A1, A2, A3]
    - name: 2_drops
      drops_per_position: 2
      wells: [B1, B2, B3]
    - name: 3_drops
      drops_per_position: 3
      wells: [C1, C2, C3]
  ordering_mode: layer_then_row_then_column
  inter_layer_rest_minutes: 5.0
```

`drops_per_position` maps to repeated v9 layer depositions. It never maps to one
larger dispense. The example therefore produces 18 separate 5 uL deposits, totaling
90 uL.

The registered material ID remains `sample`, which is the trusted standard source
profile. `display_name` preserves the scientist's experiment-specific identity
without asking the model to select a machine source well.

## Identity and provenance

The experiment config SHA-256 is computed from canonical JSON-equivalent content,
not YAML formatting bytes. A separate file SHA is available for byte-level audit.

```text
canonical experiment config SHA
  -> PrintJobV1 job_id
  -> ResolvedPrintPlanV1 plan_id
  -> generated protocol SHA
  -> simulation result for that exact protocol SHA
```

Experiment config and job references are evidence-chain metadata on the resolved
plan. They are intentionally excluded from physical `plan_id` semantics, preserving
Stage 1 golden plan hashes. The generated Python header records all three upstream
hashes, and simulation rehashes the file before running it.

## State machine

Allowed success transitions are:

```text
REQUEST_RECEIVED
  -> CONFIG_DRAFTED
  -> CONFIG_VALIDATED
  -> PLAN_PRESENTED
  -> AWAITING_APPROVAL
  -> APPROVED
  -> RESOLVED
  -> PROTOCOL_BUILT
  -> SIMULATED
  -> READY_FOR_EXECUTION
```

Revision and failure paths are:

```text
CONFIG_DRAFTED -> CONFIG_INVALID
AWAITING_APPROVAL -> USER_REQUESTED_CHANGES -> CONFIG_DRAFTED
APPROVED -> USER_REQUESTED_CHANGES -> CONFIG_DRAFTED
AWAITING_APPROVAL -> PLAN_REJECTED
PROTOCOL_BUILT -> SIMULATION_FAILED
```

Every other transition is rejected by deterministic code. A revision writes a child
YAML with a new version, canonical hash, and `parent_config_sha256`; it clears prior
approval and all downstream artifacts.

Invalid drafts record `config_schema_validation`, `reference_resolution`, or
`physical_plan_validation`; simulator failures record `simulation`. These categories
remain distinct rather than collapsing into a generic agent error.

## Responsibility split

- Tools serialize, load, validate, hash, version, compile, build, and simulate.
- Skills teach bounded condition-to-well and four-clover planning semantics.
- The Printing Agent interprets scientific language and proposes logical positions.
- Structured schemas reject unknown fields and invalid scientific meaning.
- The workflow controller enforces every approval and simulation prerequisite.

The YAML is therefore the auditable interface between AI scientific reasoning and
trusted deterministic laboratory automation.
