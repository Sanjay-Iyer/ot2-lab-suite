# Experiment memory

The existing YAML store has moved here without changing its data format or
storage location. `experiment_memory.py` retains the former public imports.
YAML already provides the lightweight structured persistence needed here; no
JSON migration or database is necessary.

Successful GUI/agent runs save operator identity, sequential run ID, date, mode,
summary, session metadata and the full experiment configuration under:

```text
experiment_history/<operator>/index.yaml
experiment_history/<operator>/run_0001.yaml
```

`save_run`, `load_index`, `find_runs` and `load_run` provide the reusable Python
interface. History lives on the laptop that performed the run and is ignored by
Git. It is experimental data: back it up rather than treating it as cache.
Loading previous settings uses the existing session proposal mechanism.
Manual preview/build artifacts live in `runs/manual/` and are not recorded as
successful physical experiments. The store assumes one writer per operator.
