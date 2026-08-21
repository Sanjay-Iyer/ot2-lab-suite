---
name: standard-paper-printing
description: Select and safely prepare exact-well paper printing workflows on the registered discrete grid.
domain: printing
agent: printing
families:
  - standard
designs: []
references: []
---

# Standard paper printing

Use this skill when intent is expressed as paper wells, rows, columns, layers, or
replicate columns on the registered discrete paper grid.

Construct a scientist-facing `well_conditions` layout. Each condition records:

- a meaningful name,
- `drops_per_position`, and
- its logical replicate `wells`.

The deterministic config loader converts this layout into a strict
`well_selection` PrintJobV1 pattern:

- `rows`: unique rows A-H in requested order.
- `columns`: unique columns 1-12 in requested order.
- `layers_by_row`: exactly one positive layer count for every selected row.
- `volume_ul`: liquid deposited at each selected well on each applicable layer.

The selected wells are the Cartesian product of rows and columns. “A1 and A2” is
rows `[A]`, columns `[1, 2]`; “rows A-B, columns 1-2” produces A1, A2, B1, B2.
If a requested irregular list is not a Cartesian selection, ask for clarification;
do not silently add wells.

Columns are the distinct replicate placements for the proven v9 family. For a
request such as “1, 2, and 3 drops in triplicate,” a bounded default layout is:

- 1 drop: A1, A2, A3
- 2 drops: B1, B2, B3
- 3 drops: C1, C2, C3

`drops_per_position` means repeated deposition events at the same location; it is
not one larger-volume dispense. Conditions must use distinct rows and the same
replicate columns so the trusted v9 Cartesian-layer behavior remains exact.

Use registered substrate `paper_print_96_flat` for “our standard paper plate.”
The registered standard material is `sample`. These may be omitted from the tool
call when the registered defaults are intended. A missing row layer defaults to
nothing: every selected row must be stated or explicitly assigned one layer.

Submit the scientific fields with `draft_printing_experiment` using template
`standard_paper_printing/v1`. Python creates and validates a new versioned YAML and
returns `AWAITING_APPROVAL`. Do not compile or simulate until explicit approval.
Never calculate paper/deck coordinates, source wells, air gaps, piston volumes, or
pipette settings.
