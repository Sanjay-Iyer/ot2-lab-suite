# Python architecture audit

Date: 2026-07-28

Scope: the first YAML-only Raman analysis and filename-migration implementation
under `raman/`, audited against the complete user requirements. This review was
read-only except for this report. I inspected the implementation, configs,
focused tests, legacy workflow, documentation, the checked-in raw spectrum, and
an existing generated run. I did not rerun tests or workflows as part of this
report-only audit.

## Executive assessment

The implementation has a sound functional core. The exact routine command
`python raman\analyze_raman.py` reads a fixed YAML file without CLI flags; the
separate renamer does the same; every selected spectrum uses one shared
processing function; canonical filenames feed sample/position metadata into
titles and grouping; and plots are organized both by spectrum and in aggregate
collections. The implementation also preserves the legacy CLI tools.

The main release blocker is filename-to-position safety. With
`sort_by: scan_number`, positions are assigned by enumerating the sorted files,
not by the scan-number offset. A missing or duplicate scan therefore silently
shifts row/column assignments. Because those assignments become scientific
metadata and drive grouped comparisons, this is classified Critical.

Beyond that blocker, the most important corrections are strict YAML validation,
slug/output collision validation, requested-overlay failure semantics,
transactional rename/output behavior, parser/config consistency, and splitting
the 819-line orchestration module into the existing responsibility modules.

Severity totals: 1 Critical, 12 Major, 8 Minor, and 5 Recommendations.

## Critical findings

### C-01 — Scan gaps or duplicate scans silently misassign plate positions

**Evidence:** `raman/src/raman_lib/naming.py` sorts files by parsed scan number,
then `plan_renames()` passes the enumeration offset to `_position()`. The
numeric scan is not used to determine the position. For example, scans 649 and
651 are assigned A1 and A2 rather than preserving the missing A2 position; two
files that both parse as scan 649 are assigned two different positions.

**Impact:** one missing, duplicated, or accidentally included input can shift
all subsequent column/row metadata while producing plausible filenames and no
warning. Downstream group plots would then compare mislabeled locations.

**Corrective action:** add an explicit YAML mapping mode. The safe default for
scan-number sorting should require `start_scan_number` and calculate
`position_index = scan_number - start_scan_number`. Reject duplicate scans,
negative/out-of-capacity offsets, and gaps unless an explicit
`allow_scan_gaps` policy is configured. If pure enumeration remains available,
name it clearly (for example `position_mapping: sorted_order`) and emit a
prominent manifest warning. Add tests for gaps, duplicates, out-of-order files,
and the A1-A8/B1 mapping boundary.

## Major findings

### M-01 — YAML validation accepts unknown keys and several invalid value types

**Evidence:** `workflow_config._deep_merge()` retains arbitrary keys, while the
validators check only a subset of the schema. A typo such as `overaly`,
`minimum_prominance`, or an unknown output switch is silently retained and
ignored. Booleans such as `analysis.timestamped_run_directory`,
`smoothing.enabled`, `baseline.enabled`, plot `enabled` switches,
`normalization.allow_invalid_target`, and output switches are not consistently
type-checked. Bounds for `baseline.convergence_ratio`,
`minimum_points_for_width`, and `minimum_prominence` are incomplete.

**Impact:** a run can succeed while not applying the setting the user thought
they changed, violating the requirement to validate YAML before analysis.

**Corrective action:** define a closed schema with dataclasses, TypedDict plus a
strict validator, Pydantic if already accepted by the repository, or explicit
allowed-key tables. Reject unknown keys with the full dotted path, validate all
boolean/numeric/list types and bounds, and normalize validated values once.
Validate that the expected target lies inside the search window and that the
integration window is scientifically and numerically usable.

### M-02 — Unique labels do not guarantee unique output paths

**Evidence:** the new case-insensitive display-label check is useful, but
outputs use `io_utils._slug(label)`. Distinct labels such as `A B`, `A_B`, and
`A/B` can produce the same slug. Group selection names and target names can
similarly slug-collide. Group names are described as unique in an error message,
but duplicate selection names are not actually detected.

**Impact:** processed CSVs, per-spectrum plots, aggregate copies, group plots,
or overlay data can be overwritten or associated with the wrong summary entry.

**Corrective action:** during configuration validation, compute every planned
slug and output path and require uniqueness case-insensitively. Detect duplicate
group selection names and collisions between generated partition names. Prefer
a stable identifier derived from canonical sequence/file metadata over a
display label.

### M-03 — An enabled overlay can be skipped while the command exits successfully

