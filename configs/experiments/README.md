# Experiment configurations

| Directory | Purpose and entry point |
| --- | --- |
| `dilution/` | NanoDrop stock-dilution example; `scripts/run_dilution.py` |
| `paper_printing/` | NanoDrop prepared-sample example, plus unchanged standard/clover experiment jobs for `scripts/manual/run_printing_workflow.py` |
| `specialized/` | Existing vial/plate printing, layering, transfers, height/dwell sweeps and repeatability methods; `scripts/experiments/run_printing_experiment_robot.py` retains the workflow lookup names |
| `sers/` | Existing SERS intent experiments; `scripts/experiments/run_sers_experiment.py` |
| `proposals/` | Existing experimental proposal records |

The 46 formerly flat YAML files were moved byte-for-byte. Historical header
commands and saved scientific provenance retain their original paths; the cleanup
manifest records current destinations. Current commands are in
[COMMANDS.md](../../docs/COMMANDS.md). Referenced machine profiles, labware and
NanoDrop's shared base configuration retain their established locations.

SERS experiment planning here is separate from Raman spectral analysis in
`raman/`. The pre-existing layering/dye timing ambiguity is documented in the
cleanup review; no timing or other experimental settings were corrected.
