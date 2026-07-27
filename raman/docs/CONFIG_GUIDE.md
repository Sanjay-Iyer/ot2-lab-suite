# Config Guide

Everything the pipeline does is controlled by a YAML config in `configs/`. This
is the file you edit — never the Python. Below is every key, its meaning, and
how to tune it.

> **PyYAML gotcha:** write large numbers as `100000.0` or `1.0e+5` (with a sign
> on the exponent). Bare `1.0e5` is parsed as a *string* by PyYAML. The code
> defensively casts the known numeric fields anyway, but keep configs clean.

---

## Top level

```yaml
name: "1080_only"          # goes into output folder names + master_log
description: "..."         # free text, for humans
plate: null                # optional default plate (name in plates/ or a path);
                           # CLI --plate overrides. Drives result-file naming.
```

> Result **naming** (scan number → dot spot → `sample_spot_date`) is configured
> separately in `plates/<sample>.yaml`, not here — see
> [`../plates/README.md`](../plates/README.md). Set `plate:` above (or pass
> `--plate`) to activate it.

## `io` — input/output

```yaml
io:
  input_dir: raw           # folder scanned for inputs (relative to raman/)
  output_dir: results      # where runs/ and master_log.csv are written
  file_glob: "*.csv"       # which files to pick up
  csv:
    has_header: null        # null = auto-detect; true/false to force
    wavelength_col: 0       # 0-based column index for Raman shift
    intensity_col: 1        # 0-based column index for intensity
    delimiter: ","          # e.g. "\t" for tab-separated
    comment: null           # e.g. "#" to skip comment lines
```

## `analysis_range` — optional crop

```yaml
analysis_range:
  enabled: false           # true to restrict processing to [min, max]
  min: 400
  max: 2300
```

## `preprocessing`

### Baseline
```yaml
baseline:
  method: arpls           # arpls | als | poly | rolling_min | none
  arpls: {lam: 100000.0, ratio: 1.0e-6, niter: 50}
  als:   {lam: 100000.0, p: 0.01, niter: 10}
  poly:  {order: 3}
  rolling_min: {window: 151}
```
- **arpls** (default) — best general Raman baseline; handles fluorescence.
  `lam` = stiffness (↑ = smoother/stiffer baseline). Try `1.0e+4`–`1.0e+7`.
- **als** — classic asymmetric least squares. `p` = asymmetry (0.001–0.1).
- **poly** — global polynomial of `order`. Fast, for gently sloping baselines.
- **rolling_min** — smoothed rolling minimum; `window` in points.
- **none** — no baseline (already-corrected data).

### Smoothing
```yaml
smoothing:
  method: savgol          # savgol | none
  savgol: {window: 11, polyorder: 3}   # window must be odd, > polyorder
```
Wider `window` = more smoothing (can blunt narrow peaks). `none` to disable.

### Normalize
```yaml
normalize:
  method: none            # none | max | area | minmax | snv
```
For comparing scans: `max` (tallest peak = 1), `area` (unit area), `minmax`
(0–1), `snv` (standard normal variate). Absolute heights change under
normalization — set thresholds accordingly.

## `noise` — SNR basis

```yaml
noise:
  method: mad_derivative  # mad_derivative | region
  region: [null, null]    # [min, max] if method: region
```
- **mad_derivative** (default) — robust noise σ from first differences; ignores
  peaks. No tuning needed.
- **region** — plain std over a known signal-free band, e.g. `region: [1900, 2100]`.

SNR reported per peak = peak height / σ.

## `detection`

```yaml
detection:
  mode: targeted          # targeted | global
  min_height: null        # absolute corrected-intensity floor
  min_prominence: null    # peak prominence floor
  min_snr: null           # require height >= min_snr * noise σ
  min_width: null         # FWHM floor in cm⁻¹ (rejects spikes)
  max_width: null         # FWHM ceiling in cm⁻¹ (rejects broad humps)
  distance: 8             # (global) min peak spacing in cm⁻¹
  global_max_peaks: 30    # (global) cap on # strongest peaks; null = no cap
  default_window: 20.0    # ± cm⁻¹ used for a peak that gives `center` but no `window`
  area_fwhm_multiplier: 2.0  # integrate area over center ± this × FWHM
```
`null` thresholds are disabled. A peak is `found: true` only if it passes **all**
set thresholds.

- **targeted** — searches each window in `peaks:` and characterizes the strongest
  point. Best when you know roughly where bands are.
- **global** — `scipy.find_peaks` across the whole spectrum; reports everything
  clearing the thresholds. Best when the peak count is unknown.

## `fitting`

```yaml
fitting:
  enabled: true
  model: gaussian         # gaussian | lorentzian | voigt | none
```
Fits a local model per peak for a sub-pixel center + model FWHM + R². Set
`enabled: false` (or `model: none`) to report raw metrics only.

## `peaks` — the target list (targeted mode)

```yaml
peaks:
  - name: "band_1080"
    center: 1080
    window: 20             # searches center ± window  => 1060–1100
    primary: true
    # optional per-peak threshold overrides:
    # min_snr: 3.0
    # min_prominence: 150
  # --- add more peaks by copying the block ---
  - name: "band_1580"
    center: 1580
    window: 25
```

**This is how you scale from 1 peak to many.** Add/remove list entries — the
count is just the list length. Alternatively specify an explicit window:

```yaml
  - name: "band_1080"
    search_min: 1070
    search_max: 1090
```

In **global** mode, `peaks:` is ignored (leave it `[]`).

## `plotting`

```yaml
plotting:
  overview: true          # full-spectrum figure
  zoom: true              # per-target zoom panels (targeted mode)
  dpi: 150
  figsize: [12, 6]
  show_raw: true
  show_baseline: true
  annotate: true          # label peaks on the overview
```

---

## Recipes

**Only accept confident peaks** (targeted):
```yaml
detection: {mode: targeted, min_snr: 3.0, min_prominence: 100}
```

**Survey everything, strongest 15** (global):
```yaml
detection: {mode: global, min_snr: 3.0, min_prominence: 50, global_max_peaks: 15}
```

**Track a drifting band widely** — widen the window:
```yaml
peaks:
  - {name: band_1080, center: 1080, window: 30}   # 1050–1110
```

**Sharp Lorentzian bands** (e.g. crystalline):
```yaml
fitting: {enabled: true, model: lorentzian}
```
