# OT-2 Laptop Roles

Before running OT-2 commands, identify the laptop role.

## Simulation laptop

- This laptop is simulation-only and is not connected to the real Opentrons.
- Only run simulations, builds, validation, static checks, and generated-protocol inspection here.
- Use `conda activate ai` for OT-2 simulation and validation work.
- Do not run live experiments from this laptop.
- Do not use robot HTTP live-run commands, `--live`, or any command that starts real liquid-handling motion.

## Real robot laptop

- Use `conda activate llm`.
- Live OT-2 runs are allowed only on the real robot laptop.
- Before any live run, require explicit user confirmation that the robot is physically ready and that the command should run live.
- The OT-2 robot IP for live runs is `169.254.46.57`.
- For the vial dilution print workflow, the real-laptop live command is:

```powershell
python scripts\run_vial_print_robot.py --robot-ip 169.254.46.57 --live --skip-build --skip-validate
```

- Only use `--skip-build --skip-validate` when `src/protocols/generated/vial_dilution_print_latest.py` has already been rebuilt, reviewed, committed, pushed, and pulled onto the real robot laptop.
