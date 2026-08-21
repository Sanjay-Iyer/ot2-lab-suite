---
name: 96-well-labware
description: Create one regular 8 x 12 Opentrons custom plate from measured dimensions or the validated paper-plate template.
metadata:
  domain: labware
  agent: custom_labware
  families:
    - well_plate_96
  references: []
---

# Regular 96-well plate procedure

Use this family only for exactly 8 rows by 12 columns of identical wells with even center-to-center row and column spacing. Reject mixed well sizes, irregular spacing, multiple grids, and other labware families.

For the known paper surface, load `paper_print_96_flat_v1` and change only the requested fields. For a new physical part, require measured footprint length/width/height, well volume and shape dimensions, bottom shape, depth, well-bottom Z, X/Y pitch, and the A1 X/Y center. Never guess these values. Prefer manufacturer drawings, validated CAD, or careful measurement.

Coordinates use the Opentrons labware origin at the front-left-bottom. +X points right and +Y points toward the back. A1 is the back-left well center. Columns increase in +X; rows A through H move toward the front, so their Y coordinates decrease.

Populate `96WellPlateSpecV1` (implemented in Python as `WellPlate96SpecV1`). Circular wells accept only `diameter_mm`; rectangular wells accept only `x_size_mm` and `y_size_mm`. Spacing is center-to-center. Offsets locate the A1 center, not an edge. `well_bottom_z_mm + depth_mm` must fit within total height.

Call `generate_96_well_labware` only after the complete schema validates. Its deterministic pipeline creates all 96 wells, ordering, schema-2 JSON, geometry checks, and Opentrons validation before saving. Never construct or edit the 96 well entries manually. Local validation is not physical verification; position-check exclusion does not prove the measurements.
