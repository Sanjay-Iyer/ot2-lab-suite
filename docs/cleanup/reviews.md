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

## C1a — leaf script grouping (PASS/PASS)

Starting commit `3839c36`. Moved 19 maintenance/developer scripts according to
inventory.csv; top-level Python scripts 48 → 29. External batch
`C1a-leaf-scripts` preserves all 19 originals with verified SHA/CRC. Changed-file
list: `.test_tmp/C1a-map.changes.json`; mapping `.test_tmp/C1a-map.json`.

Repaired root discovery, direct/module commands, library imports and file-based
test imports. Deployment/sync remain module-only. Connection-check mains were
not executed. `configs/robot.yaml` bytes are unchanged; its historical header
comment is retained. Other config edits are command-path documentation only.

Checks: 19 moved module imports and root checks PASS; four help-only launch
checks PASS (`.test_tmp/C1a-imports.json`); existing mocked SSH tests **9 passed**
in17.48s; V11 offline case `std_18_state_persists_across_turns` **1/1 passed**.
No shared builder/runner relocation or experimental method change in this batch.

Reviewer correction: fixed the current printing SOP to use module-only
`python -m scripts.maintenance.sync_robot`. Old script strings inside immutable
protocol/generated docstrings remain historical comments. Preservation recheck:
393 parsed existing YAML values identical; one pre-existing invalid parameter
reference remains unchanged; all config changes are command paths, and 33
maintained printing protocol/labware/image files are byte-identical.

Final Python **PASS**, laboratory **PASS**, mutual cross-review **PASS**. Python
checked all 19 archived source diffs, runtime/test consumers and 787-module old
import/path scan. Laboratory independently checked 3,026 scientific/data/settings
files and all parsed configs. No required corrections remain.

## C1b — manual and optional experiment scripts (PASS/PASS)

Starting commit `4fb7dff`. Moved 20 scripts to `scripts/manual/` and
`scripts/experiments/`; top-level Python scripts 29 → 9. Preserved originals in
external `C1b-manual-experiments` (20 SHA/CRC verified). Exact mapping and affected
files: `.test_tmp/C1b-map.json` and `.test_tmp/C1b-map.changes.json`.

Only source roots, imports, command/resource paths and prose changed. The original
one-well settings/protocol and all optional operations are preserved. Updated
existing tests and documentation consumers, including file-based numeric imports.
Focused tests **41 passed in5.53s**; one-well offline preview and standard-print
summary exited0. Additional imports/roots20 and offline clover/SERS plans are
recorded in `.test_tmp/C1b-imports.json`. Shared builder/validator/runner remain
top-level until the final script batch. No physical/model/network main executed.

Final Python **PASS**, laboratory **PASS**, mutual cross-review **PASS**. Python
inspected all 20 moved scripts, 19 nonmoved Python consumers and 357 maintained
modules for stale executable references. Lab independently verified 393 parsed
YAML values and 3,026 protected scientific/data/settings files. No corrections
remain; optional methods are retained with unchanged operations.

## C1c — shared tooling and final script index (PASS/PASS)

Starting commit `94d6894`. Moved builder/validator/runner to `scripts/manual/`;
top-level Python scripts 9 → 6. External `C1c-shared-tooling` preserves three
originals. Mapping/change list `.test_tmp/C1c-map*.json`. Updated library imports,
session subprocess dispatch, validator/build commands, registry runner paths,
tests, operator instructions and current docs. Script index now describes six
primary launchers and four functional groups; no duplicate wrappers retained.

`python -B docs/cleanup/checks.py`: all seven checks PASS, evidence
`.test_tmp/cleanup-checks-0c1df8b2c4`. Added two focused dispatch-path assertions
(construct commands only; no live execution). Both pinned simulations, terminal,
GUI loopback, offline config and WORK-profile preview pass. Physical protocol
logic/parameters are unchanged; no credentials or robot connection values edited.

Final Python **PASS**, laboratory **PASS**, mutual cross-review **PASS**. Python
compared prior-dirty consumers to the actual C0 working snapshot, confirmed only
cleanup path replacements, and scanned 357 maintained modules for all 42 moves.
Lab independently reconfirmed 393 YAML value pairs and 3,026 scientific byte pairs.
Focused tests: **43 passed in7.50s**. No required corrections remain.

