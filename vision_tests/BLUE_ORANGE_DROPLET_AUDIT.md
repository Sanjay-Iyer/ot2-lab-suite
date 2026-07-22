# Individual droplet manual-audit tables

These tables map every CV result back to its physical location in each photograph.
They are generated from `outputs/blue_orange_camera_comparison/all_droplets.csv`.

## Photograph orientation and droplet IDs

Read every table exactly as you see the photograph:

- **Left to right:** C1, C2, C3 are the three intended blue columns; 
  C4, C5, C6 are the three intended orange columns.
- **Top to bottom:** R1 is the top/faintest dilution row and R8 is the 
  bottom/strongest dilution row.
- A droplet ID combines column and row. For example, `C4R2` is the 
  leftmost orange column and the second row from the top.
- The far-left blue/wet-paper artifact visible in some photos is outside 
  the intended grid and is not C1.

| Vertical position | C1 Blue | C2 Blue | C3 Blue | C4 Orange | C5 Orange | C6 Orange |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | `C1R1` | `C2R1` | `C3R1` | `C4R1` | `C5R1` | `C6R1` |
| **R2 upper** | `C1R2` | `C2R2` | `C3R2` | `C4R2` | `C5R2` | `C6R2` |
| **R3 upper** | `C1R3` | `C2R3` | `C3R3` | `C4R3` | `C5R3` | `C6R3` |
| **R4 upper-middle** | `C1R4` | `C2R4` | `C3R4` | `C4R4` | `C5R4` | `C6R4` |
| **R5 lower-middle** | `C1R5` | `C2R5` | `C3R5` | `C4R5` | `C5R5` | `C6R5` |
| **R6 lower** | `C1R6` | `C2R6` | `C3R6` | `C4R6` | `C5R6` | `C6R6` |
| **R7 lower** | `C1R7` | `C2R7` | `C3R7` | `C4R7` | `C5R7` | `C6R7` |
| **R8 BOTTOM (strongest)** | `C1R8` | `C2R8` | `C3R8` | `C4R8` | `C5R8` | `C6R8` |

## Table legends

- Detection: **D** = detected, **B** = borderline, **ND** = not detected, 
  and **UA** = unassessable. `score` is an engineering confidence score, 
  not a probability. `contrast` is signal above local paper noise.
- Color: `final` may use same-column consensus; `direct` is the color 
  sampled from that individual position before consensus.
- Shape: `D` = equivalent diameter, `C` = circularity, and `AR` = aspect 
  ratio. Unsupported means there were too few pixels for a reliable call.
- Coffee ring: ratio compares edge signal with center signal; contrast is 
  edge minus center. `not-evident` means no clear ring in that photograph, 
  not proof that no physical ring exists.

## OT-2: after deck

Source image: `C:\code\opentrons_home\ot2-lab-suite\vision_tests\raw\test\OT-2\after_deck.jpg`

[Open annotated grid](outputs/blue_orange_camera_comparison/ot2_after_deck/annotated.jpg) | [Open enlarged droplet montage](outputs/blue_orange_camera_comparison/ot2_after_deck/droplet_montage.jpg)

### Detection

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **D**; score=0.51; contrast=0.56 | **ND**; score=0.00; contrast=0.00 | **D**; score=0.96; contrast=1.06 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 | **B**; score=0.48; contrast=0.53 |
| **R2 upper** | **D**; score=1.00; contrast=12.91 | **D**; score=1.00; contrast=4.50 | **D**; score=1.00; contrast=2.28 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 | **D**; score=1.00; contrast=1.18 |
| **R3 upper** | **D**; score=1.00; contrast=7.53 | **D**; score=0.95; contrast=1.04 | **D**; score=1.00; contrast=1.93 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 | **D**; score=1.00; contrast=1.45 |
| **R4 upper-middle** | **D**; score=1.00; contrast=9.84 | **D**; score=1.00; contrast=4.21 | **D**; score=1.00; contrast=4.68 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 | **D**; score=0.98; contrast=1.08 |
| **R5 lower-middle** | **ND**; score=0.00; contrast=0.00 | **D**; score=1.00; contrast=2.19 | **D**; score=1.00; contrast=6.78 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 |
| **R6 lower** | **D**; score=1.00; contrast=20.96 | **D**; score=1.00; contrast=17.34 | **D**; score=1.00; contrast=21.38 | **D**; score=1.00; contrast=5.81 | **D**; score=1.00; contrast=6.74 | **D**; score=1.00; contrast=2.40 |
| **R7 lower** | **D**; score=1.00; contrast=8.89 | **ND**; score=0.08; contrast=0.09 | **D**; score=1.00; contrast=7.24 | **D**; score=1.00; contrast=1.79 | **ND**; score=0.08; contrast=0.09 | **B**; score=0.47; contrast=0.52 |
| **R8 BOTTOM (strongest)** | **D**; score=1.00; contrast=10.04 | **D**; score=1.00; contrast=3.77 | **D**; score=1.00; contrast=15.59 | **ND**; score=0.09; contrast=0.10 | **D**; score=1.00; contrast=10.33 | **D**; score=1.00; contrast=3.05 |

