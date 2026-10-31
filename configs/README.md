# Python operations, YAML experiments

| Configuration | Used by |
| --- | --- |
| `home/agent.yaml`, `work/agent.yaml` | `scripts/launch_agent.py --config ...` |
| `experiments/dilution/example.yaml` | `scripts/run_dilution.py --config ...` |
| `experiments/paper_printing/example.yaml` | `scripts/run_paper_printing.py --config ...` |
| `workflows/defaults/ai_agent_dilution_print_demo.yaml` | Existing terminal and NiceGUI `--config`; shared NanoDrop base |
| `checks/ot2.yaml` → `robot.yaml` | `scripts/check_ot2.py --config ...`; existing connection helpers use `robot.yaml` |
| `checks/single_dilution.yaml` | `scripts/manual/test_single_dilution_c12.py --config ...` |
| `experiments/paper_printing/01_printing_standard.yaml` | `scripts/manual/run_printing_workflow.py standard --config ...` |
| `experiments/paper_printing/02_printing_four_clover.yaml` | `scripts/manual/run_printing_workflow.py four-clover --config ...` |
| `machines/`, `labware/`, `constraints/`, root `labware/*.json` | Existing shared laboratory profiles, geometry and physical bounds |
| Other `experiments/`, `workflows/defaults/` | Existing specialized builders/runners; preserved at their current paths |

The two new NanoDrop examples use **one optional `base_config` reference**, then
ordinary section overrides. It resolves from the repository root on either
laptop. Mappings merge using the existing merge helper; lists replace the
previous list. The referenced base must be a complete YAML file with no further
`base_config`. There is no recursive inheritance, environment interpolation or
new schema framework. Full existing NanoDrop YAML files still load as before.
The agent, GUI and manual NanoDrop paths all use `model.load_config`.
Older builders have their original schemas; do not feed a NanoDrop reference
file directly into a builder for another protocol family.

For a new experiment, copy an example and change factors/volumes, wells, paper
positions, droplet counts or replicates. Use the same Python command with the
new `--config` path. Retain laboratory-owned geometry unless it is intentionally
being commissioned on WORK. The inherited base is the existing laboratory
configuration; experimental examples do not duplicate connection details.

Dilution factors specify stock concentration / desired concentration. The
NanoDrop example prepares each well directly from stock and solvent. Its paper
example assumes A11/B11/C11 already contain the declared prepared samples.
For a combined dilution-and-print experiment, use the existing full default YAML
or set both `run_modes` and section `enabled` flags consistently in a copy.

HOME/WORK profiles are launch settings, not replacements for existing machine
profiles, `robot.yaml`, `.env`, calibration data or SSH references. `model: null`
retains the existing model selection. HOME `offline: false` enables the existing
API-key agent path; WORK uses Vertex AI / gcloud ADC. Skills and memory storage
retain the existing shared paths, so restarting does not lose history.
