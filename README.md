# OT-2 Lab Suite — Agent NanoDrop

AI-assisted liquid dilution and paper printing on an Opentrons OT-2. Natural
language, NiceGUI and manual Python/YAML interfaces reuse the existing workflow
planner, compiler and protocol generator.

```text
Natural language → Agent → Skills and Tools → Python/YAML workflow
                                            ↓
                                  OT-2 execution → Experiment memory
```

Start on the HOME laptop (offline; no instrument or model connection):

```powershell
cd C:\code\opentrons_home\ot2-lab-suite
conda activate ai
python scripts/launch_agent.py --config configs/home/agent.yaml
```

Preview a manual experiment without writing a protocol:

```powershell
python scripts/run_dilution.py --config configs/experiments/dilution/example.yaml
python scripts/run_paper_printing.py --config configs/experiments/paper_printing/example.yaml
```

Python defines reusable operations; YAML defines experimental parameters.
NanoDrop supports custom dilution-factor series from stock, prepared-sample
printing, logical sample sources, exact paper positions, droplets and replicates.
The current NanoDrop series is not a serial well-to-well transfer chain.

| Location | Purpose |
| --- | --- |
| [src/agents/dye_demo/](src/agents/dye_demo/README.md) | Primary agent, tools, skills, memory, session and NiceGUI |
| [src/agents/optional/](src/agents/optional/README.md) | Existing optional general, labware, printing and earlier vial agents |
| `src/printing/`, `src/core/` | Existing generalized workflow components and configuration |
| `src/protocols/`, `src/lab/` | Standalone OT-2 protocols, connection and instrument helpers |
| [scripts/](scripts/README.md) | Manual, agent and GUI entry points |
| [configs/](configs/README.md) | Experiments, launcher profiles and existing laboratory settings |
| [skills/](skills/README.md) | Shared Markdown experimental instructions |
| `experiment_history/`, `runs/` | Local experiment memory and run artifacts; ignored by Git |
| [raman/](raman/README.md) | Separate Raman analysis; not automated through NanoDrop |
| [archive/](archive/README.md) | Historical agent implementations and documentation |

**Operating guide:** [commands](docs/COMMANDS.md), [architecture](docs/ARCHITECTURE.md),
[configuration](configs/README.md), [reorganization checks](docs/REORGANIZATION.md).

WORK uses `conda activate llm` and the existing Vertex AI / gcloud ADC setup.
Robot identity remains in `configs/robot.yaml`; machine profiles, labware and
credential references retain their existing paths. Physical execution is WORK
only and requires explicit operator confirmation of robot readiness.

The older large redesign/review plan is historical; this practical organization
does not resume its approval process or activate its experimental patches.