### Color

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | final **blue/cyan**; direct green | - | final **blue/cyan**; direct yellow | - | - | - |
| **R2 upper** | final **blue/cyan**; direct green | final **blue/cyan**; direct green | final **blue/cyan**; direct green | - | - | yellow |
| **R3 upper** | final **blue/cyan**; direct green | final **blue/cyan**; direct green | final **blue/cyan**; direct yellow | - | - | yellow |
| **R4 upper-middle** | final **blue/cyan**; direct green | final **blue/cyan**; direct green | final **blue/cyan**; direct green | - | - | yellow |
| **R5 lower-middle** | - | final **blue/cyan**; direct green | final **blue/cyan**; direct green | - | - | - |
| **R6 lower** | blue/cyan | blue/cyan | blue/cyan | yellow | final **orange**; direct yellow | yellow |
| **R7 lower** | blue/cyan | - | blue/cyan | yellow | - | - |
| **R8 BOTTOM (strongest)** | final **blue/cyan**; direct green | blue/cyan | blue/cyan | - | orange | yellow |

### Shape

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **unsupported**; D=0.00px | - | **unsupported**; D=2.26px | - | - | - |
| **R2 upper** | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | - | - | **unsupported**; D=0.00px |
| **R3 upper** | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | - | - | **unsupported**; D=0.00px |
| **R4 upper-middle** | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | - | - | **unsupported**; D=0.00px |
| **R5 lower-middle** | - | **unsupported**; D=0.00px | **unsupported**; D=0.00px | - | - | - |
| **R6 lower** | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=1.60px |
| **R7 lower** | **unsupported**; D=0.00px | - | **unsupported**; D=0.00px | **unsupported**; D=0.00px | - | - |
| **R8 BOTTOM (strongest)** | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | - | **unsupported**; D=0.00px | **unsupported**; D=0.00px |

### Coffee-ring effect

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **unsupported** | - | **unsupported** | - | - | - |
| **R2 upper** | **unsupported** | **unsupported** | **unsupported** | - | - | **unsupported** |
| **R3 upper** | **unsupported** | **unsupported** | **unsupported** | - | - | **unsupported** |
| **R4 upper-middle** | **unsupported** | **unsupported** | **unsupported** | - | - | **unsupported** |
| **R5 lower-middle** | - | **unsupported** | **unsupported** | - | - | - |
| **R6 lower** | **unsupported** | **unsupported** | **unsupported** | **unsupported** | **unsupported** | **unsupported** |
| **R7 lower** | **unsupported** | - | **unsupported** | **unsupported** | - | - |
| **R8 BOTTOM (strongest)** | **unsupported** | **unsupported** | **unsupported** | - | **unsupported** | **unsupported** |

Manual review notes:

- [ ] Grid circles are centered on the intended positions.
- [ ] Detection calls agree with the photograph.
- [ ] Color calls agree with the photograph.
- [ ] Reliable shape calls agree with the footprint.
- [ ] Reliable coffee-ring calls agree with edge-versus-center appearance.

## OT-2: after plate

Source image: `C:\code\opentrons_home\ot2-lab-suite\vision_tests\raw\test\OT-2\after_plate.jpg`

[Open annotated grid](outputs/blue_orange_camera_comparison/ot2_after_plate/annotated.jpg) | [Open enlarged droplet montage](outputs/blue_orange_camera_comparison/ot2_after_plate/droplet_montage.jpg)

### Detection

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **D**; score=1.00; contrast=1.58 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.26; contrast=0.28 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 | **D**; score=0.66; contrast=0.72 |
| **R2 upper** | **D**; score=1.00; contrast=12.87 | **D**; score=1.00; contrast=6.27 | **D**; score=1.00; contrast=2.97 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 | **D**; score=1.00; contrast=1.65 |
| **R3 upper** | **D**; score=1.00; contrast=8.65 | **D**; score=0.80; contrast=0.88 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 |
| **R4 upper-middle** | **D**; score=1.00; contrast=11.62 | **D**; score=1.00; contrast=5.94 | **D**; score=1.00; contrast=4.51 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 | **D**; score=0.70; contrast=0.77 |
| **R5 lower-middle** | **D**; score=1.00; contrast=11.27 | **D**; score=1.00; contrast=2.98 | **D**; score=1.00; contrast=7.28 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 |
| **R6 lower** | **D**; score=1.00; contrast=14.18 | **D**; score=1.00; contrast=9.43 | **D**; score=1.00; contrast=9.42 | **D**; score=1.00; contrast=5.06 | **D**; score=1.00; contrast=5.93 | **D**; score=1.00; contrast=2.95 |
| **R7 lower** | **D**; score=1.00; contrast=8.78 | **D**; score=1.00; contrast=5.90 | **D**; score=1.00; contrast=7.93 | **D**; score=1.00; contrast=2.99 | **ND**; score=0.00; contrast=0.00 | **D**; score=1.00; contrast=1.43 |
| **R8 BOTTOM (strongest)** | **D**; score=1.00; contrast=12.04 | **D**; score=1.00; contrast=10.81 | **D**; score=1.00; contrast=6.40 | **D**; score=1.00; contrast=5.98 | **D**; score=1.00; contrast=8.27 | **D**; score=1.00; contrast=6.13 |

