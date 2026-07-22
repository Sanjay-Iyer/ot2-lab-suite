# Representative printed-droplet test images

This folder is the controlled offline CV benchmark set.  The files are local
photos only; running analysis against them never connects to an OT-2.

## Count and color benchmarks

- `OT-2/after_deck.jpg` and `after_plate.jpg`: two fixed-camera views of the
  same 6-column by 8-row blue/orange paper print (48 expected positions). A
  third OT-2 image has not yet been added.
- `camera/blue_orange/PXL_20260616_194303424.jpg` through
  `...194305768.jpg`: three phone views of that same blue/orange print.
- `camera/gold/PXL_20260618_171036344.jpg`: full phone view of the 6-column by 8-row
  purple/pink print (48 expected positions).

## Detail references

- `camera/gold/PXL_20260618_171057903.jpg`, `...171101789.jpg`, and
  `...171546721.MP.jpg`: progressively closer purple/pink views for shape and
  coffee-ring inspection.  Some crop the grid, so they are not used as 48-drop
  count benchmarks.
The benchmark definitions and calibrated grid coordinates live in
`vision_tests/configs/print_quality.yaml`.

The complete run instructions, current camera comparison, and interpretation
rules are in `vision_tests/PRINT_QUALITY_WORKFLOW.md`.

A detailed teaching and study guide is in
`vision_tests/COMPUTER_VISION_STUDY_GUIDE.md`.
