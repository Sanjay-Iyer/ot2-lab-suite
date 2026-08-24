# Version 11 — Independent Re-Audit (Defect Closure Verification)

Auditor: second, independent read-only review. **No git commands run. No robot
contacted.** Interpreter: `~/miniconda3/envs/ai/python.exe`. Repo root:
`C:\code\opentrons_home\ot2-lab-suite`. Branch at audit time: `paper`.

Scope: re-derive the 20-question verdict from scratch and verify closure of the
eight defects raised by the first audit. Nothing from the prior report was taken
on trust; every claim below is backed by a command I ran in this session.

**Headline: 20 of 20 PASS. D1–D6 CLOSED. D7 and D8 remain OPEN by design
(accepted, no functional impact). One new INFO-level observation (N1).**

Real gate numbers, this session:

| Gate | Result |
|---|---|
| `scripts/11_test_agent_configs.py --rules` — agent config cases | **80/80 passed** |
| `scripts/11_test_agent_configs.py --rules` — offline English parser | **55/65 conversations resolved** |
| `scripts/11_run_simulation_matrix.py` | **71/71 passed** |
| `scripts/11_run_simulation_matrix.py --random 40 --seed 11` | **111/111 passed** |
| `invalid:` section of `configs/tests/11_agent_test_cases.yaml` | 15/15, two spot-checked as genuine refusals |

Files I modified: **this report only.** The two test scripts no longer mutate
the repository (that was D1; see the hash evidence below).

---

## The 20 questions

| # | Question | Verdict |
|---|---|---|
| 1 | Were any original Version 1 working files modified? | **PASS** |
| 2 | Are all V11 experiment parameters config-driven? | **PASS** |
| 3 | Dilution: vial/plate sources AND configurable plate destinations? | **PASS** |
| 4 | Standard print: vial/plate sources and arbitrary paper targets? | **PASS** |
| 5 | Clover: vial/plate sources and configurable geometry? | **PASS** |
| 6 | Droplet volume / count / timing configurable? | **PASS** |
| 7 | Paper print height configurable? | **PASS** |
| 8 | Air gap configurable? | **PASS** |
| 9 | Tip reuse configurable, and does it change tip consumption? | **PASS** |
| 10 | Dilution amounts and mixing configurable? | **PASS** |
| 11 | Clover spacing in meaningful physical units? | **PASS** (numerically re-proven) |
| 12 | Does the agent maintain state across conversation turns? | **PASS** |
| 13 | Templates/defaults rather than hidden defaults in Python or prompts? | **PASS** (was PARTIAL FAIL — D4/D5/D6 fixed) |
| 14 | Does the agent modify YAML rather than Python? | **PASS** |
| 15 | Can the agent logic be tested WITHOUT GCloud? | **PASS** |
| 16 | Does the production wrapper use the existing GCloud/Vertex infra? | **PASS** |
| 17 | Do the generated configs build successfully? | **PASS** (was masked by D2 — now byte-exact) |
| 18 | Do simulated protocols exercise intended robot commands? | **PASS — 111/111** |
| 19 | Are invalid configurations rejected clearly? | **PASS — 15/15** |
| 20 | Is the Version 11 family isolated from Version 1? | **PASS** (D7 caveat, non-functional) |

**Tally: 20 PASS / 0 FAIL / 0 PARTIAL.**

---

## DEFECT STATUS D1–D8

### D1 — test suite overwrote the committed clover artifact — **CLOSED**

Hashes before and after two full matrix runs are byte-identical:

```
$ md5sum src/protocols/generated/11_*_latest.py            # BEFORE
dc2da03a9c21ab6766bb5068322d1c93  11_clover_print_latest.py
a33ebe24655980950ea517a22554ad62  11_general_dilution_latest.py
61a7bfed3409d50bbbb9061eb8428720  11_standard_print_latest.py

$ python scripts/11_run_simulation_matrix.py
SIMULATIONS: 71/71 passed
ALL SIMULATIONS PASSED

$ md5sum src/protocols/generated/11_*_latest.py            # AFTER
dc2da03a9c21ab6766bb5068322d1c93  11_clover_print_latest.py
a33ebe24655980950ea517a22554ad62  11_general_dilution_latest.py
61a7bfed3409d50bbbb9061eb8428720  11_standard_print_latest.py

$ python scripts/11_run_simulation_matrix.py --random 40 --seed 11
SIMULATIONS: 111/111 passed
ALL SIMULATIONS PASSED

$ md5sum src/protocols/generated/11_*_latest.py            # AFTER RANDOM
dc2da03a9c21ab6766bb5068322d1c93  11_clover_print_latest.py
a33ebe24655980950ea517a22554ad62  11_general_dilution_latest.py
61a7bfed3409d50bbbb9061eb8428720  11_standard_print_latest.py
```

