# Raman Processing Suite

Config-driven Raman spectrum processing: baseline correction, smoothing, peak
detection, curve fitting, and clean timestamped output — all steered by YAML
config files. **You never edit the Python to change the analysis; you switch or
edit a config.**

```
raman/
├── raw/                 # drop input CSVs here (col 1 = shift cm⁻¹, col 2 = intensity)
├── configs/             # analysis presets (which peaks/thresholds) — you edit these
│   ├── peaks_1080.yaml       # DEFAULT: single band ~1080 (wide 1060–1100)
│   ├── peaks_1080_1580.yaml  # two bands: 1080 + 1580
│   └── peaks_all.yaml        # auto-detect ALL peaks (global mode)
├── plates/              # sample plate maps (scan number → dot spot → result name)
│   └── bp.yaml               # sample "bp": 3 cols × 8 rows, column-major from scan 673
├── src/
│   ├── process_raman.py      # the one script you run to process spectra
│   ├── plate_map.py          # preview scan → spot mapping for a range (no processing)
│   └── raman_lib/            # library: config, io, preprocessing, detection, plotting, plate
├── results/             # generated: per-run folders + master_log.csv (git-ignored)
└── docs/                # ARCHITECTURE.md · USAGE.md · CONFIG_GUIDE.md
```

Two independent config dimensions: **`configs/`** decides *what analysis* runs
(peaks, thresholds, baseline); **`plates/`** decides *how results are named* from
the scan number. Mix and match any analysis with any plate.

## Quickstart

Run from the `raman/` directory using the conda **`ai`** env:

```bash
python src/process_raman.py
```

That uses the default config (`peaks_1080`) on every `*.csv` in `raw/`. Switch configs:

```bash
python src/process_raman.py --config peaks_1080_1580
python src/process_raman.py --config peaks_all
python src/process_raman.py --config peaks_1080 --input raw/Randomized_Scan_00649.csv
```

### Naming results by plate spot

Give a plate and each input file is named by the dot it came from —
`{sample}_{spot}_{date}`, e.g. `bp_A1_072726`:

```bash
python src/process_raman.py --config peaks_1080 --plate bp
python src/process_raman.py --config peaks_1080 --plate bp --date 072726
```

The plate maps scan numbers → spots (scan `00673` → `A1`, `00680` → `A8`,
`00681` → `B1`, …). Files that can't be mapped (no plate, no scan number in the
name, or scan out of range) fall back to `{originalname}_{date}`. Preview a
mapping without processing anything:

```bash
python src/plate_map.py --plate bp --range 673-696
```

See [`plates/README.md`](plates/README.md) for the plate format (dynamic columns/rows).

Full command with the `ai` env python on this machine:

```bash
C:/Users/iyer95/miniconda3/envs/ai/python.exe src/process_raman.py --config peaks_1080
```

## What you get per run

Under `results/runs/<label>__<config>/` (label = `bp_A1_072726` or, unmapped,
`<originalname>_<date>`). Every file is prefixed with the label:

| File | Contents |
|------|----------|
| `<label>_overview.png` | Full spectrum: raw, baseline, corrected, marked peaks + search windows |
| `<label>_zoom_<peak>.png` | Per-target zoom (targeted mode only) |
| `<label>_peaks.csv` | One row per peak: center, fitted center, height, prominence, FWHM, area, SNR |
| `<label>_processed_spectrum.csv` | Raw / baseline / corrected / smoothed columns |
| `<label>_summary.json` | Everything above + sample/spot/scan metadata + a full snapshot of the config |

Plus `results/master_log.csv` — one appended row per (run, peak) with
label/sample/scan/spot columns, so batches of many files are trivial to review
in Excel/pandas.

## The three configs

| Config | Mode | Use when |
|--------|------|----------|
| `peaks_1080` (default) | targeted | You care about the 1080 band only |
| `peaks_1080_1580` | targeted | You track 1080 (primary) + 1580 (secondary) |
| `peaks_all` | global | You want every peak above threshold, count unknown |

**Adding peaks is just editing YAML** — see [`docs/CONFIG_GUIDE.md`](docs/CONFIG_GUIDE.md).
Going from 1 → 3 → 5 → N peaks is adding entries to the `peaks:` list. No code changes.

## Docs

- [`docs/USAGE.md`](docs/USAGE.md) — running, batch processing, reading outputs
- [`docs/CONFIG_GUIDE.md`](docs/CONFIG_GUIDE.md) — every config knob explained
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how the code is organized & extended

## Two-laptop workflow

This (home) laptop is for **writing/testing scripts against the fake template**.
Push to git, pull on the work laptop, and run the same commands on **real data**
placed in `raw/`. The code has no machine-specific paths — only the `raw/` inputs
differ.
