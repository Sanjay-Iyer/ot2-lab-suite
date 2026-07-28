# Audit resolution

This file records corrections applied after the independent first-implementation
audits. The original audit reports are intentionally unchanged.

## Raman science audit

- **C1 resolved:** detection now separates `candidate_found` from `peak_valid`.
  Normalization requires a resolved multi-sample width by default, configurable
  physical width and shift bounds, SNR, and prominence-to-noise. The checked-in
  randomized template and a synthetic single-point spike are negative-control
  tests and remain unnormalized.
- **M1 substantially resolved:** arPLS now reports convergence, iterations,
  final weight change, and finite status. Detrended sidebands provide residual
  median/slope quality indicators, and a target-region baseline diagnostic is
  generated. Baseline quality remains an explicit warning rather than a
  universal hard rejection because acceptable limits are instrument-dependent.
- **M2 resolved for the current algorithms:** median sampling interval,
  irregularity, and effective smoothing span are recorded. Nonuniform axes fail
  by default instead of silently changing the physical baseline/smoothing
  behavior.
- **M3 resolved:** area is signed, is omitted for invalid candidates, records
  actual bounds/point count, and is omitted if the detected candidate falls
  outside the integration window.
- **M4 resolved:** default noise now uses robust MAD residuals from independently
  linearly detrended, configurable target sidebands, with bounds and point
  counts saved. The old global derivative method remains an explicit alternate.
- **M5 resolved:** the SciPy quantity is now named
  `width_at_half_prominence_cm1`; bases, crossings, evaluation height, and
  sample width are saved. `fwhm_cm1` remains missing because no fitted or
  local-continuum FWHM is invented.
- **M6 resolved:** automatic y limits use only finite points inside the visible
  x window for individual, overlay, group, and target-baseline plots.
- **M7 resolved:** invalid candidates use a red cross, an “unvalidated
  candidate” legend, and an explicit “not normalized” plot message.
- **M8 resolved:** expected position must lie inside search and integration
  windows; runtime coverage, resolved width, and maximum shift are checked.
- **M9 substantially resolved:** tests now include noise/no-target behavior,
  a single-point spike, the randomized repository template, scan gaps,
  duplicate scans, strict-schema failures, and the prior synthetic/end-to-end
  cases.

## Python architecture audit

- **C-01 resolved:** safe scan mapping uses
  `scan_number - start_scan_number`; duplicates fail, gaps fail by default, and
  intentional gaps preserve empty positions.
- **M-01 substantially resolved:** unknown YAML keys are rejected with dotted
  paths; key booleans, numeric bounds, windows, sampling, and rename policies
  are validated.
- **M-02 resolved:** display labels, filesystem slugs, selection names, and
  selection slugs are checked for collisions.
- **M-03 resolved:** missing requested overlays/groups become structured batch
  failures and the entry point returns nonzero.
- **M-05 resolved:** the metadata-rich filename pattern is canonical and fixed,
  guaranteeing formatter/parser round trips.
- **M-06 substantially resolved:** the full manifest is persisted before
  mutation and updated after each item; copies use verified temporary files
  followed by atomic same-directory replacement. A process or filesystem crash
  can still leave a `.partial` file, which is clearly named and never parsed as
  Raman CSV input.
- **M-07 resolved:** equal input/output roots fail, and recursive discovery is
  rejected when output is nested beneath input.
- **M-08 substantially resolved:** named selectors force header use, selector
  identity is rejected, leading-space comments are recognized, and selected
  columns receive actionable errors.
- **M-09 resolved:** input rows, removed nonfinite rows, sorting, duplicate
  averaging, and final point counts are written per spectrum.
- **M-10 resolved:** `run_analysis()` deep-copies its configuration before
  resolving shared y limits.
- **M-12 substantially resolved:** the focused suite covers the critical
  migration and scientific failure modes in addition to exact no-flag
  entry-point verification performed during final validation.

## Deliberately retained limitations

- The orchestration module remains larger than ideal (**M-04**). Splitting it
  across legacy modules during the scientific safety correction would create a
  high regression risk without changing behavior; its public processing
  functions and typed `SpectrumResult` provide safe seams for a later refactor.
- Run-directory output is not fully transactional (**M-11**). Individual input
  failures, batch-output failures, logs, and successful/degraded status are
  recorded, but a sudden process termination during final summary writing can
  leave a partial timestamped directory. Such directories do not contain a
  successful `run_status`.
- True fitted-band FWHM and uncertainty are not reported. The transparent
  half-prominence width is retained only when resolved; fitting overlapping or
  asymmetric bands is outside this initial 1080 cm⁻¹ workflow.
- Quantitative comparisons still require compatible acquisition conditions
  (laser power, integration time, optics, calibration, and cosmic-ray
  treatment). These instrument metadata are not inferable from the current CSV
  format and should be added to YAML when they become available.
