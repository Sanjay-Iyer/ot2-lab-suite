# Configuration Guide — unified printing schema

Every physical detail of a run lives in the YAML. Sections (all visually distinct):

```yaml
deck:            # slot number for each labware role
labware:         # loadName (+ namespace/version) for each role
pipettes:        # what is mounted on each mount
materials:       # what liquid is in each 20 mL vial
dilution_plan:   # which plate columns are prepared, from what, at what folds
mixing_plan:     # how prepared columns are mixed (P300, 8-up)
print_groups:    # each droplet volume: which pipette, layout, source, destination, tips
imaging:         # begin/end image capture
tip_policy:      # global reuse/return default
```

## deck + labware
```yaml
deck:
  tuberack: {slot: 7}
  plate:    {slot: 4}
  paper:    {slot: 5}
  tiprack_p300: {slot: 9}
  tiprack_p20:  {slot: 2}     # only for P20/mixed runs
labware:
  tuberack: {load_name: tuberack_3dprint_20ml_8vials_v2, namespace: custom_beta, version: 1}
  plate:    {load_name: corning_96_wellplate_360ul_custom, namespace: custom_beta, version: 1}
  paper:    {load_name: corning_96_wellplate_360ul_custom, namespace: custom_beta, version: 1}
  tiprack_p300: {load_name: opentrons_96_tiprack_300ul}
  tiprack_p20:  {load_name: opentrons_96_tiprack_20ul}
```

## pipettes
```yaml
pipettes:
  - {name: p300_multi_gen2, mount: right, single_start: A1}
  - {name: p20_single_gen2, mount: left}      # omit for P300-only runs
```

## materials (never hardcode liquids in Python)
```yaml
materials:
  water:      {role: solvent, labware: tuberack_3dprint_20ml_8vials_v2, vial: A1, initial_volume_ul: 15000}
  ethanol:    {role: solvent, labware: tuberack_3dprint_20ml_8vials_v2, vial: A2, initial_volume_ul: 15000}
  orange_dye: {role: dye,     labware: tuberack_3dprint_20ml_8vials_v2, vial: A3, initial_volume_ul: 8000}
```
`role` ∈ {solvent, stock, reagent, wash, dye, other} (free-form allowed; unknown roles
warn). Optional `aspirate_height_mm`, `dead_volume_ul` (default 1000), `allow_shared_vial`.

## dilution_plan + mixing_plan
```yaml
dilution_plan:
  enabled: true
  total_volume_ul: 200
  water_material: water
  water_setup_tip: H12
  factors: {mode: explicit, explicit: [1, 2, 5, 10, 20, 30, 40, 50]}
  series:
    - {name: orange, material: orange_dye, destination_column: "11", setup_tip: H11}
    - {name: blue,   material: blue_dye,   destination_column: "9",  setup_tip: H10}
mixing_plan: {mix_reps: 5, mix_volume_ul: 150}
```
`factors.mode` ∈ explicit | geometric | linear | log. Number of folds = number of dilution
rows (A..H). Setup tips must be unique H-row tips (with `single_start: A1`) not in a print
block column.

## print_groups (the core of printing)
```yaml
print_groups:
  - name: coarse_30ul
    volume_ul: 30
    pipette: auto            # auto | p300_multi_gen2 | p20_single_gen2
    layout: column_8up       # column_8up (P300, 8-up) | single_spot (P20, one at a time)
    source: {plate_column: "11"}                 # or: {plate_column: "11", wells: [A11,B11]}
    replicates: 2            # -> paper columns paper_start .. paper_start+replicates-1
    destination: {paper_start_column: 1, spacing_mm: {x: 9, y: 0, z: 0}}
    dispense: {z_mm: 1.0, air_gap_ul: 5, blow_out: true, post_dispense_delay_s: 0.5, move_speed_mm_per_s: 50}
    tips: {block_column: 1, reuse: true, return: true}   # P300: 8-tip block
  - name: fine_5ul
    volume_ul: 5
    pipette: auto
    layout: single_spot
    source: {plate_column: "11", wells: [A11, B11, C11]}
    replicates: 2
    destination: {paper_start_column: 5}
    dispense: {z_mm: 1.0, air_gap_ul: 2, blow_out: true}
    tips: {well: A1, reuse: true, return: true}          # P20: single tip well (20 µL rack)
```
Rules the validator enforces: volume ∈ pipette range; layout ↔ channel count; pipette
mounted; paper columns 1..12; no duplicate paper columns across groups; source wells
valid. Warnings: shared source column; per-well draw > well volume.

## imaging + tip_policy
```yaml
imaging: {capture_before: true, capture_after: true, robot_image_dir: /data/vision/vial_dilution_print}
tip_policy: {return_tips: true}
```

## Backward compatibility
Legacy flat configs (`pipette:` + `printing:` + `color_series:`) still build: they are
auto-migrated to one P300 `column_8up` group per series (a note is printed). Mixing a
legacy `printing:` block with new `print_groups:` is rejected.