### Color

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | green | - | - | - | - | yellow |
| **R2 upper** | green | final **blue/cyan**; direct green | final **blue/cyan**; direct green | - | - | yellow |
| **R3 upper** | green | final **blue/cyan**; direct green | - | - | - | - |
| **R4 upper-middle** | green | final **blue/cyan**; direct green | final **blue/cyan**; direct green | - | - | yellow |
| **R5 lower-middle** | green | final **blue/cyan**; direct green | final **blue/cyan**; direct green | - | - | - |
| **R6 lower** | final **green**; direct blue/cyan | blue/cyan | blue/cyan | yellow | yellow | yellow |
| **R7 lower** | final **green**; direct blue/cyan | blue/cyan | blue/cyan | yellow | - | yellow |
| **R8 BOTTOM (strongest)** | green | blue/cyan | blue/cyan | yellow | yellow | yellow |

### Shape

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **unsupported**; D=0.00px | - | - | - | - | **unsupported**; D=0.00px |
| **R2 upper** | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.80px | - | - | **unsupported**; D=0.00px |
| **R3 upper** | **unsupported**; D=0.00px | **unsupported**; D=0.00px | - | - | - | - |
| **R4 upper-middle** | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | - | - | **unsupported**; D=0.00px |
| **R5 lower-middle** | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | - | - | - |
| **R6 lower** | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px |
| **R7 lower** | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | - | **unsupported**; D=0.00px |
| **R8 BOTTOM (strongest)** | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px | **unsupported**; D=0.00px |

### Coffee-ring effect

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **unsupported** | - | - | - | - | **unsupported** |
| **R2 upper** | **unsupported** | **unsupported** | **unsupported** | - | - | **unsupported** |
| **R3 upper** | **unsupported** | **unsupported** | - | - | - | - |
| **R4 upper-middle** | **unsupported** | **unsupported** | **unsupported** | - | - | **unsupported** |
| **R5 lower-middle** | **unsupported** | **unsupported** | **unsupported** | - | - | - |
| **R6 lower** | **unsupported** | **unsupported** | **unsupported** | **unsupported** | **unsupported** | **unsupported** |
| **R7 lower** | **unsupported** | **unsupported** | **unsupported** | **unsupported** | - | **unsupported** |
| **R8 BOTTOM (strongest)** | **unsupported** | **unsupported** | **unsupported** | **unsupported** | **unsupported** | **unsupported** |

Manual review notes:

- [ ] Grid circles are centered on the intended positions.
- [ ] Detection calls agree with the photograph.
- [ ] Color calls agree with the photograph.
- [ ] Reliable shape calls agree with the footprint.
- [ ] Reliable coffee-ring calls agree with edge-versus-center appearance.

## Phone: frame 1

Source image: `C:\code\opentrons_home\ot2-lab-suite\vision_tests\raw\test\camera\blue_orange\PXL_20260616_194303424.jpg`

[Open annotated grid](outputs/blue_orange_camera_comparison/phone_blue_orange_frame_1/annotated.jpg) | [Open enlarged droplet montage](outputs/blue_orange_camera_comparison/phone_blue_orange_frame_1/droplet_montage.jpg)

### Detection

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **D**; score=0.71; contrast=1.93 | **D**; score=1.00; contrast=2.77 | **D**; score=1.00; contrast=2.79 | **ND**; score=0.00; contrast=0.00 | **D**; score=0.52; contrast=1.40 | **ND**; score=0.26; contrast=0.70 |
| **R2 upper** | **D**; score=1.00; contrast=5.25 | **D**; score=1.00; contrast=3.43 | **D**; score=1.00; contrast=4.77 | **ND**; score=0.29; contrast=0.79 | **B**; score=0.35; contrast=0.96 | **ND**; score=0.24; contrast=0.66 |
| **R3 upper** | **D**; score=1.00; contrast=7.85 | **D**; score=1.00; contrast=7.17 | **D**; score=1.00; contrast=14.49 | **D**; score=0.74; contrast=2.01 | **D**; score=0.52; contrast=1.41 | **ND**; score=0.28; contrast=0.77 |
| **R4 upper-middle** | **D**; score=1.00; contrast=10.00 | **D**; score=1.00; contrast=11.33 | **D**; score=1.00; contrast=14.70 | **D**; score=1.00; contrast=4.01 | **B**; score=0.41; contrast=1.11 | **D**; score=0.63; contrast=1.71 |
| **R5 lower-middle** | **D**; score=1.00; contrast=12.19 | **D**; score=1.00; contrast=15.58 | **D**; score=1.00; contrast=18.60 | **D**; score=1.00; contrast=2.96 | **D**; score=0.99; contrast=2.67 | **ND**; score=0.32; contrast=0.87 |
| **R6 lower** | **D**; score=1.00; contrast=12.89 | **D**; score=1.00; contrast=8.56 | **D**; score=1.00; contrast=33.36 | **D**; score=1.00; contrast=9.68 | **D**; score=1.00; contrast=7.25 | **D**; score=1.00; contrast=7.47 |
| **R7 lower** | **D**; score=1.00; contrast=15.87 | **D**; score=1.00; contrast=10.52 | **D**; score=1.00; contrast=34.62 | **D**; score=1.00; contrast=11.34 | **D**; score=1.00; contrast=12.80 | **D**; score=1.00; contrast=13.90 |
| **R8 BOTTOM (strongest)** | **D**; score=1.00; contrast=40.65 | **D**; score=1.00; contrast=39.69 | **D**; score=1.00; contrast=39.02 | **D**; score=1.00; contrast=18.73 | **D**; score=1.00; contrast=12.48 | **D**; score=1.00; contrast=11.22 |