## C2 — optional agents and historical harnesses (PASS/PASS)

Starting commit `58565da`. Grouped 13 older working modules under
`src/agents/optional/`; NanoDrop, its runtime Skills and Memory stay in place.
External `C2-agent-families` preserves all 15 original files with verified SHA/CRC.
Archived `test_simulation_agent.py` and `run_stress_tests.py` after both reviewers
confirmed no unique liquid-handling implementation: the former uses removed
tools, the latter only counts returned responses to historical prompts.

Updated absolute imports, file paths, tests and docs; sibling-relative imports
retain their meaning. Current SOP no longer advertises archived harness commands.
No compatibility duplicates or eager optional-package imports were added.
`check_models.py` retains its existing import-time network behavior; AST only
on HOME. The other 12 optional modules import successfully.

Focused existing NanoDrop + generic mock/dependency checks: **46 passed in15.46s**,
two existing upstream deprecation warnings. Evidence `.test_tmp/C2-imports.json`;
mapping/changed files `.test_tmp/C2-map.json`, `.test_tmp/C2-changes.json`.

Final Python **PASS**, laboratory **PASS**, mutual cross-review **PASS**. Python
verified all 15 archive pairs and the 355-module old-reference scan; prior-dirty
test changes were compared with C0. Lab independently checked 415 method/resource
pairs and all moved source diffs. Corrected the model-catalog root comment;
no method or runtime behavior changes. No required corrections remain.

## C3a — legacy protocol classification (PASS/PASS)

Starting commit `59a0ea3`. Moved seven templates for IDs1,2,3,4,6,7,8 byte-identically
to `src/protocols/printing/legacy/`; external originals in `C3a-legacy-protocols`.
Registry IDs, stems, default configs, capabilities and pinned-v3 handling remain.
Updated `_legacy` registry prefix, builder default, validator fallback paths,
literal file tests and current documentation references. Original method/header
bytes are preserved, including historical comments; YAML contains no active
references to former template paths (only two historical comments).

Existing focused checks: registry/default/compatibility tests **3 passed**, seven
unselected; existing API2.15-v3 + NanoDrop fake-protocol regressions **31 passed**.
Mapping/change files `.test_tmp/C3a-map*.json`. No liquid-handling algorithm,
physical parameter, experiment record or generated protocol content changed.

Final Python **PASS**, laboratory **PASS**, mutual cross-review **PASS**. Both
independently checked all seven original/archive/new template bytes and unchanged
version/API/default semantics. Corrected the scoped changelist to include the
critical registry prefix and both handwritten indexes. No corrections remain.

## C3b — experiment YAML grouping (PASS/PASS)

Starting commit `900b3b3`. Moved 46 formerly flat experiment YAML files byte-for-byte
into paper_printing (2), sers (2) and specialized (42). NanoDrop example directories,
shared base, machine profiles, labware and template catalogs stay in place.
External `C3b-experiment-configs` preserves 46 originals; all archive/current byte
pairs passed. Recursive YAML scalar audit found no include fields referencing the
moved files; no experiment-root glob discovery was found. Original method headers
and immutable run/provenance records retain historical paths, with mapping here.

Repaired manual defaults, optional workflow lookup, SERS default, layered scripts,
file-based tests, exporter inputs and current command guide. Standard/clover
canonical job/plan hashes are unchanged; SERS offline plan exits0. Evidence:
`.test_tmp/C3b-before-plans.json`, `C3b-verification.json`, `C3b-map*.json`.

Existing A11/manual workflow checks: **31 passed, 2 pre-existing failures**. The
failures are the already documented1.1-versus0.5mm print-height expectation and
clover frozen-engine comparison; relevant method/config bytes are unchanged.
Do not change physical parameters or methods to make these tests green. These
remain WORK-validation/method-history issues, not relocation regressions.

Preserved descriptive metadata in the maintained clover protocol also mentions
its original YAML location. It is not opened at runtime; it remains immutable
method provenance alongside historical header comments.

Final Python **PASS**, laboratory **PASS**, mutual cross-review **PASS**. Both
verified all 46 YAML byte pairs and durable relocation associations; canonical
standard/clover fingerprints match. Corrected two relative paths in the current
config index and included that file in the scoped commit. No required cleanup
corrections remain; the two pre-existing method failures are retained explicitly.
