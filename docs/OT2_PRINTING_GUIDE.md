# OT-2 Printing Architecture and Operating Guide

This guide covers the modern paper-printing architecture on the simulation laptop
and the later, explicitly manual OT-2 handoff on the instrument-connected work
laptop. It does not replace a physical deck check or prove liquid-handling quality.

## Architecture

```text
User scientific intent
        ↓
Printing Agent                 selects a capability
        ↓
Runtime SKILL.md               supplies scoped procedure
        ↓
High-level printing tool       inspect / validate / build / simulate
        ↓
Typed workflow patch schema    forbids unknown fields; carries units
        ↓
Printing workflow registry     preserves existing config/protocol paths
        ↓
Standard well-grid or design workflow
        ↓
Deterministic validation       geometry, capacity, source, deck, API
        ↓
Generated protocol + exact hashes
        ↓
Local simulation OR manual work-laptop execution
```

`standard` workflows deposit at exact paper-well locations. `design` workflows
resolve continuous XY coordinates. `four_clover` is the first registered design,
not a special case embedded in the agent. The registries are in
`src/printing/workflows/registry.py` and `src/printing/designs/registry.py`.

## Laptop roles and environments

### Home/simulation laptop

Use the `ai` environment. Allowed work is local inspection, validation, build,
simulation, mock execution, and generated-file inspection.

```powershell
conda activate ai
Set-Location C:\code\opentrons_home\ot2-lab-suite
python -m src.printing.cli --help
```

Never run `scripts/run_vial_print_robot.py`, pass `--live`, or contact the robot
from this laptop. A local simulation with motion exercised is classified:

```text
HOME SIMULATION VERIFIED
REQUIRES WORK-LAPTOP HARDWARE VERIFICATION
```

### Instrument-connected work laptop

Use the `llm` environment. If an LLM is used there, configure Vertex AI / gcloud
ADC with `LLM_PROVIDER=vertexai`, `GOOGLE_CLOUD_PROJECT`, and
`GOOGLE_CLOUD_LOCATION`; do not use `GOOGLE_API_KEY` for a robot session.

Live motion remains outside the modern Printing Agent and its tool surface. A human
operator must review the exact artifact/config, inspect the physical deck, and
explicitly decide to run the manual CLI command.

## Discover current capabilities

These commands read the production registries; they do not contact a robot.

```powershell
python -m src.printing.cli list
python -m src.printing.cli designs
python -m src.printing.cli describe --workflow complementary_bp_v10a
python -m src.printing.cli describe --workflow four_clover_spacing
```

The description contains the exact AI-selectable JSON schema and units. Material
identity, source well/profile, deck slots, pipette/mount, labware, calibrated flow
settings, and live authorization stay in config/operator control.

## Standard printing

The supported default standard workflow is `complementary_bp_v10a`, backed by
`configs/printing/complementary_bp_print_v10a.yaml`.

```powershell
# 1. Inspect its allowed patch fields and units.
python -m src.printing.cli describe --workflow complementary_bp_v10a

# 2. Validate the registered YAML with no AI overrides.
python -m src.printing.cli validate --family standard --workflow complementary_bp_v10a

# 3. Build a plan-only artifact under .test_tmp/printing-artifacts.
python -m src.printing.cli build --family standard --workflow complementary_bp_v10a

# 4. Build and simulate a separate exact artifact with protocol dry_run=false.
#    This exercises the motion path only inside the local Opentrons simulator.
python -m src.printing.cli simulate --family standard --workflow complementary_bp_v10a
```

An override must use only fields shown by `describe`. PowerShell requires careful
quoting; this example changes a volume explicitly in microlitres:

```powershell
python -m src.printing.cli validate --family standard --workflow complementary_bp_v10a --parameters '{"droplet_volume_ul":5.0}'
```

For a replicate-column experiment, inspect `plate_well_direct_v9`; a replicate
count alone is not enough to invent physical destination columns.

## Design printing and four-clover

