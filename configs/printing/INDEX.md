# Printing Config Registry

Existing config paths are preserved for reproducibility. The runtime catalogue in
`src/printing/workflows/registry.py` supplies semantic organization without moving them.

## Standard exact-well printing

- `plate_well_direct_print_v9.yaml`
- `complementary_bp_print_v10a.yaml` (default standard workflow)
- `complementary_dmmp_print_v10b.yaml`
- `combined_bp_dmmp_print_v11.yaml`
- `complementary_bp_quick_print_v10c.yaml` (experimental)
- `complementary_dmmp_spot_test_v10bv2.yaml` (experimental)

## Continuous-coordinate design printing

Registered design: `four_clover`.

- `four_clover_v12.yaml`
- `four_clover_air_chase_v12.yaml` (experimental, committed plan-only)
- `four_clover_grid_v12.yaml`
- `four_clover_spacing_v13.yaml` (default design workflow)

The registry contains historical builder versions 1-8 as hidden compatibility entries.
They remain buildable by version/config but are not advertised as current printing
capabilities.

## Stage 4 scientist-facing experiment configs

The files above are trusted capability/runtime configs. They are not overwritten for
each experiment. New scientist-facing artifacts are created from registered templates:

- `../templates/printing/standard_paper_printing.yaml`
- `../templates/printing/four_clover_printing.yaml`

and saved as versioned files beneath `../experiments/`. The reference case is
`../experiments/nanoparticle_drop_series_triplicate_v1.yaml`. These experiment YAMLs
contain scientific conditions and logical labware positions; deterministic adapters
resolve them into the runtime configurations listed in this index only after approval.