### Color

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | final **blue/cyan**; direct yellow | final **blue/cyan**; direct yellow | final **blue/cyan**; direct green | - | orange | - |
| **R2 upper** | final **blue/cyan**; direct yellow | final **blue/cyan**; direct orange | final **blue/cyan**; direct green | - | - | - |
| **R3 upper** | blue/cyan | blue/cyan | blue/cyan | orange | orange | - |
| **R4 upper-middle** | final **blue/cyan**; direct green | final **blue/cyan**; direct yellow | blue/cyan | orange | - | orange |
| **R5 lower-middle** | final **blue/cyan**; direct yellow | final **blue/cyan**; direct yellow | blue/cyan | orange | orange | - |
| **R6 lower** | blue/cyan | blue/cyan | blue/cyan | orange | orange | orange |
| **R7 lower** | blue/cyan | blue/cyan | blue/cyan | orange | orange | orange |
| **R8 BOTTOM (strongest)** | blue/cyan | blue/cyan | blue/cyan | orange | orange | orange |

### Shape

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **round**; D=166.25px; C=0.874; AR=1.103 | **round**; D=157.79px; C=0.822; AR=1.046 | **blob/irregular**; D=122.81px; C=0.931; AR=1.299 | - | **round**; D=192.17px; C=0.986; AR=1.012 | - |
| **R2 upper** | **round**; D=154.61px; C=0.874; AR=1.089 | **blob/irregular**; D=111.06px; C=0.763; AR=1.675 | **blob/irregular**; D=118.42px; C=0.910; AR=1.542 | - | - | - |
| **R3 upper** | **round**; D=130.40px; C=0.698; AR=1.096 | **round**; D=138.01px; C=0.738; AR=1.112 | **blob/irregular**; D=104.29px; C=0.838; AR=1.806 | **round**; D=175.81px; C=0.930; AR=1.059 | **round**; D=169.22px; C=0.945; AR=1.104 | - |
| **R4 upper-middle** | **blob/irregular**; D=143.20px; C=0.741; AR=1.828 | **round**; D=99.09px; C=0.858; AR=1.153 | **blob/irregular**; D=105.63px; C=0.578; AR=1.033 | **blob/irregular**; D=141.98px; C=0.814; AR=1.493 | - | **round**; D=177.65px; C=0.946; AR=1.020 |
| **R5 lower-middle** | **blob/irregular**; D=108.98px; C=0.743; AR=1.526 | **blob/irregular**; D=89.82px; C=0.822; AR=1.406 | **round**; D=132.02px; C=0.715; AR=1.030 | **round**; D=175.37px; C=0.913; AR=1.022 | **round**; D=161.11px; C=0.855; AR=1.154 | - |
| **R6 lower** | **round**; D=174.02px; C=0.902; AR=1.060 | **round**; D=180.59px; C=0.928; AR=1.018 | **blob/irregular**; D=130.47px; C=0.874; AR=1.487 | **round**; D=157.55px; C=0.973; AR=1.187 | **blob/irregular**; D=103.76px; C=0.847; AR=1.637 | **blob/irregular**; D=103.69px; C=0.822; AR=1.349 |
| **R7 lower** | **round**; D=172.20px; C=0.907; AR=1.077 | **round**; D=185.38px; C=0.953; AR=1.019 | **blob/irregular**; D=102.87px; C=0.825; AR=1.837 | **blob/irregular**; D=133.74px; C=0.942; AR=1.335 | **blob/irregular**; D=99.50px; C=0.815; AR=1.729 | **blob/irregular**; D=89.31px; C=0.803; AR=1.837 |
| **R8 BOTTOM (strongest)** | **blob/irregular**; D=82.01px; C=0.736; AR=2.153 | **blob/irregular**; D=91.38px; C=0.850; AR=1.714 | **blob/irregular**; D=100.42px; C=0.761; AR=2.035 | **blob/irregular**; D=79.02px; C=0.774; AR=2.136 | **blob/irregular**; D=103.57px; C=0.766; AR=1.991 | **blob/irregular**; D=52.93px; C=0.619; AR=2.774 |

