# Validation & Simulation

## Environment
Use the `ai` conda env; it has opentrons 9.0.0, pytest, pydantic. opentrons on numpy≥2
needs the `numpy.trapz` shim — applied automatically by `conftest.py` (tests) and by the
build/sim scripts. Direct invocation:
```
C:/Users/<you>/miniconda3/envs/ai/python.exe -m pytest      # from repo root
```

## One authoritative validator per rule
All config validation lives in `src/core/*` and is invoked by the build script — the
build script and protocol never re-implement a rule.

| Rule | Implemented in |
|---|---|
| volume ↔ pipette range, mount available, not-mounted | `pipette_selection.py` |
| single-channel vs multichannel layout | `pipette_selection.assert_layout_supported` + `print_groups` |
| source/dest presence, duplicate + out-of-bounds paper columns, per-well volume | `print_groups.resolve_and_validate` |
| material/vial validity, duplicate vials, dead-volume accounting, filename==loadName, aspirate height | `materials.validate_materials` |
| new-section normalization, deck-slot uniqueness, legacy structural checks | `workflow_config` + build script |

Validation runs **before** protocol generation. Errors name the section, group/step,
field, requested value, the mounted pipettes/loaded labware, and a correction, e.g.:
```
[ERROR] orange_30: 5 µL is outside p300_multi_gen2's range [20-300 µL]. Choose a volume in range or a different pipette.
[ERROR] materials.water: vial 'Z9' is not a well of 'tuberack_3dprint_20ml_8vials_v2' (valid: [...]).
[ERROR] materials.water: insufficient volume: needs ~3671 uL (consumed 2671 + dead 1000) but only 500 uL configured.
```

## Build + simulate
```bash
python scripts/build_vial_dilution_print.py --config configs/printing/01_vial_dilution_paper_print.mixed.yaml
# add --no-sim to validate + generate only
```
Pipeline: load YAML → `workflow_config.normalize_and_validate` (shared validators) →
deck-slot check → (legacy only) structural `validate()` → embed CONFIG → generate to
`src/protocols/generated/` → simulate.

## Interpreting simulation output (exact criteria)
`opentrons.simulate` **exits 0 even on runtime errors**, so the verifier scans the output
text. A build is `SIMULATION OK` iff `returncode == 0` AND no line matches the error
regex (`scripts/build_vial_dilution_print.py`):
```
Traceback (most recent call last) | RuntimeError | LabwareNotFoundError |
ProtocolCommandFailedError | InvalidProtocolData | KeyError | AttributeError
```
Watch for these failure signatures (all surface as matched lines / non-zero rc):
- `PartialTipMovementNotAllowedError` — deck collision (a rack in the P300 partial-tip
  envelope; keep slots 1 and 6 clear, put the 20 µL rack in slot 2/3/8/11).
- `LabwareNotFoundError` — custom labware not on the `-L labware` path / bad loadName.
- volume-limit / tip-state / nozzle-layout errors — bad group volume, missing tip, or an
  8-nozzle layout on the P20.
Normal informational lines ("robot_settings.json not found. Loading defaults",
"Configure pipette … NozzleLayout", mock-photo lines) are NOT failures.

## Multi-mode matrix
`scripts/validate_vial_print.py` runs the generated protocol through dry_run /
dilution_only / print_only / full_run and checks expected/forbidden phrases; a
deliberately corrupted labware case must FAIL. `ALL CASES PASSED` = green.

## Tests to run
```bash
python -m pytest tests/printing/test_pipette_selection.py tests/printing/test_print_groups.py \
                 tests/printing/test_materials.py tests/printing/test_workflow_config.py     # this workflow
python -m pytest                                                           # full suite
```
Known pre-existing failures unrelated to this workflow: `tests/test_robot_automation.py`
and `tests/test_robot_http_tools.py` (AI-agent LLM-auth gate, env-dependent) and one
`tests/test_printing_demo_config.py` overlap case (Family B).
