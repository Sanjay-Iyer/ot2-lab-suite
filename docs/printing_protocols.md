# OT-2 Printing Demo — Protocol Variants

Two parallel protocol files exist for the printing demo. Choose based on your
physical hardware and test readiness.

---

## protocol_96.py — **RECOMMENDED FIRST PHYSICAL TEST**

| Property | Value |
|---|---|
| File | `src/protocols/generated/protocol_96.py` |
| Config | `configs/workflows/defaults/printing_96.yaml` |
| Plate | `opentrons_96_wellplate_200ul_pcr_full_skirt` |
| Namespace | Default Opentrons (NOT `custom_beta`) |
| Pipette | `p300_multi_gen2` (8-channel, right mount) |
| Well layout | Column-based: `A1`, `A2`, `A3`, `A4`, `A5`, `A6` |
| First run mode | `dry_position_check: true` (no liquid, move only) |
| Custom labware | ❌ Not required |

### Why this first

- Standard Opentrons labware with published geometry — no custom JSON risk.
- `p300_multi_gen2` is fully compatible with 96-well columns.
- `dry_position_check: true` lets you verify tip clearance without dispensing.

### Well layout for 8-channel pipette

When the `p300_multi_gen2` addresses `A1`, all 8 tips align over column 1
(`A1`–`H1`). Use **A-row** addresses for column-based transfers:

| Role | Wells |
|---|---|
| Food coloring stock | A1 (col 1), A2 (col 2) |
| Water source | A3 (col 3), A4 (col 4) |
| Dilution destination | A5 (col 5), A6 (col 6) |
| Print source | A5, A6 |

### Safe Z offsets

```python
TRAVEL_CLEARANCE_MM = 15.0   # well.top(15.0)  — safe lateral travel
ASPIRATE_HEIGHT_MM  = 3.0    # well.bottom(3.0) — aspiration
DISPENSE_HEIGHT_MM  = 5.0    # well.bottom(5.0) — dispense / print
```

No `well.bottom()`, `well.bottom(0)`, or `well.bottom(-...)` calls exist.

### SCP deploy command

```powershell
scp -O -i C:\Users\iyersn\.ssh\id_rsa_opentrons `
    .\src\protocols\generated\protocol_96.py `
    root@169.254.46.57:/var/lib/opentrons/user_storage/ot2_runs/protocol_96.py
```

### Robot-side verification

```bash
ssh -i C:\Users\iyersn\.ssh\id_rsa_opentrons root@169.254.46.57 \
  "grep -n 'opentrons_96_wellplate\|custom_beta\|usascientific\|load_labware\|dry_position_check\|bottom' \
   /var/lib/opentrons/user_storage/ot2_runs/protocol_96.py"
```

### Checklist before first physical run

1. Set `dry_position_check: true` in CONFIG (already the default).
2. Load `opentrons_96_tiprack_300ul` in slot 1.
3. Load `opentrons_96_wellplate_200ul_pcr_full_skirt` in slot 2.
4. Load a second `opentrons_96_wellplate_200ul_pcr_full_skirt` in slot 3
   (paper reference slot — used for geometric reference only).
5. Run protocol → verify tip moves to each well without collision.
6. Set `dry_position_check: false` to enable liquid transfer.

---

## protocol_12.py — **EXPERIMENTAL — DO NOT USE UNTIL VALIDATED**

| Property | Value |
|---|---|
| File | `src/protocols/generated/protocol_12.py` |
| Config | `configs/workflows/defaults/printing_12.yaml` |
| Plate | `usascientific12well_12_wellplate_6000ul` |
| Namespace | `custom_beta` |
| Version | `1` |
| Labware JSON | `/data/labware/v2/custom_definitions/custom_beta/usascientific12well_12_wellplate_6000ul/1.json` |
| Pipette | `p300_multi_gen2` (⚠️ blocked by default) |
| Custom labware | ✅ Required |

### Why experimental

- Previous physical test (2026-05-27): pipette hit plate/well bottom.
  - Root cause: `dispense_height_mm: 1.0` was too low for the custom geometry.
  - Fixed to `5.0 mm` in this version — but physical re-validation is needed.
- An 8-channel multi pipette is poorly matched to a 12-well layout.
  The protocol raises `ValueError` if `p300_multi_gen2` is used unless
  `CONFIG["pipette"]["allow_multi_on_12well"]` is explicitly `True`.

### Multi-channel safety guard

```python
if (
    "multi" in pipette_cfg["name"].lower()
    and plate_cfg["labware"] == "usascientific12well_12_wellplate_6000ul"
    and not pipette_cfg.get("allow_multi_on_12well", False)
):
    raise ValueError("Unsafe configuration: ...")
```

### Safe Z offsets

```python
TRAVEL_CLEARANCE_MM = 15.0   # well.top(15.0)  — safe lateral travel
ASPIRATE_HEIGHT_MM  = 5.0    # well.bottom(5.0) — aspiration
DISPENSE_HEIGHT_MM  = 5.0    # well.bottom(5.0) — dispense / print
```

### When to use

Only after:
1. Physical inspection confirms the custom labware JSON dimensions are correct.
2. A single-channel pipette dry-run verifies tip height clearance.
3. `allow_multi_on_12well: true` is set if multi-channel is required.

---

## printing_demo_latest.py — Legacy 12-Well (Historical)

Kept for backward compatibility. This is the 12-well variant as generated
before the split. See `protocol_12.py` for the corrected version with
safety guards, and `protocol_96.py` for the recommended test protocol.

---

## Config files

| Config | Plate | Notes |
|---|---|---|
| `printing_demo.yaml` | 12-well custom | Original config |
| `printing_12.yaml` | 12-well custom | Parallel 12-well config with Z fixes |
| `printing_96.yaml` | 96-well standard | **Use this first** |