**Evidence:** when fewer than two selected spectra have valid normalized data,
`_create_batch_plots()` logs a warning and skips the requested overlay.
`analyze_raman.py` returns failure only when `summary["n_failed"]` is nonzero;
batch-output warnings do not affect status.

**Impact:** automation and a user looking only at the exit code can see success
even though a specifically requested output was not created. This does not meet
the requirement to fail clearly when fewer than two valid spectra are selected.

**Corrective action:** validate the static overlay selection count before
processing. After processing, represent missing requested overlays/groups as
structured output failures, include them in a `run_status`/failure count, and
return a nonzero exit code (or a clearly documented degraded status) when a
required configured product cannot be generated.

### M-04 — The new orchestration module combines too many responsibilities

**Evidence:** `analysis_workflow.py` is approximately 819 lines and contains
peak characterization, normalization, processed-frame construction, file
writing, title formatting, collection copying, group selection, plotting
orchestration, run-directory creation, logging setup, and main orchestration.
Peak metrics and normalization also overlap responsibilities already represented
by `detection.py` and `preprocessing.py`.

**Impact:** scientific algorithms, filesystem behavior, and orchestration are
tightly coupled, increasing regression risk and making independent testing and
future extensions harder.

**Corrective action:** move target peak characterization into `detection.py`,
normalization into `preprocessing.py`, and run/manifest/output writing into a
small output module. Keep `analysis_workflow.py` as orchestration over typed
inputs/results. Reuse the existing dispatch patterns rather than maintaining
parallel scientific APIs.

### M-05 — Custom rename patterns are not compatible with the fixed analyzer parser

**Evidence:** the rename YAML advertises a configurable `filename_pattern` and
validation permits any ordering containing the four required tokens.
`parse_filename_metadata()` recognizes only the single fixed canonical order
`sequence__sample__column__row`.

**Impact:** the renamer can successfully create a documented/config-valid
filename that the analysis later treats as noncanonical, silently losing
sample, column, and row metadata and excluding it from expected groups.

**Corrective action:** either make the canonical filename pattern fixed and
remove misleading configurability, or share one compiled parser/formatter
schema between renaming and analysis. A manifest-driven metadata lookup is
another robust option. Add a round-trip invariant test for every allowed
pattern: `format -> parse -> same metadata`.

### M-06 — Rename execution is not transactional and can lose its audit trail

**Evidence:** files are copied/moved one at a time. An I/O failure leaves a
partially completed batch. The manifest is written only after all operations
return, so a partial move can occur without any persisted record of completed
items. `overwrite: true` increases the consequence.

**Impact:** source and destination trees can be left in an ambiguous partial
state, especially for `operation: move`.

**Corrective action:** persist the complete planned manifest before mutation;
write per-item completion/error status durably as operations occur; copy to
temporary destination names, verify size or checksum, then atomically rename.
For moves, either implement rollback or explicitly prohibit move until a
verified copy manifest exists. Preserve/version manifests instead of blindly
rewriting the only audit record.

### M-07 — Input/output overlap can cause renamed outputs to be ingested later

**Evidence:** destination and manifest containment inside `output_root` is now
checked, which is a strength, but no check prevents `output_root == input_root`
or recursive `file_glob` patterns from traversing an output directory nested
under the input. A generated manifest can also match a broad CSV glob.

**Impact:** a subsequent migration can treat prior outputs or its own manifest
as new source files, causing collisions, sequence shifts, or incorrect
positions.

**Corrective action:** resolve both roots and reject equality; when output is
nested under input, explicitly prune it from discovery. Reject or carefully
handle recursive globs, and always exclude the configured manifest and already
canonical outputs unless an explicit remigration mode is enabled.

### M-08 — Automatic header detection is not aligned with configurable columns

**Evidence:** `_looks_like_header()` examines only the first two fields, while
the configured Raman-shift/intensity columns may be later fields or named
columns. It also uses simple string splitting while pandas uses the Python CSV
engine. A file with numeric metadata in its first two fields and named spectral
columns later can be misdetected as headerless.

**Impact:** valid repository-compatible CSVs can fail column lookup or lose the
header row; worse, coerced rows may be silently discarded.

**Corrective action:** use Python's `csv` parser with the configured delimiter,
inspect the configured candidate columns, and prefer explicit `has_header` when
named selectors are used. Validate that shift and intensity selectors are
distinct and uniquely identify Series. Add tests for later named columns,
quoted delimiters, comments with leading whitespace, empty files, and duplicate
headers.

### M-09 — Data cleaning is configured but not quantitatively recorded

