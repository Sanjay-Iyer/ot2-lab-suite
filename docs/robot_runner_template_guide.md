# Robot Runner Template Guide

How to build a terminal runner for any OT-2 protocol, the way that **works every time**.

The reference template is [`scripts/run_robot_template.py`](../scripts/run_robot_template.py).
The canonical working examples are
[`scripts/run_vial_print_robot.py`](../scripts/run_vial_print_robot.py) and
[`scripts/run_droplet_error_check.py`](../scripts/run_droplet_error_check.py).

---

## Why this pattern (HTTP API, not `opentrons_execute`)

All runners talk to the robot over the **HTTP API on port 31950**. This matters:

- The robot server provides the **deck configuration**. Protocols at **apiLevel ≥ 2.14**
  run through the Protocol Engine, which *requires* a deck config. The App and the HTTP
  API supply it; **bare `opentrons_execute` over SSH does not** — so it dies at the first
  `load_labware` with `AreaNotInDeckConfigurationError: <slot> not provided`.
- It needs **no SSH** for the run itself, so there are no key/password prompts.
- It returns a live run **status** you can poll (`running → finishing → succeeded`).

> **Rule of thumb:** new protocols → pick the apiLevel you need (usually **2.28**), expose
> knobs as **runtime parameters**, and run them with an HTTP-API runner from this template.
> Only drop to `opentrons_execute`/SSH (apiLevel ≤ 2.13, legacy executor) for the rare case
> you deliberately want the headless path — and know it can't do partial-tip features.

---

## The shape of a runner

Every runner has the same five HTTP calls (copy them verbatim — they don't change):

| Function | Endpoint | Purpose |
|----------|----------|---------|
| `_upload_protocol` | `POST /protocols` | upload the `.py`, get a `protocolId` |
| `_create_run` | `POST /runs` | create a run with `runTimeParameterValues` |
| `_play_run` | `POST /runs/{id}/actions` | press play |
| `_monitor` | `GET /runs/{id}` | poll status until terminal |
| `_request` | (all) | shared HTTP wrapper with error surfacing |

You only customize **`main()`**: the protocol path, the runtime parameters, and the CLI flags.

---

## Step-by-step: adapt the template

1. **Copy it:** `scripts/run_robot_template.py` → `scripts/run_<your_protocol>.py`.

2. **Point `DEFAULT_PROTOCOL`** at the exact file you upload
   (e.g. `src/protocols/<your_protocol>.py`, or the generated `_latest.py` if it's built).

3. **Expose knobs as runtime parameters in the protocol** (apiLevel ≥ 2.18):
   ```python
   def add_parameters(parameters):
       parameters.add_bool(variable_name="dry_run", display_name="Dry run (no liquid)",
                           description="Load + pre-flight only.", default=False)
       parameters.add_int(variable_name="my_column", display_name="My column",
                          description="...", default=1, minimum=1, maximum=12)
   ```
   In `run()`, read them from `protocol.params.my_column`.
   - `description` must be **≤ 100 chars**; `display_name` **≤ 30 chars** (the engine rejects longer).

4. **Map CLI flags → `rtp`** in `main()`. Send only what the user set so the rest fall back
   to the protocol defaults:
   ```python
   ap.add_argument("--my-column", type=int, help="...")
   ...
   rtp = {"dry_run": not args.live}
   if args.my_column is not None:
       rtp["my_column"] = args.my_column
   ```
   The `rtp` keys **must exactly match** the `variable_name`s in `add_parameters()`.

5. **Keep `--live` = real liquid** (default = dry run). Every runner behaves this way, so
   the operator's muscle memory (`--live --skip-build`) stays consistent. Add `--skip-build`/
   `--skip-validate` as no-ops if your protocol has no build/validate step.

6. **Update the deck reminder** printed before the run.

---

## Running it

```powershell
# dry run first (no liquid):
python scripts\run_<your_protocol>.py --robot-ip 169.254.46.57

# real run:
python scripts\run_<your_protocol>.py --robot-ip 169.254.46.57 --live --my-column 5
```

---

## Optional: pulling files (images, logs) back

The run uses the HTTP API, but pulling files off the robot needs **SCP**. Do it after a
successful run, and use the **OT-2 key** — not the default `~/.ssh/id_rsa`:

- key: `--ssh-key` → `.env ROBOT_SSH_KEY_PATH` → `~/.ssh/id_rsa_opentrons`
- options: `-o IdentitiesOnly=yes -o PubkeyAcceptedAlgorithms=+ssh-rsa -o BatchMode=yes -i <key>`
- **always** `scp -O` (the OT-2 dropbear server needs the legacy SCP protocol)

See `_pull_images()` in [`run_droplet_error_check.py`](../scripts/run_droplet_error_check.py)
for the exact, working block. (Manual check the key works:
`ssh -o IdentitiesOnly=yes -o PubkeyAcceptedAlgorithms=+ssh-rsa -i
C:\Users\<you>\.ssh\id_rsa_opentrons root@169.254.46.57`.)

---

## Gotchas (all learned the hard way)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `AreaNotInDeckConfigurationError` | ran a ≥ 2.14 protocol via bare `opentrons_execute` | run via the HTTP API (this template) |
| `Permission denied (publickey)` + passphrase prompt | SSH fell back to `~/.ssh/id_rsa` | pass `-i id_rsa_opentrons` (only relevant to the file-pull step) |
| `Description ... greater than 100 characters` | runtime-parameter description too long | shorten it to ≤ 100 chars |
| Run says succeeded but did nothing | `dry_run` left true | pass `--live` |
| SCP hangs / `subsystem request failed` | missing `-O` | add `-O` to the `scp` command |
