# Usage

All commands are run from the `raman/` directory with the conda **`ai`** env.

## Running

```bash
# default config (peaks_1080), all *.csv in raw/
python src/process_raman.py

# choose a config by short name (resolved to configs/<name>.yaml)
python src/process_raman.py --config peaks_1080_1580
python src/process_raman.py --config peaks_all

# a single file
python src/process_raman.py --config peaks_1080 --input raw/Randomized_Scan_00649.csv

# a different input folder / output folder
python src/process_raman.py --config peaks_all --input C:/data/scans --output results
```

On this machine the `ai` env python is:

```bash
C:/Users/iyer95/miniconda3/envs/ai/python.exe src/process_raman.py --config peaks_1080
```

### CLI flags

| Flag | Meaning | Default |
|------|---------|---------|
| `--config` / `-c` | Config name in `configs/`, or a path to a YAML file | `peaks_1080` |
| `--input` / `-i` | A file **or** a folder; overrides `io.input_dir` | config value |
| `--output` / `-o` | Output directory; overrides `io.output_dir` | config value |
| `--plate` / `-p` | Plate name in `plates/` (or path); names results by dot spot | none (fallback naming) |
| `--date` / `-d` | Date label for result names (`MMDDYY`) | today |

## Naming results by plate spot

With `--plate <sample>`, each input file's scan number is mapped to its dot on
the printing paper and the result is named `{sample}_{spot}_{date}`:

```bash
python src/process_raman.py --config peaks_1080 --plate bp
```

Example (plate `bp` = 3 cols A–C × 8 rows, column-major from scan 00673):

| input file | scan | spot | result label |
|------------|------|------|--------------|
| `Scan 00673 ....csv` | 673 | A1 | `bp_A1_072726` |
| `Scan 00680 ....csv` | 680 | A8 | `bp_A8_072726` |
| `Scan 00681 ....csv` | 681 | B1 | `bp_B1_072726` |
| `Scan 00696 ....csv` | 696 | C8 | `bp_C8_072726` |
| `Scan 00700 ....csv` | 700 | — (out of range) | `Scan_00700_..._072726` |
| `mystery.csv` | — (no scan #) | — | `mystery_072726` |

Set the plate once in a config instead of passing `--plate` every time by adding
`plate: bp` at the top of the analysis config (CLI `--plate` still overrides).

**Preview a mapping** without processing (great for sanity-checking before a run):

```bash
python src/plate_map.py --plate bp                  # whole plate
python src/plate_map.py --plate bp --range 673-696  # explicit range
python src/plate_map.py --plate bp --out results/map_bp.csv
```

See [`../plates/README.md`](../plates/README.md) for the (dynamic) plate format.

## Batch processing

Point `--input` at a folder (or just fill `raw/`) and every matching file is
processed in one run. One bad file doesn't stop the batch — it's reported as
`[FAIL]` and the rest continue. Every peak from every file lands in
`results/master_log.csv`.

```bash
python src/process_raman.py --config peaks_1080 --input C:/data/todays_scans
```

## Reading the outputs

### Per run — `results/runs/<label>__<config>/`

`<label>` is `bp_A1_072726` (mapped) or `<originalname>_<date>` (unmapped). All
files inside are prefixed with the label so they stay self-describing if moved.

- **`<label>_overview.png`** — raw (grey), baseline (orange dashed), corrected (blue).
  Red ▼ = detected peak; green shading = targeted search windows.
- **`<label>_zoom_<peak>.png`** — one per target (targeted mode), with center/FWHM/SNR.
- **`<label>_peaks.csv`** — machine-readable peak table. Key columns:
  | column | meaning |
  |--------|---------|
  | `center_cm1` | peak position from the data (argmax in window) |
  | `fit_center_cm1` | sub-pixel center from curve fit |
  | `shift_from_target_cm1` | how far the peak moved from its nominal center (uses the fitted center when a fit succeeded) |
  | `at_window_edge` | true if the peak sat on a window edge with no interior local max — treat with suspicion |
  | `height` | baseline-corrected intensity at the peak |
  | `prominence` | peak prominence |
  | `fwhm_cm1` | full width at half max |
  | `area` | integrated peak area |
  | `snr` | height ÷ noise σ |
  | `found` | passed all configured thresholds |
- **`<label>_processed_spectrum.csv`** — `wavelength_cm1, raw, baseline, corrected, smoothed`.
- **`<label>_summary.json`** — all of the above plus sample/spot/scan/plate/date
  metadata and a full snapshot of the config used, so any run is fully
  reproducible from its own folder.

### Across runs — `results/master_log.csv`

One row per (run, peak), including `label, sample, scan_number, spot, column,
row, original_filename`. Load and pivot in pandas to trend a band across many
spots:

```python
import pandas as pd
df = pd.read_csv("results/master_log.csv")
b1080 = df[df.peak_name == "band_1080"]
print(b1080[["spot", "scan_number", "center_cm1", "shift_from_target_cm1", "snr"]])
```

## Output organization

- Each run folder is named `<label>__<config>` (label carries sample/spot/date).
  Re-running the same spot with the same config on the same day appends a
  `HHMMSS` suffix rather than overwriting.
- `results/` is git-ignored — it's regenerated, not a source of truth. Delete it
  anytime to clear old runs.

## Two-laptop workflow

1. **Home laptop:** edit configs, test against the template in `raw/`, commit, push.
2. **Work laptop:** pull, drop real scans into `raw/`, run the same commands.

No paths are hardcoded to a machine — only the contents of `raw/` differ.