Root cause is fixed at source, not merely masked:

- `src/printing/v11/clover_builder.py:127` — `write_latest: bool = False`
- `src/printing/v11/clover_builder.py:215` — `False` on the `build_and_simulate` wrapper too
- `src/printing/v11/standard_builder.py:108` and `:171` — `False`
- `src/printing/v11/dilution_builder.py` — no `write_latest` parameter at all;
  publishing is an explicit separate call, `write_generated_protocol()`
- `scripts/11_run_simulation_matrix.py:184-185` — belt-and-braces:
  `if "write_latest" in inspect.signature(build).parameters: build_kwargs["write_latest"] = False`
- The only `write_latest=True` callers left are the two deliberate publish paths
  in `scripts/run_printing_experiment_robot.py:202` and `:209`.

### D2 — committed artifacts stale versus their configs — **CLOSED**

For each of the three, I loaded `configs/generated/11_current_*.yaml` through its
V11 loader, rebuilt with the real builder into a throwaway directory, and
byte-compared against the committed artifact:

```
D2  build_* artifact vs committed (byte)
  standard  equal=True  sha_new=e9244d435eaea301 sha_committed=e9244d435eaea301
  clover    equal=True  sha_new=7548cf12e19c7b6a sha_committed=7548cf12e19c7b6a
  dilution  equal=True  sha_new=ab34a7b7b017057d sha_committed=ab34a7b7b017057d
D2 verdict: CLOSED
```

(sha256, first 16 hex chars, over the whole file including the provenance
header that `build_clover_protocol` prepends. Comparing the *rendered body*
alone is not sufficient for clover — `render_protocol_source` omits the header
that `build_*` writes, so a body-only comparison would give a false diff. The
numbers above are from the full `build_*` path, which is what ships.)

### D3 — agent left a stale top-level `targets:` contradicting what ran — **CLOSED**

**(a) Clean-state refinement sequence.** From a fresh `ExperimentState`, applying
three intents in order — `{workflow standard_print, source_type well_plate,
source_slot 1, source_well C11}`, then `{droplets_per_target 2}`, then
`{target_selection {column 3}}`:

```
config keys: ['groups', 'machine_profile', 'paper', 'pipetting', 'printing',
              'protocol_label', 'replicates', 'run_modes', 'schema_version',
              'source', 'timing', 'tips', 'workflow']
top-level targets: False   top-level target_selection: False
groups: [{"source_well": "C11", "droplets": 2, "target_selection": {"column": 3}}]
validate: True  ok
GROUP targets= ['A3','B3','C3','D3','E3','F3','G3','H3'] droplets= 2 source= C11
order= layer_major
plan: {"total_deposits": 16, "total_volume_ul": 80.0, "max_layers": 2,
       "tips_required": 1, "source_totals": {"C11": 80.0}}
```

No top-level `targets` and no top-level `target_selection` survive. The refinement
folded into the single group. Resolved targets are exactly A3..H3, droplets 2,
16 deposits — which is what actually runs. The contradiction is gone.

**(b) Template.** `configs/templates/11_standard_print_template.yaml` ships
`groups` live and `targets` absent — it does not ship both:

```
print.targets live: False   print.groups live: True
top-level keys: ['groups','machine_profile','paper','pipetting','printing',
                 'protocol_label','replicates','run_modes','schema_version',
                 'source','timing','tips','workflow']
```

**(c) Loader rejection.** I hand-built a config declaring a top-level
`targets: ["A1","B1"]` while both groups carry their own targets, so the
top-level block can never take effect. `resolve_standard_print` refuses it:

```
RAISED StandardPrintLoadError: targets/target_selection is declared at the top
level but every group overrides it, so it has no effect. Remove the top-level
block, or remove the per-group targets that shadow it.
```

That is a real raise on a real constructed config, not a test-fixture assertion.

### D4 — hard-coded 5000.0 / 300.0 / 100.0 / 20.0 in `agent_core` — **CLOSED**

