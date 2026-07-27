# plates/ — sample plate maps

Each file here maps **scan numbers → physical dots** on the printing paper, and
sets how result files are named. One file per sample (e.g. `bp.yaml`). This is
kept separate from the peak-analysis configs in `../configs/` so the two are
independent — any analysis can run on any plate.

## How the mapping works

Given (from `bp.yaml`):

```yaml
columns: [A, B, C]
rows: [1, 2, 3, 4, 5, 6, 7, 8]
start_scan: 673
order: column_major
```

`column_major` fills a whole column (all rows) before moving to the next column:

| scan | spot | | scan | spot | | scan | spot |
|------|------|-|------|------|-|------|------|
| 673  | A1   | | 681  | B1   | | 689  | C1   |
| 674  | A2   | | 682  | B2   | | 690  | C2   |
| …    | …    | | …    | …    | | …    | …    |
| 680  | A8   | | 688  | B8   | | 696  | C8   |

`row_major` instead fills a row (all columns) first: 673→A1, 674→B1, 675→C1, 676→A2, …

Everything is **dynamic** — change `columns`, `rows`, `start_scan`, or `order`
and both processing and the mapping script follow automatically. More or fewer
columns/rows need no code changes.

## Result naming

- Mapped file → `{sample}_{spot}_{date}` → e.g. **`bp_A1_072726`**
- Unmappable file (no plate, no scan number, or scan out of range) →
  `{stem}_{date}` → e.g. **`Scan_00649_random_072726`**

`date` defaults to today (`MMDDYY`); override with `--date` on the command line.

## Preview a mapping without processing

```bash
python src/plate_map.py --plate bp                 # whole plate (start_scan .. end)
python src/plate_map.py --plate bp --range 673-696 # explicit range
python src/plate_map.py --plate bp --out map.csv   # also save a CSV
```

## Use it when processing

```bash
python src/process_raman.py --config peaks_1080 --plate bp
python src/process_raman.py --config peaks_1080 --plate bp --date 072726
```
