# Intelligent Automation Architecture

Implementation record for the modern OT-2 paper-printing layer. This document names
the production modules and APIs as implemented; the pre-refactor state remains in
`STAGE0_BASELINE.md`.

```text
User scientific intent
        ↓
Printing Agent
        ↓
deterministically selected runtime SKILL.md content
        ↓
high-level Printing Agent tool
        ↓
strict request envelope + registry-bound patch model
        ↓
workflow/design registry
        ↓
deterministic validation
        ↓
exact generated artifact + hashes
        ↓
local forced-motion simulation
        ↓
manual work-laptop handoff (outside the agent)
```

## Printing families

`PrintingFamily` has exactly two values:

| Family | Meaning | Current implementation |
|---|---|---|
| `standard` | Discrete destinations addressed as exact paper wells/rows/columns | protocol versions 9-14 (`09_`, `10_`, `11_`) |
| `design` | Continuous XY offsets resolved by a registered coordinate design | protocol versions 15-18 (`12_four_clover_...`) |

A design request uses `family="design"` plus `design_name`. `four_clover` is the first
registered design. The Printing Agent contains no four-clover branch; it discovers
design names from the workflow registry and skill metadata.

## Tools: what the laboratory software can do

`src/agents/printing_tools.py` exposes only these high-level capabilities:

| Tool | Result |
|---|---|
| `list_printing_workflows` | discoverable family/workflow/design/lifecycle metadata |
| `describe_printing_workflow` | exact registered Pydantic patch JSON schema, ownership, units, base config |
| `list_printing_designs` | registered coordinate generators |
| `validate_printing_request` | deterministic `ValidationReport` |
| `preview_design_coordinates` | validated design coordinate preview |
| `build_printing_protocol` | plan-only `BuildArtifact` under `.test_tmp` |
| `simulate_printing_protocol` | exact `SimulationResult` after local motion-path simulation |

`load_printing_skill` is the only additional Printing Agent tool. There is no raw
aspirate/move/dispense, deploy, execute, live, robot host, or arbitrary file-path tool
in `PRINTING_AGENT_TOOLS`. `src/printing/cli.py` is a deterministic command-line adapter
over the same production tools.

## Skills: runtime procedural knowledge

The runtime skills are:

- `skills/standard-paper-printing/SKILL.md`
- `skills/design-paper-printing/SKILL.md`
- `skills/four-clover-printing/SKILL.md`

`src/printing/skills.py` parses YAML frontmatter and discovers only skills with
`domain: printing` and `agent: printing`. The static agent prompt contains only the
name/description index. For each user request, the production dynamic prompt:

1. derives family/design/workflow routing from current registries;
2. selects the family skill and any matching design specialization;
3. loads those real `SKILL.md` bodies;
4. inserts only those bodies into the first model context before tool choice.

Declared reference files must exist beneath the selected skill directory. Traversal
and undeclared references are rejected. Robot-control skills are not discoverable by
the Printing Agent.

## Agent: bounded scientific-intent routing

`src/agents/printing_agent.py` owns interpretation and selection only:

- family, workflow and design selection;
- relevant skill selection;
- high-level tool and local execution-mode selection;
- structured parameter proposal and clarification of missing physical choices.

`PrintingAgentPlan` makes these internal decisions testable. `printing_agent_prompt()`
is the production dynamic context hook used by `create_printing_agent()`. Schemas,
validators, registries, compilers and protocol code—not natural-language instructions—
decide physical validity and robot-level behavior.

The generic `src/agents/main.py --mock` compatibility path is explicitly deprecated
and fails closed without tools/network/robot. `src/agents/vial_print_agent.py` remains
the legacy workflow-01 specialist but its AI surface also stops at manual handoff.

## Schemas: the AI-to-deterministic boundary

All printing boundary models in `src/printing/schemas/models.py` inherit
`StrictPrintingModel` (`extra="forbid"`). The request union is discriminated by
`family`:

```text
PrintingRequest
├── StandardPrintingRequest(workflow_name, parameters)
└── DesignPrintingRequest(workflow_name, design_name, parameters)
```

The envelope's `parameters` mapping is immediately validated by
`resolve_printing_request()` against the patch model bound to the selected workflow.
The actual patch models are:

- `StandardWellGridPatch`
- `ComplementaryColumnPatch`
- `ComplementaryRowPatch`
- `ComplementaryQuickPatch`
- `CombinedOverlayPatch`
- `FourCloverPatch`, composed from `FourCloverGeometry`,
  `FourCloverManualCenter`, `FourCloverGrid`, `XYOffset`, and `XYOverride`

This preserves heterogeneous v9-v11/v12 behavior. `src/printing/compiler.py` applies
only explicit workflow-specific fields; it never deep-merges an arbitrary model dict.
There is no live execution field in any printing request.

### Parameter ownership

| Owner | Examples |
|---|---|
| AI-selectable | family/workflow/design, droplet volume in µL, allowed layer maps, centers/grid geometry in mm, allowed delays in seconds/minutes |
| Workflow/source-profile controlled | BP/DMMP material identity and its configured physical source profile |
| Config-controlled | deck slots, labware, source wells/loaded volume/aspiration height, calibrated flow/air/push-out settings |
| Deterministically calculated | absolute XY, piston load, consumption, print order, resolved layer plan |
| Hardware-fixed | pipette/mount, tip rack, robot API capability |
| Human only | physical readiness and live authorization, outside every Printing Agent schema/tool |

