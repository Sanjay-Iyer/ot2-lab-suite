# Architecture

## Design principles

1. **Config, not code.** Every scientific choice (which peaks, which thresholds,
   which baseline) lives in a YAML config. The Python only implements mechanics.
   Nothing analysis-specific is hardcoded.
2. **One entry script.** `src/process_raman.py` is the only thing you run. New
   analyses = new config. Future *different* tools = new scripts beside it that
   reuse `raman_lib`.
3. **Self-contained, timestamped output.** Each run writes an isolated folder;
   a flat `master_log.csv` aggregates across runs for batch review.
4. **Machine-agnostic.** Paths resolve relative to the `raman/` directory, so the
   same repo runs identically on the home and work laptops.

## Layout

```
raman/
├── raw/                     # inputs (data)
├── configs/                 # analysis presets (what peaks/thresholds) — YAML
├── plates/                  # plate maps (scan number → spot → result name) — YAML
├── src/
│   ├── process_raman.py     # CLI orchestrator (process spectra)
│   ├── plate_map.py         # CLI to preview scan → spot mapping (no processing)
│   └── raman_lib/           # reusable library (no CLI, no I/O side effects on import)
│       ├── config.py        # load + default + validate analysis YAML
│       ├── io_utils.py      # load spectra; write organized, label-named outputs
│       ├── preprocessing.py # baseline, smoothing, normalize, noise
│       ├── detection.py     # targeted + global peak finding, fitting, metrics
│       ├── plotting.py      # overview + zoom figures (headless Agg backend)
│       └── plate.py         # plate load/validate, scan→spot mapping, result naming
├── results/                 # generated outputs (git-ignored)
└── docs/
```

**Two orthogonal config dimensions.** `configs/` controls the *analysis*;
`plates/` controls *result naming*. They're loaded and validated by separate
modules (`config.py`, `plate.py`) and combined only in `process_raman.py`, so a
change to plate geometry never touches analysis code and vice versa.

## Data flow

```
raw/*.csv
   │  io_utils.load_spectrum        (auto-detect header, sort, dedupe x)
   ▼
   │  analysis_range crop           (optional)
   ▼
   │  preprocessing.apply_baseline  → baseline;  corrected = raw − baseline
   ▼
   │  preprocessing.apply_smoothing (Savitzky–Golay)
   ▼
   │  preprocessing.apply_normalize (optional)
   ▼
   │  preprocessing.estimate_noise  → σ  (for SNR)
   ▼
   │  detection.detect              targeted → per-window; global → find_peaks
   │      └─ detection.fit_peak     Gaussian / Lorentzian / Voigt (optional)
   ▼
   │  plate.build_label            scan number → spot → label (or {stem}_{date})
   ▼
   │  io_utils.write_*              <label>_peaks.csv, _summary.json, _processed_spectrum.csv
   │  plotting.plot_overview/zoom   <label>_overview.png, <label>_zoom_*.png
   │  io_utils.append_master_log    results/master_log.csv
   ▼
results/runs/<label>__<config>/
```

The scan number is parsed from the input filename with a config regex, offset
from `start_scan`, and turned into a (column, row) via integer div/mod — column-
major uses `divmod(i, n_rows)`, row-major uses `divmod(i, n_cols)`. Nothing is
tied to a fixed grid size or ordering.

## Module responsibilities

| Module | Responsibility | Never does |
|--------|----------------|-----------|
| `config.py` | Merge user YAML over structural defaults; validate; expose helpers | Invent peak positions or thresholds |
| `plate.py` | Load/validate plate maps; scan→spot mapping; build result labels | Analysis or plotting |
| `io_utils.py` | Read spectra robustly; write the run folder + master log | Analysis decisions |
| `preprocessing.py` | Baseline / smooth / normalize / noise — pure numeric functions | Read config keys directly (params passed in) |
| `detection.py` | Locate & characterize peaks; optional curve fit | Plot or write files |
| `plotting.py` | Render figures headlessly | Compute metrics |
| `process_raman.py` | Parse args, resolve paths, run the pipeline, print status | Contain numeric constants |

## Extending

**Add a preprocessing method** (e.g. a new baseline):
1. Add the function to `preprocessing.py`.
2. Register it in `apply_baseline` dispatch and in `config._BASELINE_METHODS`.
3. Add its parameter block to `config.DEFAULTS`.

**Add a fit model:** add the shape function + `_fwhm_from_fit` case in
`detection.py`, and to `config._FIT_MODELS`.

**Add a whole new tool** (e.g. batch comparison, mapping): create a new script in
`src/` that imports `raman_lib`. Keep `process_raman.py` focused on single-file
peak processing.

## Dependencies

numpy, scipy, pandas, matplotlib, PyYAML — all present in the conda `ai` env.
Matplotlib uses the `Agg` backend so plotting works headless (SSH/CI/work laptop).

## Notes / gotchas

- **numpy 2.x** removed `np.trapz`; `preprocessing.py` uses `np.trapezoid` with a
  fallback shim.
- **PyYAML** parses bare `1.0e5` as a *string*; configs use `100000.0` / `1.0e+5`
  and the code casts numeric fields defensively.
- Duplicate/unsorted x values are collapsed and sorted on load.