### Coffee-ring effect

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **strong**; ratio=2.033; contrast=1.569 | **strong**; ratio=3.847; contrast=2.964 | **not-evident**; ratio=1.103; contrast=0.431 | - | **not-evident**; ratio=1.419; contrast=0.487 | - |
| **R2 upper** | **possible**; ratio=1.281; contrast=1.415 | **not-evident**; ratio=1.196; contrast=0.892 | **possible**; ratio=1.225; contrast=1.130 | - | - | - |
| **R3 upper** | **strong**; ratio=1.798; contrast=4.721 | **strong**; ratio=2.761; contrast=5.531 | **possible**; ratio=1.411; contrast=4.463 | **not-evident**; ratio=1.235; contrast=0.477 | **not-evident**; ratio=1.185; contrast=0.356 | - |
| **R4 upper-middle** | **strong**; ratio=1.759; contrast=3.567 | **not-evident**; ratio=0.987; contrast=-0.178 | **strong**; ratio=6.045; contrast=11.971 | **possible**; ratio=1.433; contrast=1.037 | - | **not-evident**; ratio=1.099; contrast=0.262 |
| **R5 lower-middle** | **strong**; ratio=1.927; contrast=6.973 | **not-evident**; ratio=1.000; contrast=0.002 | **possible**; ratio=1.203; contrast=3.232 | **possible**; ratio=2.066; contrast=1.403 | **possible**; ratio=1.363; contrast=0.914 | - |
| **R6 lower** | **strong**; ratio=2.185; contrast=6.888 | **strong**; ratio=1.954; contrast=5.227 | **not-evident**; ratio=1.078; contrast=2.511 | **not-evident**; ratio=1.142; contrast=1.289 | **strong**; ratio=2.002; contrast=4.385 | **not-evident**; ratio=1.195; contrast=1.466 |
| **R7 lower** | **not-evident**; ratio=1.017; contrast=0.391 | **strong**; ratio=4.197; contrast=10.382 | **not-evident**; ratio=1.108; contrast=3.819 | **not-evident**; ratio=1.033; contrast=0.479 | **strong**; ratio=1.659; contrast=5.572 | **strong**; ratio=2.158; contrast=7.128 |
| **R8 BOTTOM (strongest)** | **strong**; ratio=1.519; contrast=12.232 | **possible**; ratio=1.272; contrast=8.070 | **possible**; ratio=1.277; contrast=8.519 | **strong**; ratio=1.447; contrast=4.582 | **strong**; ratio=1.861; contrast=3.982 | **strong**; ratio=1.574; contrast=2.941 |

Manual review notes:

- [ ] Grid circles are centered on the intended positions.
- [ ] Detection calls agree with the photograph.
- [ ] Color calls agree with the photograph.
- [ ] Reliable shape calls agree with the footprint.
- [ ] Reliable coffee-ring calls agree with edge-versus-center appearance.

## Phone: frame 2

Source image: `C:\code\opentrons_home\ot2-lab-suite\vision_tests\raw\test\camera\blue_orange\PXL_20260616_194304780.jpg`

[Open annotated grid](outputs/blue_orange_camera_comparison/phone_blue_orange_frame_2/annotated.jpg) | [Open enlarged droplet montage](outputs/blue_orange_camera_comparison/phone_blue_orange_frame_2/droplet_montage.jpg)

### Detection

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **D**; score=0.69; contrast=1.86 | **D**; score=0.88; contrast=2.36 | **D**; score=0.80; contrast=2.16 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.08; contrast=0.23 | **ND**; score=0.07; contrast=0.18 |
| **R2 upper** | **D**; score=1.00; contrast=3.80 | **ND**; score=0.26; contrast=0.69 | **D**; score=1.00; contrast=6.26 | **ND**; score=0.30; contrast=0.82 | **ND**; score=0.24; contrast=0.66 | **ND**; score=0.26; contrast=0.70 |
| **R3 upper** | **D**; score=1.00; contrast=11.19 | **D**; score=1.00; contrast=7.80 | **D**; score=1.00; contrast=11.67 | **D**; score=0.66; contrast=1.77 | **B**; score=0.38; contrast=1.04 | **D**; score=0.73; contrast=1.96 |
| **R4 upper-middle** | **D**; score=1.00; contrast=5.18 | **D**; score=1.00; contrast=7.90 | **D**; score=1.00; contrast=11.00 | **ND**; score=0.00; contrast=0.00 | **B**; score=0.40; contrast=1.07 | **D**; score=0.69; contrast=1.85 |
| **R5 lower-middle** | **D**; score=1.00; contrast=16.08 | **D**; score=0.74; contrast=2.00 | **D**; score=1.00; contrast=12.49 | **ND**; score=0.00; contrast=0.00 | **D**; score=0.90; contrast=2.42 | **D**; score=0.94; contrast=2.54 |
| **R6 lower** | **D**; score=1.00; contrast=9.20 | **D**; score=1.00; contrast=12.34 | **D**; score=1.00; contrast=23.63 | **D**; score=1.00; contrast=9.68 | **D**; score=1.00; contrast=6.05 | **D**; score=1.00; contrast=7.37 |
| **R7 lower** | **D**; score=1.00; contrast=12.74 | **D**; score=1.00; contrast=12.72 | **D**; score=1.00; contrast=23.09 | **D**; score=1.00; contrast=3.51 | **D**; score=1.00; contrast=8.17 | **D**; score=1.00; contrast=14.74 |
| **R8 BOTTOM (strongest)** | **D**; score=1.00; contrast=34.71 | **D**; score=1.00; contrast=38.13 | **D**; score=1.00; contrast=40.15 | **D**; score=1.00; contrast=16.59 | **D**; score=1.00; contrast=7.96 | **D**; score=1.00; contrast=11.28 |

### Color

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | final **blue/cyan**; direct green | final **blue/cyan**; direct yellow | final **blue/cyan**; direct green | - | - | - |
| **R2 upper** | final **blue/cyan**; direct green | - | final **blue/cyan**; direct green | - | - | - |
| **R3 upper** | blue/cyan | blue/cyan | blue/cyan | orange | - | orange |
| **R4 upper-middle** | final **blue/cyan**; direct green | blue/cyan | blue/cyan | - | - | orange |
| **R5 lower-middle** | final **blue/cyan**; direct green | blue/cyan | blue/cyan | - | orange | orange |
| **R6 lower** | blue/cyan | blue/cyan | blue/cyan | final **orange**; direct green | orange | orange |
| **R7 lower** | blue/cyan | blue/cyan | blue/cyan | final **orange**; direct blue/cyan | orange | orange |
| **R8 BOTTOM (strongest)** | blue/cyan | blue/cyan | blue/cyan | orange | orange | orange |

