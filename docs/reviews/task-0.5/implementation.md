# Task 0.5 implementation

New test-only files; no application, protocol or configuration file changed.

| File | Role |
|---|---|
| `tests/golden_traces/recorder.py` | `motion_trace` runs the real protocol file on a recording fake. `analysis_trace` builds the same config with the real builder and runs pinned `opentrons.cli analyze` on temporary files. |
| `tests/golden_traces/cases.py` | 20 plans, each a change to the shipped demo config, passed through `compile_plan` as `session._run` does. |
| `tests/golden_traces/generate.py` | Rewrites stored traces only with `--update`. One command per line, so a motion change reads as a small diff. |
| `tests/golden_traces/data/*.json` | Compiled config, motion trace and pinned analysis for each plan. |
| `tests/test_nanodrop_golden_traces.py` | Comparison, reproducibility, fake-versus-analysis agreement and coverage tests. |

## Why two recordings

The existing fake logs no flow rates, ignores the mount, and drops `delay(minutes=)`. `TracingPipette`
and `TracingProtocol` subclass it and record every value the protocol passes, so existing tests
keep their log format. The fake still cannot show what Opentrons computes, so each plan also
stores pinned analysis output. That output includes top-relative heights and values the protocol
leaves to defaults, such as a 7.56 uL/s blow-out rate and the air-gap move to 5 mm above the well.

`test_the_fake_robot_agrees_with_opentrons_analysis` compares the two field by field for all 20
plans. Excluded from that comparison: blow-out flow rate (the protocol does not set it),
`pickUpTip` location, and labware and pipette loading.

## Findings the traces make visible

No value below is declared correct; each is recorded for later tasks and WORK.

- Blow-out runs at Opentrons' default 7.56 uL/s, not the configured 3 uL/s.
- A transfer's air gap has no height argument, so it uses Opentrons' default 5 mm above the well.
- The paper release height in traces is the demo's current 1.1 mm.
- With a shared tip, a sample tip used to mix a dilution returns to the stock vial.

## Limits

- Traces apply to Opentrons 7.0.2 analysis and the recording fake. Neither models liquid, tip
  state, paper height, or real robot timing. Physical behavior is WORK VALIDATION PENDING.
- The corpus starts from HOME's demo config. Real `executed_config_run*.yaml` files from live
  runs at WORK should join it during WORK commissioning.
- Stored `generated_from.commit` is informational and is not compared.
