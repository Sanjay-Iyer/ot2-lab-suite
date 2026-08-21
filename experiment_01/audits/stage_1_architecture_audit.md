# Architecture Audit 1 — Static ground truth

## Verdict

**APPROVED** on 2026-08-20 after one correction cycle. No blocking or
non-blocking findings remain.

## Initial findings and resolution

1. **HIGH — low-volume plate accessibility was not demonstrated.**
   The static protocol now uses a 0.2 mm plate aspiration/mix height and an
   action-by-action volume ledger based on the registered 6.86 mm well diameter.
2. **HIGH — vial loaded and minimum remaining volumes were unspecified.**
   Each source vial now declares a 5,000 uL load and 2,600 uL minimum remaining
   volume for its configured 4.0 mm aspiration height.
3. **MEDIUM — runtime flow rates were absent from the canonical schema.**
   The canonical `LOAD_PIPETTE` action now declares 3.0 uL/s aspiration and
   dispense rates, which the runtime consumes directly.

## Independent verification

- 178 transfer/mix/print source actions passed independent ledger replay.
- Minimum nominal submerged-volume margin: 2.607895 uL.
- Vial endings: 4,819.765625–4,940.234375 uL; 4 mm cover requires
  2,463.00864 uL.
- Exactly 64 x 5 uL print deposits and four explicit 300 s delays.
- Exactly 61 tip groups, fitting one 96-tip rack.
- Forced-motion simulation observed all 64 paper dispenses at z=6.5 mm,
  matching the 6.0 mm paper bottom plus 0.5 mm standoff.
- Focused suite: 17 passed.
- Canonical SHA-256:
  `60200e8560266da9f8c7cf059c6a001c4f632b5f2557631bc41f44ed0346cd87`.
- Protocol SHA-256:
  `fe44eedfe0c9937a276b94c49b13899d52015ef6951f7e16137c79ed82614950`.
- Repository isolation search found no generic executor, tool, skill, agent, or
  workflow importing the static implementation.

The 0.2 mm clearance and 3 uL mixing remain nominally modeled until real-instrument
validation. That limitation is explicit and appropriate on this HOME,
simulation-only laptop.
