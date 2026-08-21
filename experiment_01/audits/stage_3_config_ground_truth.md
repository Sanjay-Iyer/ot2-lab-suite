# Stage 3 - Human-written config ground truth and A == B equivalence

Recorded on 2026-08-20 on the HOME simulation laptop. Simulation only; no robot
connection, upload, HTTP run, SSH run, or `--live` command was used.

## The claim

```text
A. STATIC GROUND TRUTH
   src/protocols/printing/01_printing_standard_ground_truth.py   (frozen Stage 1)
                    ==
B. CONFIG GROUND TRUTH
   configs/experiments/01_printing_standard.yaml
     -> PrintExperimentJobV1
     -> ResolvedExperimentPlanV1
     -> src/protocols/printing/01_printing_standard.py
     -> OT-2 local simulation
```

evaluated on canonical resolved physical actions, never on file names, YAML text, or
visual similarity.

## Result

```text
static actions            : 187
config actions            : 187
physical differences      : none
physical match            : True
setup differences         : none
setup match               : True
execution match           : True
structural match          : True

static  physical SHA-256  : 3ce809a8133a95207da62fce7bea44977cf4b134490478559c903b6b77e77313
config  physical SHA-256  : 3ce809a8133a95207da62fce7bea44977cf4b134490478559c903b6b77e77313
static  setup SHA-256     : 5ca13fa16f2e4302f109bb69518e4593955465d3ddeca48d1619ecdd1bf02c34
config  setup SHA-256     : 5ca13fa16f2e4302f109bb69518e4593955465d3ddeca48d1619ecdd1bf02c34
static  structural SHA-256: 43f483f270f1db93dacf46c640b97a162b9c4d716d5489599ddd631bdc30b864
config  structural SHA-256: 43f483f270f1db93dacf46c640b97a162b9c4d716d5489599ddd631bdc30b864

config job SHA-256        : 2d290f38abca6a6a8e1c908e1d643e29d16a5db7c2c4c81a86570fdd3ae78a49
config plan SHA-256       : 6f2c68045a956b67cd004f3115368691ee631ff0714e80261298a9d9d70b39f3
```

Totals are identical: 187 actions, 58 transfers, 56 mixes, 64 prints, 4 delays,
1200 s of configured rests, 320 uL printed, 61 tips.

The dilution arithmetic matches exactly - the same back-calculated twofold cascade,
59.765625 uL of original stock per series, 30 uL retained in each of the eight wells,
and the same per-step outgoing and diluent volumes. Per-liquid consumption matches:
59.765625 uL of nanoparticle stock, 180.234375 uL of nanoparticle diluent,
179.765625 uL of crystal violet stock, 180.234375 uL of crystal violet diluent.

## How equivalence is measured

`src/printing/standard/equivalence.py` provides action, setup, and diagnostic
normalizations.

| Trace | Keeps | Drops | Used for |
|---|---|---|---|
| `canonical` | the entire resolved plan | nothing | determinism, artifact drift |
| `physical` | action type, liquid ids, source/destination labware-well-reference-z, volumes, chunk index and count, mix cycles and volume, drop index and repeat count, delay duration, pipette, and a monotonic **tip index** | `operation_id`, `condition_id`, delay `reason`, tip-group *names* | A vs B |
| `setup` | liquid identity, source labware/well/height, loaded volume, reserve, scientific allocation | display labels and declaration order | required with `physical` for execution equivalence |
| `structural` | the physical trace with labware roles replaced by `namespace/load_name@slot` and liquid ids replaced by `L1, L2, ...` in order of first appearance | every remaining human-chosen label | topology-only diagnostic; never sufficient for A vs C |

The distinction that matters: *when the tip changes* is a physical fact and is kept,
as an index computed the same way the executor changes tips (including the rule that
a rest drops the tip first). What the tip group is *called* is commentary and is
dropped. Operation ids and delay reason strings are likewise commentary.

