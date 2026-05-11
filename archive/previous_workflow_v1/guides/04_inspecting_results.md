# Inspecting Results

After a simulation run, you have two output files to examine: the
structured log and the raw simulator transcript.

## Where to find the files

Both paths are defined in your config under the `outputs` key:

```yaml
outputs:
  log_file: "logs/testsample_a_2026-04-28.log"
  transcript: "outputs/testsample_a_2026-04-28_transcript.txt"
  summary: "outputs/testsample_a_2026-04-28_summary.yaml"
```

The runner creates the `logs/` and `outputs/` directories if they don't
already exist. The exact filenames are also printed at the end of each
run.

## Reading the structured log

The log file is line-oriented, with each line containing a timestamp, log
level, and tagged message.

### Log line anatomy

```
2026-04-28T14:02:01 INFO  [DILUTE] Well A1 (1/5): 2x dilution  |  V_stock=50.0 µL, V_diluent=50.0 µL
│                   │      │        └─── human-readable detail
│                   │      └──── phase tag (matches protocol stage)
│                   └──── log level
└──── ISO timestamp
```

### Tags and what to look for

| Tag | What to check |
|-----|--------------|
| `[LOAD]` | Correct labware in each slot? Right pipette and mount? |
| `[VALIDATE]` | Should say "Config OK." If not, there's a validation error. |
| `[DILUTE]` | Correct V_stock and V_diluent for each factor? Wells in expected order? |
| `[PRINT]` | Correct source → dest mapping? Right print volume? |
| `[DONE]` | Total count of dilutions and prints matches your config? |

### Quick checks

```bash
# Count how many dilution steps ran
grep -c '\[DILUTE\] Well' logs/testsample_a_2026-04-28.log

# Show only print transfers
grep '\[PRINT\] Transfer' logs/testsample_a_2026-04-28.log
```

## Reading the simulator transcript

The transcript is the raw output from `opentrons.simulate`. It includes
every physical action the robot would take: aspirate, dispense, pick up
tip, drop tip, etc.

### What to look for

1. **Correct volumes.** Each `Aspirating X.X uL` should match the
   V_stock or V_diluent from your config.

2. **Correct wells.** `from A1 of ...` and `to B1 of ...` should match
   the expected source and destination wells.

3. **Tip tracking.** The transcript shows `Picking up tip from A1 of
   Opentrons 96 Tip Rack...`. Tips should be used sequentially. If you
   see tip-tracking errors, something is wrong with the protocol logic.

4. **Mix steps.** After each stock transfer, you should see 5 aspirate/
   dispense cycles (the mix step). The volume should be ~80% of
   `final_volume_ul`.

### Sample transcript excerpt

```
Picking up tip from A1 of Opentrons 96 Tip Rack 300 µL on 11
Aspirating 50.0 uL from A1 of NEST 12 Well Reservoir 15 mL on 5 at 1.0 speed
Dispensing 50.0 uL into A1 of NEST 96 Well Plate 200 µL Flat on 2 at 1.0 speed
Dropping tip into A1 of Opentrons Fixed Trash on 12
Picking up tip from B1 of Opentrons 96 Tip Rack 300 µL on 11
Aspirating 50.0 uL from A1 of ... on 1 at 1.0 speed
Dispensing 50.0 uL into A1 of NEST 96 Well Plate 200 µL Flat on 2 at 1.0 speed
Aspirating 80.0 uL from A1 of NEST 96 Well Plate ...
Dispensing 80.0 uL into A1 of NEST 96 Well Plate ...
[repeated 5 times — this is the mix]
Dropping tip into A1 of Opentrons Fixed Trash on 12
```

This shows the 2x dilution for well A1: diluent first (50 µL from
slot 5), then stock (50 µL from slot 1), followed by mixing.

## Comparing two runs

When you change a parameter (e.g. increase `final_volume_ul` from 100 to
200), run the new config and compare logs:

```bash
diff logs/testsample_a_run1.log logs/testsample_a_run2.log
```

Or compare just the dilution lines:

```bash
diff \
  <(grep '\[DILUTE\]' logs/run1.log) \
  <(grep '\[DILUTE\]' logs/run2.log)
```

Things to verify:

- V_stock values doubled when final volume doubled.
- V_diluent adjusted accordingly.
- Total stock consumed changed.
- Well assignments didn't shift (they shouldn't, unless you changed the
  number of factors).

## Next steps

- [guides/05_troubleshooting.md](05_troubleshooting.md) — if something
  doesn't look right.
- [guides/03_understanding_the_protocol.md](03_understanding_the_protocol.md) —
  to understand the volume math behind what you're seeing.