A literal scan of `src/printing/v11/agent_core.py` for `5000.0`, `300.0`,
`100.0` and `20.0` returns **zero** hits. Every match on that grep is now a
`LABWARE[...]` lookup. The source-block resolver reads the registry
(`agent_core.py:161-177`):

```python
block["slot"] = int(intent.get(f"{prefix}_slot", LABWARE[key]["usual_slot"]))
if intent.get(f"{prefix}_aspirate_height_mm") is None:
    block["aspirate_height_mm"] = LABWARE[key]["default_aspirate_height_mm"]
if intent.get(f"{prefix}_loaded_volume_ul") is None:
    block["loaded_volume_ul"] = LABWARE[key]["default_loaded_volume_ul"]
if intent.get(f"{prefix}_minimum_remaining_ul") is None:
    block["minimum_remaining_ul"] = LABWARE[key]["default_minimum_remaining_ul"]
```

The four values now live once, in `src/printing/v11/labware.py:LABWARE`
(`vial_rack` 5000.0/100.0, `corning_plate` and `well_plate` 300.0/20.0).

**Q13 re-answered: PASS.** D4 was the sole cause of the previous PARTIAL FAIL,
and D5/D6 (the two supporting complaints under the same question) are also
closed. Defaults now come from templates and the labware registry, not from
literals buried in Python or in the prompt string.

### D5 — dilution executor fallbacks diverged from the loader — **CLOSED**

`src/protocols/printing/11_general_dilution.py:283-286`:

```python
# loader; they must match the loader/template defaults (18.0 / 1.5), not be
# ...
max_chunk = float(xfer.get("max_chunk_ul", 18.0))
air_gap = float(xfer.get("air_gap_ul", 1.5) or 0.0)
```

18.0 / 1.5, matching the loader and the template. The old 20.0 / 0.0 pair is
gone. The remaining `20.0` literals in that file are the P20 *capacity*
(`safety.p20_max_volume_ul`), which is hardware, not an experiment parameter.

### D6 — hard-coded slots in the LLM prompt + unused `LABWARE` import — **CLOSED**

The import is used. `src/printing/v11/llm_adapter.py:16-23`:

```python
from .labware import LABWARE

#: Rendered from the registry so prompt text can never drift from the hardware.
_LABWARE_LINES = "\n".join(
    f'              {"| " if index else "  "}"{key}" ({spec["description"]}, '
    f'usual slot {spec["usual_slot"]})'
    for index, (key, spec) in enumerate(LABWARE.items())
)
```

An AST pass over the whole module reports no unused imports (the only "unused"
name is `annotations` from `from __future__`, which is correct):

```
UNUSED IMPORTS: []      # after excluding __future__.annotations
all imports: [annotations, json, re, Any, Callable, Protocol, LABWARE, Config]
```

The rendered prompt now carries registry values, not literals:

```
    "<p>_type": one of the registered labware keys below
                "vial_rack" (Custom 3D-printed 20 mL vial rack, usual slot 7)
              | "corning_plate" (Existing custom Corning 96-well plate (360 uL), usual slot 4)
              | "well_plate" (BRAND Ref. 781662 96-well flat-bottom plate (350 uL), usual slot 1)
```

**`INTENT_SCHEMA` literal JSON survived the f-string conversion intact.** The
doubled braces in the source render as single braces at runtime. Source
(`llm_adapter.py:50-56`) versus rendered output:

```
source:    "targets": ["A1","B1"]  OR  "target_selection": {{"column": 3}} /
rendered:  "targets": ["A1","B1"]  OR  "target_selection": {"column": 3} /

source:    "groups": [{{"targets": [...] or "target_selection": {{...}},
rendered:  "groups": [{"targets": [...] or "target_selection": {...},

source:    "clovers": [{{"reference":"B3","source_well":"A1","layers":2}}]
rendered:  "clovers": [{"reference":"B3","source_well":"A1","layers":2}]
```

All three render as valid JSON fragments. No brace mangling.

*Residual nit (not a defect):* one literal survives —
`"paper_slot": 5 or 11` — which is not rendered from
`labware.PAPER["usual_slots"]`. I checked: `PAPER["usual_slots"] == (5, 11)`,
so the prompt is currently accurate. It is a prompt hint, not a default value,
and it does not affect any resolved config.

### D7 — inconsistent reach into the shared `src/printing/config.py` — **OPEN (accepted)**