`execution_match` requires both the physical action trace and the initial setup
trace. Bare action lists cannot claim execution equivalence. Because A and B match
at both levels, the claim does not rest on structural normalization.

## What the config encodes

`configs/experiments/01_printing_standard.yaml` contains no deck slot, no calibrated
height, no air-handling value, no flow rate, and no pipette identity. All of that
arrives through `machine_profile: configs/machines/ot2_standard_printing_p20_v1.yaml`.
The experiment section declares four vial liquids and eight ordered steps:

| Step | Type | What it says |
|---|---|---|
| `np` | serial_dilution | eight twofold points, 30 uL usable each, mix 3 x 3 uL |
| `cv` | serial_dilution | eight twofold points, 30 uL usable each, mix 3 x 3 uL |
| `column1_np` | print | 8 targets, 1 x 5 uL, 300 s rest after the pass, mix before aspirating, tip per target |
| `column1_cv` | print | 8 targets, 1 x 5 uL stock crystal violet, one tip for the step |
| `column2_np` | print | 8 targets, 3 x 5 uL with 300 s between layers, mix before aspirating, tip per target |
| `column2_cv` | print | 8 targets, 1 x 5 uL stock crystal violet |
| `column3_cv_control` | print | 8 control targets, 1 x 5 uL stock crystal violet |
| `column4_cv` | print | 8 control targets, the crystal violet series, mix before aspirating, tip per target |

The step *ids* mention columns because the scientist named them that way. The
executor and resolver never read those names.

## Two resolutions worth stating explicitly

1. **The "standardized drying interval" in Column 1 is resolved to 300 s**, the same
   five minutes the specification requires between repeated nanoparticle layers. The
   specification does not give the Column 1 interval a number. Both A and B adopt the
   same reading, and it is recorded in `experiment.metadata.notes.drying_interval` in
   the YAML rather than hidden in code. A blind agent that cannot infer this value
   safely should ask instead of guessing.

2. **Products of a series may reuse a declared liquid id, but only explicitly.** The
   first well of the nanoparticle series holds an aliquot of the undiluted stock, so
   the YAML names it `np_stock` through `product_liquid_ids`. A *derived* name that
   collided with an existing liquid is rejected, because the same identifier would
   then silently mean two physical locations. Printing from a series resolves its
   source from that preparation's own wells, so the reuse can never misdirect an
   aspiration.

## Artifacts written

```text
experiment_01/ground_truth/config_job.json
experiment_01/ground_truth/config_resolved_plan.json
experiment_01/ground_truth/config_physical_trace.json
experiment_01/ground_truth/config_setup_trace.json
experiment_01/ground_truth/config_review.txt
experiment_01/ground_truth/config_hashes.json
experiment_01/comparison/static_vs_config.json
experiment_01/comparison/static_vs_config.md
```

Regenerate with:

```bash
scripts/export_experiment_01_config_ground_truth.py --simulate
```

## Simulation

The trusted executor carrying the resolved plan was built and simulated locally with
forced motion:

```text
artifact sha256 : 400c8732425ce01de33dd50346d805a3b5f940a9d9c2ae9168927ff51f1fc8a1
simulation      : PASS
paper deposits  : 64, every one at z = 6.5 mm
                  (modelled paper bottom 6.0 mm + the validated 0.5 mm standoff)
flow rates      : 3.0 uL/sec
final comment   : standard printing complete: 64 droplets, 320.0 uL printed.
```

## Verification

```text
tests/test_experiment_01_static_ground_truth.py
tests/test_experiment_01_config_ground_truth.py   23 passed combined
```

covering source-reference integrity, setup and physical execution equivalence,
numeric normalization, topology diagnostics, matching totals, matching dilution
arithmetic, matching per-liquid consumption, the four-column scientific layout, the
four 300 s rests, nanoparticle-free controls, the validated paper geometry, the
human-readable review, the build's isolation to a single delimited block, forced-motion
simulation, and refusal to simulate a tampered artifact.
