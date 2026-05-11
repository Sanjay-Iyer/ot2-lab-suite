# Overview

This project automates Nx dilution series on an Opentrons OT-2 robot —
or, more commonly during development, on a laptop using the built-in
`opentrons.simulate` simulator. No physical hardware is needed.

## What happens in a typical run

You perform three steps, each backed by a script or file:

1. **Configure** — describe your experiment in a YAML config.
2. **Simulate** — feed the config to the Opentrons simulator.
3. **Inspect** — read the structured log and simulator transcript.

## Data-flow diagram

```
                  ┌──────────────────────┐
                  │   You (operator)     │
                  └──────────┬───────────┘
                             │ answers prompts
                             ▼
                  ┌──────────────────────┐
                  │  tools/            │
                  │  make_config.py      │
                  └──────────┬───────────┘
                             │ writes
                             ▼
                  ┌──────────────────────┐
                  │  configs/            │
                  │  experiment.yaml     │
                  └─────┬──────────┬─────┘
                        │          │
           (LOCAL SIM)  │          │  (REAL ROBOT)
                        ▼          ▼
      ┌──────────────────┐        ┌──────────────────────┐
      │ tools/         │        │ tools/             │
      │ run_simulation.py│        │ deploy_to_robot.py   │
      └────────┬─────────┘        └──────────┬───────────┘
               │ generates JSON              │ generates JSON
               ▼                             ▼
      ┌──────────────────┐        ┌──────────────────────┐
      │ protocols/       │        │ (SCP Transfer via    │
      │ config.json      │        │  Ethernet Cable)     │
      └────────┬─────────┘        └──────────┬───────────┘
               │ read by                     │ read by
               ▼                             ▼
      ┌──────────────────┐        ┌──────────────────────┐
      │ protocols/       │        │ OPENTRONS OT-2       │
      │ dilution_protocol│        │ (Offline CLI)        │
      │ .py              │        │                      │
      └──────┬────────┬──┘        └──────────────────────┘
             │        │
  ┌──────────┘        └──────────┐
  ▼                              ▼
┌──────────────────┐          ┌───────────────────┐
│  logs/            │          │  outputs/          │
│  .log             │          │  _transcript.txt   │
└──────────────────┘          └───────────────────┘
```

## When to use this project

Use this workflow when you:

- Are developing or validating a dilution protocol before running it on
  real hardware.
- Want a reproducible, auditable record of every simulated run.
- Need to iterate quickly on dilution factors or volumes without a
  physical robot.

If you are ready to run on physical hardware, use the **Offline CLI** workflow. This project is optimized for robots connected via Ethernet without internet access. You will use `tools/deploy_to_robot.py` to prepare your files for the robot.

See [guides/07_offline_robot_execution.md](07_offline_robot_execution.md) for the step-by-step guide.

## Next steps

- [guides/01_writing_a_config.md](01_writing_a_config.md) — create your
  first config file.
- [guides/02_running_a_simulation.md](02_running_a_simulation.md) — run
  it through the simulator.
