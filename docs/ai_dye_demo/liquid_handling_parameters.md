# Liquid-handling parameters — dye dilution + paper print demo (protocol v19)

Status as of 2026-09-10. The **Source** column says where each value was established. All
of these values are lab-owned: the conversation cannot change any of them (the agent
refuses), and `settings` in the demo prints the live comparison with the machine profile.

## What changed, what did not, what needs a physical decision

| | |
|---|---|
| **Changed** | 1. Blow-out after every dilution dispense (`dilution.blow_out_after_dispense: true`). 2. No transfer below the P20's 1 µL minimum: a small remainder is merged with the previous transfer (20 + 0.63 µL becomes 10.31 + 10.31 µL). 3. Tips are taken only for steps that run. |
| **Unchanged** | Every height, the print air gap, push-out, print blow-out, dwell, flow rates, mixing, transfer size, pipette. |
| **Needs a physical decision (not changed in code)** | Print release height 1.1 mm: 0.5 mm is the last physically confirmed value. The protocol's plate aspirate height (1.0 mm) differs from the profile (0.2 mm). |

## Dilution transfers (dye and water from the vial rack into the dilution plate)

| Parameter | Value | Source | Notes |
|---|---|---|---|
| Aspirate height in vial | 4.0 mm above vial bottom | Machine profile `vial_rack`; 01_print_from_vial, 11_standard_print, 03_dye demo | A 28 mm bore needs about 2.46 mL just to cover 4.0 mm. The summary now prints "load at least X mL" per vial. |
| Water dispense | 2.0 mm below the well top (`top(-2.0)`) | Machine profile `plate`; v6 | In air above the liquid, so the shared water tip never touches a dilution. |
| Dye dispense | 1.0 mm below the well top (`top(-1.0)`) | v6 (profile says −2.0) | Documented difference, kept as the working v6 value. |
| Largest single transfer | 20 µL | v6 | Transfers above 20 µL are split. |
| Remainder under 1 µL | merged with the previous transfer | **new** | Example: 140.63 µL of water had ended with a 0.63 µL aspirate. It is now 6 × 20 + 10.31 + 10.31 µL, the same number of transfers. |
| Push-out on dispense | pipette default = **0 µL** | `opentrons_shared_data` p20 single GEN2 liquid definitions (7.0.2 and 9.0.0) | The dispense stops at the plunger "bottom" position and pushes nothing past it. |
| Blow-out after dispense | **yes**, at the dispense position (in air) | **new** (2026-09-10) | Before this, nothing cleared the tip after a dilution dispense. |
| Trailing air gap | none | v6 | See "Considered and not adopted". |
| Touch tip | none | — | Only the legacy v1/v2 protocols used `blow_out` + `touch_tip`. |
| Flow rates | 3.0 µL/s aspirate, 3.0 µL/s dispense | Machine profile | |
| Tips | one water tip, one dye tip (`per_liquid`) | v6 | The alternative `new_tip_every_transfer` uses a fresh tip each time. |

**Exact sequence now, for each transfer:**

```text
aspirate(chunk,  vial.bottom(4.0))
dispense(chunk,  well.top(-2.0 water | -1.0 dye))    # pipette default push-out = 0 µL
blow_out(        well.top(-2.0 water | -1.0 dye))    # new: empties the tip, in air
```

**Why droplets could stay on the tip:** the dispense ended at the plunger bottom with no
push-out and no blow-out. The last droplet could stay in or on the tip. The tip then went
back to the vial, so the well received less than planned and the drop went back into the
stock. The blow-out fixes that. It happens above the liquid, so it cannot bubble the
dilution.

**API 2.15 side effect, same as the validated printing loops:** after a blow-out, the
next `aspirate()` first re-prepares the plunger at the top of the next source (here, the
vial top, in air). `prepare_to_aspirate()` cannot be used instead because it arrives only
in API 2.16.

**Considered and not adopted:**

- **Air gap + bottom dispense.** The other dilution scripts (`04_general_dilution.py`,
  `11_general_dilution.py`, `03_dye_dilution_print_demo.py`) take a 1.5 µL trailing air
  gap and dispense at `bottom(2.0)`, then blow out. Adopting this would mean 18.5 µL
  transfers (more of them) and a submerged tip. That breaks the demo's rule that the
  shared dye tip never touches a dilution and carries it back to the stock vial.
- **Lab switch.** `dilution.blow_out_after_dispense: false` turns the new blow-out off
  without a code change.

## Mixing (before every print step)

| Parameter | Value | Source | Notes |
|---|---|---|---|
| Repetitions × volume | 2 × 15 µL | v6 | The operator can change reps and volume. |
| Height | 2.0 mm above well bottom | v6 | Lab-owned. |
| Minimum liquid to mix without drawing air | **89 µL** in the well | new check | Well area is π × 3.43² = 36.96 mm². After the tip draws 15 µL, the level must still be above 2.0 mm: 15 + 2.0 × 36.96 = 88.9 µL. |

