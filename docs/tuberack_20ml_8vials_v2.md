# Guide: Custom 3D-Printed 8 × 20 mL Vial Rack — **v2**

The custom OT-2 tube rack that holds **eight 20 mL scintillation vials**, version 2.
v2 is a re-print of [v1](../labware/tuberack_3dprint_20ml_8vials_v1.json) with a
slightly **smaller outer rectangle**; the vial cavities are unchanged.

- Definition: [`labware/tuberack_3dprint_20ml_8vials_v2.json`](../labware/tuberack_3dprint_20ml_8vials_v2.json)
- Reproducible source: [`configs/labware/tuberack_3dprint_20ml_8vials_v2.yaml`](../configs/labware/tuberack_3dprint_20ml_8vials_v2.yaml)
- Generator: [`scripts/generate_labware.py`](../scripts/generate_labware.py)

---

## 1. What slots are available & how much it holds

8 vial positions in a **2 rows × 4 columns** grid (column-major ordering, like every
OT-2 labware):

| | Col 1 | Col 2 | Col 3 | Col 4 |
|----|----|----|----|----|
| **Row A** (back) | A1 | A2 | A3 | A4 |
| **Row B** (front)| B1 | B2 | B3 | B4 |

| Property | Value |
|----------|-------|
| Positions | 8 (A1–A4 / B1–B4) |
| Capacity **per vial** | **20 mL = 20 000 µL** |
| Capacity total (8 vials) | 160 mL |
| Well shape | circular, **Ø 28 mm**, **55 mm** deep, flat bottom |
| Column spacing / Row spacing | 31 mm / 34 mm |
| Well floor above deck (`z`) | 5 mm (`zDimension − depth` = 60 − 55) |
| Outer footprint (`x × y × z`) | **127 × 85 × 60 mm** |
| `loadName` | `tuberack_3dprint_20ml_8vials_v2` |
| Namespace / version | `custom_beta` / `1` |

`well.top()` resolves to the 60 mm vial rim; `well.bottom()` is the vial floor.

## 2. v1 → v2 difference (this is the *only* change)

| | v1 | v2 |
|----|----|----|
| Outer footprint (x × y) | 127.76 × 85.48 mm | **127.0 × 85.0 mm** |
| Row A / B centre Y | 59.74 / 25.74 mm | **59.26 / 25.26 mm** (shifted 0.48 mm front) |
| Vial Ø / depth / volume / spacing | Ø28 / 55 / 20 mL / 31×34 | **unchanged** |
| `loadName` | `…_v1` | `…_v2` (distinct → both coexist) |

> ⚠️ The originally-supplied `tuberack_3dprint_20ml_8vials_v2_smallerrectangle.json`
> still carried v1's internal `loadName`/`displayName`. The canonical v2 file above
> fixes that. Delete the `…_smallerrectangle.json` so there is one v2 definition and
> no two files claim `loadName tuberack_3dprint_20ml_8vials_v1`.

---

## 3. Regenerate the JSON (source-controlled)

The JSON is generated from the YAML so geometry is version-controlled, not hand-edited:

```powershell
conda activate ai
python scripts\generate_labware.py configs\labware\tuberack_3dprint_20ml_8vials_v2.yaml
# -> Wrote labware/tuberack_3dprint_20ml_8vials_v2.json
```

Edit the YAML (well size, spacing, footprint) and re-run to produce a new JSON.
The generator omits the cosmetic `groups[].brand` keys the Labware Creator adds;
Opentrons ignores their absence.

## 4. Simulate with it

The simulator loads custom definitions from a directory via `-L labware`
(by `loadName`, not filename). Any protocol that does
`load_labware("tuberack_3dprint_20ml_8vials_v2", slot, namespace="custom_beta", version=1)`
resolves against `labware/`:

```powershell
python src\protocols\simulate_protocol.py src\protocols\vial_dilution_print.py
```

## 5. Deploy to the robot

`opentrons_execute` resolves custom labware from
`/data/labware/v2/custom_definitions/<namespace>/<loadName>/<version>.json`.
Use the deploy helper (reads namespace/loadName/version from the JSON):

```powershell
# preview the destination, touch nothing
python -m scripts.deploy --labware labware\tuberack_3dprint_20ml_8vials_v2.json --dry-run
# real upload (lab laptop, SSH key present)
python -m scripts.deploy --labware labware\tuberack_3dprint_20ml_8vials_v2.json
```
Lands at `…/custom_beta/tuberack_3dprint_20ml_8vials_v2/1.json`. **Alternatively**
import the JSON via the Opentrons App (Labware → Import).

> If you revise the rack again, either bump `version` in the YAML **and** any
> protocol's `load_labware(..., version=)`, or use a new `loadName` (as v2 did), so
> you never silently overwrite an existing definition.

---

## 6. Using it with an 8-channel pipette — clearances to respect

The vials are **tall (60 mm)** and **widely spaced** (34 mm rows, 31 mm cols) vs the
8-channel head's 9 mm nozzle pitch. Two consequences:

1. **Single-nozzle (partial) access only.** The 9 mm pitch can't line up 8 nozzles on
   the 31/34 mm vial grid, so you reach the vials with `configure_nozzle_layout(SINGLE)`.
2. **Mind the slot *behind* the rack.** In SINGLE mode the 7 idle nozzles hang over the
   slot behind the active nozzle. If that slot holds a tall item (a tip box, another
   rack), reaching the vials raises `PartialTipMovementNotAllowedError`. Keep the slot
   directly behind the rack clear, or use `start="H1"` with the slot **in front** clear
   — see the worked example in
   [vial_dilution_print_demo.md](vial_dilution_print_demo.md).
3. **Real-robot idle-nozzle clearance.** Even when the API allows the move, run
   **Labware Position Check** and watch idle nozzles clear neighbouring vial rims by eye
   (the B-row gap is the tightest). Seat every vial fully — a proud vial exceeds the
   modelled 60 mm top.

---

## Related
- [vial_dilution_print_demo.md](vial_dilution_print_demo.md) — the demo that uses this rack
- [verify_tuberack.md](verify_tuberack.md) — the v1 centering/depth diagnostic (same geometry family)
- [skills/ot2-labware/SKILL.md](../skills/ot2-labware/SKILL.md) — making/regenerating labware
- [SOP_Robot_Deployment.md](SOP_Robot_Deployment.md) — connectivity, deploy, execute
