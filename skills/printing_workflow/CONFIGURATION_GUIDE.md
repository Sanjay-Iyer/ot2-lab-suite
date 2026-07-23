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
    droplets_per_spot: 1     # >1 stacks N droplets on the SAME spot (see below)
    mix_before: true         # pre-mix this group's source column 8-up with the P300
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

### droplets_per_spot — more than one droplet on the same spot
`droplets_per_spot: N` (integer ≥ 1, default 1) prints N droplets onto **each**
destination, one after another. Every droplet is a full aspirate → dispense cycle: the
tip goes back to the source well and returns to the same paper coordinate, so the spot
receives `volume_ul * N` in total. Use it to build up loading on one location instead of
spreading it across more paper columns — e.g. `volume_ul: 5, droplets_per_spot: 3` puts
15 µL on each spot as three separate 5 µL drops.
- It does **not** change which paper columns a group occupies (that is `replicates`).
- It multiplies the group's draw from each source well: the "per-well draw > well volume"
  warning accounts for `volume_ul * replicates * droplets_per_spot`.
- Raise `dispense.post_dispense_delay_s` so each drop can wick in before the next lands.
- After a `blow_out` the plunger is re-prepared **in air** between droplets, so repeat
  aspirations do not pick up an extra slug.

### mix_before — mixing a shared source column only once
`mix_before: true` (default) makes a group mix its source plate column 8-up with the P300
before printing. When several groups share one source column, set `mix_before: false` on
all but the first: without it the P300 re-picks its print block — which has already
touched paper — and dips it back into the plate on every group.

## protocol_version + small-volume dilution (v2)
```yaml
protocol_version: 2        # 1 (default) = P300-only dilution; 2 = P20-assisted

dilution_plan:
  # ... factors / series / water_setup_tip as usual ...
  small_volume:
    enabled: true
    pipette: p20_single_gen2
    threshold_ul: 20               # transfers <= this run on the P20, above on the P300
    min_volume_ul: 1.0             # below this NOTHING on deck is accurate -> pre-flight aborts
    water_setup_tip: A12           # 20 µL rack; only picked up if a water transfer is small
    series_setup_tips: {bp: A11}   # one P20 tip per series, no carryover

flow_rates:
  aspirate: 20.0                   # P300
  dispense: 80.0
  small_volume: {aspirate: 3.0, dispense: 3.0}   # P20 — slow, for 1-20 µL accuracy
```
The P300 is inaccurate below ~20 µL, so on a wide dilution series the most dilute points
carry real concentration error. v2 dispatches each vial → plate transfer **by volume**:
at or below `threshold_ul` it runs on the P20 (accurate to 1 µL), above it on the P300.
Each pipette keeps its own setup tip per phase, picked up only if that pipette is
actually needed and returned before the next phase.

Pre-flight rejects: a `small_volume.pipette` that is not mounted; a missing 20 µL rack;
a dilution tip that is not in that rack; a dilution tip that collides with another
dilution tip or with a print group's reusable P20 tip; and any transfer below
`min_volume_ul`. v2 requires `deck.tiprack_p20` — it is not optional as in v1.

Physical note: in v2 the P20 reaches into the 55 mm deep 20 mL vials. Simulation clears
it, but confirm on the robot that a 20 µL tip reaches the liquid without the nozzle body
fouling the vial mouth; raise `sources.vial_aspirate_height_mm` if not.

## imaging + tip_policy
```yaml
imaging: {capture_before: true, capture_after: true, robot_image_dir: /data/vision/vial_dilution_print}
tip_policy: {return_tips: true}
```

## Backward compatibility
Legacy flat configs (`pipette:` + `printing:` + `color_series:`) still build: they are
auto-migrated to one P300 `column_8up` group per series (a note is printed). Mixing a
legacy `printing:` block with new `print_groups:` is rejected.
