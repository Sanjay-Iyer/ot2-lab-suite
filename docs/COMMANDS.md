# Operating commands

HOME examples were checked offline. WORK network/physical commands require the
WORK laptop and remain unverified here. Start in the repository:

```powershell
cd C:\code\opentrons_home\ot2-lab-suite
conda activate ai
```

## NiceGUI and the AI agent

HOME defaults to no model or robot connection:

```powershell
python scripts/launch_agent.py --config configs/home/agent.yaml
python scripts/launch_agent.py --config configs/home/agent.yaml --terminal
```

Enter `quit` to leave the terminal. NiceGUI serves `http://127.0.0.1:8080`.
For AI-assisted simulation, copy the HOME launcher YAML, set `offline: false`
and retain `simulate: true`. Use the existing `.env` API-key setup
(`LLM_PROVIDER=api-key`, `GOOGLE_API_KEY`). No credentials belong in YAML.
`model: null` retains `.env` model selection; an explicit model overrides it for
this launcher process. `temperature` is also configurable.

Original entry points remain available:

```powershell
python scripts/ai_dye_demo_gui.py --simulate --offline --config configs/workflows/defaults/ai_agent_dilution_print_demo.yaml
python scripts/ai_dye_demo.py --simulate --offline --operator Tester --config configs/experiments/dilution/example.yaml
```

Launcher `--config` selects a **launcher profile**; its `workflow_config` selects
the experiment. Direct GUI/agent/manual `--config` selects **experiment YAML**.

## Manual dilution and paper printing

Preview without an LLM, internet, protocol write or robot contact:

```powershell
python scripts/run_dilution.py --config configs/experiments/dilution/example.yaml
python scripts/run_paper_printing.py --config configs/experiments/paper_printing/example.yaml
```

Build a standalone protocol and resolved YAML into a new `runs/manual/` directory:

```powershell
python scripts/run_dilution.py --config configs/experiments/dilution/example.yaml --build
python scripts/run_paper_printing.py --config configs/experiments/paper_printing/example.yaml --build
```

Local simulation with the existing pinned API-2.15 environment (both checked):

```powershell
python scripts/run_dilution.py --config configs/experiments/dilution/example.yaml --simulate --simulator-python .venv/ot2-api-2.15-py310/python.exe
python scripts/run_paper_printing.py --config configs/experiments/paper_printing/example.yaml --simulate --simulator-python .venv/ot2-api-2.15-py310/python.exe
```

If that interpreter is unavailable, omit `--simulator-python` to use the active
environment's simulator; this is not an exact version-equivalence claim.
Logs are saved beside `protocol.py` and `config.yaml`. `--output` selects a new
artifact directory; existing directories are not overwritten.

Either manual script also accepts the existing combined dilution-and-print YAML:

```powershell
python scripts/run_dilution.py --config configs/workflows/defaults/ai_agent_dilution_print_demo.yaml
```

Existing standard/four-clover engines retain their schemas and scripts (both
summary commands checked):

```powershell
python scripts/manual/run_printing_workflow.py standard --config configs/experiments/paper_printing/01_printing_standard.yaml --summary
python scripts/manual/run_printing_workflow.py four-clover --config configs/experiments/paper_printing/02_printing_four_clover.yaml --summary
```

Omitting `--summary` builds and simulates with those existing engines, including
their existing shared latest-artifact updates. New NanoDrop manual scripts use
separate run directories instead.

## Create another experiment

```powershell
Copy-Item configs/experiments/dilution/example.yaml configs/experiments/dilution/my_experiment.yaml
notepad configs/experiments/dilution/my_experiment.yaml
python scripts/run_dilution.py --config configs/experiments/dilution/my_experiment.yaml
```

Change factors, volume, wells and enabled steps. For printing, copy its example
and change sources, positions, droplet volumes/counts and replicates. Examples
use one `base_config`; section mappings merge, lists replace. See
[configuration](../configs/README.md). NanoDrop factors are independent dilutions
from stock, not serial well-to-well transfers. The printing-only example assumes
its declared sample wells are already filled.

## Instrument and one-well checks

Both commands are offline and checked on HOME:

```powershell
python scripts/check_ot2.py --config configs/checks/ot2.yaml
python scripts/manual/test_single_dilution_c12.py --config configs/checks/single_dilution.yaml --preview
```

The one-well YAML exposes the former Python transfer, mixing, flow, labware and
tip settings. Default parameters and the existing protocol motion are retained.

## WORK laptop only

Retain WORK's `.env`, ADC, robot configuration and machine profiles. Do not copy
HOME credentials or settings over them.

```powershell
conda activate llm
python scripts/check_ot2.py --config configs/checks/ot2.yaml --check
python scripts/launch_agent.py --config configs/work/agent.yaml
```

Diagnostics use existing network/SSH checks without rewriting robot settings.
The WORK profile launches the existing live-capable GUI. Its arguments were
checked with `--preview` here; connectivity, Vertex AI and physical execution
were not tested. WORK agent authentication remains `LLM_PROVIDER=vertexai` with
its existing cloud project/location and gcloud ADC.

After explicit physical-readiness confirmation, the existing one-well command is:

```powershell
python scripts/manual/test_single_dilution_c12.py --config configs/checks/single_dilution.yaml
```

It retains its `LIVE` prompt before connection. For full experiments, use the
established GUI run controls or existing runner procedure in
[the demo guide](ai_dye_demo/README.md). No physical runner or deployment switch
is replaced. Raman commands remain in [raman/README.md](../raman/README.md).
