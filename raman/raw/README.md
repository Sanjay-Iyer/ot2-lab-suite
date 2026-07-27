# raw/ — input spectra

Drop your Raman scan CSV files here. The processor reads every file matching
the config's `io.file_glob` (default `*.csv`) from this folder.

**Expected format** (matches the template `Randomized_Scan_00649.csv`):

- Two columns: **column 1 = Raman shift (cm⁻¹)**, **column 2 = intensity**
- Header row optional — it is auto-detected (or force it via `io.csv.has_header`)
- Delimiter, column indices, and header handling are all configurable per config
  file under `io.csv`

The template scan here is **randomized test data** — it is only for verifying the
pipeline runs end-to-end. Real analysis happens on the work laptop, where you
replace/add real scans in this folder.

> Real `*.csv` data is git-ignored (see `../.gitignore`) so large data dumps
> don't get committed. Only the template scan is tracked.