Before this check, a plan with, say, 60 µL per dilution was accepted and mixed in air:
bubbles, poor mixing, and liquid left on the outside of the tip. Such a plan is now
refused with the reason. The protocol also logs a warning if it ever sees one.

## Printing (dilution plate well to paper)

| Parameter | Value | Source | Status |
|---|---|---|---|
| Drop volume | 5 µL default (operator can change: 1–18.5 µL) | Every validated print used 5 µL | unchanged |
| Aspirate height in dilution well | 1.0 mm above well bottom | v6 | Profile plate value is **0.2 mm**; experiments 03/19/28 used 0.5 mm. Kept at 1.0 mm. The new depth check guarantees the tip stays submerged. |
| Trailing air gap | 1.5 µL, taken 5.0 mm above the source | Machine profile; 01/02/09/11 print protocols | unchanged |
| Dispense | drop + air gap (6.5 µL for 5 µL) with **push-out 3.0 µL** | Machine profile | unchanged |
| Release height | **1.1 mm** above the paper well bottom | Machine profile, requested 2026-08-31 | **0.5 mm** is the last physically confirmed value (`configs/printing/four_clover_spacing_v13.yaml`, `01_print_from_vial.py`, `11_standard_print` template). Revalidation of 1.1 mm is pending. |
| Blow-out | yes, at the release position | Machine profile | unchanged, deliberately (see below) |
| Dwell after each drop | 2.0 s | Machine profile; v9 golden trace | unchanged |
| Pre-air chase | 0 µL | Machine profile | The v12 air-chase experiment used 5 µL chase with blow-out **off**, a different mechanism. |
| Tip | one per dilution row (`per_liquid`) | v6 | |

**Exact sequence, unchanged:**

```text
mix(2, 15 µL, source.bottom(2.0))
for each drop:
    aspirate(5.0,   source.bottom(1.0))
    air_gap(1.5,    height=5.0)
    dispense(6.5,   paper.bottom(1.1), push_out=3.0)
    blow_out(       paper.bottom(1.1))
    delay(2 s)
```

**Why the print blow-out was not changed.** The small liquid volume, trailing air gap,
push-out and blow-out together are the validated release cycle, used by every recent
printing protocol. Removing or adding steps would change the established droplet
behaviour.

**What remains if drops still hang:** the release height. The 11_standard_print docstring
records that heights above 0.5 mm "left drops hanging on the tip in earlier testing". The
demo uses 1.1 mm because that was the requested lab value. The tip-to-paper distance is
measured from the paper labware's modelled well bottom (`paper_print_96_flat`: well depth
0.1 mm), so paper thickness and labware calibration shift it directly.

**Recommended physical check (lab laptop, not done here):** print one column at 1.1 mm
and one at 0.5 mm from the same dilution and compare hanging drops. Then set the chosen
value in `configs/machines/ot2_standard_printing_p20_v1.yaml` and in the demo default YAML
together. The startup check flags any mismatch between the two.

## Tip-to-paper and other distances at a glance

| Distance | Value |
|---|---|
| Tip end above paper surface at release | 1.1 mm |
| Tip end above well bottom, print aspirate | 1.0 mm |
| Tip end above well bottom, mixing | 2.0 mm |
| Tip end below well top, water / dye dispense | 2.0 mm / 1.0 mm; well depth 10.67 mm, so 8.67 / 9.67 mm above the bottom |
| Liquid height at 150 µL | 4.06 mm, below both dispense heights |
| Carry-over warning | The summary warns when a fill brings the liquid within 1 mm of a dispense height: about 283 µL of water, or about 320 µL total. |

## Verification

- `tests/test_ai_dye_demo_protocol.py` runs the real protocol file against a recording
  fake OT-2. It checks:
  - every dilution dispense is followed by a blow-out at the same location;
  - the print cycle is exactly as above;
  - no aspirate is below 1 µL;
  - the motion matches the plan shown to the operator.
- The real opentrons 7.0.2 simulator (builder + runlog) was run on the default plan,
  3 dilutions, a print-only run with the vial rack off deck, and the new-tip policy:
  - Every run: `SIMULATION OK`, no aspirate under 1 µL.
  - Default plan: 66 dilution dispenses, all 66 followed by a blow-out.
  - 3-dilution plan: 17 of 17.

## Changing a lab-owned value

1. Decide on the instrument, with a physical test.
2. Edit `configs/machines/ot2_standard_printing_p20_v1.yaml` (with provenance) and the
   matching field in `configs/workflows/defaults/ai_agent_dilution_print_demo.yaml`.
3. Start the demo: the startup line must say the settings match the machine profile.
   `settings` shows every field.
