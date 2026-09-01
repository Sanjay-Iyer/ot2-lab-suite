# Raman analysis

This directory contains a YAML-only workflow for initial analysis of the Raman
band near 1080 cm⁻¹, plus a separate YAML-only filename migration tool. Routine
use does not require command-line flags or Python edits.

## One-command analysis

Activate the repository's `ai` conda environment, then run from the repository
root:

```powershell
cd C:\code\opentrons_home\ot2-lab-suite
python raman\analyze_raman.py
```

Edit [`configs/raman_analysis.yaml`](configs/raman_analysis.yaml) to select an
input directory and change every processing or plotting option. Paths in that
file are relative to `raman/`.

```yaml
analysis:
  input_root: raw/renamed
  output_root: results

discovery:
  enabled: true
  file_glob: "*.csv"
  recursive: false
  canonical_filenames_only: true
  include:
    columns: [A, B, C]
    rows: []
    sample_types: [bp]
  exclude:
    columns: []
    rows: []
    sample_types: []
  include_in_overlay: false
  include_in_groups: true

spectra: []
```

Discovery processes every canonical Raman file in the directory except files
removed by the metadata filters. Empty include or exclude lists mean no
restriction. Filters are case-insensitive, and exclusions take priority. The
example guarantees that only BP files in columns A-C are processed. To start
with every column and remove D instead, use `include.columns: []` together with
`exclude.columns: [D]`. `include.rows: [1, 2, 3, 4]` selects only those rows. With
`canonical_filenames_only: true`, files such as `rename_manifest.csv` are
ignored. Set `recursive: true` only when spectra are stored in nested
directories.

The optional `spectra:` list remains available for per-file overrides or files
that do not use canonical names. An entry with the same relative file path as a
discovered spectrum overrides its label, metadata, and overlay/group switches.

The loader supports headerless or headered CSV files. Configure
`csv.raman_shift_column` and `csv.intensity_column` with zero-based column
indices or exact header names. The first selected axis must be Raman shift in
cm⁻¹; wavelength and pixel axes are not converted implicitly.

## Processing and metrics

Every selected spectrum uses the same pipeline:

1. Load numeric Raman shift and intensity values.
2. Remove non-finite rows, sort the axis, and average duplicate shifts when
   their YAML switches are enabled.
3. Optionally apply conservative Savitzky–Golay smoothing (off by default).
4. Estimate a smooth fluorescence baseline with arPLS (default) or ALS.
5. Subtract the baseline.
6. Find the strongest interior local maximum inside
   `target_peak.search_window_cm1`.
7. Calculate raw intensity, baseline at the peak, corrected height, signed
   fixed-window area for valid peaks, detrended sideband noise, SNR,
   prominence, shift, and width at half prominence when both crossings are
   resolved.
8. Normalize the complete corrected spectrum to the validated target peak.

For target-peak normalization:

```text
scaled intensity =
  baseline-corrected intensity
  / baseline-corrected target-peak height
  * target value
```

The detected peak is therefore 1.0 when `target_value: 1.0`. A candidate must
also have coherent multi-point width, plausible physical width/shift, adequate
SNR, and adequate prominence-to-noise before it is valid. An invalid,
nonpositive, unresolved, or missing peak produces warnings and `NaN` scaled
values by default; it is never silently normalized to another peak. Alternate
YAML modes are `none`, `global_max`, `vector_norm`, and `area`.

arPLS iteratively downweights positive Raman bands while fitting a penalized,
smooth baseline. `baseline.lambda` controls stiffness: larger values bend less.
Convergence, sampling spacing, and sideband residual-quality information are
saved in the run summary. Noise is estimated robustly after independently
linear-detrending the configured peak-free sidebands. The default smoothing is
deliberately disabled so narrow or weak peaks are not suppressed. Always
inspect both baseline diagnostics on new sample types.

## Position metadata and grouped plots

The canonical filename is:

```text
0001__sample-bp__column-A__row-1.csv
```

The analysis reads sequence, sample type, column, and row automatically. You
can override any metadata in the corresponding `spectra:` item. Known sample
type and position are included in plot titles. Leave
`plots.individual.title_template` and `plots.diagnostics.title_template` as
`null` for that automatic title, or use placeholders such as
`"{label} | {sample_type} | {spot}"`.

Each target-region spectrum is written twice when normalization is valid:

- `normalized/` contains only scaled intensity plots.
- `baseline_corrected/` contains unscaled, baseline-corrected intensity in
  instrument arbitrary units.

The titles, y-axis labels, filenames, and directories state the intensity type.
The baseline-corrected plot is written by default. A normalized plot is omitted
when normalization is disabled or its denominator is invalid. Under
`target_peak` normalization, a failed target validation therefore omits the
normalized plot; alternate normalization methods can still produce a
method-labeled normalized plot while visibly marking the target as unvalidated.
Configure these independently with
`plots.individual.normalized_enabled`, `baseline_corrected_enabled`,
`normalized_y_range`, and `baseline_corrected_y_range`.

