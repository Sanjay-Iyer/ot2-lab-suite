---
name: ot2-robot-profile
description: The hardware + software profile of THIS lab's physical OT-2, and the rules every protocol must follow to actually run on it. Read this BEFORE writing or deploying any new protocol — it pins the pipette, mount, tip rack, apiLevel policy, the deck-configuration gotcha (why high-apiLevel protocols fail under opentrons_execute), custom-labware deploy path, and the single-nozzle deck-layout rules. Use as the template/checklist for new protocols.
---

# OT-2 Robot Profile — the lab's physical robot (read before building protocols)

This is the ground-truth profile of **this lab's OT-2**. A protocol that simulates
fine on a dev laptop can still be impossible to *run* here if it ignores these
constraints. Treat this file as the template + pre-flight checklist for every new
protocol.

## Hardware (fixed — do not assume otherwise)

| Item | Value |
|------|-------|
| Robot model | **Opentrons OT-2** (not a Flex) |
| Robot software / `opentrons` version | **9.0.0** |
| Pipette | **`p300_multi_gen2`** (8-channel, 300 µL) |
| Pipette mount | **RIGHT** |
| Left mount | (empty — no second pipette) |
| Standard tip rack | `opentrons_96_tiprack_300ul` |
| Trash | fixed trash, slot 12 |
| Deck calibration | present at `/data/robot/deck_calibration.json` |

Because the only pipette is an **8-channel**, any operation on a *single well*
(e.g. a per-well dilution gradient) must use **partial-tip / single-nozzle mode**
(`configure_nozzle_layout(style=SINGLE, ...)`). That feature drives the apiLevel
decision below.

## apiLevel policy — THE thing that decides if a protocol can run

Opentrons 9.0.0 runs protocols on two different engines depending on `apiLevel`:

| apiLevel | Engine | Deck config needed? | Run path that works | Use when |
|----------|--------|---------------------|---------------------|----------|
| **2.13 – 2.15** | legacy | No | `opentrons_execute` over SSH (and the Opentrons App) | full 8-channel or single-channel moves only — **no partial tip** |
| **2.16 – 2.28** | protocol engine | **Yes** | **Opentrons App only** (bare `opentrons_execute` fails) | you need partial tip, `configure_nozzle_layout`, waste chute, or other new-engine features |

Rules of thumb:

1. **Default to the LOWEST apiLevel that supports your features.** If the protocol
   never addresses a single well and never calls `configure_nozzle_layout`, use
   **2.15** and you can run it head-less over SSH like the older protocols.
2. **Need single-nozzle / partial tip?** You must go to the new engine:
   - `configure_nozzle_layout()` → new engine (≥ 2.16; OT-2 partial pickup ≥ 2.20).
   - `return_tip()` in partial mode → **2.28** (lower raises in partial config).
   These protocols **must be run from the Opentrons App**, which supplies the deck
   configuration the engine requires.
3. **Never raise the apiLevel "just in case."** A higher level silently moves you
   onto the new engine and the deck-config requirement.

### The error this prevents

```
AreaNotInDeckConfigurationError: 7 not provided by deck configuration.
```

Seen at the first `load_labware()` when a **≥ 2.16** protocol is run via bare
`opentrons_execute` (SSH). It does **not** mean your slot is wrong — it means the
new engine wants a deck configuration and the head-less CLI didn't provide one.
**Fix: run the protocol through the Opentrons App** (Protocols → import → run). The
App talks to the robot-server, which supplies the deck configuration. Do **not**
"fix" it by lowering the apiLevel if the protocol needs partial tip — that deletes
the feature.

## Run paths

| Protocol apiLevel | How to run on this robot |
|-------------------|--------------------------|
| ≤ 2.15 | `python scripts/run_smoke_test.py --local-protocol <file> --remote-protocol /var/lib/opentrons/user_storage/ot2_runs/<file>` (SSH + `opentrons_execute`), or the App |
| ≥ 2.16 | **Opentrons App**: import the custom labware JSONs, import the `.py`, press Run |

