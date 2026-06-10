# Vial-Dilution-Print — Parameter Dictionary

Every key in
[`configs/workflows/defaults/vial_dilution_print.yaml`](../../configs/workflows/defaults/vial_dilution_print.yaml).
Each top-level block (except `run_modes`) becomes the protocol's `CONFIG` dict.
"Bounds" are the safe operational range; the builder's `validate()` and the
protocol's `_preflight()` enforce most of them and abort on violation.

Legend: **Type** · **Units** · **Deck/physical impact** · **Safe bounds**.

---

## `deck:` — slot + labware identity

For each of `tuberack`, `plate`, `paper`, `tiprack`:

| Key | Type | Units | Physical impact | Safe bounds |
|-----|------|-------|-----------------|-------------|
| `slot` | int | deck slot # | Where the labware physically sits. | 1–11; **all four must be distinct**. Keep the single-nozzle labware (tuberack, plate, tiprack) in the **middle rows (4-5-6 / 7-8-9)** — out of the front row (1-2-3) and back row (10-11-12) — or partial-tip moves go out of bounds. |
| `load_name` | str | — | Selects the labware geometry the OT-2 uses. | Must exactly match a known/loaded definition; tuberack must equal `safety.expected_tuberack_load_name`. |
| `namespace` | str | — | Custom-labware namespace. | `custom_beta` for the custom rack/plate; omit for standard Opentrons labware. |
| `version` | int | — | Labware definition version. | Must match the JSON's `version` (currently `1`); bump in lockstep when you revise the JSON. |

Verified defaults: tuberack→**7**, plate→**4**, paper→**5**, tiprack→**9**. **Paper**
uses a 96-well plate purely as a coordinate anchor (no liquid loaded).

## `pipette:` — HARDWARE (do not change without re-rigging)

| Key | Type | Units | Physical impact | Safe bounds |
|-----|------|-------|-----------------|-------------|
| `name` | str | — | Which pipette is loaded; sets max volume + channel count. | `p300_multi_gen2` (must match the attached pipette). |
| `mount` | str | — | Physical mount side. | `left` \| `right` (rig is `right`). |
| `single_start` | str | well | Which nozzle is active in single-tip mode; the other 7 idle nozzles hang ~63 mm to one side. | A corner nozzle (`A1`/`H1`). Point idle nozzles AWAY from the tall vial rack: rack at the **back** of the cluster → use **`A1`** (idle forward); rack at the front → `H1`. Default **`A1`** (rack at slot 7). |

## `sources:` — which vial holds what

| Key | Type | Units | Physical impact | Safe bounds |
|-----|------|-------|-----------------|-------------|
| `water_vial` | str | vial well | Vial aspirated for the diluent (water). | A well in the tube rack (`A1`–`B4`); default `A1`. |
| `food_coloring_vial` | str | vial well | Vial aspirated for the dye stock. | A different tube-rack well; default `A2` (same row as water, second rack column). Keep ≠ `water_vial`. |

## `dilution:` — the series

| Key | Type | Units | Physical impact | Safe bounds |
|-----|------|-------|-----------------|-------------|
| `enabled` | bool | — | Run Phase A at all. | `true`/`false`. |
| `destination_column` | str | plate column | Which plate column holds the series (its A→H wells). | A valid plate column (`"1"`–`"12"`); string. |
| `total_volume_ul` | float | µL | Final volume in **every** dilution well; sets fill height. | `0 < total ≤ plate well max` (360 µL) **and** `≤ tip max` (300 µL). Default `200.0`. |
| `mix_reps` | int | cycles | Mix cycles per well after stock dispense; homogenises the dilution. | `≥ 0` (0 disables mixing). Default `3`. |
| `mix_volume_ul` | float | µL | Volume per mix stroke. Runtime-capped to `min(this, well_fill, 300)`. | `0 < v ≤ total_volume_ul` and `≤ 300`. Default `120.0`. |
| `single_tip_columns` | list[int] | tiprack columns | Source columns for single-channel dilution tips, consumed in order. | Each `1`–`12`; must not overlap `printing.single_tip_columns`; must supply `≥ 1 + len(factors)` tips total. Default `[12, 11]`. |

### `dilution.factors:` — fold-factor generator

