# Vial-Dilution-Print — Protocol Mechanics

How [`src/protocols/vial_dilution_print.py`](../../src/protocols/vial_dilution_print.py)
actually moves. Everything here is config-driven from the embedded `CONFIG` dict;
see [PARAMETERS.md](PARAMETERS.md) for the knobs.

---

## Deck layout

| Slot | Labware | Role |
|------|---------|------|
| 7 | `tuberack_3dprint_20ml_8vials_v2` (custom_beta v1) | Two 20 mL vials in row A: **A1 (column 1) = water**, **A2 (column 2) = food colouring**. |
| 4 | `corning_96_wellplate_360ul_custom` | Dilution series lives in column 9 (rows A→H). |
| 5 | `corning_96_wellplate_360ul_custom` | **Paper proxy** — a plate object used only as an X/Y/Z coordinate anchor; no liquid is loaded. |
| 9 | `opentrons_96_tiprack_300ul` | Tips. |

**Why these slots (single-nozzle rule):** the dilution runs in single-nozzle
(partial-tip) mode, where the 7 idle nozzles of the 8-channel head hang ~63 mm to one
side. The single-nozzle labware (rack 7, plate 4, tips 9) must therefore stay in the
**middle rows (4-5-6 / 7-8-9)** — off the front row (1-2-3) and back row (10-11-12) —
or a partial-tip move falls outside robot bounds (`PartialTipMovementNotAllowedError`).
With the tall rack at the back of the cluster (slot 7), `single_start=A1` (back nozzle)
points the idle nozzles **forward** over empty/short slots, clearing the 60 mm rack.
Verified end-to-end in simulation.

**Pipette:** `p300_multi_gen2`, right mount (HARDWARE — fixed).

## The vial tube rack (slot 7) geometry

The rack is 2 rows × 4 columns = 8 vials. Pulled straight from the labware JSON:

| Property | Value | Why it matters |
|----------|-------|----------------|
| `diameter` | 28.0 mm | Wide-mouth 20 mL scintillation vial. |
| `depth` | 55.0 mm | Tip descends inside a tall, narrow glass well — wrong geometry = Z crash. |
| well `z` (bottom) | 5.0 mm above deck | Floor of the vial. |
| row spacing (A→B y) | 34.0 mm | Cross-checked in pre-flight. |
| col spacing (1→2 x) | 31.0 mm | Cross-checked in pre-flight. |
| `totalLiquidVolume` | 20000 µL | 20 mL. |

The protocol loads the rack with explicit `namespace`/`version`, so the OT-2 uses
**this** geometry, never a fallback default rack. Aspiration targets the vial with
no X/Y offset → the tip sits on the **volumetric centre axis**; the default
aspiration depth is the API's 1 mm above the well bottom, well within the ~32 mm
liquid column at 20 mL. The pre-flight gate (below) refuses to move if the loaded
geometry doesn't match.

## Execution flow

```
load labware + pipette
  → derive row/column structure FROM the loaded labware objects
  → resolve factors, dilution wells, dilution tips, print tips
  → PRE-FLIGHT (raise to abort, no motion)
  → [dry_run? comment + return]
  → CV: before
  → Phase A: dilution (SINGLE nozzle)
  → Phase B: single-tip print (SINGLE nozzle)
  → CV: after
```

### Resolution is data-driven, not hardcoded

Row labels come from `lw["plate"].rows_by_name()` / `lw["tiprack"].rows_by_name()`,
**not** the literal `"ABCDEFGH"`. So a 384-style rack (16 rows) or a reordered
plate adapts automatically. Destination wells, single tips, and camera capture
wells are all built from these derived rows.

### Pre-flight validation gate (`_preflight`)

Runs **before any motion** and raises `RuntimeError` (aborting the run) on any
mismatch. It checks:

- Deck slot uniqueness.
- Tube rack **identity** (`load_name`) and **well count** (8).
- Vial **diameter** (28 mm) and **depth** (55 mm) within ±`geometry_tolerance_mm`
  (0.5 mm) of `safety:` — read from well A1.
- **Row/column spacing** (34 / 31 mm) derived from the rack's own column ordering
  (no hardcoded well names) within tolerance.
