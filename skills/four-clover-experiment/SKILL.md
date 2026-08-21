---
name: four-clover-experiment
description: Build reviewed four-clover printing experiment YAML from a pattern, spacing, volume, layer, and delay request.
domain: printing
agent: printing
families: [design]
designs: [four_clover_experiment]
references: []
---

# Four-clover printing experiment

Use this skill when a scientist asks for **clover** patterns: groups of four
droplets clustered around a centre point so their dried rings can overlap toward
the middle. Words that point here are "clover", "four droplets around a centre",
"spacing sweep", "ring overlap", and any request phrased as *N patterns at these
separations*.

Do **not** use this skill for well-by-well printing on the 96-position grid, for
dilution series, or for anything needing more than one liquid. That is the
`standard-printing-experiment` skill. This workflow prints exactly one liquid and
performs no dilution and no mixing.

Start from `configs/templates/printing/02_printing_four_clover.template.yaml`.

## What you own, and what you must never touch

You choose the science: which liquid, how much of it is loaded, where the
patterns go, how far apart the droplets are, the droplet volume, how many layers,
the rests, and the print order.

You never choose the hardware. Deck slots, labware identities, pipette identity
and limits, aspiration and park heights, the validated dispense standoff, air
handling, flow rates, printable-area bounds, and tip selection all come from the
registered `machine_profile`. Select the profile capability discovery gives you
and change nothing inside it. If no profile matches the hardware, stop and report
the problem rather than inventing values.

You never compute a coordinate. The deterministic executor turns a reference
well, an offset, and the half-offsets into the four absolute D1-D4 positions. If
you find yourself doing arithmetic on millimetres to produce an XY pair, stop.

## The geometry convention, which is the one easy mistake

`half_width_mm` and `half_height_mm` are offsets **from the centre**, so two
opposing droplets end up **twice** that far apart.

    a request for 2 mm separation  ->  half_width_mm: 1.0, half_height_mm: 1.0
    a request for 3 mm separation  ->  half_width_mm: 1.5, half_height_mm: 1.5
    a request for 4 mm separation  ->  half_width_mm: 2.0, half_height_mm: 2.0
    a request for 5 mm separation  ->  half_width_mm: 2.5, half_height_mm: 2.5

Never write the separation itself into those fields. A scientist saying "4 mm
clovers" almost always means 4 mm between opposing droplets.

The droplets are laid out around the centre as:

    D1 ....... D2      +y is toward paper row A
       .  C  .         +x is toward paper column 12
    D3 ....... D4

## Building the experiment

`clovers` is a list, and **its length is the number of patterns**. There is no
`replicates` or `count` field; four patterns means four entries.

- `reference_well` anchors a pattern on any paper position A1-H12.
- `x_offset_mm` / `y_offset_mm` shift the centre off that well, for placing a
  pattern between wells. Omit them for zero.
- `geometry` on an entry overrides `default_geometry` for that pattern alone.
  This is how a spacing comparison is expressed: one entry per separation, each
  with its own `geometry`.
- `layers` on an entry overrides `printing.layers` for that pattern alone.

Give patterns enough room. The resolver enforces a minimum separation between
droplets of different clovers and refuses any droplet whose footprint leaves the
paper. When a request asks for several patterns without saying where, spread them
across a row with a few wells between them and record that choice in
`metadata.notes`.

`layers` repeats the **whole four-droplet pattern** on the same coordinates. It
is not the number of patterns. Three layers with a five-minute dry between them
is `printing.layers: 3` and `printing.inter_layer_delay_s: 300.0`.

`order` is `clover_by_clover` (finish one pattern before the next) or
`position_by_position` (all D1s, then all D2s, which maximises drying time inside
a single pattern).

Anything the request did not state and you had to decide - a well choice, a
spacing, a volume, a reserve - goes in `metadata.notes` so a reviewer can
challenge it.

## Tools, in order

1. `list_four_clover_experiment_capabilities` - the registered profile and the
   exact fields you may set.
2. `validate_four_clover_experiment` - schema and profile validation.
3. `preview_four_clover_experiment` - the authoritative resolved coordinates and
   the scientist review. Present this, never a summary you wrote yourself.
4. `create_four_clover_experiment_config` - persist the immutable YAML under
   `configs/generated/`.
5. `simulate_four_clover_experiment` - local simulation only.

Use `report_printing_request_issue` when the request is missing scientific
information, asks for dilution or mixing, asks for a non-clover pattern, or
cannot be expressed in the fields above. Do not approximate an unsupported
request with a supported one.

Tool results are authoritative. If validation or resolution fails, fix the
science in the configuration; never work around a rejection by changing the
profile, the resolver, or the executor.

This surface cannot deploy or execute live OT-2 motion.
