# Guide: Vial Dilution → 8-Channel Paper Print Demo

End-to-end demo that builds a food-colouring **dilution series** in one column of a
96-well plate — drawing water and dye from two 20 mL vials in the custom
[v2 tube rack](tuberack_20ml_8vials_v2.md) — then picks up **8 tips at once** and
"prints" all 8 wells of that column onto **paper** as 8 simultaneous droplets. Tips
are **returned to the box, never trashed**. Computer-vision snapshots are taken at the
start, a few middle steps, and the end, and a CV script logs a reviewable verdict
(did we get all 8 droplets? what colour/shape?).

| File | Role |
|------|------|
| [`configs/workflows/defaults/vial_dilution_print.yaml`](../configs/workflows/defaults/vial_dilution_print.yaml) | **The config — edit this.** All knobs + defaults |
| [`scripts/build_vial_dilution_print.py`](../scripts/build_vial_dilution_print.py) | Builds a robot-ready protocol from the YAML, then simulates |
| [`src/protocols/vial_dilution_print.py`](../src/protocols/vial_dilution_print.py) | The protocol template (embedded `CONFIG` + pre-flight + camera capture) |
| [`scripts/validate_vial_print.py`](../scripts/validate_vial_print.py) | Multi-case simulation gate (asserts on output text) |
| [`vision_tests/scripts/verify_print_droplets.py`](../vision_tests/scripts/verify_print_droplets.py) | CV droplet count/colour/shape → CV log |
| [`vision_tests/lib.py`](../vision_tests/lib.py) | Shared classical-CV pipeline |
| [`labware/tuberack_3dprint_20ml_8vials_v2.json`](../labware/tuberack_3dprint_20ml_8vials_v2.json) | The vial rack definition |

---

## 0. Configuration — make it modular

Nothing in this demo is hard-coded into the run: everything lives in
[`vial_dilution_print.yaml`](../configs/workflows/defaults/vial_dilution_print.yaml).
Edit the YAML, then build a robot-ready protocol with the config embedded:

```powershell
conda activate ai
python scripts\build_vial_dilution_print.py        # validate -> embed -> simulate
```