| Key | Type | Units | Physical impact | Safe bounds |
|-----|------|-------|-----------------|-------------|
| `mode` | str | — | How the fold list is built. | `explicit` \| `geometric` \| `linear` \| `log`. |
| `explicit` | list[num] | fold (×) | Used verbatim when `mode: explicit`. `1` = undiluted stock. | Each `> 0`; `len ≤ plate rows` (8); `stock = total/fold ≤ 300`. Default `[1,2,5,10,20,30,40,50]`. |
| `step_factor` | num | × per step | `geometric`: `factor[i] = start·step_factorⁱ`. | `> 0` (use `> 1` for increasing dilution). Default `2`. |
| `start` | num | fold (×) | First factor for `geometric`/`linear`/`log`. | `> 0`. Default `1`. |
| `end` | num | fold (×) | Last factor for `linear`/`log`. | `> 0`. Default `50`. |
| `count` | int | wells | Number of factors for non-explicit modes. | `1 ≤ count ≤ plate rows` (8). Default `8`. |

> **Derived rule:** number of factors = number of dilution wells used (top A
> downward). A plate column has 8 rows, so keep ≤ 8. `stock_uL = total/fold`,
> `water_uL = total − stock`; both must be ≥ 0 and `stock ≤ 300`.

## `printing:` — the single-tip paper print

| Key | Type | Units | Physical impact | Safe bounds |
|-----|------|-------|-----------------|-------------|
| `enabled` | bool | — | Run Phase B at all. | `true`/`false`. |
| `source_column` | str | plate column | Plate column printed one well at a time. | Valid plate column; usually equals `dilution.destination_column`. |
| `droplet_volume_ul` | float | µL | Volume dispensed per single-tip droplet per replicate. | `> 0` and `≤ 300`; keep small (default `15.0`) to preserve the air headspace. |
| `num_replicates` | int | prints | How many times the column is printed across the paper. | `≥ 1`; ensure `(n−1)·spacing` stays on the paper. Default `4`. |
| `paper_start_well` | str | well | Paper-proxy well used as the spatial origin for the droplet row. | A valid plate well; default `A9`. |
| `dispense_z_mm` | float | mm | Tip height **above the paper-proxy well bottom**. The tip never touches paper. | `> 0`; default `3.0` (≈3 mm above the sheet given the ~5 mm well bottom). Lower cautiously; raise if paper is on a mat. |
| `single_tip_columns` | list[int] | tiprack columns | Source columns for one-at-a-time print tips. | Each `1`–`12`; must not overlap dilution `single_tip_columns`; must supply `≥ len(factors)` tips total. Default `[1]`. |
| `print_block_column` | int | tiprack column | Legacy alias kept for older tooling; no 8-tip block is picked up. | Default `1`. |
| `blow_out` | bool | — | Blow out after each dispense (expels the air headspace). | Default `false` — leave off to avoid splatter unless clearing the tip is intended. |
| `touch_tip` | bool | — | Touch tip to the well wall after dispense. | Default `false` (no wall in the paper proxy). |

### `printing.replicate_spacing_mm:` — offset between replicate prints

| Key | Type | Units | Physical impact | Safe bounds |
|-----|------|-------|-----------------|-------------|
| `x` | float | mm | Horizontal step between replicate columns. | `≥ 0`; `(num_replicates−1)·x` must stay on the paper. Default `9.0`. |
| `y` | float | mm | Vertical-on-deck (front/back) step. | `≥ 0`; default `0.0`. |
| `z` | float | mm | Height step between replicates. | `0.0` for flat paper; non-zero only for stacked replicates (unusual). Default `0.0`. |

## `tips:`

| Key | Type | Units | Physical impact | Safe bounds |
|-----|------|-------|-----------------|-------------|
| `return_tips` | bool | — | `true` → `return_tip()` to the box; `false` → `drop_tip()` to trash. | `true` per lab requirement (needs apiLevel ≥ 2.28 in partial mode). |

## `camera:` — CV capture timing (runtime-only; sim skips)