### Shape

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **blob/irregular**; D=126.21px; C=0.651; AR=1.892 | **blob/irregular**; D=146.10px; C=0.865; AR=1.374 | **blob/irregular**; D=140.94px; C=0.887; AR=1.513 | - | - | - |
| **R2 upper** | **blob/irregular**; D=82.84px; C=0.898; AR=1.607 | - | **blob/irregular**; D=116.24px; C=0.934; AR=1.395 | - | - | - |
| **R3 upper** | **round**; D=147.81px; C=0.753; AR=1.064 | **blob/irregular**; D=78.22px; C=0.738; AR=2.217 | **blob/irregular**; D=112.80px; C=0.929; AR=1.342 | **round**; D=105.02px; C=0.893; AR=1.215 | - | **round**; D=151.77px; C=0.913; AR=1.080 |
| **R4 upper-middle** | **blob/irregular**; D=130.49px; C=0.728; AR=1.441 | **round**; D=158.98px; C=0.833; AR=1.031 | **round**; D=160.55px; C=0.836; AR=1.139 | - | - | **round**; D=183.55px; C=0.945; AR=1.002 |
| **R5 lower-middle** | **round**; D=90.45px; C=0.855; AR=1.210 | **round**; D=137.67px; C=0.764; AR=1.132 | **blob/irregular**; D=147.88px; C=0.776; AR=1.476 | - | **round**; D=181.63px; C=0.961; AR=1.036 | **round**; D=150.96px; C=0.795; AR=1.004 |
| **R6 lower** | **round**; D=169.26px; C=0.875; AR=1.045 | **blob/irregular**; D=145.69px; C=0.814; AR=1.315 | **round**; D=158.47px; C=0.948; AR=1.071 | **blob/irregular**; D=49.36px; C=0.436; AR=3.461 | **round**; D=149.72px; C=0.771; AR=1.105 | **blob/irregular**; D=126.33px; C=0.920; AR=1.396 |
| **R7 lower** | **round**; D=172.18px; C=0.890; AR=1.080 | **round**; D=157.10px; C=0.815; AR=1.063 | **round**; D=139.84px; C=0.952; AR=1.199 | **blob/irregular**; D=27.51px; C=0.432; AR=4.690 | **round**; D=152.64px; C=0.785; AR=1.017 | **blob/irregular**; D=118.88px; C=0.922; AR=1.451 |
| **R8 BOTTOM (strongest)** | **round**; D=113.91px; C=0.672; AR=1.193 | **round**; D=118.92px; C=0.675; AR=1.223 | **blob/irregular**; D=118.74px; C=0.845; AR=1.783 | **round**; D=133.47px; C=0.849; AR=1.141 | **unsupported**; D=0.00px | **blob/irregular**; D=109.91px; C=0.798; AR=1.749 |

### Coffee-ring effect

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **possible**; ratio=1.446; contrast=1.105 | **possible**; ratio=1.581; contrast=1.157 | **possible**; ratio=1.761; contrast=1.412 | - | - | - |
| **R2 upper** | **strong**; ratio=2.054; contrast=1.987 | - | **not-evident**; ratio=1.179; contrast=1.121 | - | - | - |
| **R3 upper** | **strong**; ratio=1.508; contrast=3.861 | **strong**; ratio=2.045; contrast=3.163 | **possible**; ratio=1.172; contrast=1.946 | **not-evident**; ratio=1.070; contrast=0.299 | - | **possible**; ratio=1.602; contrast=0.945 |
| **R4 upper-middle** | **possible**; ratio=1.378; contrast=2.659 | **strong**; ratio=3.027; contrast=6.901 | **strong**; ratio=2.179; contrast=6.079 | - | - | **possible**; ratio=2.012; contrast=1.147 |
| **R5 lower-middle** | **possible**; ratio=1.394; contrast=4.978 | **strong**; ratio=1.990; contrast=4.738 | **not-evident**; ratio=0.990; contrast=-0.141 | - | **not-evident**; ratio=1.127; contrast=0.404 | **possible**; ratio=1.584; contrast=1.003 |
| **R6 lower** | **strong**; ratio=2.151; contrast=6.628 | **possible**; ratio=1.226; contrast=2.767 | **not-evident**; ratio=1.075; contrast=2.013 | **not-evident**; ratio=1.026; contrast=0.227 | **strong**; ratio=2.483; contrast=3.602 | **not-evident**; ratio=0.945; contrast=-0.401 |
| **R7 lower** | **strong**; ratio=1.542; contrast=5.933 | **possible**; ratio=1.403; contrast=4.208 | **not-evident**; ratio=1.027; contrast=0.814 | **not-evident**; ratio=1.049; contrast=0.540 | **strong**; ratio=4.172; contrast=7.001 | **not-evident**; ratio=1.032; contrast=0.446 |
| **R8 BOTTOM (strongest)** | **strong**; ratio=5.702; contrast=26.465 | **strong**; ratio=2.231; contrast=18.847 | **possible**; ratio=1.167; contrast=5.767 | **possible**; ratio=1.215; contrast=1.973 | **unsupported** | **strong**; ratio=1.602; contrast=3.100 |

