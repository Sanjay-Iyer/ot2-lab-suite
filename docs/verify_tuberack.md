# Guide: Tube Rack Verification Protocol

How to simulate, deploy, and physically run [`src/protocols/verify_tuberack.py`](../src/protocols/verify_tuberack.py) —
the diagnostic that checks the centering, depth, and clearances of the custom
3D-printed **8 × 20 mL** tube rack before it is used for real liquid handling.

> This is a **hand-written diagnostic protocol**, separate from the generated
> workflow protocols (dilution/printing). It is not produced by the workflow
> pipeline and is run on its own.

---

## 1. What it does

Drives a **single active nozzle** (`H1`) of the 8-channel `p300_multi_gen2`
(right mount, `configure_nozzle_layout(SINGLE)`) to each of the 8 vials in turn,
pausing for visual inspection. One tip is picked up at the start and dropped at
the end — it is reused across all wells (this is a water/dry test).

The run is shaped by three flags (see §3). Depending on them it either hovers at
the well tops (collision check), descends into empty holes and probes the bore
(`touch_tip`), or descends and mixes in water.

## 2. Files involved

| File | Role |
|------|------|
| [`src/protocols/verify_tuberack.py`](../src/protocols/verify_tuberack.py) | The protocol (runtime parameters + pre-flight validation) |
| [`labware/tuberack_3dprint_20ml_8vials_v1.json`](../labware/tuberack_3dprint_20ml_8vials_v1.json) | The rack definition loaded on the robot |
| [`configs/labware/tuberack_3dprint_20ml_8vials_v1.yaml`](../configs/labware/tuberack_3dprint_20ml_8vials_v1.yaml) | Reproducible source for that JSON (`generate_labware.py`) |
| [`scripts/validate_protocol.py`](../scripts/validate_protocol.py) | Auto-runs every flag combination through the simulator |
| [`scripts/deploy.py`](../scripts/deploy.py) | `--labware` deploys the rack JSON to the robot |

### Rack geometry (what the definition encodes)

| Property | Value |
|----------|-------|
| Layout | 2 rows × 4 cols = 8 wells (A1–A4 / B1–B4) |
| Well | circular, Ø 28 mm, 55 mm deep, flat bottom, 20 mL |
| Row spacing / Col spacing | 34 mm / 31 mm |
| Labware top (`zDimension`) | 60 mm (= the vial rim; `well.top()` resolves here) |
| Namespace / version | `custom_beta` / `1` |

The 8-channel head has 9 mm nozzle pitch. With 34 mm rows and Ø28 vials the gap
between rows is only **6 mm**, so idle nozzles can sit over a neighbouring vial —
this is the core physical risk the run order in §5 is built around.

---

## 3. Run-mode flags (Runtime Parameters)

The three flags are exposed as **Opentrons App Runtime Parameters** (set on the
Run Setup screen — no code edit). For simulation, the `DEFAULT_*` constants near
the top of the protocol provide the same defaults.

| Parameter | Meaning | Default |
|-----------|---------|---------|
| `dry_run_top_only` | Visit well tops only — no descent, mix, or wall-touch | `True` |
| `vials_present` | Glass vials physically loaded in the rack | `True` |
| `wall_touch_check` | `touch_tip` bore/centering probe (**empty rack only**) | `False` |
| `wells_to_test` | `all` / `a1` / `corners` — subset for a cautious first run | `all` |
| `bottom_clearance_mm` | Height above well bottom for descent/mix (0–50) | `15` |
| `mix_reps` / `mix_volume_ul` | Mix cycles / volume (≤ 300 µL) | `3` / `150` |

**Safety interlock:** `wall_touch_check` requires `vials_present = False`. The
pre-flight block (§4) raises and performs **no motion** if both are set —
`touch_tip` must never run against glass.

## 4. Pre-flight validation (before any motion)

`_preflight()` runs first and **aborts the run** if anything is wrong:

- Interlock (above) and "bore probe only at depth".
- **Labware geometry cross-check** — the *loaded* definition must match the
  expected load name, well count (8), Ø (28), depth (55), and row/col spacing
  (34/31). This catches the **wrong or edited labware being on the robot** before
  the head moves. (Verified: pointing it at the 6-vial mold fails with all
  mismatches listed.)
- Range checks: clearance within `[0, depth)`, travel arc ≥ labware top, mix
  volume ≤ pipette max, touch radius `(0,1)`.

A failure prints `PRE-FLIGHT VALIDATION FAILED - no motion performed:` with the
reasons. Because Opentrons analysis runs the protocol, a wrong labware is caught
**at upload/analysis time**, not mid-run.

---

## 5. The physical run order (do not reorder)

Simulation only models the well **cavities** — not the glass vials standing proud
nor the printed rack body. So a passing sim proves the **active nozzle's** path is
legal and says **nothing** about idle-nozzle clearance. Hence:

