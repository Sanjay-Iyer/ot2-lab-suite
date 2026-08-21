# Stage 4 - Generalized template

Recorded on 2026-08-20 on the HOME simulation laptop. Simulation only.

## The artifact

`configs/templates/printing/01_printing_standard.template.yaml`

The template teaches the *language* of the standard printing workflow. It has to
show how to express liquid sources, labware roles, transfer, mixing, direct
dilution, serial dilution, printing, repeated printing, delays, replicates, and
controls - without pre-solving Experiment 01, because a blind agent must later
infer Experiment 01 from a natural-language request alone.

## How leakage is prevented, and proven

The template's worked example is deliberately unrelated:

| Experiment 01 answer | Template placeholder |
|---|---|
| eight twofold points, 1x .. 1/128 | three points at `fold: 3` |
| 30 uL usable per point | 25 uL usable per point |
| 5 uL droplets | 4 uL droplets |
| three repeated depositions | one, with a commented two-repeat variant |
| 300 s between layers | 60 s |
| nanoparticles and crystal violet | `solution_a` and `solvent` |
| four printed columns A1..H4 | six positions, A1..C2 |

`tests/test_standard_printing_template.py` enforces this mechanically rather than
by inspection. It scans both the raw template and an explicit allowlist of files in
the future blind generalized context for a list of Experiment 01 answers -
`nanoparticle`, `crystal`, `violet`, `sers`, `twofold`, `128`, `np_`, `cv_`, `300`,
`five minute`, and every `column N` spelling - and fails if any appears. It
separately asserts the structured values are not Experiment 01's: `fold != 2`,
point count `!= 8`, usable volume `!= 30`, droplet `!= 5 uL`, rest `!= 300 s`,
repeats `!= 3`, target count `!= 8`.

Two further boundaries are enforced:

- **No machine-owned values.** The experiment half of the template is scanned for
  `slot:`, `aspirate_height_mm`, `dispense_height_mm`, `air_gap`, `push_out`,
  `blow_out`, `flow_rates`, `load_name`, `namespace`, `p20_single_gen2`, and
  `tip_rack`. All of those live in the referenced machine profile.
- **No executable instructions.** The whole file is scanned for `aspirate(`,
  `dispense(`, `pick_up_tip`, `drop_tip`, `protocol.`, `import`, `def`, and
  `lambda`. A configuration must have no place to put a robot command.

## Completeness, also proven

The template must still teach everything. Tests assert it mentions all six step
types (`serial_dilution`, `direct_dilution`, `transfer`, `mix`, `print`, `delay`)
and every configurable behaviour: `repeats`, `delay_after_pass_s`,
`mix_before_aspirate`, `tip_policy` with `per_target` and `per_step`, `purpose`,
`control`, `product_liquid_ids`, `minimum_remaining_ul`, and
`require_drying_delay_between_deposits`. Replicates are shown concretely, as one
liquid printed onto several targets in a step marked `purpose: control`.

## The template is itself runnable

It validates, resolves, builds the trusted executor, and simulates with forced
motion: 25 actions, 7 transfers, 6 mixes, 6 prints, 1 rest, 8 tips, 24 uL printed.
A template that cannot itself run is a template that can drift from the schema
without anyone noticing.

## Alternative experiments proving generality

Variants are built from the template's vocabulary alone - no Python changes and no
executor changes:

| Variant | Demonstrates | Result |
|---|---|---|
| printing only | no preparation, no mixing, no rests | 0 transfers, 0 mixes, 0 delays, 2 prints |
| one droplet | one source and one target | 1 print, 1 tip |
| transfer and print | a named aliquot prepared before printing | 1 transfer, 1 print from the plate |
| mix and print | explicit source mixing before printing | 1 mix, 1 print |
| dilute and print, no mixing | a fourfold two-point ladder with `mix` omitted | 0 mixes, factors `[1, 4]` |
| direct dilution from a 96-well plate source | a plate as the *only* source labware | every source is `plate`, method `independent_direct_dilution` |
| seven drops, rested and wet-on-wet | repeat count and the drying policy | 7 prints with 7 rests; 7 prints with 0 rests |

Together with the Stage 2 resolver tests, this covers printing, transfer, mix,
direct and serial dilution, single and multiple droplets, vial and 96-well sources,
delays and mixing both enabled and disabled, same-row targets, and safe tip policy.

## Verification

```text
tests/test_standard_printing_template.py   16 passed
Stage 2 + template combined                92 passed
```