The builder validates the YAML, writes
`src/protocols/generated/vial_dilution_print_run_<ts>.py` (and `…_latest.py`) with your
`CONFIG` baked in, and simulates it. That generated file is what you deploy/run on the
robot (the robot can't read the repo YAML — the config must be embedded). For a quick
one-off you can also edit the `CONFIG` dict directly at the top of
`src/protocols/vial_dilution_print.py`.

### Every knob (YAML section → what it controls)

| Section / key | What it adjusts | Default |
|---------------|-----------------|---------|
| `deck.<pos>.slot` / `.load_name` / `.namespace` / `.version` | where each labware sits + its identity | 7/4/5/9 (rack/plate/paper/tips) |
| `pipette.name` / `.mount` | **hardware** — leave as p300_multi_gen2 / right | p300_multi_gen2, right |
| `pipette.single_start` | active nozzle for single-tip work (`A1` back; idle nozzles point off the tall rack) | A1 |
| `sources.water_vial` / `.food_coloring_vial` | **which vial holds water vs dye** | A1 / B1 |
| `dilution.destination_column` | **which 96-plate column** holds the series | "9" |
| `dilution.total_volume_ul` | volume in every well | 200 |
| `dilution.factors.mode` | `explicit` / `geometric` / `linear` / `log` | explicit |
| `dilution.factors.explicit` | **the exact fold list** (1 = stock) | [1,2,5,10,20,30,40,50] |
| `dilution.factors.step_factor` | **geometric step** — "2x" or "10x" each well | 2 |
| `dilution.factors.start`/`end`/`count` | endpoints + **how many dilutions** (geometric/linear/log) | 1 / 50 / 8 |
| `dilution.mix_reps` / `.mix_volume_ul` | mixing after each dilution | 3 / 120 |
| `dilution.single_tip_columns` | tiprack columns the single tips come from | [12, 11] |
| `printing.source_column` | which plate column to print | "9" |
| `printing.droplet_volume_ul` | droplet size | 15 |
| `printing.num_replicates` | **how many times** to print the column on the paper | 4 |
| `printing.paper_start_well` / `.dispense_z_mm` | where/how-high droplets land | A9 / 3 mm |
| `printing.replicate_spacing_mm` | x/y gap between replicate columns | 9 / 0 |
| `printing.print_block_column` | tiprack column grabbed as the 8-tip block | 1 |
| `printing.blow_out` / `.touch_tip` | extra dispense actions | false / false |
| `tips.return_tips` | **return to box (true)** vs trash (false) | true |
| `camera.enabled` / `capture_before` / `capture_after` | CV snapshot toggles | true |
| `camera.capture_mid_rows` | **which middle rows** get a snapshot (dest column appended at runtime) | [C, E, H] |
| `camera.robot_image_dir` | where the robot saves JPEGs | /data/vision/vial_dilution_print |
| `flow_rates.aspirate` / `.dispense` / `.mix` | µL/s (null = default) | null |
| `cv.expected_droplets` / `.min_circularity_ok` / `.detection` | host-side CV check (pass `--expect`) | 8 / 0.6 / otsu |
| `safety.expected_*` / `.geometry_tolerance_mm` | pre-flight geometry cross-check of the rack | v2 geometry |
| `run_modes.dry_run` / `.do_dilution` / `.do_print` | run-mode flags (also App Runtime Parameters) | false/true/true |

### Number of droplets vs number of dilutions
An 8-channel print of one plate column is **8 droplets at once** (one per channel). The
**number of dilutions = number of droplets that carry liquid** — set it via the factor
list length (`explicit`) or `factors.count`. Fewer than 8 leaves the lower channels
printing nothing; keep `cv.expected_droplets` in sync. To print the same series several
times across the paper, raise `printing.num_replicates`.

### Customization examples
```yaml
# 2x serial-style geometric series, 6 wells, in plate column 3:
dilution: {destination_column: "3", factors: {mode: geometric, step_factor: 2, start: 1, count: 6}}
printing: {source_column: "3"}
cv: {expected_droplets: 6}

# water in vial B1, dye in B2; print the column 3 times; trash tips instead of returning:
sources: {water_vial: B1, food_coloring_vial: B2}
printing: {num_replicates: 3}
tips: {return_tips: false}
```

---

## 1. Deck layout

```
        LEFT ───────────────► RIGHT
 back   ┌───────┬───────┬───────┐
 row    │  10   │  11   │  12🗑 │
        ├───────┼───────┼───────┤
        │7 VIAL │   8   │ 9 TIP │   ← rack + tip box (p300 300 µL)
        ├───────┼───────┼───────┤
        │4 PLATE│5 PAPER│   6   │
        ├───────┼───────┼───────┤
 front  │   1   │   2   │   3   │
        └───────┴───────┴───────┘
```

| Slot | Labware | Contents |
|------|---------|----------|
| 7 | `tuberack_3dprint_20ml_8vials_v2` | **vial A1 (back) = water**, **vial B1 (front) = food colouring** (20 mL each) |
| 4 | `corning_96_wellplate_360ul_custom` | dilution column 9 (A9…H9) |
| 5 | `corning_96_wellplate_360ul_custom` | paper target (96-well reference so the 8 channels map to 8 spots) |
| 9 | `opentrons_96_tiprack_300ul` | the "pipette box" |

**Pipette:** `p300_multi_gen2` on the **right mount** (fixed — never changes).
**apiLevel:** `2.28`.

> ### Why these slots + `single_start: A1`
> The dilution runs in single-nozzle (partial-tip) mode, where the 7 idle nozzles of
> the 8-channel head hang ~63 mm to one side. Two rules follow:
> 1. **Keep the single-nozzle labware (rack, plate, tips) in the middle rows
>    (4-5-6 / 7-8-9).** In the front row (1-2-3) or back row (10-11-12) the idle
>    nozzles fall outside robot bounds → a hard `PartialTipMovementNotAllowedError`.
> 2. **Point the idle nozzles off the tall (60 mm) vial rack.** The rack is at slot 7
>    (back of the cluster), so `start="A1"` (back nozzle) makes the idle nozzles hang
>    **forward** over empty/short slots — never back across the rack. (`H1` simulates
>    fine but would sweep the bare idle nozzles over the rack — a real collision risk.)
>
> Verified end-to-end in simulation: rack 7, plate 4, paper 5, tips 9, `A1`.

> ### Why apiLevel 2.28
> Returning a tip to the rack while in **partial** (single-nozzle) configuration is
> blocked **before API 2.28**. We need it so the dilution tips go back to the box
> instead of the trash. 2.28 is the max this Opentrons build (9.0.0) supports. **The
> real robot must also run software that supports API 2.28** for a live run; if it is
> older, lower the level and switch the partial-mode `return_tip()` calls to
> `drop_tip()` (which disposes them).

---

## 2. What it does (step by step)

1. **Pre-flight** — cross-checks the loaded v2 rack geometry (loadName, 8 wells, Ø28,
   depth 55, 31/34 spacing), that the plate/paper are 96-well, the pipette is
   `p300_multi_gen2` right, slots are distinct, and all volumes are in range. Aborts
   with `PRE-FLIGHT VALIDATION FAILED` (no motion) on any mismatch.
2. **CV: before** — `before_deck.jpg`, `before_plate.jpg`.
3. **Dilution (single nozzle, `SINGLE` start `A1`)** — direct dilution, total **200 µL**
   per well, `stock = 200 / fold`:

   | Well | Fold | Stock µL (vial B1) | Water µL (vial A1) |
   |------|------|--------------------|--------------------|
   | A9 | 1× | 200 | 0 |
   | B9 | 2× | 100 | 100 |
   | C9 | 5× | 40 | 160 |
   | D9 | 10× | 20 | 180 |
   | E9 | 20× | 10 | 190 |
   | F9 | 30× | 6.7 | 193.3 |
   | G9 | 40× | 5 | 195 |
   | H9 | 50× | 4 | 196 |

   - **Water pass:** one clean tip (`A12`) distributes water into B9…H9 — the tip only
     ever touches water, so the water vial stays pure.
   - **Stock + mix pass:** a **fresh tip per well** (`B12…H12`, `A11`) draws dye from
     vial B1, dispenses, and mixes — no dye carry-over and the dye vial never sees a
     used tip. Each tip is **returned** to the box.
   - **CV: middle** — a snapshot after wells C9, E9, H9 (`plate_dilution_*.jpg`) plus
     `plate_after_dilution.jpg`.
4. **Print (full 8-channel, `ALL`)** — picks up **column 1 of the tip box (8 tips) as a
   block**, aspirates 15 µL from plate column 9 (all 8 wells at once), and dispenses
   onto the paper → **8 droplets in one shot**, repeated for each of the 4 replicates.
   Tips **returned**.
   - **CV: print** — `paper_print_01.jpg` ... `paper_print_04.jpg`.
5. **CV: after** — `after_deck.jpg`, `after_plate.jpg`.

> ⚠️ **Accuracy caveat.** The 20×–50× stock volumes (4–10 µL) are **below the p300's
> ~20 µL accurate minimum** (the protocol prints a `WARNING` for each). This is fine for
> a **visual water+dye test** (the goal is "do all 8 droplets print, in a gradient"),
> but not for quantitative concentrations. For quantitative work use a two-step
> dilution (make an intermediate, then dilute) or a smaller pipette.

---

## 3. Run it on this (simulation) laptop

All commands from the repo root in the `ai` conda env (which has opentrons 9.0.0 +
cv2 + rich). **Prereq:** opentrons must be **9.x** (API 2.28) — the env already is;
note `requirements.txt` still pins `opentrons<9.0`, which is stale vs the working env.

```powershell
conda activate ai

# 0. (re)generate the v2 labware JSON from its YAML
python scripts\generate_labware.py configs\labware\tuberack_3dprint_20ml_8vials_v2.yaml

# 1. BUILD from the YAML config (edit the YAML first): validate -> embed CONFIG -> simulate
python scripts\build_vial_dilution_print.py

# 1b. (alt) simulate the template with its default CONFIG directly
python src\protocols\simulate_protocol.py src\protocols\vial_dilution_print.py

# 2. ROBUST multi-case gate — the source of truth. Expect: ALL CASES PASSED
python scripts\validate_vial_print.py

# 3. CV log end-to-end (synthesizes a droplet image — no camera needed here)
python vision_tests\scripts\verify_print_droplets.py --mock --expect 8                    # PASS
python vision_tests\scripts\verify_print_droplets.py --mock --expect 8 --inject-missing 4 # FAIL path
```

`validate_vial_print.py` rewrites the protocol's `DEFAULT_*` flags to exercise
`full_run`, `dry_run`, `dilution_only`, `print_only`, and a `wrong_labware`
abort, and scans the simulator **output text** (not the exit code) for errors and the
expected operations.

### Reading the CV log
The CV script writes to `vision_tests/outputs/`:

| File | Contents |
|------|----------|
| `cv_report.txt` | **Skim this.** PASS/FAIL banner, droplets found vs expected, per-droplet well/fold/RGB/brightness/circularity |
| `cv_summary.json` | Machine-readable verdict + per-image stats |
| `cv_results.csv` | One row per detected droplet |
| `annotated/<stem>_droplets.jpg` | Droplets circled + labelled |

The hard PASS/FAIL is the **count** ("found 8, expected 8" vs "found 4") — this is the
"all 8 droplets, not 4" check. Circularity reports print **shape** quality; the RGB /
brightness column shows the **dilution gradient** (`gradient detected` when brightness
trends light→dark down the column).

> In simulation the on-robot camera capture is a **no-op** (`[SIMULATION] Mock photo`),
> so `--mock` synthesizes a representative 8-droplet image to drive the CV pipeline.
> Real droplet counting/colour only has meaning on the real robot's images (§4).

---

## 4. Run it on the real robot (lab laptop)

1. **Deploy the labware** (once): `python -m scripts.deploy --labware labware\tuberack_3dprint_20ml_8vials_v2.json`
   (and import the Corning plate + tip rack via the App if not already present).
2. **Confirm the robot supports API 2.28.** If not, see the apiLevel note above.
3. **Build from your YAML** (`python scripts/build_vial_dilution_print.py`) and **run the
   generated file** `src/protocols/generated/vial_dilution_print_latest.py` via the
   Opentrons App (upload, set Runtime Parameters, Run). Do not use bare
   `opentrons_execute` for this API 2.28 protocol; it does not provide the deck
   configuration this protocol needs.
   Start with **dry_run = True** to confirm loading + pre-flight, then a real run.
4. **Pull images & run CV:** retrieve `/data/vision/vial_dilution_print/*.jpg` (see
   [computer_vision.md](computer_vision.md) for `scp -O`), then run the CV check on
   one printed replicate, e.g.
   `python vision_tests/scripts/verify_print_droplets.py --image <paper_print_01.jpg> --expect 8`.

### Real-robot watch-outs (simulation cannot catch these)
- **Run Labware Position Check** for the vial rack, plate, and paper. Nominal positions
  drift 1–2 mm.
- **Idle-nozzle / vial clearance** in single-nozzle mode — verify by eye with the
  e-stop in hand on the first run (the tall 60 mm vials are the hazard).
- **Sub-minimum volumes** (20×–50×) print imprecise colours — expected (see caveat).
- **Paper height** — `printing.dispense_z_mm` (3 mm above the paper-reference well bottom)
  assumes paper laid flat; adjust if the paper sits high/low.
- **API 2.28 support** on the robot (see above).

---

## Related
- [tuberack_20ml_8vials_v2.md](tuberack_20ml_8vials_v2.md) — the vial rack this uses
- [computer_vision.md](computer_vision.md) — CV acquisition + analysis halves
- [SOP_Simulation_Testing.md](SOP_Simulation_Testing.md) — sim env + gates
- [printing_demo.md](printing_demo.md) — the related single-channel printing workflow