**Evidence:** nonnumeric/nonfinite rows are coerced and removed; duplicate
shifts are averaged; axes can be sorted. The result and summary do not record
input row count, removed-row count, duplicate groups merged, or whether sorting
actually occurred.

**Impact:** scientific data are modified in allowed ways, but the run record
does not fully disclose what changed, contrary to the transparent and
reproducible processing requirement.

**Corrective action:** return a typed load/preprocessing report with original
row count, valid row count, removed row indices/count, duplicate count and
aggregation method, and sort status. Log it and include it in per-spectrum
summary/metrics.

### M-10 — `run_analysis()` mutates its caller's resolved configuration

**Evidence:** `_set_consistent_individual_range()` writes the computed y-range
back into `cfg`. Reusing the same loaded config object for another run retains
the first dataset's automatic range.

**Impact:** repeated programmatic calls can produce order-dependent outputs and
the object named as input configuration is not immutable.

**Corrective action:** deep-copy the config at the run boundary or create a
separate resolved-run-settings object. Keep the raw validated configuration
immutable and write derived values only to `resolved_config.yaml`.

### M-11 — Output finalization and logger cleanup are not exception-safe

**Evidence:** output writing occurs after per-spectrum processing and outside
the per-spectrum exception boundary. A plot, CSV, copy, summary, or YAML write
failure can leave a partial run without a terminal summary. Logger handlers are
closed only on the normal return path; the `no results` exception and later
write failures bypass cleanup.

**Impact:** partial directories may look like completed analyses, file handles
can leak, and the final failure state is not machine-readable.

**Corrective action:** wrap the run in `try/finally` for handler cleanup; write
a terminal run-status file on both success and failure; stage outputs and use
atomic replacement where practical. Record planned, generated, copied, and
failed outputs in one manifest.

### M-12 — Safety and entrypoint behavior are under-tested

**Evidence:** the focused tests cover config loading, missing files, named
columns, synthetic baseline/peak/normalization, invalid targets, core arrays,
overlay/group creation, and the happy-path copy rename. They do not cover scan
gaps/duplicates, row-major and multi-column boundaries, dry-run and move
failure behavior, overwrite/collision cases, root overlap, custom-pattern
round trips, slug collisions, duplicate group names, unknown YAML keys,
requested-overlay failure status, logger cleanup, or subprocess execution of
the exact no-flag entrypoints.

**Impact:** the highest-risk filename and output failure modes can regress
without detection.

**Corrective action:** add a parametrized migration safety suite and subprocess
smoke tests for both exact commands. Add negative config-schema tests and
failure-injection tests for partial I/O. Exercise A1-A8, A/B/C partitioning and
both traversal orders rather than only two A-row spectra.

## Minor findings

### m-01 — `include_in_groups` has an inconsistent implicit default

`_group_partitions()` treats a missing key as `True`, while overlay inclusion
defaults to `False` and the README says only spectra with
`include_in_groups: true` are eligible. Make the group default explicit and
consistent—prefer `False` for opt-in comparison plots.

### m-02 — Group plot titles can omit the generated partition identity

When one selection partitions A, B, and C, each plot uses the same configured
selection title. The filename contains the group key but the visible title may
not say column A/B/C. Append the group-by field/value automatically or support
validated placeholders such as `{group_by}`, `{group_value}`, and
`{group_name}`.

### m-03 — Title-template typos silently become blanks

`_BlankFormat.__missing__()` substitutes an empty string for unknown template
fields. A typo therefore produces an incomplete title rather than a validation
error. Validate allowed placeholders and reject unknown fields.

### m-04 — Aggregate copies are not listed in generated-output summaries

The summary lists original per-spectrum plot paths but not copies written to
`by_type` and `by_peak`. Include all copies in the output manifest, ideally
annotated as aliases/copies of a canonical artifact.

### m-05 — Rename completion status uses incorrect words

`rename["operation"] + "d"` produces `copyd` rather than `copied`. Use an
explicit status mapping (`copy -> copied`, `move -> moved`).

### m-06 — Diagnostic axis controls are not available in YAML

Individual, overlay, and group limits are configurable, but full-spectrum and
baseline-diagnostic axis limits are fixed to automatic behavior. Add optional
diagnostic x/y ranges if the requirement that reasonable plot controls live in
YAML is interpreted consistently.

### m-07 — Overlay y-axis wording is wrong for `normalization.method: none`

The overlay plotting function always labels the y-axis "Normalized intensity".
When normalization is disabled it plots corrected intensity. Pass the actual
display quantity/y-label from the processed result and verify that all overlay
members use the same mode.

### m-08 — Legacy documentation conflicts with the new preferred workflow

