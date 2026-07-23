# OT-2 Laptop Roles

Before running OT-2 commands, identify the laptop role.

## Simulation laptop

- This laptop is simulation-only and is not connected to the real Opentrons.
- Only run simulations, builds, validation, static checks, and generated-protocol inspection here.
- Use `conda activate ai` for OT-2 simulation and validation work.
- LLM-backed agent testing on this simulation laptop may use the regular Gemini API-key path:
  set `LLM_PROVIDER=api-key` and `GOOGLE_API_KEY` in `.env`.
- Do not run live experiments from this laptop.
- Do not use robot HTTP live-run commands, `--live`, or any command that starts real liquid-handling motion.

## Real robot laptop

- Use `conda activate llm`.
- Real OT-2 agent interactions must use Vertex AI / gcloud ADC, not `GOOGLE_API_KEY`.
- On the real robot laptop, set `LLM_PROVIDER=vertexai`, `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION` in `.env`.
- Live OT-2 runs are allowed only on the real robot laptop.
- Before any live run, require explicit user confirmation that the robot is physically ready and that the command should run live.
- Resolve the OT-2 through `configs/robot.yaml`; run
  `python scripts/find_robot.py --check` when discovery fails.
- For the vial dilution print workflow, the real-laptop live command is:

```powershell
python scripts\run_vial_print_robot.py --live --skip-build --skip-validate
```

- Only use `--skip-build --skip-validate` when `src/protocols/generated/vial_dilution_print_latest.py` has already been rebuilt, reviewed, committed, pushed, and pulled onto the real robot laptop.