To create one overlay for A1–A8:

```yaml
plots:
  groups:
    enabled: true
    selections:
      - name: column_A_rows_1_to_8
        group_by: column
        columns: [A]
        rows: [1, 2, 3, 4, 5, 6, 7, 8]
```

Change `columns` to `[A, B, C]` to create a separate group plot for each
selected column. `group_by` can also be `row`, `sample_type`, or `none`.
Only spectra with `include_in_groups: true` are eligible; discovered files use
`discovery.include_in_groups`. Groups and ordinary overlays require at least
two spectra with valid normalized data; otherwise the log and run summary
clearly record why the plot was skipped and the command returns a nonzero
status. Vertical offset defaults to `0.0`.

## Renaming old files

Edit [`configs/raman_rename.yaml`](configs/raman_rename.yaml), especially
`sample_type`, `columns`, `rows`, and `order`, then run:

```powershell
python raman\rename_raman_files.py
```

The safe defaults are `operation: copy`, `dry_run: true`, and
`overwrite: false`. A dry run writes `raw/renamed/rename_manifest.csv` without
changing a source file. Inspect the manifest, set `dry_run: false`, and rerun to
create the canonical files. `operation: move` is available, but copying is
recommended until the new analysis has been verified.

Files are ordered numerically by the scan number in their old names, assigned
sequential zero-padded output numbers, and mapped by
`scan_number - start_scan_number`. Missing scans therefore preserve an empty
physical position instead of shifting every later row; gaps fail unless
`allow_scan_gaps: true`, and duplicate scans always fail. Use
`position_mapping: sorted_order` only when the old files genuinely have no
position-bearing sequence. The canonical filename pattern is fixed so every
file produced by the renamer is parseable by the analyzer. The complete plan is
persisted before file mutation, and each copy is size-verified through a
temporary file before its final name is installed.

## Output layout

Each run creates:

```text
results/<timestamp>_<analysis_name>/
├── config_snapshot.yaml
├── resolved_config.yaml
├── run_summary.json
├── peak_metrics.csv
├── processed/
│   └── <label>_processed.csv
├── overlay_data/
│   └── <overlay-or-group>.csv
├── plots/
│   ├── by_spectrum/<label>/
│   │   ├── individual/
│   │   │   ├── normalized/
│   │   │   └── baseline_corrected/
│   │   └── diagnostics/
│   ├── by_type/
│   │   ├── individual/
│   │   │   ├── normalized/
│   │   │   └── baseline_corrected/
│   │   ├── diagnostics/
│   │   └── full_spectrum/
│   ├── by_peak/band_1080/
│   │   ├── normalized/
│   │   └── baseline_corrected/
│   ├── overlay/
│   └── groups/
└── logs/
    └── analysis.log
```

`by_spectrum` preserves the organized per-sample folders. `by_type` and
`by_peak` mirror the same plots so all individual target plots, baseline
diagnostics, or 1080 plots are available in one place. Target plots are kept
separate at every level:

```text
plots/by_spectrum/<label>/individual/normalized/
plots/by_spectrum/<label>/individual/baseline_corrected/
plots/by_type/individual/normalized/
plots/by_type/individual/baseline_corrected/
plots/by_peak/band_1080/normalized/
plots/by_peak/band_1080/baseline_corrected/
```

Processed CSV files preserve Raman shift, raw intensity, optional-smoothed
intensity, baseline, unscaled baseline-corrected intensity, scaled intensity,
and the target-region indicator. The configuration snapshot records the exact
user input; `resolved_config.yaml` also records defaults and automatic shared
axis limits.

## Experiment reports

Alongside the general processor there is one standalone report script per
experiment. Each is driven by its own YAML in `configs/` and writes to
`results/<experiment>/`. All four score against the same band definitions,
which live in `exp1_cv_report.BANDS`.

| script | config | question |
|---|---|---|
| `exp1_cv_report.py` | `peaks_cv_sers.yaml` | which paper/concentration gives the best CV SERS signal |
| `exp2_dilution_report.py` | `peaks_cv_sers_exp2*.yaml` | how far the dye can be diluted before the band is lost |
| `exp3_timeseries_report.py` | `timeseries_exp3.yaml` | which integration time to use |
| `exp4_stars_report.py` | `stars_exp4.yaml` | do gold nanostars boost the dye signal |

Run any of them from `raman/`:

```powershell
python raman\exp3_timeseries_report.py
```

### Exp 3 - integration time (083126)

