# Repository cleanup — 2026-10-31

Only `01_paper` is developed. This is HOME: offline software work and simulations
only. No hardware, cloud calls, pushes, new branches or new worktrees.

Baseline: HEAD `0129290`, 48 top-level Python scripts, 430 local generated Python
protocols (36 tracked), 11 registered worktrees. Earlier uncommitted reorganization
and historical candidates are starting state, not newly accepted implementation.

One inventory/manifest is maintained in `inventory.csv`; batch evidence and both
independent read-only reviews are in `reviews.md`. Only the coordinator edits.

Private external archive: `C:\code\opentrons_home\ot2-lab-suite-archive\20261031`.
Each batch contains `files.zip` and `manifest.private.json` with original paths,
source commit, SHA-256, sizes and restoration instructions. Credentials and
scientific data in the baseline archive are private; never publish its manifest.
Restore selected members to a new directory and verify hashes before use.

The pinned environment and mixed `.test_tmp` tree remain in place at baseline.
Earlier verified preservation snapshots remain independently available under
`ot2-lab-suite-preservation`; no claim is made that ignored caches are empty.

| Phase | Status |
| --- | --- |
| C0 baseline and inventory | PASS/PASS; external snapshot verified |
| C1 scripts | PASS/PASS; 48 → 6 top-level scripts; 42 grouped, callers repaired |
| C2 agent families | PASS/PASS; 13 optional modules grouped, 2 historical harnesses archived |
| C3 protocols/configs | PASS/PASS; 7 legacy templates and 46 YAMLs grouped byte-identically |
| C4 generated artifacts/caches | Pending |
| C5 documentation | Pending |
| C6 old worktrees | Pending |
| C7 final audit | Pending |
