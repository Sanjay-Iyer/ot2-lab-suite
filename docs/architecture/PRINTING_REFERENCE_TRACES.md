# Stage 0 Printing Reference Traces

This document freezes the two established printing paths used as behavioral references
before printing abstractions are generalized. It is architecture documentation, not a
runtime skill. The reference tests run locally in the simulation-only `ai` Conda
environment and never authorize OT-2 hardware motion.

## Standard printing reference: `plate_well_direct_v9`

The golden case prints 5 uL from source plate well A1 to paper rows A and B in
columns 1 and 2. Row A receives one layer and row B receives two. The exact order is
A1, A2, B1, B2, B1, B2: six deposits, 30 uL liquid, one 0.25-minute inter-pass rest,
a 1.5 uL trailing air gap, 3 uL push-out, blow-out, and a 2-second post-dispense
dwell at each location.

```text
request/config
  tests/fixtures/printing/plate_well_direct_v9_golden.json
  configs/printing/plate_well_direct_print_v9.yaml
        |
        v
schema and workflow resolution
  src/printing/schemas/models.py::parse_printing_request
  src/printing/workflows/registry.py::resolve_printing_request
        |
        v
allowlisted configuration patch
  src/printing/compiler.py::apply_workflow_patch
        |
        v
deterministic validation
  src/printing/artifacts.py::prepare_printing_request
  src/printing/validation.py::validate_standard_config
  src/protocols/printing/09_plate_well_direct_paper_print_v9.py::_layer_plan
        |
        v
protocol generation
  src/printing/artifacts.py::build_prepared_artifact
  scripts/build_vial_dilution_print.py::build_source
        |
        v
exact-motion local simulation
  src/printing/artifacts.py::simulate_prepared_request
  scripts/build_vial_dilution_print.py::simulate
  src/protocols/printing/09_plate_well_direct_paper_print_v9.py::run
  src/protocols/printing/09_plate_well_direct_paper_print_v9.py::_preflight
  src/protocols/printing/09_plate_well_direct_paper_print_v9.py::_print_paper
```

The normalized behavior and canonical resolved-configuration SHA-256 are asserted in
`tests/test_printing_golden_baselines.py`. The simulation test also verifies the actual
destination order, aspiration count, air gaps, piston dispense volume, blow-outs,
per-drop delays, and inter-pass delay in the Opentrons simulator output.

## Clover printing reference: `four_clover_air_chase`

The golden case is the shipped one-clover air-chase experiment. A clover centered at
paper-local `(63.88, 42.74)` mm uses four offsets `(-2,+2)`, `(+2,+2)`, `(-2,-2)`,
and `(+2,-2)` mm in D1-D4 order. It prints one 5 uL liquid layer at four locations,
using 5 uL pre-air chase and a 1.5 uL trailing anti-drip gap. The piston dispense is
11.5 uL, blow-out is disabled, and the post-drop dwell is 2 seconds. Total liquid
consumption remains 20 uL because neither air volume counts as liquid.

```text
request/config
  tests/fixtures/printing/four_clover_air_chase_v12_golden.json
  configs/printing/four_clover_air_chase_v12.yaml
  configs/printing/four_clover_air_chase_locations.yaml
        |
        v
schema and workflow resolution
  src/printing/schemas/models.py::parse_printing_request
  src/printing/workflows/registry.py::resolve_printing_request
        |
        v
allowlisted configuration patch and destination merge
  src/printing/compiler.py::apply_workflow_patch
  src/printing/config.py::load_printing_config
        |
        v
geometry and deterministic validation
  src/protocols/printing/12_four_clover_paper_print.py::_geometry_from_spec
  src/protocols/printing/12_four_clover_paper_print.py::_center_specs
  src/protocols/printing/12_four_clover_paper_print.py::_resolve_clovers
  src/protocols/printing/12_four_clover_paper_print.py::_print_order
  src/printing/validation.py::validate_four_clover_config
        |
        v
protocol generation
  src/printing/artifacts.py::build_prepared_artifact
  scripts/build_vial_dilution_print.py::build_source
        |
        v
exact-motion local simulation
  src/printing/artifacts.py::simulate_prepared_request
  scripts/build_vial_dilution_print.py::simulate
  src/protocols/printing/12_four_clover_paper_print.py::run
  src/protocols/printing/12_four_clover_paper_print.py::_preflight
  src/protocols/printing/12_four_clover_paper_print.py::_print_clovers
```

The fixture stores both paper-local coordinates and the resulting OT-2 deck coordinates
for slot 5. The simulation regression verifies the D1-D4 execution order, deck XY
comments, two 5 uL aspirations per deposit (chase air then liquid), trailing gaps,
11.5 uL piston dispenses, absence of blow-out, and per-drop delays.

## Baseline test command

From the simulation laptop:

```powershell
conda activate ai
python -m pytest tests/test_printing_golden_baselines.py tests/test_four_clover_air_chase_v12.py -q
```

The fixtures intentionally omit generated-protocol timestamps and do not pin the full
Python source hash. They pin canonical resolved-config hashes plus normalized scientific
and operational behavior. Each simulation still verifies that its generated protocol
file matches the build artifact's own SHA-256.

## Stage 1 parallel representation

The reference protocols above remain the physical execution implementations. Stage 1
adds a parallel, non-executing projection from each trusted resolver into
`ResolvedPrintPlanV1`:

```text
Standard v9 ──→ existing v9 protocol
            └─→ ResolvedPrintPlanV1

Clover v12  ──→ existing v12 protocol
            └─→ ResolvedPrintPlanV1
```

See `docs/architecture/RESOLVED_PRINT_PLAN_V1.md` for schema responsibility,
canonical hashing, validation, and the explicitly not-yet-implemented future path.
