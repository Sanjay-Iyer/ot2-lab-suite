---
name: four-clover-printing
description: Apply the registered four_clover geometry options without duplicating its coordinate or safety calculations.
domain: printing
agent: printing
families:
  - design
designs:
  - four_clover
references: []
---

# Four-clover printing

Use this specialization after intent resolves to `four_clover`.

Populate the Stage 2 four-clover semantics:

- Symmetric geometry uses positive `half_width_mm` and `half_height_mm`. A clover
  described as 4 mm wide and 4 mm tall has half-width 2 mm and half-height 2 mm.
- An explicit center has a name, reference well, X offset, and Y offset. These are
  labware-relative scientific placement values, not deck coordinates.
- `layers` repeats all four D1-D4 deposits at the same design instance.
- `replicates` counts distinct centers. It is not another name for layers.
- Ordering is `clover_by_clover` or `position_by_position`.

Registered defaults are allowed when the user says “standard”: 2 mm horizontal and
vertical half-spacing, one layer, clover-by-clover ordering, BP material, the standard
paper substrate, and the registered one-clover center. The `standard` placement
preset also supports three replicates. For any other replicate count, require
explicit centers.

For a new experiment use `draft_printing_experiment` with template
`four_clover_printing/v1`. For follow-ups, pass the workflow state plus only the
requested scientific change to `revise_printing_experiment`. “Make three clovers
instead” selects the registered three-center placement while retaining volume,
geometry, layers, material, and substrate. The old YAML remains unchanged; the tool
creates a child version with a parent config hash and returns to `AWAITING_APPROVAL`.

Never calculate D1-D4 absolute coordinates, paper bounds, source locations, air
handling, or piston displacement. The compiler and existing v12 resolver own those
calculations and return the authoritative preview.