- Plate + paper **well count** (96 each).
- Tiprack **rows-per-column** vs `safety.tiprack_rows_per_column`.
- Pipette **name** and **mount**.
- `len(factors) == len(destination wells)`, and wells fit the plate column.
- **Volume sanity:** `total_volume_ul` ≤ the most conservative plate-well max;
  every stock draw ≤ pipette max; droplet and mix volumes ≤ pipette max; no
  negative volumes.
- `printing.single_tip_columns` does **not** overlap `dilution.single_tip_columns`.
- Every `camera.capture_mid_rows` letter is a real plate row.

On pass it emits non-fatal **accuracy warnings** for any stock draw below the p300
~20 µL accurate minimum (visual-demo tolerated).

### Phase A — dilution (single nozzle)

`configure_nozzle_layout(style=SINGLE, start=A1)` — only the back (A1) nozzle is
active, so the head behaves like a single-channel pipette.

1. **Water pass — one clean tip.** The *first* tip allocated does water only,
   across every well that needs water (`total − stock`). Keeping water on its own
   tip stops dye back-contaminating the water vial.
2. **Stock + mix pass — fresh tip per well.** For each well: a new tip aspirates
   the dye `stock = total / fold` from the FC vial, dispenses it, then mixes. One
   tip per well means zero carry-over between dilutions and a clean FC vial.

Tips are **returned** to their box slots (`return_tip()`), not trashed —
`tips.return_tips: true`. This is the reason for apiLevel 2.28.

### Phase B — single-tip print (one nozzle)

`configure_nozzle_layout(style=SINGLE, start=A1)` — one physical tip/nozzle is active.

1. Pick one print tip at a time from `printing.single_tip_columns` (default column 1).
2. Aspirate `droplet_volume_ul` from one source plate well.
3. Dispense onto the matching paper row, offset per replicate by
   `replicate_spacing_mm` (x/y/z). `dispense_z_mm` is height above the paper-proxy
   well bottom — the tip never touches the paper.
4. Optional `blow_out` / `touch_tip` (both off by default).
5. Return that single print tip, then move to the next source well.

## Tip allocation — the single-tip routine in detail

`resolve_single_tips(dilution_cfg, printing_cfg, n_needed, tiprack_rows)`:

- Walks `dilution.single_tip_columns` **in order**, and within each column walks
  the tiprack rows **in order** (A→H), emitting `f"{row}{col}"` well names.
- Skips the legacy `print_block_column` so dilution tips do not consume the print-tip
  column.
- Needs `n_needed = 1 + len(dilution_wells)` tips (1 water + 1 per dilution well).
- Raises if the listed columns can't supply enough tips.

**⚠ Order matters.** The very first tip emitted (row A of the first listed column)
becomes the dedicated **water-only** tip; the rest are the per-well stock tips.
Reordering `single_tip_columns` changes which physical tips are used but stays
safe — the count and separation invariants still hold.

## Tip headspace during the print (the "P300 air cap")

The print aspirates only `droplet_volume_ul` (15 µL) into a **300 µL** tip, leaving
~285 µL of **air headspace** above the liquid plug. The protocol relies on this:

- It dispenses **above** the paper (`dispense_z_mm`, default 3 mm) — never touching
  down — and lets the droplet fall/contact, so the air column is the buffer that
  keeps the small plug controlled.
- `blow_out` is **off** by default, so that residual air column is *not* expelled
  onto the paper (which would splatter the droplet). Turn `blow_out` on only if you
  deliberately want to clear the tip.
- The mix step is volume-capped to `min(mix_volume_ul, well_fill, pipette.max)`, so
  mixing can never try to pull more than the well holds or more than the tip's
  300 µL — preventing an over-aspiration that would collapse this headspace.

> There is **no explicit `air_gap()` command**; the headspace is the natural
> consequence of a small draw in a large tip plus `blow_out: false`. Don't "fix"
> the small aspiration volume — it is the safety margin.

## CV capture points

Guarded by `protocol.is_simulating()` (no-op in sim). On hardware, `_capture_image`
POSTs to the OT-2 camera HTTP endpoint and writes JPEGs to `robot_image_dir`:
`before_*`, mid-dilution snapshots at `capture_mid_rows` wells, `plate_after_dilution`,
one `paper_print_NN` per replicate, and `after_*`. These feed
[`verify_print_droplets.py`](../../vision_tests/scripts/verify_print_droplets.py).