Still present and unchanged, as intended. `standard_builder.py:25`,
`dilution_builder.py:22` import `from ..config import REPO_ROOT`;
`dilution_loader.py:26` imports `from ..config import resolve_repo_path`; while
`clover_builder.py:28` and `clover_loader.py:63` roll their own
`REPO_ROOT = Path(__file__).resolve().parents[3]`.

`src/printing/config.py` is a path-helper module, not a V1 protocol module, so
this does not violate the isolation guarantee that matters (see Q20). Cosmetic
inconsistency only. No functional impact.

### D8 — paper grid geometry duplicated into Python — **OPEN (accepted)**

`src/printing/v11/labware.py:PAPER` still hard-codes `grid_origin_x_mm 14.38`,
`grid_origin_y_mm 74.24`, `pitch_mm 9.0`, `x_dimension_mm 127.76`,
`y_dimension_mm 85.48`. As the first audit established, these agree with
`labware/paper_print_96_flat.json` today, and the executor derives its real
bounds from the loaded labware rather than from this copy. Latent
desynchronisation risk, no bug today.

---

## Re-confirmations

### V11 isolation from Version 1 — confirmed

A recursive grep over `src/printing/v11/` and the three `11_*` executors for
`print_from_vial`, `printing.clover`, `printing.dilution`, `dye_demo`,
`source_config` returns **only prose**, never an import:

```
src/printing/v11/__init__.py:4:      src/printing/{print_from_vial,clover,dilution,dye_demo}, so the frozen Version 1
src/protocols/printing/11_standard_print.py:16:  (src/protocols/printing/01_print_from_vial.py, itself taken from
```

Both are comment/docstring lines describing the isolation, not code. No V11
module imports any of the five frozen V1 printing modules.

### `scripts/run_printing_experiment_robot.py` is purely additive — confirmed

All 13 pre-existing workflow names are present and unaltered in `WORKFLOWS`:
`print-from-vial`, `print-from-corning-plate`, `print-from-brand-plate`,
`clover`, `clover-from-corning-plate`, `clover-from-brand-plate`, `dye-demo`,
`single-spot`, `dilution`, `standard-print`, `clover-print`, `standard`,
`four-clover`. Three V11 names (`v11-dilution`, `v11-standard-print`,
`v11-clover-print`) are added under a delimited comment block, with distinct
family strings that shadow nothing.

All five V1 build functions remain: `_build_standard:125`,
`_build_four_clover:132`, `_build_print_from_vial:139`, `_build_dye_demo:154`,
`_build_dilution:173`. The three V11 builders are appended at `:198`, `:206`,
`:213`.

### No path emits liquid + air gap > 20 uL — confirmed numerically

**Printing paths.** The guard is `piston = pre_air_chase + volume + air_gap`
against `safety.p20_max_volume_ul`, enforced in both the loader
(`standard_loader.py:314`) and again in the executor
(`11_standard_print.py:199` and `:495`). Boundary probe through the agent:

```
droplet 18.5 + gap 1.5 = 20.0 : ok=True
droplet 18.6 + gap 1.5 = 20.1 : ok=False
    "droplet 18.6 uL + air gap 1.5 uL needs 20.1 uL, exceeding the 20 uL pipette capacity"
```

Exactly 20.0 is admitted; 20.1 is refused.

**Dilution path.** `_chunk_volume` clamps `max_chunk` to `capacity - air_gap`
before splitting, and the dispense loop further clamps with
`gap = min(air_gap, max(0.0, p20_max - chunk))`. Sweep of the real function:

```
total=50   max_chunk=20.0 gap=1.5 -> chunks=[16.67, 16.67, 16.67]  worst piston=18.17
total=50   max_chunk=18.0 gap=1.5 -> chunks=[16.67, 16.67, 16.67]  worst piston=18.17
total=19.9 max_chunk=20.0 gap=0.1 -> chunks=[19.9]                 worst piston=20.00
total=100  max_chunk=20.0 gap=0.0 -> chunks=[20.0 x5]              worst piston=20.00
```

Worst case across the sweep is exactly 20.00 uL. Never above.

### `layer_major` really is layer-then-wait — confirmed

`src/protocols/printing/11_standard_print.py:525-537`:

```python
# layer_major (default): the proven Version 1 pass structure.
for layer in range(1, resolved["max_layers"] + 1):
    active_groups = [g for g in resolved["groups"] if g["droplets"] >= layer]
    if not active_groups:
        continue
    protocol.comment(f"--- LAYER {layer} of {resolved['max_layers']} ---")
    for group in active_groups:
        for target in group["targets"]:
            deposit(group, target, layer)

    remaining = [g for g in resolved["groups"] if g["droplets"] >= layer + 1]
    if remaining and layer_delay > 0:
        protocol.comment(f"Drying {layer_delay:g} s before layer {layer + 1}.")
        protocol.delay(seconds=layer_delay)
```

