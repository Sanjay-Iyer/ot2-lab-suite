# Config-Driven Pipette Selection — Refactor Plan

## Context

Today every printing/dilution protocol hardcodes a single pipette, `p300_multi_gen2`
on the right mount, and prints **8 paper spots at once** (8-channel). There is no way
to say "print 5 µL droplets with the P20 and 30 µL droplets with the P300 in the same
run." The only volume→pipette logic that exists (`src/printing/tools/config.py::select_pipette`)
is dead code that never reaches a robot protocol.

This refactor makes the pipette a **function of the configured droplet volume / print
group**, so one experiment can mix a single-channel P20 (small volumes, printed one
spot at a time) and the 8-channel P300 (larger volumes, printed 8-up).

### Hardware reality (confirmed)
- P300 = `p300_multi_gen2`, right mount, 8-channel (existing, fixed).
- P20 = `p20_single_gen2`, left mount, **single-channel** → small-volume prints are
  **one spot at a time**, a different motion pattern than 8-up. The schema and
  validator must make this explicit and reject "8 wells at once" for a single-channel
  pipette.

### Scope decisions
- **Staged.** Phase 1 (this doc): functional config-driven selection + validation +
  tests + simulation, on the flagship `vial_dilution_print`. Phase 2 (separate,
  re-reviewed): migrate the `printing_demo`/registry family onto the unified schema,
  and the repo-wide numbering/naming/archive reorg (~400 references — see the
  dependency map in the session notes).
- **Unify.** One new pipette-group schema + one selection service, adopted by the
  flagship first.

## Selection logic (implemented: `src/core/pipette_selection.py`)

- Canonical volume ranges come from `configs/constraints/pipette_constraints.yaml`
  (now includes `p300_multi_gen2` / `p20_multi_gen2`; a built-in mirror keeps the
  service usable in isolation). Channel count is derived from the name (`multi` → 8).
- `select_pipette(volume, mounted, explicit=None)`:
  - **explicit** pipette → validate it's mounted and the volume is in its hard range;
    else a clear error naming the range.
  - **auto** → among mounted pipettes whose range covers the volume, pick the
    **smallest-capacity** one (P20 before P300). Prefer pipettes for which the volume
    is within the *recommended* (accurate) range; a volume below recommended-min is
    allowed but flagged with an accuracy warning. No "dead zone": with ranges
    P20 [1,20] and P300 [20,300], 21–29 µL resolves to the P300 with a low-accuracy
    warning.
  - Fails clearly when nothing mounted can perform the transfer.
- `assert_layout_supported(spec, wells_per_dispense)` rejects a single-channel pipette
  driving >1 well simultaneously.

## Unified schema (the contract)

New top-level `pipettes:` list (what is mounted) + `print_groups:` list (each a
homogeneous batch of droplets). This cleanly separates the four things the prompt asks
to distinguish: **dilution plate columns** (the existing `dilution:` block, unchanged),
**source wells** (`group.source`), **paper destinations** (`group.destination`), and
**pipette per volume group** (`group.pipette`).

```yaml
# WHAT IS MOUNTED (replaces the single `pipette:` mapping)
pipettes:
  - {name: p300_multi_gen2, mount: right, single_start: A1}
  - {name: p20_single_gen2, mount: left}

# DILUTION PREP (unchanged: which 96-well columns are prepared/mixed)
dilution:
  enabled: true
  destination_column: "9"
  total_volume_ul: 200
  factors: {mode: explicit, explicit: [1, 2, 5, 10, 20, 50, 100, 200]}
  mix_reps: 5
  mix_volume_ul: 150

# PRINT GROUPS (each: one volume, one pipette, one source, one destination pattern)
print_groups:
  - name: fine_p20
    volume_ul: 5
    pipette: auto              # auto -> p20_single_gen2 ; or name it explicitly
    layout: single_spot        # single-channel: one spot per dispense
    source: {plate_column: "9", wells: [A9]}   # where liquid is aspirated
    replicates: 3
    destination: {paper_start_column: 1, spacing_mm: {x: 9, y: 0, z: 0}}
    dispense: {z_mm: 1.0, air_gap_ul: 2, blow_out: true, touch_tip: false}
    tips: {well: H12, reuse: true, return: true}   # explicit tip; reuse+return

  - name: coarse_p300
    volume_ul: 30
    pipette: auto              # auto -> p300_multi_gen2 (8-up)
    layout: column_8up         # 8-channel: 8 wells per dispense
    source: {plate_column: "11"}
    replicates: 3
    destination: {paper_start_column: 4, spacing_mm: {x: 9, y: 0, z: 0}}
    dispense: {z_mm: 1.0, air_gap_ul: 5, blow_out: true}
    tips: {block_column: 1, reuse: true, return: true}   # full 8-tip pickup

tips: {return_tips: true}       # global default; per-group `tips.return` overrides
```

`layout` is validated against the chosen pipette's channel count: `single_spot`
requires (or forces single-nozzle) 1 well/dispense; `column_8up` requires an 8-channel
pipette. `auto` + `column_8up` will only pick a multichannel pipette.

## Validation (Phase 1)

A single validator over the resolved config checks, with actionable messages:
volume within the selected pipette's range · requested pipette mounted · `layout` vs
channel count · enough tips for the assigned wells/blocks · duplicate paper
destinations · overlapping dilution wells (unless intended) · missing source/dest ·
per-fold/stock transfer ≤ source available volume. This extends the existing
`scripts/build_vial_dilution_print.py::validate()` and reuses the constraint data.

## Tip reuse / return (preserved)

The existing behavior — assign a specific tip (or 8-tip block), reuse it across the
group, and **return it to its original rack position** (`pipette.return_tip()` when
`tips.return`/`return_tips` is true, else `drop_tip()`) — is kept per group. No silent
switch to new-tip-per-transfer.

## Backward compatibility

Old configs with a single `pipette:` mapping + flat `printing:`/`color_series:` still
load: a compat layer synthesizes `pipettes:` (one entry) and one `print_group` per
color series, all `pipette: p300_multi_gen2`, `layout: column_8up`. If a config mixes
old `printing:` and new `print_groups:`, it **fails** with a clear message (no silent
reinterpretation). A short migration note documents the field mapping.

## Status

- [x] `pipette_constraints.yaml`: add `p300_multi_gen2` / `p20_multi_gen2`.
- [x] `src/core/pipette_selection.py`: selection service.
- [x] `tests/printing/test_pipette_selection.py`: 28 tests, all passing.
- [ ] Unified Pydantic models + group validator (+ tests).
- [ ] Wire into flagship `vial_dilution_print` (config + protocol + build validator),
      incl. single-channel P20 single-spot print path + compat layer.
- [ ] Regression tests (P20-only, P300-only, mixed, tip return, unchanged behavior) +
      simulation matrix.

## Verification

- Unit: `python -m pytest tests/printing/test_pipette_selection.py` (run with the `ai` env).
- Sim: build the flagship and run `opentrons.simulate -L labware` on the generated
  protocol for P20-only / P300-only / mixed configs; scan output for error markers
  (`opentrons.simulate` exits 0 even on runtime errors — must grep the text).
- Full: `python -m pytest` from repo root under the `ai` env.