| Key | Type | Units | Physical impact | Safe bounds |
|-----|------|-------|-----------------|-------------|
| `enabled` | bool | — | Master switch for capture. | `true`/`false`. |
| `capture_before` | bool | — | Snap deck + plate before motion. | — |
| `capture_after` | bool | — | Snap deck + plate after the run. | — |
| `capture_mid_rows` | list[str] | row letters | Plate **rows** to photograph mid-dilution; column appended at runtime (`"C"`→`"C1"`). | Each must be a real plate row; **letters only, no column**. Default `["C","E","H"]`. |
| `robot_image_dir` | str | POSIX path | On-robot JPEG output dir (Linux). | An absolute path on the robot FS; default `/data/vision/vial_dilution_print`. Its basename is reused by `verify_print_droplets.py --run-dir`. |
| `robot_api_url` | str | URL | OT-2 camera capture endpoint. | Default `http://localhost:31950/camera/picture`. |
| `capture_timeout_s` | int | s | curl `--max-time` per capture. | `> 0`; default `5`. |

## `flow_rates:` — µL/s; `null` = Opentrons default

| Key | Type | Units | Physical impact | Safe bounds |
|-----|------|-------|-----------------|-------------|
| `aspirate` | float \| null | µL/s | Aspiration speed. | `null` or within p300 limits. |
| `dispense` | float \| null | µL/s | Dispense speed; affects droplet formation. | `null` or within p300 limits. |
| `mix` | float \| null | µL/s | **Informational only** — the OT-2 API has no separate mix rate. | `null`. |

## `cv:` — host-side droplet QC expectations

| Key | Type | Units | Physical impact | Safe bounds |
|-----|------|-------|-----------------|-------------|
| `expected_droplets` | int | droplets | Expected count; pass `--expect` equal to this. | Should equal the number of dilution wells (8). |
| `min_circularity_ok` | float | ratio 0–1 | Shape-quality threshold for "round". | `0 < v ≤ 1`; default `0.6`. |
| `detection.threshold_method` | str | — | Binarisation method. | `otsu` \| `fixed`. Default `otsu`. |
| `detection.min_area` | int | px² | Smallest blob counted as a droplet (rejects speckle). | `> 0`; default `250`. |
| `detection.invert` | bool | — | Invert mask (dark droplets on light paper). | `true` for this demo. |

## `safety:` — pre-flight geometry cross-check + dynamic bounds

These are the values pre-flight compares the **loaded labware** against. Mismatch
→ abort (this is the glass/Z-crash protection).

| Key | Type | Units | Physical impact | Safe bounds |
|-----|------|-------|-----------------|-------------|
| `expected_tuberack_load_name` | str | — | Identity the loaded rack must match. | `tuberack_3dprint_20ml_8vials_v2`. |
| `expected_well_count` | int | wells | Required vial count. | `8`. |
| `expected_diameter_mm` | float | mm | Required vial mouth diameter. | `28.0` ±`geometry_tolerance_mm`. |
| `expected_depth_mm` | float | mm | Required vial depth (Z travel budget). | `55.0` ±tol. |
| `expected_row_spacing_mm` | float | mm | Required A→B centre spacing. | `34.0` ±tol. |
| `expected_col_spacing_mm` | float | mm | Required col-1→2 centre spacing. | `31.0` ±tol. |
| `geometry_tolerance_mm` | float | mm | Allowed deviation on all the above. | `0.5` (tighten with caution). |
| `pipette_min_accurate_ul` | float | µL | Below this a stock draw only warns (visual demo). | `20.0` for p300. |
| `expected_plate_well_count` | int | wells | Required plate + paper well count. | `96`. |
| `tiprack_rows_per_column` | int | rows | Expected tiprack rows/column (tip-count math). | `8` (96-tip rack); update if rack changes. |
| `pipette_max_volume_ul` | float | µL | Hard cap for stock/mix/droplet draws. | `300.0` (p300). |

## `run_modes:` — NOT part of CONFIG (written into `DEFAULT_*` flags)

| Key | Type | Physical impact | Safe bounds |
|-----|------|-----------------|-------------|
| `dry_run` | bool | Load + pre-flight + comments only; **no liquid motion**. | `false` for a real run; `true` to validate the deck. |
| `do_dilution` | bool | Enable Phase A. | `true`/`false`. |
| `do_print` | bool | Enable Phase B. | `true`/`false`. |

In the Opentrons App these surface as Runtime Parameters the operator overrides per
run; the builder bakes them into the generated file's `DEFAULT_*` constants for
headless/simulation runs.