Layer is the outer loop; every target in every active group receives layer *n*
before the run delays and moves to layer *n+1*. `target_major` (`:512-523`)
inverts the nesting, as documented. The delay is correctly suppressed after the
final layer (`remaining` is empty).

### Clover: `separation_x_mm` N → `half_width_mm` N/2 → measured distance N — confirmed

I set `separation_x_mm = separation_y_mm = 3.0` on the real generated config
(removing the half values so the separation path is exercised) and measured the
resolved droplet coordinates:

```
clover_01 centre (32.38, 65.24)
  d1 (30.88, 66.74)    d2 (33.88, 66.74)
  d3 (30.88, 63.74)    d4 (33.88, 63.74)
  separation_x_mm 3.0   separation_y_mm 3.0
```

Measured: `33.88 - 30.88 = 3.00 mm` in x, `66.74 - 63.74 = 3.00 mm` in y.
Half-offset from centre is `32.38 - 30.88 = 1.50 mm`, i.e. N/2. The user-facing
number is the actual droplet-to-droplet distance, as intended.

### `invalid:` cases genuinely reject — spot-checked

15 cases in the `invalid:` section. Two run by hand through
`ExperimentState.validate()`:

```
inv_05_droplet_over_capacity
  validate ok = False
  "droplet 25 uL + air gap 1.5 uL needs 26.5 uL, exceeding the 20 uL pipette capacity"
  VERDICT: REJECTED

inv_08_clover_out_of_bounds
  validate ok = False
  "clover droplets fall outside the usable paper area:
   - clover_01.d1 at paper x -5.62 y 94.24 (droplet radius 1.5 mm) falls outside
     the usable paper box x [9.88, 117.88] y [6.74, 78.74] mm ..."
  VERDICT: REJECTED
```

Both are true refusals with actionable messages — not a test that passes because
the case was quietly accepted.

---

## New observations

### N1 — INFO: the offline-parser miss rate is reported but not gated

`scripts/11_test_agent_configs.py --rules` prints
`OFFLINE ENGLISH PARSER: 55/65 conversations resolved` and then exits **0**
(verified: `rc=0`). Only the 80 agent config cases gate the exit code
(`11_test_agent_configs.py:303-305`). Ten conversations do not resolve offline:

```
clv_02_brand_c11_at_b3            no workflow chosen yet
clv_06_separation_3mm             no workflow inferred from the conversation
clv_07_asymmetric                 no workflow inferred from the conversation
clv_09_different_references       no workflow inferred from the conversation
clv_12_inter_layer_delay          no workflow inferred from the conversation
clv_19_multiple_positions_with_offsets   no workflow inferred
dil_10_serial_across_row          stock_source well(s) A1 are also destination wells on slot 1
dil_14_tip_reuse_false            no workflow chosen yet
dil_16_air_gap_chunking           no workflow chosen yet
dil_21_delays                     no workflow inferred from the conversation
```

This is **not a regression and not a correctness defect**: the offline regex
parser is an explicit no-GCloud fallback (Q15), and nine of the ten misses are
the parser declining to guess a workflow rather than guessing wrong — the safe
failure mode. The tenth (`dil_10_serial_across_row`) is a genuine
source-equals-destination refusal, which is arguably correct behaviour surfaced
in the wrong bucket. Worth a note in the docs so nobody reads
`ALL AGENT CONFIG CASES PASSED` as "65/65 English conversations work offline".

No other new defects found.

---

## Commands run in this session

```
md5sum src/protocols/generated/11_*_latest.py                        (x3, before/after each matrix run)
python scripts/11_run_simulation_matrix.py                           -> 71/71
python scripts/11_run_simulation_matrix.py --random 40 --seed 11     -> 111/111
python scripts/11_test_agent_configs.py --rules                      -> 80/80 cases, 55/65 offline, rc=0
```

plus read-only Python probes against the V11 loaders, builders, `ExperimentState`
and `_chunk_volume`, all through `~/miniconda3/envs/ai/python.exe`.

No file in the repository other than this report was created, modified or
deleted. No git command was executed. No robot was contacted.
