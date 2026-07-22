# Printed-droplet computer-vision workflow

This workflow is offline image analysis only. It reads photographs and writes
reports; it does not connect to or move an OT-2.

For a detailed explanation of the algorithms, equations, libraries, output
fields, and presentation talking points, see
`vision_tests/COMPUTER_VISION_STUDY_GUIDE.md`.

## What it measures

For every expected grid position, the analysis reports:

1. detected, borderline, not detected, or unassessable because of a background artifact;
2. observed color, using the strongest drops in the same dilution column to stabilize faint-drop color;
3. round versus blob/irregular shape when the image has enough pixels;
4. strong, possible, or not-evident coffee-ring effect when the image has enough pixels; and
5. the exact potentially missing or below-detection rows in each 8-drop column.

The output includes JSON and CSV data, a full-image grid overlay, and a
row-by-column crop montage for visual auditing.

## Current blue/orange comparison (2026-07-14)

The corrected direct comparison uses the same intended 6 x 8 print for both
cameras. The OT-2 test folder currently contains two images, not three.

| Image series | Frames | Expected | Seen in any frame | Repeatable detection | Shape supported | Coffee-ring supported |
|---|---:|---:|---:|---:|---:|---:|
| OT-2 fixed camera | 2 | 48 | 35 | 27 in both frames | 0 | 0 |
| Phone blue/orange | 3 | 48 | 43 | 39 in at least 2/3 frames | 43 | 43 |

Important interpretation:

- The earlier OT-2 `24 expected` result used an incorrect 3 x 8 calibration.
  The corrected print layout is 6 x 8, so every image expects 48 positions.
- OT-2 frames detected 30/48 and 32/48. Across both frames, 35 positions were
  seen at least once and 27 were seen in both.
- Phone frames detected 40/48, 37/48, and 39/48. Across the three frames, 43
  positions were seen at least once and 39 were seen in a strict majority.
- The phone supported shape and coffee-ring measurements at 43 unique
  positions. The OT-2 droplets were about 1-2 pixels wide, so both morphology
  results were deliberately marked unsupported.
- "Not detected" means potentially missing **or below that camera's detection
  limit**. It is not proof that the robot failed to print a droplet.

The complete tables, color agreement, per-column missing rows, shape results,
and coffee-ring results are in `vision_tests/BLUE_ORANGE_CAMERA_COMPARISON.md`.
The position-by-position manual review tables are in
`vision_tests/BLUE_ORANGE_DROPLET_AUDIT.md`.

## Run only the direct blue/orange camera comparison at home

```powershell
conda activate ai
python vision_tests\scripts\analyze_print_quality.py `
  --suite `
  --config vision_tests\configs\blue_orange_camera_comparison.yaml `
  --out vision_tests\outputs\blue_orange_camera_comparison
```

## Run the complete reference suite at home

Use the simulation-laptop environment:

```powershell
conda activate ai
python vision_tests\scripts\analyze_print_quality.py --suite
```

The main summaries are written to:

- `vision_tests\outputs\print_quality\comparison.csv`
- `vision_tests\outputs\print_quality\series_comparison.csv`
- `vision_tests\outputs\print_quality\series_positions.csv`

Each named image also gets `analysis.json`, `droplets.csv`, `annotated.jpg`,
and `droplet_montage.jpg` in its own output folder.

## Run only computer vision on the real-robot laptop

Use the real-laptop environment, but pass only already-captured local images:

```powershell
conda activate llm
python vision_tests\scripts\analyze_print_quality.py `
  --image C:\path\frame_1.jpg C:\path\frame_2.jpg C:\path\frame_3.jpg `
  --profile ot2_fixed `
  --series-name work_laptop_ot2_check
```

This command performs no live run and needs no robot IP. Capture at least three
frames without moving the paper. The `ot2_fixed` calibration assumes the same
fixed-camera view and paper placement as the representative images. If the
overlay misses the printed grid, recalibrate the normalized grid coordinates
in `vision_tests\configs\print_quality.yaml` before trusting counts.

## Visual acceptance check

Before accepting any CSV count:

1. Open `annotated.jpg` and confirm every circle is centered on its expected grid cell.
2. Open `droplet_montage.jpg` and check red/orange cells manually.
3. Use `series_positions.csv` to investigate positions seen in only one frame.
4. Treat shape and coffee-ring values as valid only when their reliability fields are true.