List designs first, then select a workflow registered to that design. The supported
default is `four_clover_spacing`, backed by
`configs/printing/four_clover_spacing_v13.yaml`.

```powershell
python -m src.printing.cli designs
python -m src.printing.cli describe --workflow four_clover_spacing
python -m src.printing.cli validate --family design --workflow four_clover_spacing --design four_clover
python -m src.printing.cli preview --family design --workflow four_clover_spacing --design four_clover
python -m src.printing.cli build --family design --workflow four_clover_spacing --design four_clover
python -m src.printing.cli simulate --family design --workflow four_clover_spacing --design four_clover
```

Coordinate preview delegates to the current production four-clover resolver. Paper
bounds, minimum spacing, source volume, aspiration geometry, pipette capacity, and
timing are still deterministic validation gates.

## Safe config update and Git cycle

Historical configs are not reorganized. Edit the registered YAML in place or add a
new config/registry entry when it represents a genuinely new workflow profile.

```text
edit registered YAML
  → describe schema and validate
  → build plan-only artifact
  → simulate exact forced-motion artifact locally
  → inspect coordinates/output and hashes
  → rebuild the safe tracked latest artifact
  → review diff and run tests
  → commit/push
  → pull on work laptop
  → repeat validation and physical checks
```

After the architecture CLI has validated and simulated the unpatched registered
config, rebuild its tracked `*_latest.py` from the committed safe YAML:

```powershell
python scripts\build_vial_dilution_print.py --config configs\printing\complementary_bp_print_v10a.yaml --no-sim
python scripts\build_vial_dilution_print.py --config configs\printing\four_clover_spacing_v13.yaml --no-sim
git diff --check
git diff -- configs\printing src\protocols\generated
git status --short
```

Do not hand-edit embedded `CONFIG` blocks. Do not commit a latest artifact with
`DEFAULT_DRY_RUN=False`; forced-motion simulation artifacts belong under
`.test_tmp/printing-artifacts`, not `src/protocols/generated`.

## What local simulation verifies

The architecture simulator validates the request, builds a unique artifact and
canonical resolved-config snapshot, records SHA-256 for both, sets protocol
`dry_run=false` only in that temporary artifact, and passes the same artifact to the
local Opentrons simulator. A PASS shows that preflight and planned motion completed
under the local API/labware model.

It does not verify real deck placement, calibration, paper flatness/standoff,
aspiration liquid level, tip pickup, droplet formation, material response, camera,
network access, or collision clearance on the physical instrument.

## Manual work-laptop connection and execution

Connection identity is maintained in `configs/robot.yaml`. It currently records the
robot mDNS name, HTTP port `31950`, SSH port `22`, software capability, and last
discovered address. Link-local addresses can change, so do not copy a stale IP from
documentation. On the instrument-connected laptop run:

```powershell
conda activate llm
python scripts\find_robot.py --check
python scripts\check_ot2_ssh.py --legacy-rsa
```

SSH uses the dedicated `id_rsa_opentrons` key resolved from
`ROBOT_SSH_KEY_PATH`; never print, copy into documentation, or commit the private
key. HTTP is the protocol upload/run transport. SSH/SCP is used for diagnostics and
image pullback. No connection was attempted while preparing this guide.

Before live motion, the human operator must verify:

1. Pulled commit and registered config match the reviewed revision.
2. Registered config validates and the just-in-time live artifact passes simulation.
3. Robot discovery and configured API compatibility pass.
4. Deck slots, labware identity/orientation, paper fixture, tip racks, pipette/mount,
   source identity, source volume, aspiration height, waste clearance, and camera
   path match the selected config.
5. The robot is homed/clear, liquids and tips are physically ready, and the operator
   explicitly chooses to start live motion.

The actual runner is the HTTP API runner. Modern versions do not use the legacy
validation matrix, so run the architecture validator explicitly for the same config.
Then use a two-phase operator handoff: `--no-start` builds the live (`dry_run=false`)
artifact, simulates it, uploads it, and creates a run without pressing play; inspect
that exact file's hash against the recorded home forced-motion simulation hash before
a second command uploads the unchanged artifact into a new run and starts motion.
The unused phase-1 run record on the robot is expected.
Runner preflight/simulation is not a substitute for architecture validation.

