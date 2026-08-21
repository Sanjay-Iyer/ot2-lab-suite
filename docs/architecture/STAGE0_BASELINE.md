# Stage 0 — Baseline Repository Map

Recorded before any refactoring. Every claim here was verified against the code on
2026-08-18 (branch `paper`). This file is the "architecture before" reference.

## Environment

| Item | Value |
|---|---|
| Python | `C:\Users\iyer95\miniconda3\envs\ai\python.exe` (3.11.15, conda env `ai`) |
| opentrons | 9.0.0 (max Protocol API on this robot: **2.15**, per `configs/robot.yaml`) |
| pydantic | 2.13.4 |
| langchain / langgraph | 1.2.18 / present |
| Known shim | `numpy.trapz` removed in numpy>=2; restored by `conftest.py` and each build/plot script |

## Baseline test run

`python -m pytest -q` -> **303 passed, 16 failed, 5 skipped** (92 s).

All 16 failures are PRE-EXISTING. See "Pre-existing failures" below.

## Live agents (2)

| Agent | File | Tools wired | Skills loaded |
|---|---|---|---|
| Generic OT-2 agent | `src/agents/main.py` | 18 (12 workflow + 6 labware) | none |
| Vial-print agent | `src/agents/vial_print_agent.py` | 10 config + 3 robot HTTP | none |

Both are `langgraph.prebuilt.create_react_agent` with a single hard-coded
`system_prompt` string.

## Tools (32 LangChain `@tool` functions)

| Module | Count | Domain |
|---|---|---|
| `src/agents/tools.py` | 13 | generic workflow registry, generate/simulate, SSH deploy+execute |
| `src/agents/vial_print_tools.py` | 10 | workflow-01 vial->dilution->print only |
| `src/agents/labware_tools.py` | 6 | custom labware JSON generation |
| `src/agents/robot_http_tools.py` | 3 | OT-2 HTTP API run of workflow 01 |

**Finding:** zero tools reach the modern printing families. A grep for
`four_clover|complementary|PROTOCOL_VERSIONS` across `src/agents/` returns nothing but
comments. The current science (v10 standard grid, v12/v13 four-clover) is invisible to
every agent.

## Skills (6 SKILL.md files) — NOT loaded at runtime

`skills/ot2-labware`, `ot2-protocols`, `ot2-robot-control`, `ot2-robot-profile`,
`printing_workflow`, `vial-dilution-print`.

Each has valid YAML frontmatter (`name`, `description`). Verified by grep across all
`*.py`: **no loader, no SKILL.md read, no SKILLS_DIR constant exists.** They are
referenced only inside comments and docstrings. Procedural knowledge is therefore
duplicated across system prompts, YAML comments, Python docstrings and markdown.

**Staleness found:** `skills/ot2-robot-profile/SKILL.md` pins the pipette as
`p300_multi_gen2` on the RIGHT mount with "left mount (empty)". Every current printing
protocol (v9-v13) uses `p20_single_gen2` on the **left**. `skills/printing_workflow/SKILL.md`
claims workflow 01 is "the single active production workflow"; four-clover v13 is the
one actually being iterated.

## Printing workflows — the real registry is an integer table

`scripts/build_vial_dilution_print.py::PROTOCOL_VERSIONS` maps `int -> (base protocol,
generated stem)`. This, not `src/core/workflows/registry.py`, is what actually runs.

| id | base protocol | generated stem | family |
|---|---|---|---|
| 1, 2 | `01_/02_vial_dilution_paper_print*` | `vial_dilution_print[_v2]` | vial->dilution->print |
| 3, 4, 6, 7, 8 | `03_/04_/06_/07_/08_*` | `vial_dilution_print_v3..v8` | historical |
| 9 | `09_plate_well_direct_paper_print_v9.py` | `plate_well_direct_print_v9` | **grid** |
| 10, 11, 13, 14 | `10_complementary_direct_paper_print.py` | `complementary_*_v10a/b/c/bv2` | **grid** |
| 12 | `11_combined_bp_dmmp_paper_print.py` | `combined_bp_dmmp_print_v11` | **grid** |
| 15, 16, 17, 18 | `12_four_clover_paper_print.py` | `four_clover_*_v12/v13` | **design** |