The main README correctly gives the no-flag commands, but
`docs/ARCHITECTURE.md`, `docs/USAGE.md`, `docs/CONFIG_GUIDE.md`, and plate docs
still describe the CLI workflow as the primary/only entrypoint. Keep them as
clearly labeled "Legacy workflow" documents or update the architecture and
usage docs to lead with `analyze_raman.py`, while preserving the old commands
for compatibility.

## Recommendations

### R-01 — Record source and software provenance

Add source SHA-256, byte size, modification time, library versions, Python
version, `raman_lib` version, and Git commit/dirty state to the run summary.
This would materially improve reproducibility beyond config snapshots.

### R-02 — Use typed configuration and result models

`SpectrumResult` is a good start, but pervasive `dict[str, Any]` makes schema
drift easy. Add typed models for analysis config, spectrum selection, peak
metrics, preprocessing report, plot group, rename config, and run summary.

### R-03 — Declare and test the supported Python environment

The code uses Python 3.10+ typing syntax but `raman/requirements.txt` does not
state a Python version and omits test tooling. Document Python 3.11 (the
observed environment) or the actual supported range, add a development/test
requirements file, and test against the intended NumPy/SciPy bounds.

### R-04 — Avoid private helpers across modules

`analysis_workflow` calls `io_utils._slug`. Promote this to a public naming
utility so output identity rules are centralized and directly tested.

### R-05 — Add a machine-readable output manifest

In addition to `run_summary.json`, write an artifact manifest containing path,
kind, source spectrum/group, checksum, status, and canonical/copy relationship.
This simplifies audit, cleanup, and downstream automation.

## Strengths and no-finding areas

- **Exact no-CLI-flags workflow:** `analyze_raman.py` uses the fixed
  `raman/configs/raman_analysis.yaml`; `rename_raman_files.py` likewise uses
  `raman/configs/raman_rename.yaml`. Routine selection and processing do not
  require flags or Python edits.
- **Separation at entrypoint level:** analysis and filename migration are
  separate scripts/configs, as requested.
- **Shared processing path:** individual plots, overlay data, and grouped
  overlays derive from the same `SpectrumResult` produced by
  `process_spectrum()`. The end-to-end test compares plotted overlay data with
  processed CSV data.
- **Configurable future target:** expected peak, search/integration windows,
  plot windows, normalization, baseline, smoothing, and thresholds are YAML
  values rather than repeated hard-coded 1080 constants.
- **Group overlays:** column, row, and sample-type filtering and partitioning
  are configurable; A1-A8 and A/B/C are representable without source edits.
- **Plot organization:** per-spectrum folders coexist with `by_type`,
  `by_peak`, overlay, and group collections. Individual titles automatically
  include known sample/position metadata and now support YAML templates.
- **Raw-data compatibility:** the checked-in headerless two-column spectrum is
  supported, named column selectors are supported, axes are validated as
  strictly increasing after configured cleaning, and units must be declared as
  Raman shift in cm^-1.
- **Output transparency:** processed CSVs retain raw-after-load, smoothed,
  baseline, corrected, scaled, and target-region columns. Config snapshot,
  resolved config, metrics, log, and run summary are created.
- **Safe scientific failure representation:** invalid target normalization
  produces NaN scaled data rather than silently normalizing to another peak;
  per-spectrum warnings and failures are recorded.
- **Appropriate dependencies:** NumPy, SciPy, pandas, Matplotlib, and PyYAML are
  sufficient; no GUI, notebook, or heavy new runtime is required.
- **Path handling:** new code consistently uses `pathlib`; labels and analysis
  names are slugged; recent rename checks prevent filename patterns and
  manifest names from escaping their configured output directory.
- **Logging:** the main workflow uses structured Python logging to console and
  `logs/analysis.log`; processing exceptions are actionable and retain
  tracebacks.
- **Legacy preservation:** the existing CLI processing, plate mapping, multi-
  peak configs, and legacy numerical modules remain present. Changes to shared
  I/O and plotting are additive or retain legacy selector fallbacks; no
  robot-control or unrelated OT-2 code was modified.
- **Safety:** no live robot or hardware behavior is introduced. The Raman work
  remains a local, simulation-laptop-safe data-analysis workflow.

## Required correction order

1. Fix C-01 and add migration mapping tests before using renamed files as
   scientific position metadata.
2. Fix M-01, M-02, M-03, M-05, M-06, and M-07 before general user release.
3. Refactor M-04 while implementing the failure-safe run/output changes in
   M-09 through M-11.
4. Close M-12 and the Minor findings, then rerun the focused suite and the two
   exact no-flag commands.

