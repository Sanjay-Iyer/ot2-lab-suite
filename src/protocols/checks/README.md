# Maintained instrument checks

These three handwritten protocols were formerly mixed into generated output.
Their source bytes and physical parameters are unchanged:

- `smoke_test.py`: API2.15 P20 dry-motion check.
- `p300_smoke_test.py`: API2.15 P300 dry-motion check.
- `p300_single_nozzle_smoke_test.py`: API2.28 P300 single-nozzle check.

The corresponding scripts remain in `scripts/manual/` and select these paths.
API2.28 requires appropriate instrument/software support; it is distinct from
the pinned API2.15 environment. Simulation and static checks are not physical
validation. Existing explicit physical-execution flags remain unchanged.