Note the vocabulary collision: builder id 12 is protocol file `11_`, and the
four-clover "v12"/"v13" labels are builder ids 15-18. The number means nothing on its
own — a researcher cannot tell family membership from it.

`src/core/workflows/registry.py` is a *separate, older* registry holding
`dilution`, `printing`, `printing_demo`, plus `austar` and `cleanup` whose generators
raise `NotImplementedError`. `list_available_workflows` (the only discovery tool the
agent has) advertises those two stubs.

## Validation ownership today

| Rule | Where it lives | Runs when |
|---|---|---|
| dilution schema, pipette selection, materials, print groups | `src/core/{workflow_config,pipette_selection,materials,print_groups}.py` | build, **v1/v2 only** |
| structural deck/volume checks | `build_vial_dilution_print.py::validate()` | build, legacy flat configs only |
| paper boundary, intra/inter-clover distance, piston load, source volume | `12_four_clover_paper_print.py::_preflight()` (private) | **inside the Opentrons simulation** |
| grid layer plan, source volume | `10_complementary_*.py::_preflight()` (private) | inside the simulation |

**Finding:** every version in `EMBED_RAW_VERSIONS = {4,6,7,8,9,10,11,12,13,14,15,16,17,18}`
embeds its YAML raw and is validated *only* by the protocol's own `_preflight`, i.e.
only once a full `opentrons.simulate` run is executed. There is no build-time structured
schema for either modern printing family, so nothing can validate AI-chosen parameters
cheaply or deterministically before simulation.

The good news: the four-clover geometry engine is essentially pure. `_geometry_from_spec`,
`_capacity_errors`, `_boundary_violations`, `_distance_report` and `_piston_load` take
plain arguments; `_resolve_clovers(well_xy)` and `_paper_bounds(well_xy, well_names)` take
a `well_xy` callable and read module-global `CONFIG`. `scripts/plot_four_clover_layout.py`
and `tests/test_four_clover_*.py` already exercise them headlessly by importing the
protocol module by file path and assigning `module.CONFIG`. **That is the reuse hook.**

## Configs

`configs/printing/` mixes both families in one flat directory with version suffixes as
the only distinction (`bp_20260723_v2..v8`, `complementary_*_v10a/b/c`,
`four_clover_*_v12/v13`). Layout geometry is split into companion `*_locations.yaml`
files pulled in by the builder's `destination_config:` key. There is no "current
default" marker anywhere.

`configs/workflows/user/` holds ~120 timestamped run configs (gitignored).

## Custom labware

| File | loadName | namespace | role |
|---|---|---|---|
| `labware/paper_print_96_flat.json` | `paper_print_96_flat` | `custom_beta` | paper as a 96-cell grid; 127.76 x 85.48 mm, well depth 0.1 mm, A1 center (14.38, 74.24), 9.00 mm pitch |
| `labware/tuberack_3dprint_20ml_8vials_v2.json` | `tuberack_3dprint_20ml_8vials_v2` | `custom_beta` | 20 mL source vials |
| `labware/corning_96_wellplate_360ul_custom.json` | — | — | dilution plate |
| `labware/usascientific12well_12_wellplate_6000ul.json` | — | — | reservoir |

Filenames must equal the internal `loadName`.

## OT-2 connection (canonical sources)

- **Identity/address:** `configs/robot.yaml` (committed) — name `OT2CEP20220929R02`,
  mDNS `OT2CEP20220929R02.local`, HTTP port `31950`, SSH port 22, last discovered IP
  `169.254.252.252`, robot software 7.0.2, max API 2.15.
- **Resolution logic:** `src/lab/robot_connection.py::resolve_host` — CLI override ->
  `OT2_ROBOT_HOST` env -> mDNS host -> last-known IP -> active discovery.
- **Secrets:** `.env` (gitignored) holds `ROBOT_SSH_KEY_PATH`, `GOOGLE_API_KEY`.
  `.env.template` (committed) documents the keys without values. The private key itself
  lives outside the repo at `~/.ssh/id_rsa_opentrons` and is never read into the repo.
