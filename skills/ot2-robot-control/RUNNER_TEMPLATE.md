# Runner Template — build every robot runner like this

**Standard pattern for any OT-2 protocol runner.** Use it whenever the user asks for a
new "run X on the robot" script. Template file: [`scripts/run_robot_template.py`](../../scripts/run_robot_template.py).
Full guide: [`docs/robot_runner_template_guide.md`](../../docs/robot_runner_template_guide.md).

## The rule
Runners talk to the robot over the **HTTP API (port 31950)**, NOT `opentrons_execute`.
The robot server supplies the **deck configuration**, so engine-era protocols
(apiLevel ≥ 2.14, e.g. 2.28) load labware without `AreaNotInDeckConfigurationError`.
The run needs **no SSH**. Proven examples: `run_vial_print_robot.py`,
`run_droplet_error_check.py`.

## Recipe
1. Copy `scripts/run_robot_template.py` → `scripts/run_<protocol>.py`.
2. Set `DEFAULT_PROTOCOL` to the uploaded file.
3. In the protocol, expose knobs as **runtime parameters** via `add_parameters()`
   (apiLevel ≥ 2.18); read them in `run()` from `protocol.params`.
   - `description` ≤ 100 chars, `display_name` ≤ 30 chars (engine hard limits).
4. Add a CLI flag per knob; build `rtp` sending **only what the user set**; keys must match
   the `variable_name`s exactly.
5. `--live` = real liquid (default = dry run). Accept `--skip-build`/`--skip-validate` as
   no-ops if there's no build step, for command consistency.
6. The five HTTP helpers (`_upload_protocol`, `_create_run`, `_play_run`, `_monitor`,
   `_request`) are copied verbatim — only `main()` changes.

## Pulling files back (optional)
The run is HTTP, but pulling images/logs needs **SCP** with the OT-2 key:
`--ssh-key` > `.env ROBOT_SSH_KEY_PATH` > `~/.ssh/id_rsa_opentrons` (never the bare
`id_rsa`), plus `-o BatchMode=yes -o StrictHostKeyChecking=no -i <key>` and **`scp -O`**.
Working block: `_pull_images()` in `run_droplet_error_check.py`. See also
[[reference-ot2-ssh-key]] and `SKILL.md`.

## apiLevel cheat-sheet
- **≥ 2.14** → Protocol Engine → needs deck config → **HTTP API / App** (this template).
- **≤ 2.13** → legacy executor → can run via bare `opentrons_execute`/SSH, but no
  partial-tip / no runtime parameters. Only for deliberate headless cases.