Field ownership and units are present in Pydantic JSON-schema metadata and are emitted
by `describe_printing_workflow`.

## Deterministic validation

`src/printing/validation.py` runs before build or preview. It verifies, as applicable:

- positive layer counts, droplet volume and nonnegative timing/air gaps;
- exact-well row/column completeness and valid paper destinations;
- fixed deck slots, pipette/mount/tip configuration and target API compatibility;
- pipette/piston capacity and source loaded-volume/reserve/liquid-column coverage;
- aspiration height/depth and configured source profile;
- four-clover paper bounds, geometry, minimum distances, override names and order.

Four-clover validation/generation loads a fresh copy of the production protocol for
each call because its resolvers read module-global `CONFIG`. The adapter delegates to
the real resolvers and boundary/capacity/distance functions; equivalence tests protect
against drift. Production validation owns the NumPy `trapz` compatibility shim so
direct CLI and pytest use behave the same.

## Workflow registry and historical compatibility

`src/printing/workflows/registry.py` contains frozen `PrintingWorkflowSpec` records:

```python
PrintingWorkflowSpec(
    name="four_clover_spacing",
    builder_version=18,
    base_protocol=...,
    generated_stem="four_clover_spacing_v13",
    default_config=...,
    family=PrintingFamily.DESIGN,
    design_name="four_clover",
    patch_model=FourCloverPatch,
    lifecycle=Lifecycle.SUPPORTED,
    is_default=True,
    discoverable=True,
    description="...",
)
```

Lifecycle (`supported`, `experimental`, `deprecated`) is separate from `is_default`.
Modern versions 9-18 are discoverable. Legacy versions 1-8 remain builder-compatible
but non-discoverable. Stub/nonfunctional workflows are absent.

The builder imports `builder_protocol_versions()` from this registry. The runner
derives generated artifact paths and version classifications from the same registry;
missing/malformed/unknown supplied configs raise rather than falling back to v1.
Historical YAML paths are preserved. `configs/printing/INDEX.md` provides the human
standard/design view without moving referenced experiment files.

## Design registry

`src/printing/designs/registry.py` uses a small frozen `DesignSpec` containing
`name`, `description`, `patch_model`, and `generate`. It exports `get_design()` and
`list_designs()`. There is no abstract base class or registration framework.

`src/printing/designs/four_clover.py` is the first adapter. Adding a new design means:

1. add a strict design-specific patch model only if needed;
2. add a deterministic generator/validator;
3. add one `DesignSpec` and at least one functioning workflow/config registration;
4. add negative, equivalence, preview, build and simulation tests;
5. optionally add a scoped procedural skill specialization.

No Printing Agent prompt or routing branch changes are required.

## Exact artifact provenance and simulation

`src/printing/artifacts.py` owns:

- `PreparedPrintingRequest`: resolved request/workflow/patch/config/report;
- `BuildArtifact`: workflow/family/design, base config, exact request payload,
  canonical resolved-config snapshot and SHA-256, protocol path and SHA-256, and
  protocol dry-run state;
- `SimulationResult`: exact artifact, PASS/FAIL, whether motion was exercised, and
  simulator output tail.

A plan build uses `dry_run=true`. Local simulation builds a separate temporary
artifact with protocol `dry_run=false`, then passes that exact hash/path to the local
Opentrons simulator. Thus local simulation exercises the liquid-handling path without
contacting or authorizing hardware. Committed latest artifacts are restored to safe
`DEFAULT_DRY_RUN=True`.

## Live execution boundary

No modern Printing Agent tool can start hardware. `run_vial_print_robot_http(live=True)`
refuses AI authorization. Real work-laptop operation is a documented manual two-phase
HTTP-runner handoff: architecture validation; `--live --no-start` build/simulation/upload;
exact SHA-256 and physical review; then `--skip-build` start of the unchanged artifact.
The safe dry-run latest is rebuilt afterward. See `docs/OT2_PRINTING_GUIDE.md`.

## File ownership

| Concept | Production files |
|---|---|
| Agent | `src/agents/printing_agent.py` |
| Skills | three scoped printing `SKILL.md` files, `src/printing/skills.py` |
| Tools/CLI | `src/agents/printing_tools.py`, `src/printing/cli.py` |
| Schemas/compiler | `src/printing/schemas/`, `src/printing/compiler.py`, `src/printing/config.py` |
| Validation | `src/printing/validation.py` plus delegated production protocol resolvers |
| Workflows/designs | `src/printing/workflows/`, `src/printing/designs/` |
| Provenance/simulation | `src/printing/artifacts.py` |
| Historical build/run | `scripts/build_vial_dilution_print.py`, `scripts/run_vial_print_robot.py` |
| Config discovery | `configs/printing/INDEX.md`; existing YAML paths remain in place |

The architecture is additive. It does not rewrite stable protocol motion logic or move
historical configs. Registries impose semantic ownership while preserving experiment
reproducibility.