```powershell
# WORK LAPTOP ONLY — phase 1: validate, build/simulate/upload, but DO NOT press play.
python -m src.printing.cli validate --family standard --workflow complementary_bp_v10a
python scripts\run_vial_print_robot.py --config configs\printing\complementary_bp_print_v10a.yaml --live --no-start --no-pull-images
Get-FileHash src\protocols\generated\complementary_bp_print_v10a_latest.py -Algorithm SHA256

# Compare the exact hash, complete the physical checklist, and explicitly confirm.
# Phase 2 starts live liquid motion without changing that artifact.
python scripts\run_vial_print_robot.py --config configs\printing\complementary_bp_print_v10a.yaml --live --skip-build --skip-validate

# Four-clover equivalent, also WORK LAPTOP ONLY.
python -m src.printing.cli validate --family design --workflow four_clover_spacing --design four_clover
python scripts\run_vial_print_robot.py --config configs\printing\four_clover_spacing_v13.yaml --live --no-start --no-pull-images
Get-FileHash src\protocols\generated\four_clover_spacing_v13_latest.py -Algorithm SHA256
# After hash review and explicit physical confirmation:
python scripts\run_vial_print_robot.py --config configs\printing\four_clover_spacing_v13.yaml --live --skip-build --skip-validate
```

`--skip-build --skip-validate` is used above only to preserve the exact hash produced
and simulated in phase 1. Never use it without that immediately preceding gate.
The live build temporarily leaves tracked latest code with `DEFAULT_DRY_RUN=False`.
Do not commit it. After the physical run, restore the safe latest artifact:

```powershell
python scripts\build_vial_dilution_print.py --config configs\printing\complementary_bp_print_v10a.yaml --set-dry-run true --no-sim
# Or, after the four-clover run:
python scripts\build_vial_dilution_print.py --config configs\printing\four_clover_spacing_v13.yaml --set-dry-run true --no-sim
git diff --check
git status --short
```

## Troubleshooting

| Symptom | Action |
|---|---|
| Unknown workflow/design | Run `python -m src.printing.cli list` and `designs`; hidden legacy versions are intentionally not advertised. |
| Unknown parameter | Run `describe`; workflow patch models reject extra fields instead of deep-merging them. |
| Validation reports bounds/spacing/capacity/source error | Correct the request or YAML. Do not persuade the agent to ignore deterministic validation. |
| Simulation PASS but no motion appears | Use `src.printing.cli simulate`; it builds a temporary forced-motion artifact. A committed config with `dry_run:true` may otherwise exercise only preflight. |
| Config changed but latest protocol did not | Re-run `scripts/build_vial_dilution_print.py --config <exact-yaml> --no-sim`, then inspect the generated diff. |
| Missing/malformed runner config | Fix the supplied YAML. The runner intentionally refuses rather than falling back to version 1. |
| Robot not found on work laptop | Check Ethernet, then run `python scripts/find_robot.py --check`; use the resolved config/mDNS result. |
| SSH public-key failure | Confirm `ROBOT_SSH_KEY_PATH` points to `id_rsa_opentrons`, then run `python scripts/check_ot2_ssh.py --legacy-rsa`. |
| API incompatibility | Do not lower protocol requirements blindly. Select a compatible workflow/transport and review `configs/robot.yaml`. |

## Adding a new coordinate design

Add a design-specific strict patch model only if the generic request cannot express
the real parameters; add its deterministic coordinate generator/validator; register
the design; register a functioning workflow/config; and add equivalence, negative,
preview, build, and simulation tests. Add an optional scoped SKILL.md specialization
when the design has distinct procedural knowledge. The Printing Agent discovers
designs and skill metadata, so it requires no design-specific branch or prompt list.