1. **Run 1 — collision check.** `dry_run_top_only=True`, `vials_present=True`,
   vials loaded, **e-stop in hand**. Start with `wells_to_test=a1`, then `all`.
   Watch that idle nozzles clear neighbouring vial rims (worst at the B-row wells).
2. **Run 2 — depth + bore, empty rack.** Only if Run 1 is clear.
   `dry_run_top_only=False`, `vials_present=False`, `wall_touch_check=True`.
   Empty so idle nozzles cannot hit glass. Confirms centering/depth into the holes.
3. **Run 3 — water.** `dry_run_top_only=False`, `vials_present=True`,
   `bottom_clearance_mm=15` first, then `0` once depth is confirmed. Keep the mix
   below the water line so aspiration is observable.

---

## 6. Simulate it (dev or lab laptop)

Simulation needs the `ai` conda env (it has Opentrons). See
[SOP_Simulation_Testing.md](SOP_Simulation_Testing.md) for the env details.

**Single run (default flags):**
```powershell
conda activate ai
python src\protocols\simulate_protocol.py src\protocols\verify_tuberack.py
```

**All flag combinations at once (recommended):**
```powershell
python scripts\validate_protocol.py
```
This exercises every run mode and **asserts on the simulator's output text**, not
its exit code — because `opentrons.simulate` returns 0 even when a protocol errors
at runtime. Expect `ALL CASES PASSED` (6 cases).

> ⚠️ Do not trust a bare `simulate_protocol.py` "SUCCESS" banner alone for this
> protocol — it is keyed off the exit code, which hides runtime errors. Use
> `validate_protocol.py`, or read the simulator output for `Error`/`Exception`.

---

## 7. Get it onto the robot

Two pieces must reach the OT-2: the **labware definition** and the **protocol**.

### 7a. Labware definition → custom_definitions store

`opentrons_execute` resolves `load_labware(..., namespace=, version=)` from
`/data/labware/v2/custom_definitions/<namespace>/<loadName>/<version>.json`.
Use the deploy helper (reads namespace/loadName/version from the JSON, creates the
nested dir, copies to `<version>.json`, and verifies):

```powershell
# preview the destination without touching the robot
python -m scripts.deploy --labware labware\tuberack_3dprint_20ml_8vials_v1.json --dry-run

# real upload (lab laptop, SSH key present)
python -m scripts.deploy --labware labware\tuberack_3dprint_20ml_8vials_v1.json
```
Lands at `…/custom_beta/tuberack_3dprint_20ml_8vials_v1/1.json`.
**Alternative:** import the JSON through the Opentrons App (Labware → Import),
which writes to the same place.

> If you revise the rack, bump `version` in the JSON **and** in the protocol's
> `load_labware(..., version=)`, so you don't silently overwrite `1.json`.

### 7b. The protocol

Because this protocol's safety model is **pause-and-inspect** at every position,
the recommended runner is the **Opentrons App** (the App gives you a *Resume*
button at each pause):

1. App → Labware → Import the rack JSON (if not deployed via §7a).
2. App → Protocols → upload `verify_tuberack.py`.
3. Set the Runtime Parameters for the run you want (§5), then Run.

> ⚠️ **Headless caveat.** Under `opentrons_execute` over SSH the inspection
> `protocol.pause()` calls halt the run, and the OT-2 has no touchscreen to resume
> them — you would resume via the App's run controls regardless. For this
> interactive protocol, **run it through the App.** (Headless `opentrons_execute`
> is intended for the non-interactive workflow protocols, not this one.)

---

## 8. Real-robot watch-outs (sim cannot catch these)

1. **Run Labware Position Check (LPC)** for the rack. Definition positions are
   nominal; real placement drifts 1–2 mm, which matters with a 6 mm idle gap.
2. **Idle-nozzle / glass collision** — only verifiable by eye. Run 1 with e-stop.
3. **Seat every vial fully.** A proud vial exceeds the modelled 60 mm top and eats
   into the 75 mm travel-arc margin.
4. **Calibration current** — pipette offset + tip length; load a **full** 300 µL
   tiprack (single-nozzle pickup tracks from a fixed start).
5. **`bottom_clearance_mm=0` is literally the floor** — consider 1–2 mm as the
   practical minimum; keep the water line above the mix depth (15 mm ≈ 9 mL here).
6. **Robot software must support API 2.20** (and SINGLE partial-tip on OT-2).

---

## Related
- [SOP_Simulation_Testing.md](SOP_Simulation_Testing.md) — simulation env + gates
- [SOP_Robot_Deployment.md](SOP_Robot_Deployment.md) — connectivity, deploy, execute
- [skills/ot2-labware/SKILL.md](../skills/ot2-labware/SKILL.md) — making/regenerating labware
- [skills/ot2-robot-control/SKILL.md](../skills/ot2-robot-control/SKILL.md) — robot SSH/SCP