## Custom labware — two separate installs

Custom labware lives in **two** places and you may need both:

1. **For `opentrons_execute` (SSH runs):** deploy to the robot's definition store —
   ```bash
   python -m scripts.deploy --labware labware/<load_name>.json
   ```
   lands at `/data/labware/v2/custom_definitions/<namespace>/<loadName>/<version>.json`.
2. **For the Opentrons App:** import each JSON via the App's **Labware → Import**
   (the App has its own store, separate from the path above).

This lab's custom definitions: `tuberack_3dprint_20ml_8vials_v2`,
`corning_96_wellplate_360ul_custom` (both `namespace=custom_beta`, `version=1`).

## Single-nozzle (partial-tip) deck-layout rules

When a protocol uses `configure_nozzle_layout(style=SINGLE, ...)`, the 8-channel
head keeps all 8 nozzles physically present; only one is "active" and the other 7
idle nozzles stick out ~63 mm in a line. Two failure modes follow:

1. **Out-of-bounds** (`PartialTipMovementNotAllowedError`): keep every labware the
   single nozzle visits (vial rack, tip rack, plate) **out of the front row
   (1-2-3) and back row (10-11-12)**. Stay in the **middle rows (4-5-6 / 7-8-9)** so
   the idle nozzles have room on both sides.
2. **Collision with tall labware** (NOT caught by the simulator): set
   `single_start` so the idle nozzles point **away** from any tall item (e.g. the
   60 mm vial rack):
   - tall labware toward the **back** of the cluster → use **`A1`** (idle nozzles
     hang forward).
   - tall labware toward the **front** → use **`H1`** (idle nozzles hang back).

Verified-good layout for the vial-dilution-print demo: vial rack **slot 7**, plate
**slot 4**, paper **slot 5**, tips **slot 9**, `single_start: A1`.

## Connectivity (.env) gotchas

- **`ROBOT_IP` defaults to `127.0.0.1`** if absent from `.env` — every SSH/deploy
  then silently targets localhost and "fails to connect." Set the real link-local
  IP explicitly: `ROBOT_IP=169.254.46.57` (it can change when the robot reconnects;
  re-check in the App → Network or `ping`).
- SSH: user `root`, key `C:\Users\<you>\.ssh\id_rsa_opentrons`,
  `IdentitiesOnly=yes`, **BatchMode**, and
  `PubkeyAcceptedAlgorithms=+ssh-rsa` when `ROBOT_SSH_LEGACY_RSA=true`. SCP to
  the robot also needs `-O` (legacy protocol; Dropbear has no SFTP). Host-key
  checking remains enabled.
- Verify the whole link with `python -m scripts.check_connectivity` (expect every
  step PASS before deploying).

## New-protocol checklist

- [ ] Pipette = `p300_multi_gen2`, mount = **right**; tips = `opentrons_96_tiprack_300ul`.
- [ ] Pick the **lowest** apiLevel that supports the features used.
- [ ] If it uses partial tip / `configure_nozzle_layout` → apiLevel **2.28**, and plan
      to run it via the **Opentrons App** (not SSH).
- [ ] Single-nozzle labware in **middle rows**; `single_start` points idle nozzles
      away from tall labware.
- [ ] Custom labware deployed (SSH store and/or App import).
- [ ] `.env` `ROBOT_IP` set to the real robot, `check_connectivity` all PASS.
- [ ] Simulated clean in the `ai` env (and validated, for the vial demo).

## Related

- [ot2-robot-control](../ot2-robot-control/SKILL.md) — deploy/SSH/execute mechanics.
- [vial-dilution-print](../vial-dilution-print/SKILL.md) — the flagship 2.28 partial-tip demo.
- [ot2-protocols](../ot2-protocols/SKILL.md) — simulate-first workflow.
