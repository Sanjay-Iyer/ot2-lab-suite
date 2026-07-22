# Blue/orange print: OT-2 versus phone camera

Generated on 2026-07-14 with the offline computer-vision workflow in the
`ai` conda environment. The expected print is 6 columns by 8 dilution rows:
48 intended positions. In the analyzed image view, columns 1-3 are blue and
columns 4-6 are orange.

## Inputs and scope

- OT-2 fixed camera: `after_deck.jpg` and `after_plate.jpg`.
- Phone close-up: the three images in `raw/test/camera/blue_orange/`.
- The OT-2 folder currently contains only **two** images. A three-frame OT-2
  conclusion must wait until the third file is added.
- This is offline image analysis only. It does not connect to or move the OT-2.

The same physical paper contains a far-left blue/wet-paper artifact outside the
intended 6-column print. The calibrated grid excludes that artifact. Because
the phone was handheld, frame 1 has a different grid origin from frames 2 and
3. Every generated `annotated.jpg` was visually checked before accepting the
numbers below.

## Headline comparison

| Camera series | Frames | Expected positions | Seen in any frame | Repeatable detection | Never detected | Color resolved in any frame | Shape supported | Coffee ring supported |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| OT-2 fixed camera | 2 | 48 | 35 (72.9%) | 27 in both frames (56.3%) | 13 | 35 | 0 | 0 |
| Phone close-up | 3 | 48 | 43 (89.6%) | 39 in at least 2/3 frames (81.3%) | 5 | 43 | 43 | 43 |

`Never detected` means potentially missing **or below the camera's detection
limit**. It does not prove that the robot failed to dispense.

## Per-image quantitative results

| Image | Detected / 48 | Detection rate | Broad color agreement among detections | Median measured diameter | Round / irregular (reliable only) | Coffee ring strong / possible / not evident |
|---|---:|---:|---:|---:|---:|---:|
| OT-2 after deck | 30 | 62.5% | 30/30 (100%) | 1.93 px | unsupported | unsupported |
| OT-2 after plate | 32 | 66.7% | 24/32 (75.0%) | 0.80 px | unsupported | unsupported |
| Phone frame 1 | 40 | 83.3% | 40/40 (100%) | 130.44 px | 18 / 22 | 17 / 9 / 14 |
| Phone frame 2 | 37 | 77.1% | 37/37 (100%) | 138.76 px | 20 / 16; 1 unresolved | 13 / 13 / 10; 1 unresolved |
| Phone frame 3 | 39 | 81.3% | 39/39 (100%) | 128.15 px | 19 / 19; 1 unresolved | 17 / 9 / 12; 1 unresolved |

Broad color agreement treats blue/cyan as the expected blue family and
orange/yellow as the expected warm orange family. Across all detected
frame-position observations, the phone agreed with the expected family in
116/116 cases. The OT-2 agreed in 54/62 cases; the eight disagreements were
green labels in `after_plate`, showing that OT-2 color is more sensitive to
lighting and its very small pixel footprint.

## Missing-position check by column

Each column should contain 8 dilution rows.

| Camera series | Column | Seen in any frame | Repeatable | Rows never detected |
|---|---:|---:|---:|---|
| OT-2 | C1 | 8/8 | 7/8 in both | none |
| OT-2 | C2 | 7/8 | 6/8 in both | R1 |
| OT-2 | C3 | 8/8 | 6/8 in both | none |
| OT-2 | C4 | 3/8 | 2/8 in both | R1, R2, R3, R4, R5 |
| OT-2 | C5 | 2/8 | 2/8 in both | R1, R2, R3, R4, R5, R7 |
| OT-2 | C6 | 7/8 | 4/8 in both | R5 |
| Phone | C1 | 8/8 | 8/8 in at least 2/3 | none |
| Phone | C2 | 8/8 | 8/8 in at least 2/3 | none |
| Phone | C3 | 8/8 | 8/8 in at least 2/3 | none |
| Phone | C4 | 6/8 | 6/8 in at least 2/3 | R1, R2 |
| Phone | C5 | 7/8 | 4/8 in at least 2/3 | R2 |
| Phone | C6 | 6/8 | 5/8 in at least 2/3 | R1, R2 |

The phone sees all 24 blue positions at least once. Its five never-detected
positions are all among the palest orange rows. The OT-2 loses most of the faint
signal in orange columns C4 and C5.

## Shape and print quality

OT-2 morphology is intentionally unsupported: its measured footprints are
about 1-2 pixels across, far below the 15-pixel/80-pixel shape gate. The phone
has a median footprint near 130 pixels and supports morphology for 114 of 116
detected frame-position observations.

Across those 114 reliable phone observations:

- 57 (50.0%) were classified round;
- 57 (50.0%) were classified blob/irregular;
- median circularity was 0.837; and
- median aspect ratio was 1.266.

These are repeated observations of the same print, not 114 independent
physical droplets.

## Coffee-ring effect

Across the same 114 reliable phone observations:

- 47 (41.2%) showed a strong coffee ring;
- 31 (27.2%) showed a possible coffee ring;
- 36 (31.6%) had no evident coffee ring;
- median edge-to-center ring ratio was 1.440; and
- median edge-minus-center contrast was 2.321 LAB-evidence units.

Therefore, 78/114 (68.4%) reliable phone observations showed a strong or
possible ring. At the unique-position level, 34/48 expected positions showed a
strong or possible ring in at least one phone frame. No OT-2 coffee-ring value
is reported because the pixels cannot resolve a center and outer edge.

## Scientific conclusion

1. The phone is clearly better for this print: higher count recovery, stable
   blue/orange family recognition, defensible shape metrics, and defensible
   coffee-ring metrics.
2. The OT-2 image can provide a coarse in-workflow check for stronger droplets,
   but it is not sufficient for complete 48-drop counting in this setup.
3. The OT-2 image cannot quantify footprint shape or coffee-ring morphology at
   the present camera distance and resolution.
4. A third OT-2 frame should be added before comparing three-frame repeatability.
5. Missing calls must remain phrased as potentially missing or below detection
   until a manual close-up or another calibrated camera confirms absence.

## Reproduce the comparison on the home laptop

```powershell
conda activate ai
python vision_tests\scripts\analyze_print_quality.py `
  --suite `
  --config vision_tests\configs\blue_orange_camera_comparison.yaml `
  --out vision_tests\outputs\blue_orange_camera_comparison
```

Primary outputs:

- `vision_tests/BLUE_ORANGE_DROPLET_AUDIT.md`: photograph-oriented tables for
  manually checking every individual detection, color, shape, and coffee-ring call;
- `comparison.csv`: one row per photo;
- `series_comparison.csv`: OT-2 versus phone series summary;
- `series_positions.csv`: row/column repeatability and missing-position audit;
- `all_droplets.csv`: every raw metric; and
- each image folder: `analysis.json`, `droplets.csv`, `annotated.jpg`, and
  `droplet_montage.jpg`.
