# Optional agent families

These are existing implementations, grouped without changing scientific methods.
Run module commands from the repository root with the appropriate HOME/WORK
environment and existing authentication. Do not infer physical validation from
their presence here.

| Family | Modules / launch |
| --- | --- |
| General workflow agent | `python -m src.agents.optional.main`; `tools.py` |
| Custom labware design | `python -m src.agents.optional.custom_labware_agent`; `labware_tools.py` |
| Registered printing jobs | `printing_agent.py`, `printing_tools.py`, `standard_experiment_workflow.py` |
| Earlier vial demo | `python -m src.agents.optional.vial_print_agent`; `vial_print_tools.py` |
| Shared optional instrument tools | `robot_http_tools.py`, `robot_protocol_registry.py` |
| Auth/model diagnostics | `check_llm_auth.py`, `check_models.py` |

Specialized experiment/V11/SERS launchers remain in `scripts/experiments/`.
Their generalized dilution, clover, layering and SERS engines retain their
existing configurations. NanoDrop does not replace all those capabilities.

`check_models.py` is a legacy network diagnostic with requests at module scope:
do not import it as part of offline initialization. There are no eager package
reexports. It was checked statically, not executed during cleanup.

The old `test_simulation_agent.py` depends on removed tools; the old stress
harness counts returned responses rather than checking current experimental
outcomes. Both are preserved as historical source in the external C2 archive,
with no loss of a liquid-handling implementation.