- **Config drift found:** `.env` carries `ROBOT_IP=169.254.46.57` while
  `configs/robot.yaml` records `discovered.ip: 169.254.252.252`. Two different addresses
  in two places; `AGENTS.md` says `configs/robot.yaml` is authoritative.
- **Transports:** HTTP API (`scripts/run_vial_print_robot.py`, `robot_http_tools.py`) is
  the standard for runs; SSH/SCP (`src/utils/ot2_ssh.py`, `src/agents/tools.py`) is used
  by the older deploy/execute path.

## Pre-existing failures (16), grouped by root cause

| Group | Count | Root cause |
|---|---|---|
| `tests/test_four_clover_air_chase_v12.py` | 5 | Tests assert on exact dry-run report wording that the protocol no longer emits. Stale test, working code. |
| `tests/test_robot_automation.py` | 8 | Tests do not set `LLM_PROVIDER=vertexai`, so the live-auth safety gate refuses before the tested behaviour runs. Gate added after the tests. |
| `tests/test_robot_http_tools.py` | 2 | Same auth-gate refusal; the mocked command is never built. |
| `tests/test_printing_demo_config.py` | 1 | Overlap validation for the legacy `printing_demo` family. |

## Current execution chain

```
SYSTEM_PROMPT (hard-coded) -> ReAct agent -> @tool -> subprocess CLI -> HTTP/SSH -> OT-2
```

Skills sit on disk, unreferenced. Schemas cover only the legacy dilution workflow.

---

# Stage 0 — Architecture Audit Agent review

An independent audit pass re-derived every claim above. Verdict: **YES WITH CORRECTIONS**.
The load-bearing claims held. The corrections below were then re-verified by hand against
the source and are folded into the design from Stage 1 onward.

## Corrections to the map

**C1 — "no tool reaches four-clover" is false.** `update_vial_print_params(advanced_updates=...)`
routes into `src/core/config_loader.py::merge_user_updates`, an unrestricted recursive
deep merge with no key allowlist. `_write_user_yaml` dumps the whole dict and the builder
reads `int(full.pop("protocol_version", 1))`. An LLM emitting
`advanced_updates={"protocol_version": 15}` therefore builds the four-clover protocol.
Correct statement: *no curated tool path reaches it; the config-merge tool is an unbounded
escape hatch.*

**C2 — two build-time checks do run for `EMBED_RAW_VERSIONS`:** deck-slot uniqueness and
`destination_config` repo-containment. Further, `_preflight` is only partly hard — boundary
violations always fail, but intra/inter-clover distance findings go through `report()` and
every shipped four-clover config sets `validation.mode: warn`, so the spacing minimums are
**advisory only**. The headline (no cheap structured schema) still stands.

**C3 — the vial-print agent exposes 14 tools, not 13**, and `simulation_only` defaults to
`False`, so the default agent is robot-capable. The fourth robot tool is
`get_robot_hardware_status` (imported from `tools.py`, not `robot_http_tools.py`). Of the
32 tools, 31 are wired to an agent; only `deploy_and_run_on_robot` is unwired.

**C4 — the `.env` `ROBOT_IP` drift is inert for the robot path.** `src/core/config.py`
reads `os.getenv("OT2_ROBOT_HOST", "")` into a variable *named* `ROBOT_IP`. But the stale
value is live in two other places: `configs/vision.yaml` declares `host_env_var: ROBOT_IP`,
and `.env` pins `NO_PROXY` to the stale address.

**C5 — one four-clover failure is a red safety assertion, not stale wording.**
`test_air_chase_config_keeps_dry_run_and_disables_blow_out` asserts `dry_run is True`
against the committed `configs/printing/four_clover_air_chase_v12.yaml`, which now reads
`run_modes: {dry_run: false, ...}` with `blow_out: true` while its own header still
describes it as a dry run.

**C6 — `configs/robot.yaml` is machine-written, not only human-maintained.**
`resolve_host` calls `write_discovery()` on the discovery branch, so any agent tool that
resolves the host can rewrite a tracked config.

