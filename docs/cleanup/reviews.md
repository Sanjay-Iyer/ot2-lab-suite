# Cleanup evidence and independent reviews

## C0 — baseline (PASS/PASS)

- Branch `01_paper`, starting commit `0129290`; all earlier dirty/untracked work
  preserved as starting state, without accepting earlier R0/feature candidates.
- External `20261031/C0-before-cleanup`: 9,230 readable files, 1,104,774,814-byte
  ZIP; all source/archive SHA-256 pairs and ZIP CRC passed. All-ref Git bundle
  verified. Private manifest, binary patch, status and 11 worktree registrations
  retained. Credentials are private and excluded from public reporting.
- Exact six previously known locked `raman/results/.pytest_tmp_20260728_02` through
  `_07` remain untouched and unpreserved by this new snapshot. Independent
  traversal with those exact exclusions found zero unknown access errors.
  Mixed `.test_tmp` and pinned `.venv` remain intact and have earlier preservation
  evidence; they are not treated as disposable wholesale.
- `conda activate ai; python -B docs/cleanup/checks.py`: PASS; evidence
  `.test_tmp/cleanup-checks-57a4bef780`. Pytest 41 passed in 7.60 s; two pinned local
  simulations exited 0; offline instrument config and WORK launch preview 0;
  terminal startup/quit 0; NiceGUI loopback HTTP200/title passed, process stopped.
- Historical broad baseline 2227 passed /25 failed /5 skipped is previous evidence,
  not a new full-suite run. No attempt to fix unrelated historic failures.
- Both reviewers' initial inspection and cross-check: retain specialized
  dilution/V11/SERS/clover/layering/API2.28 checks as OPTIONAL. Registry-deprecated
  IDs remain callable until their paths are updated, not silently removed.
- Pre-existing method ambiguity: layer configuration16 already has a 7,200 s
  hold/CV overprint, while configuration17 also has a 7,200 s initial hold. Retain
  both configs and sequence unchanged; physical method interpretation is pending.

No physical execution, model/cloud call, deployment, push or new branch/worktree.

Final independent verdicts: Python **PASS**, laboratory **PASS**, mutual
cross-review **PASS**. Python checked the full ZIP digest, exact 9,230 safe members,
38 critical pairs and the 43-ref bundle; laboratory checked 586 critical scientific
pairs and current traversal/link metadata. Both approved only this cleanup-docs
checkpoint. No prior experimental implementation is implicitly accepted.