Manual review notes:

- [ ] Grid circles are centered on the intended positions.
- [ ] Detection calls agree with the photograph.
- [ ] Color calls agree with the photograph.
- [ ] Reliable shape calls agree with the footprint.
- [ ] Reliable coffee-ring calls agree with edge-versus-center appearance.

## Phone: frame 3

Source image: `C:\code\opentrons_home\ot2-lab-suite\vision_tests\raw\test\camera\blue_orange\PXL_20260616_194305768.jpg`

[Open annotated grid](outputs/blue_orange_camera_comparison/phone_blue_orange_frame_3/annotated.jpg) | [Open enlarged droplet montage](outputs/blue_orange_camera_comparison/phone_blue_orange_frame_3/droplet_montage.jpg)

### Detection

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **D**; score=0.66; contrast=1.79 | **D**; score=0.76; contrast=2.05 | **D**; score=1.00; contrast=3.34 | **ND**; score=0.08; contrast=0.23 | **ND**; score=0.12; contrast=0.31 | **ND**; score=0.31; contrast=0.84 |
| **R2 upper** | **D**; score=1.00; contrast=3.08 | **D**; score=0.82; contrast=2.21 | **D**; score=1.00; contrast=4.78 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.00; contrast=0.00 | **ND**; score=0.24; contrast=0.65 |
| **R3 upper** | **D**; score=1.00; contrast=7.37 | **D**; score=1.00; contrast=5.42 | **D**; score=1.00; contrast=11.82 | **ND**; score=0.14; contrast=0.37 | **ND**; score=0.30; contrast=0.81 | **ND**; score=0.31; contrast=0.85 |
| **R4 upper-middle** | **D**; score=1.00; contrast=10.20 | **D**; score=1.00; contrast=6.87 | **D**; score=1.00; contrast=10.13 | **D**; score=0.92; contrast=2.47 | **D**; score=0.68; contrast=1.84 | **D**; score=0.58; contrast=1.56 |
| **R5 lower-middle** | **D**; score=1.00; contrast=14.60 | **D**; score=0.79; contrast=2.14 | **D**; score=1.00; contrast=15.98 | **D**; score=0.56; contrast=1.52 | **D**; score=0.53; contrast=1.43 | **D**; score=0.71; contrast=1.91 |
| **R6 lower** | **D**; score=1.00; contrast=11.11 | **D**; score=1.00; contrast=11.49 | **D**; score=1.00; contrast=26.38 | **D**; score=1.00; contrast=9.15 | **D**; score=1.00; contrast=7.05 | **D**; score=1.00; contrast=6.78 |
| **R7 lower** | **D**; score=1.00; contrast=14.45 | **D**; score=1.00; contrast=11.31 | **D**; score=1.00; contrast=22.49 | **D**; score=1.00; contrast=3.96 | **D**; score=1.00; contrast=10.65 | **D**; score=1.00; contrast=12.30 |
| **R8 BOTTOM (strongest)** | **D**; score=1.00; contrast=34.07 | **D**; score=1.00; contrast=37.31 | **D**; score=1.00; contrast=37.58 | **D**; score=1.00; contrast=18.13 | **D**; score=1.00; contrast=9.79 | **D**; score=1.00; contrast=11.12 |

### Color

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | final **blue/cyan**; direct yellow | final **blue/cyan**; direct yellow | final **blue/cyan**; direct green | - | - | - |
| **R2 upper** | final **blue/cyan**; direct yellow | final **blue/cyan**; direct orange | final **blue/cyan**; direct green | - | - | - |
| **R3 upper** | blue/cyan | blue/cyan | blue/cyan | - | - | - |
| **R4 upper-middle** | final **blue/cyan**; direct green | final **blue/cyan**; direct yellow | blue/cyan | orange | orange | orange |
| **R5 lower-middle** | final **blue/cyan**; direct green | blue/cyan | blue/cyan | orange | orange | orange |
| **R6 lower** | blue/cyan | blue/cyan | blue/cyan | final **orange**; direct yellow | orange | orange |
| **R7 lower** | blue/cyan | blue/cyan | blue/cyan | final **orange**; direct blue/cyan | orange | orange |
| **R8 BOTTOM (strongest)** | blue/cyan | blue/cyan | blue/cyan | orange | orange | orange |

