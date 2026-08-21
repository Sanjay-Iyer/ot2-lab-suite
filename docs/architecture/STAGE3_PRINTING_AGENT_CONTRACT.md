# Stage 3: Printing Agent to `PrintJobV1`

Stage 3 makes `PrintJobV1` the formal boundary between probabilistic scientific
interpretation and deterministic printing software.

```text
natural-language request
        |
        v
bounded Printing Agent
        |
        +-- runtime standard/design/four-clover skill
        |
        v
strict PrintJobDraftV1 tool input
        |
        +-- registered substrate/material/default resolution
        +-- canonical PrintJobV1 construction and job_id
        |
        v
compile_print_job()
        |
        v
ResolvedPrintPlanV1 + deterministic preview
```

## Agent ownership

The model identifies the supported family, interprets rows/columns or clover
geometry/placement, distinguishes layers from replicates, selects named logical
references, asks for genuinely missing scientific information, and submits a
strict typed draft. It may modify an existing canonical job through a bounded
scientific patch. Each modification constructs a new immutable job.

The model never supplies job hashes, labware-definition hashes, namespace,
labware version, plan hashes, deck coordinates, source wells, pipette settings,
air handling, piston displacement, protocol code, or raw robot commands.

## Primary agent tools

- `list_printing_capabilities`
- `list_registered_substrates`
- `list_registered_materials`
- `load_printing_skill`
- `create_and_compile_print_job`
- `modify_and_compile_print_job`
- `report_printing_request_issue`

The historical workflow-patch, coordinate-preview, build, and simulation tools
remain available to compatibility callers but are not exposed to the Printing
Agent. The new agent path stops at deterministic resolved-plan preview.

## Registered V1 defaults

- Standard well printing: substrate `paper_print_96_flat`, material `sample`,
  Cartesian rows/columns, one declared layer count per row.
- Four-clover printing: substrate `paper_print_96_flat`, material `BP`, 2 mm
  symmetric half-width/half-height, one layer, clover-by-clover ordering.
- The standard clover placement preset supports one center and a three-center
  replicate layout. Other replicate counts require explicit centers.

These defaults are scientific reference facts only. The existing trusted
profile still owns source placement and machine implementation. `MachineProfileV1`
remains deliberately deferred.

## Error boundaries

Structured results distinguish:

1. `interpretation` — ambiguity or unsupported intent before a job exists.
2. `schema_validation` — incoherent scientific fields.
3. `reference_resolution` — unknown substrate/material/preset.
4. `deterministic_compiler` — adaptation/resolution failure.
5. `physical_plan_validation` — structurally valid intent that fails physical checks.
6. `simulation` — reserved for the later lifecycle stage.

Stage 3 does not implement draft/validated/resolved/built/simulated/approved
lifecycle state. That explicit Workflow component belongs to Stage 4.
