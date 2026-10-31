# Entry points

Run from the repository root. See [copyable commands](../docs/COMMANDS.md).

Only daily-use launchers remain at the top level:

| Script | Configuration / purpose |
| --- | --- |
| `launch_agent.py` | `configs/home/agent.yaml` or `configs/work/agent.yaml`; GUI or terminal |
| `ai_dye_demo_gui.py` | Direct NiceGUI; experiment `--config` |
| `ai_dye_demo.py` | Direct terminal NanoDrop; experiment `--config` |
| `run_dilution.py` | Manual NanoDrop YAML preview/build/simulation |
| `run_paper_printing.py` | Manual NanoDrop YAML preview/build/simulation |
| `check_ot2.py` | `configs/checks/ot2.yaml`; offline by default |

| Directory | Contents |
| --- | --- |
| `manual/` (15 Python files) | Shared builders/validator/runner, standard/clover manual workflows, smoke/alignment/camera/one-well/waste checks |
| `experiments/` (8) | Optional general/V11/printing/SERS launchers and specialized layering/droplet/experiment runners |
| `maintenance/` (8) | Discovery, SSH/connectivity, deploy/sync, image transfer and labware generation |
| `developer/` (11) | Red-team/SOP checks, audits, simulation matrices, reproducibility exports and runner template |

The same implementations and physical settings are retained. Paths changed;
there are no duplicate compatibility implementations. `deploy` and `sync_robot`
retain module-only invocation, for example `python -m scripts.maintenance.sync_robot`.
A dry-run label in an older instrument utility can still involve physical robot
communication or motion: use only documented offline modes on HOME.