### Shape

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **round**; D=167.86px; C=0.870; AR=1.033 | **round**; D=152.79px; C=0.842; AR=1.034 | **round**; D=158.24px; C=0.818; AR=1.030 | - | - | - |
| **R2 upper** | **blob/irregular**; D=112.34px; C=0.815; AR=1.563 | **blob/irregular**; D=121.55px; C=0.676; AR=1.520 | **blob/irregular**; D=124.44px; C=0.722; AR=1.349 | - | - | - |
| **R3 upper** | **round**; D=137.35px; C=0.708; AR=1.008 | **blob/irregular**; D=104.60px; C=0.647; AR=1.311 | **blob/irregular**; D=104.50px; C=0.909; AR=1.547 | - | - | - |
| **R4 upper-middle** | **blob/irregular**; D=94.26px; C=0.576; AR=2.952 | **blob/irregular**; D=86.07px; C=0.862; AR=1.617 | **round**; D=153.80px; C=0.800; AR=1.070 | **round**; D=160.77px; C=0.870; AR=1.110 | **round**; D=180.52px; C=0.959; AR=1.074 | **round**; D=165.44px; C=0.913; AR=1.210 |
| **R5 lower-middle** | **blob/irregular**; D=102.28px; C=0.805; AR=1.607 | **round**; D=160.01px; C=0.827; AR=1.015 | **round**; D=121.09px; C=0.944; AR=1.268 | **unsupported**; D=0.00px | **round**; D=185.40px; C=0.960; AR=1.017 | **round**; D=179.53px; C=0.940; AR=1.103 |
| **R6 lower** | **round**; D=182.82px; C=0.943; AR=1.022 | **blob/irregular**; D=148.47px; C=0.791; AR=1.326 | **round**; D=152.51px; C=0.942; AR=1.058 | **blob/irregular**; D=48.99px; C=0.442; AR=3.765 | **round**; D=149.54px; C=0.764; AR=1.049 | **blob/irregular**; D=127.27px; C=0.937; AR=1.315 |
| **R7 lower** | **round**; D=172.69px; C=0.889; AR=1.023 | **round**; D=177.36px; C=0.913; AR=1.018 | **blob/irregular**; D=123.83px; C=0.916; AR=1.402 | **blob/irregular**; D=27.67px; C=0.489; AR=4.235 | **blob/irregular**; D=83.88px; C=0.766; AR=2.061 | **blob/irregular**; D=103.79px; C=0.849; AR=1.694 |
| **R8 BOTTOM (strongest)** | **round**; D=111.43px; C=0.649; AR=1.264 | **round**; D=129.02px; C=0.706; AR=1.055 | **blob/irregular**; D=118.76px; C=0.890; AR=1.613 | **blob/irregular**; D=79.85px; C=0.739; AR=2.318 | **blob/irregular**; D=25.95px; C=0.702; AR=2.225 | **blob/irregular**; D=131.84px; C=0.816; AR=1.680 |

### Coffee-ring effect

| Photograph row | C1 Blue (leftmost intended) | C2 Blue (middle) | C3 Blue (rightmost) | C4 Orange (leftmost) | C5 Orange (middle) | C6 Orange (rightmost intended) |
|---|---|---|---|---|---|---|
| **R1 TOP (faintest)** | **strong**; ratio=2.647; contrast=2.229 | **possible**; ratio=1.712; contrast=1.416 | **strong**; ratio=2.528; contrast=2.413 | - | - | - |
| **R2 upper** | **strong**; ratio=1.466; contrast=1.538 | **possible**; ratio=1.328; contrast=1.344 | **strong**; ratio=2.176; contrast=2.729 | - | - | - |
| **R3 upper** | **not-evident**; ratio=0.972; contrast=-0.167 | **possible**; ratio=1.218; contrast=1.134 | **strong**; ratio=1.612; contrast=4.596 | - | - | - |
| **R4 upper-middle** | **possible**; ratio=1.175; contrast=1.751 | **strong**; ratio=1.674; contrast=2.938 | **strong**; ratio=1.573; contrast=4.535 | **possible**; ratio=1.235; contrast=0.878 | **not-evident**; ratio=0.982; contrast=-0.051 | **not-evident**; ratio=1.170; contrast=0.378 |
| **R5 lower-middle** | **not-evident**; ratio=1.055; contrast=0.855 | **strong**; ratio=2.245; contrast=4.745 | **not-evident**; ratio=1.003; contrast=0.053 | **unsupported** | **possible**; ratio=1.780; contrast=1.090 | **possible**; ratio=2.191; contrast=1.288 |
| **R6 lower** | **strong**; ratio=1.700; contrast=5.307 | **possible**; ratio=1.352; contrast=3.394 | **not-evident**; ratio=1.054; contrast=1.592 | **not-evident**; ratio=1.036; contrast=0.311 | **strong**; ratio=4.351; contrast=4.375 | **not-evident**; ratio=1.018; contrast=0.122 |
| **R7 lower** | **not-evident**; ratio=1.055; contrast=0.940 | **strong**; ratio=1.653; contrast=4.725 | **not-evident**; ratio=1.016; contrast=0.515 | **not-evident**; ratio=1.145; contrast=1.542 | **strong**; ratio=3.981; contrast=7.483 | **strong**; ratio=1.583; contrast=5.041 |
| **R8 BOTTOM (strongest)** | **strong**; ratio=3.434; contrast=23.155 | **strong**; ratio=2.325; contrast=20.336 | **not-evident**; ratio=1.113; contrast=3.941 | **possible**; ratio=1.315; contrast=3.237 | **strong**; ratio=2.784; contrast=5.772 | **strong**; ratio=1.605; contrast=3.194 |

Manual review notes:

- [ ] Grid circles are centered on the intended positions.
- [ ] Detection calls agree with the photograph.
- [ ] Color calls agree with the photograph.
- [ ] Reliable shape calls agree with the footprint.
- [ ] Reliable coffee-ring calls agree with edge-versus-center appearance.