One spot per sweep, exposure swept 20 s → 2 s with AutoInt off, on offwhite and
white paper for both particle types. The metric that decides is the **noise
excess**: measured σ divided by the σ predicted by the detector's own noise law
`σ² = read² + gain·N`, which the script fits on every sliding window in the dump
below 30 000 counts. Healthy scans sit at ≈1 whatever the exposure; saturating
ones run to 3–28.

This matters because saturation here has no visible tell — the exports never
clip, and a saturated scan still looks like a spectrum and still yields fitted
peaks. Run the report before trusting any scan taken at a new exposure.

**The reference band is the dye band**, `cv_1620` (nominal 1620, observed
1616–1617) — set by `reference_band:` in the config. The 1095 cm⁻¹ paper filler
band is carried beside it in the calibration figure as a control rather than as
the reference: its true intensity cannot move with the dye, with photobleaching
or with how much analyte landed on the spot, so where the two curves agree the
dye curve is reading exposure and where they diverge something sample-side is
moving.

`figures/11_calibration_curve.png` is the calibration curve proper: SNR against
exposure with the fitted model, normalised so the four sweeps overlay against
the ∝t and ∝√t references, and a decision panel showing the percentage of the
achievable SNR gain captured at each exposure.

`results/exp3_timeseries/all_scans_screen.csv` carries the saturation verdict
for every scan in the dump, and `noise_model.json` the fitted law that exp 4
reuses.

### Exp 4 - nanostars + bipyramid rerun (083126)

The exp-2 design with a third arm: the same CV ladder (2x → 200x) scored for
stock nanostars, 5:1 diluted nanostars and dye alone, on both papers, plus a
rerun of the 082726 bipyramid print. Every gain is a spot minus its **own**
dye-only control — same paper, same exposure, same dilution. Nothing is
compared across papers.

Two things make this report worth reading before the numbers:

- **The saturation screen runs first, and it is scored at the band.** Ten scans
  ring somewhere in the spectrum, and they are the concentrated end of each
  ladder — exactly where enhancement shows up. But the ringing starts at the low
  wavenumbers and often never reaches the dye band: scan 854 is at 7.6× the
  model noise below 800 cm⁻¹ and 1.6× at 1620, quieter there than scans that
  pass outright. So the verdict the figures obey is scored on the windows
  overlapping the band being plotted (`quality.screen_scope: band`), and only
  831 and 839 still fail. Both verdicts are in `scan_screen.csv`. Separately,
  a band that is quiet but sits above the count level the noise law was fitted
  below can still be compressed — those carry `band_above_fit_ceiling`, are
  hatched in the bar figures, and should be read as lower bounds.
- **Two of the six bands are internal controls.** 1095 is carbonate filler in
  the sheet and 1375 is cellulose-dominated on paper, so neither can be
  enhanced. Both come out at 0.85x while the strong CV bands run 1.8–2.8x,
  which is the evidence that the gain is on the dye rather than on the paper.

The scan → condition → dilution map is in `configs/stars_exp4.yaml`, written out
per series rather than derived from a stride: several series were stopped early
on purpose, and a stride would silently relabel the survivors. The loader
refuses to run if a series' scan count and dilution count disagree.

Every arm measured 2x, 5x and 10x; only the offwhite nanostar ladders went
further. The figures are held to those three via `plotting.active_dilutions`,
so paper, particle and dilution can be read against each other with no missing
cell — the CSVs keep the full ladder out to 200x. The bipyramid rerun blocks
are written "2x - 20x" in the bench notes but are the first three columns of
the print, 2x/5x/10x; that was corrected on 090126, so bipyramid points in any
earlier copy of this report are labelled one or two rungs too dilute.
`scan_key.csv` and `scan_key_4_tabs.xlsx` are generated from the same config on
every run, so a corrected dilution reaches the key and the figures together.

## Warnings and limitations

- A missing width at half prominence means the crossings were under-resolved or
  outside the search window; no width or FWHM was invented.
- Failed SNR/prominence/height validation prevents target normalization.
- Area is the signed corrected signal in the configured integration window and
  is omitted for invalid candidates; it is not a fitted band area.
- The checked-in `Randomized_Scan_00649.csv` is a software template with strong
  point-to-point alternation. It is intentionally rejected as an unresolved
  target and not normalized under the defaults.
- arPLS parameters appropriate for one instrument or acquisition time may need
  adjustment for another; use the diagnostic plot rather than assuming the
  baseline is correct.

## Tests and legacy tools

Run the focused suite in the `ai` environment:

```powershell
python -m pytest raman\tests -q
```

The earlier CLI-oriented tools in `raman/src/process_raman.py` and
`raman/src/plate_map.py`, their preset configs, and the existing legacy docs
remain available for backward compatibility. New work should use
`analyze_raman.py` and `raman_analysis.yaml`.