## Things the map missed

- `ACTIVE_WORKFLOWS.md` is the repo's real human-facing workflow catalogue. It has no
  section for the 09/10/11 grid family and its four-clover table omits the two newest
  configs.
- **`src/agents/main.py` is broken.** `MockToolCallingLLM` was stripped (line 58 is a
  placeholder comment) but is still called. Verified: `create_opentrons_agent(use_mock=True)`
  raises `NameError`. `src/agents/test_simulation_agent.py` imports it.
- `_center_specs()` and `_print_order()` also read module-global `CONFIG` and are hard
  dependencies of the reuse path — the purity inventory was one function short.
- Existing Schema assets undercredited: 17 pydantic models in `config_models.py`, four
  YAML files in `configs/constraints/`, and `src/utils/preflight.py` (an AST/encoding
  deploy-safety engine that is tested but wired to nothing).
- `raman/`, `vision/`, `vision_tests/` are unmentioned; `vision_tests/scripts/verify_print_droplets.py`
  is already invoked by an agent tool, so CV is inside the agent surface.

## Safety hazards found (ranked) — all verified by hand

| # | Hazard | Evidence |
|---|---|---|
| **H1** | The `RUN ROBOT` confirmation is an **LLM-supplied string**, not a human gate. `run_vial_print_robot_http` only checks `confirmation != "RUN ROBOT"` — the model can emit it itself. `execute_protocol_on_robot` has no confirmation parameter at all; steps E/F of the safety sequence exist only inside a prompt string. | `robot_http_tools.py:101`, `tools.py:418`, `main.py:108-115` |
| **H2** | **The agent tool defaults to a LIVE liquid run:** `run_vial_print_robot_http(..., live: bool = True, ...)`. The CLI is safe-by-default (`--live` is opt-in); the agent tool inverts that. | `robot_http_tools.py:93` vs `run_vial_print_robot.py:623,663` |
| **H3** | **The simulation-PASS record can be stamped onto the wrong artifact.** `build_vial_print_protocol` unconditionally hashes the hardcoded `vial_dilution_print_latest.py` after any successful build. Combined with C1, building four-clover prints `SIMULATION OK` and records a PASS against a stale, unsimulated `vial_dilution_print_latest.py`, which `run_vial_print_robot_http` then accepts and uploads. | `vial_print_tools.py:651-653`, `GENERATED_LATEST` at `:60` |
| **H4** | A committed config carries live defaults with a red guard test (C5). | `configs/printing/four_clover_air_chase_v12.yaml:16` |
| **H5** | Local simulation does not check `apiLevel` against robot capability. `01_`/`02_` declare 2.28; the robot reports max 2.15. A 2.28 protocol simulates PASS locally, satisfies the hash gate, then fails at upload. | `tools.py:213`, `configs/robot.yaml:20` |
| **H6** | `main.py` writes the entire LangGraph message state to disk verbatim. | `main.py:177` |

## Over-confident statements corrected

- **The 16 failures are not a stable repo property.** Ten of them (the `test_robot_automation`
  and `test_robot_http_tools` groups) fail only because this machine's `.env` does not set
  `LLM_PROVIDER=vertexai`. On the lab laptop they would pass. The baseline count is
  environment-dependent and is recorded as such.
- **"opentrons 9.0.0, max API 2.15" conflated two things.** 9.0.0 is the *local simulator*.
  `configs/robot.yaml` reports the *robot* as software 7.0.2 / max API 2.15 (discovered
  2026-07-23), while `skills/ot2-robot-profile/SKILL.md` claims the robot runs 9.0.0 with
  2.16-2.28 available, and protocols `01_`/`02_` require 2.28. **These three cannot all be
  true.** Resolving this needs the robot and is a work-laptop item.
- "Skills are referenced only in comments" was re-derived under alternate spellings
  (`SKILL`, `skills/`, `SKILLS_DIR`, `load_skill`, `frontmatter`, `rglob`, `.md` literals)
  before being accepted. A bare negative grep is not evidence.
