# Stage 1 — Static Experiment 01 ground truth

## Implementation

The isolated reference protocol is
`src/protocols/printing/01_printing_standard_ground_truth.py`.

It intentionally hardcodes Experiment 01 and is not imported by any reusable
printing schema, tool, skill, agent, workflow, or executor. The protocol exposes a
pure `build_ground_truth_plan()` function so its independently prepared physical
actions can be serialized and compared in later stages.

## Scientific resolution

- Deck: custom 20 mL vial rack slot 7, custom 96-well preparation plate slot 4,
  registered paper substrate slot 5, P20 tip rack slot 9.
- Pipette: `p20_single_gen2`, left mount.
- Vial A1: NP stock; A2: NP diluent; A3: stock CV; A4: CV diluent.
- NP preparation wells: A1:H1 on the preparation plate.
- CV preparation wells: A2:H2 on the preparation plate.
- Relative factors: 1, 1/2, 1/4, 1/8, 1/16, 1/32, 1/64, 1/128.
- Dilution method: back-calculated twofold serial cascade.
- Original stock per series: 59.765625 uL.
- Retained usable volume: exactly 30 uL in every prepared well.
- Vial loads: 5,000 uL in each of A1:A4, explicitly distinguished from the
  smaller scientific consumption/allocation. A 2,600 uL minimum remaining volume
  protects the configured 4.0 mm vial aspiration height.
- Plate aspiration/mix height: 0.2 mm, resolved from the configured 6.86 mm
  flat-bottom well geometry.
- Mix: 3 cycles x 3 uL. At the lowest-volume NP step, the action-by-action ledger
  retains more than 2.6 uL of nominal column volume above the configured tip height
  even at the trough of the mix.
- Every preparation transfer is split deterministically to at most 20 uL.
- Printing: 64 physical 5 uL deposits / 320 uL total.
- Paper standoff: 0.5 mm above the modeled paper bottom.
- Release: 1.5 uL trailing air gap, 6.5 uL piston dispense, 3 uL push-out,
  blow-out, 2 s post-dispense dwell.
- Canonical P20 flow rates: 3.0 uL/s aspirate and 3.0 uL/s dispense.
- Column 1 uses one global 300 s drying pass after all eight NP deposits and
  before stock CV. This resolves the prompt's unspecified "standardized" interval
  to the same five-minute interval explicitly required for Column 2; a blind agent
  must request clarification if it cannot infer that value safely.
- Column 2 uses three NP layer passes with an explicit 300 s delay after every
  pass, including the third pass before stock CV.

## Canonical candidate

The Stage 1 exporter writes:

- `experiment_01/ground_truth/static_canonical_trace.json`;
- `experiment_01/ground_truth/static_canonical_sha256.txt`;
- `experiment_01/ground_truth/static_protocol_reference.json`.

Architecture Audit 1 approved the isolation and scientific/physical resolution.
These artifacts are frozen as the independent baseline for later equivalence
comparisons.

The frozen hashes are:

- Canonical trace SHA-256:
  `60200e8560266da9f8c7cf059c6a001c4f632b5f2557631bc41f44ed0346cd87`
- Static protocol SHA-256:
  `fe44eedfe0c9937a276b94c49b13899d52015ef6951f7e16137c79ed82614950`
- Canonical action counts: 187 total, 58 transfers, 56 mixes, 64 prints,
  4 delays, and 61 tip groups.
- Source-accessibility checks: 178 action-level source checks, all passing;
  minimum nominal submerged-volume margin 2.607895 uL.

## Verification before audit

```text
tests/test_experiment_01_static_ground_truth.py
tests/test_experiment_01_geometry_baseline.py
tests/test_printing_golden_baselines.py

17 passed in 29.69s
```

No robot connection or live command was used.
