# Experiment 01 — Stage 0 Repository and Baseline Audit

Recorded on 2026-08-20 on the HOME simulation laptop before implementing
Experiment 01 behavior. This document supplements, rather than replaces,
`docs/architecture/STAGE0_BASELINE.md`.

## Existing components

| Existing component | Path | Current purpose | Decision | Rationale |
|---|---|---|---|---|
| Standard 96-position print protocol | `src/protocols/printing/09_plate_well_direct_paper_print_v9.py` | Prints one plate source to exact paper wells with row-dependent layers | Reuse mechanics; do not reuse as the generic Experiment 01 executor | Its P20 release behavior is trusted, but its single-source Cartesian layout cannot express multiple liquids, preparation, arbitrary action order, or per-position delays. |
| Standard print configuration | `configs/printing/plate_well_direct_print_v9.yaml` | Machine and science values for the v9 1x/3x/10x demonstration | Preserve as a golden regression input | It remains the reference for existing v9 behavior, including its older 4.0 mm standoff. |
| Four-clover protocol and geometry engine | `src/protocols/printing/12_four_clover_paper_print.py` | Resolves and prints registered continuous XY four-clover designs | Reuse as geometry/standoff evidence only | Experiment 01 uses exact paper wells, not a coordinate design. The latest physical standoff evidence is in the v13 configuration that drives this protocol. |
| Latest physically validated paper-print setting | `configs/printing/four_clover_spacing_v13.yaml` | Four-clover spacing sweep using the physically confirmed print height | Reuse the `0.5 mm` dispense standoff | The file explicitly records `dispense_height_mm: 0.5` as physically confirmed and says the prior value was `4.0`. |
| 96-position paper definition | `labware/paper_print_96_flat.json` | Custom paper substrate addressed as an 8 x 12 grid | Reuse unchanged | It is the current registered definition: load name `paper_print_96_flat`, namespace `custom_beta`, version 1. |
| Paper labware source template | `configs/labware/well_plate_96/paper_print_96_flat_v1.yaml` | Reproducible source for the custom definition | Reuse unchanged | It pins the same coordinate geometry used by current printing tests. |
| Custom 20 mL vial rack | `labware/tuberack_3dprint_20ml_8vials_v2.json` and `configs/labware/tuberack_3dprint_20ml_8vials_v2.yaml` | Eight 20 mL vial sources | Reuse unchanged | It is the existing custom source rack requested for stock liquids. |
| Custom 96-well plate | `labware/corning_96_wellplate_360ul_custom.json` | Dilution/preparation plate | Reuse unchanged | It is already loaded by the standard and dilution workflows and provides A1:H12 wells. |
| P20 configuration | `configs/printing/four_clover_spacing_v13.yaml` and `configs/printing/plate_well_direct_print_v9.yaml` | `p20_single_gen2` on the left mount with a 20 uL tip rack | Reuse | It matches the requested 5 uL droplets and the current printing hardware convention. |
| P20 transfer splitting | `src/protocols/printing/06_vial_dilution_paper_print_v6_p20only.py::_split_volume` | Splits preparation transfers into chunks no larger than the configured P20 limit | Extract the deterministic rule into the new general resolver | The algorithm is useful, but the surrounding protocol is tied to eight rows and module-global `CONFIG`. |
| Direct dilution calculation | `src/protocols/printing/06_vial_dilution_paper_print_v6_p20only.py::_prepare_dilutions` | Computes stock and solvent as `total/factor` and `total-stock` | Reuse the calculation semantics in a pure resolver | The calculation is deterministic, but the implementation is specific to one legacy workflow. |
| Transfer and mixing mechanics | `src/protocols/printing/06_vial_dilution_paper_print_v6_p20only.py::_transfer`, `_prepare_dilutions`, `_print_paper` | P20 transfer, mix, and repeated print operations | Reuse mechanics through new experiment-independent capabilities | Existing functions depend on a fixed global config and cannot directly consume a canonical action plan. |
| Legacy workflow normalization | `src/core/workflow_config.py` and `src/core/models/config_models.py` | Normalizes the original vial-dilution-print YAML and validates its fixed workflow models | Preserve and reuse compatible validators, not its fixed Experiment 01 shape | It contains useful strict volume/factor concepts, but its schema owns the legacy row-series workflow and P300/P20 variants rather than a general ordered action graph. |
| Pipette selection | `src/core/pipette_selection.py` and `configs/constraints/pipette_constraints.yaml` | Selects mounted-pipette identity for a requested single-volume envelope | Reuse the existing capability and constraint source | A second independent pipette table would create safety drift. Simultaneous piston-load validation (pre-air chase + liquid + trailing air) remains owned by the general resolver and `ResolvedPrintPlanV1` semantic validation, matching the existing workflow-specific printing validators. |
| Material and source accounting | `src/core/materials.py` | Resolves material roles and enforces source-well/volume constraints for the legacy workflow | Adapt its accounting rules | Experiment 01 needs multiple named liquids and prepared intermediates, but should preserve existing known-material and volume-budget semantics where compatible. |
| Print-group planning | `src/core/print_groups.py` | Validates legacy print groups, replicates, destinations, and collision rules | Reuse collision/replicate concepts; do not reuse the fixed row-series layout | The new schema must express explicit ordered destinations without silently inheriting legacy Cartesian assumptions. |
| General workflow validator | `src/core/validation/workflow_validator.py` and `configs/constraints/*.yaml` | Validates the older generic workflow configuration models | Reuse compatible deck/labware constraints | It covers built-in labware and deck rules but not the canonical multi-liquid print action semantics required here. |
| Legacy agent tools | `src/agents/vial_print_tools.py` | Mutates, builds, validates, and simulates workflow-01 configs | Preserve for compatibility; do not expose as the Experiment 01 tool boundary | Its mutable global working config and workflow-specific parameters are not the bounded structured interface required for a new general printing workflow. |
| Legacy procedural skills | `skills/vial-dilution-print/` and `skills/printing_workflow/` | Document the original vial dilution and general printing processes | Mine for verified hardware/procedure facts; keep outside the new agent routing unless explicitly registered | Reusing validated procedural facts avoids duplication, but these skills describe legacy shapes and some live workflows that the simulation-only Printing Agent must not inherit. |
| Built-in 96-well definitions | `configs/constraints/labware_constraints.yaml` (`corning_96_wellplate_360ul_flat`, `nest_96_wellplate_200ul_flat`, `nest_96_wellplate_2ml_deep`) | Generic workflow labware constraints for Opentrons built-in plates | Preserve as supported generic references; do not select them silently for Experiment 01 | The current printing/dilution protocols consistently use the repository-defined `corning_96_wellplate_360ul_custom`; that configured definition is therefore the Experiment 01 preparation plate unless the scientist explicitly changes it. |
| Simulation utility | `src/printing/artifacts.py` and `scripts/build_vial_dilution_print.py::simulate` | Builds local artifacts and runs `opentrons.simulate` | Extend/reuse | The simulation boundary is local-only and already records exact artifact hashes. |
| Canonical serialization and hashing | `src/printing/canonical.py` | Stable canonical JSON bytes and SHA-256 | Reuse unchanged | It already implements the required formatting-independent identity. |
| `PrintJobV1` | `src/printing/schemas/jobs.py` | Strict scientist-facing single-material print-only intent | Extend additively or introduce a compatible standard-workflow intent model | It currently permits exactly one material and cannot represent liquid sources, dilution, transfer, mix, control, or arbitrary ordered repeated depositions. |
| `ResolvedPrintPlanV1` | `src/printing/schemas/plans.py` | Strict print-deposit plan for v9/four-clover | Extend without changing existing golden identities | It contains deposits but no preparation transfers, mixes, general delays, or multiple liquid identities. |
| Job compiler | `src/printing/job_compiler.py` | Adapts print-only jobs to registered v9/four-clover workflows | Preserve for existing workflows; add a separate general standard-print compiler | Its registry-bound profiles protect existing behavior, but neither profile can express Experiment 01. |
| Experiment YAML layer | `src/printing/experiment_configs.py` and `configs/templates/printing/standard_paper_printing.yaml` | Persistent print-only YAML, strict loading, versioning, summary | Extend | The approval/evidence mechanisms are reusable; the schema is presently limited to one material and Cartesian well conditions. |
| Approval-gated workflow | `src/printing/experiment_workflow.py` | YAML draft -> validation -> presentation -> approval -> resolution -> build -> local simulation | Reuse and generalize | It already fails closed and stops at `READY_FOR_EXECUTION`; Experiment 01 needs a richer configuration and resolver behind the same gate. |
| High-level printing tools | `src/agents/printing_tools.py` | Draft, inspect, revise, approve, resolve, build, and simulate bounded experiments | Extend | The tool granularity is appropriate; their input schemas must support preparation and multi-liquid printing rather than exposing robot primitives. |
| Printing Agent | `src/agents/printing_agent.py` | Routes scientific intent to runtime skills and high-level tools | Extend | Its responsibility boundary is correct: interpret intent, never write OT-2 Python. |
| Runtime printing skill loader | `src/printing/skills.py` | Deterministically discovers scoped printing `SKILL.md` files | Reuse | This is the existing runtime skill infrastructure requested by the paper architecture. |
| Current standard printing skill | `skills/standard-paper-printing/SKILL.md` | Explains the existing print-only Cartesian v9 capability | Preserve its v9 contract; extend only if semantics remain coherent, otherwise add a distinctly scoped/versioned general standard-workflow skill | It embeds a 1/2/3-drop triplicate example but cannot describe transfer, dilution, mixing, controls, or arbitrary repeated deposition. The five-minute default belongs to the YAML template, not this skill. |
| Workflow/design registry | `src/printing/workflows/registry.py` | Registers working legacy and modern printing configurations | Extend | It provides deterministic discovery and builder ownership; Experiment 01 needs a new general standard workflow entry, not a special-case branch. |
| Existing printing test suite | `tests/test_print_jobs_v1.py`, `tests/test_resolved_print_plans.py`, `tests/test_printing_*.py` | Schema, registry, golden-plan, tools, skills, agent, approval, and simulation coverage | Reuse and add Experiment 01 tests | The current modern-printing subset passes and provides regression coverage for user-owned in-progress architecture work. |
| Machine-specific configuration | `configs/robot.yaml` | Robot identity and maximum API for the separate real-robot laptop | Reuse as read-only metadata | This HOME laptop is simulation-only; no live command is permitted here. |

## Authoritative paper geometry and standoff

The authoritative coordinate definition is
`labware/paper_print_96_flat.json`:

- footprint: 127.76 x 85.48 mm;
- A1 center: x = 14.38 mm, y = 74.24 mm;
- row and column pitch: 9.00 mm;
- modeled paper surface: z = 6.0 mm;
- nominal well depth: 0.1 mm;
- load identity: `custom_beta/paper_print_96_flat/1`.

The most recent repository evidence for reliable physical droplet landing is
`configs/printing/four_clover_spacing_v13.yaml`:

- `p20_single_gen2`, left mount;
- paper in slot 5 using `custom_beta/paper_print_96_flat/1`;
- `printing.droplet_volume_ul: 5.0`;
- `printing.dispense_height_mm: 0.5`, annotated as physically confirmed and
  replacing 4.0 mm;
- no pre-air chase, 1.5 uL trailing air gap, 3.0 uL push-out, blow-out enabled.

The coordinate geometry and the physical standoff are separate facts. Experiment
01 will preserve the registered coordinate definition and use the 0.5 mm standoff.
Existing v9/v12 golden fixtures remain at 4.0 mm so their historical behavior is
not silently rewritten.

## Golden baseline

Environment: `C:\Users\iyer95\miniconda3\envs\ai\python.exe` (the `ai` conda
environment). The `conda` launcher itself was not on PATH, so its interpreter was
invoked directly.

1. Unfiltered `pytest -q` could not collect because `tests/test_gemini.py` makes a
   real Gemini request at import time and calls `sys.exit(1)` when network access
   is denied.
2. Local suite excluding only that network test, with a workspace-local pytest
   base temp: **607 passed, 11 failed, 5 skipped**.
3. Failure classification:
   - 1 pre-existing legacy printing-demo assertion;
   - 8 live-robot tests rejected by the correct HOME-laptop Vertex/live safety gate;
   - 2 legacy vial/vision tests unable to overwrite existing protected output files.
4. Modern printing architecture subset: **154 passed**.
5. Existing generated v9 protocol simulation: **PASS**, dry-run preflight.
6. Existing generated v13 spacing protocol simulation: **PASS**, dry-run preflight;
   16 planned 5 uL deposits at the 0.5 mm config setting.

Exact commands (PowerShell, repository root):

```powershell
& 'C:\Users\iyer95\miniconda3\envs\ai\python.exe' -m pytest -q
& 'C:\Users\iyer95\miniconda3\envs\ai\python.exe' -m pytest -q --ignore=tests/test_gemini.py --basetemp=.test_tmp/pytest_exp1_baseline_20260820 -p no:cacheprovider
& 'C:\Users\iyer95\miniconda3\envs\ai\python.exe' -m pytest -q tests/test_print_jobs_v1.py tests/test_resolved_print_plans.py tests/test_printing_schemas.py tests/test_printing_registry.py tests/test_printing_golden_baselines.py tests/test_printing_tools.py tests/test_printing_skills.py tests/test_printing_agent_print_jobs.py tests/test_printing_experiment_workflow.py --basetemp=.test_tmp/pytest_exp1_printing_baseline_20260820 -p no:cacheprovider
$env:OT_API_CONFIG_DIR='C:\code\opentrons_home\ot2-lab-suite\.test_tmp\opentrons-simulator'
& 'C:\Users\iyer95\miniconda3\envs\ai\python.exe' -c "import numpy as np; np.trapz=getattr(np,'trapezoid',getattr(np,'trapz',None)); from opentrons.simulate import main; main()" -L labware src/protocols/generated/plate_well_direct_print_v9_latest.py
& 'C:\Users\iyer95\miniconda3\envs\ai\python.exe' -c "import numpy as np; np.trapz=getattr(np,'trapezoid',getattr(np,'trapz',None)); from opentrons.simulate import main; main()" -L labware src/protocols/generated/four_clover_spacing_v13_latest.py
```

The 11 local-suite failures were:

- `tests/test_printing_demo_config.py::test_overlap_validation_food_coloring_and_water`;
- `tests/test_robot_automation.py::TestRobotAutomation::test_check_robot_connection_fail`;
- `tests/test_robot_automation.py::TestRobotAutomation::test_check_robot_connection_missing_key`;
- `tests/test_robot_automation.py::TestRobotAutomation::test_check_robot_connection_success`;
- `tests/test_robot_automation.py::TestRobotAutomation::test_deploy_and_execute_missing_key`;
- `tests/test_robot_automation.py::TestRobotAutomation::test_deploy_protocol_creates_manifest`;
- `tests/test_robot_automation.py::TestRobotAutomation::test_execute_on_robot_blocks_failed_simulation`;
- `tests/test_robot_automation.py::TestRobotAutomation::test_execute_on_robot_blocks_unsimulated_hash`;
- `tests/test_robot_automation.py::TestRobotAutomation::test_execute_on_robot_success`;
- `tests/test_vial_print_agent.py::test_save_and_load_vial_print_template`;
- `tests/test_vial_print_agent.py::test_offline_pipeline_build_validate_cv`.

The exact generated artifacts used for the two direct simulations were:

- v9 SHA-256: `d0caad7e9dfc789d4024147b0bb1529d70bc9973c4b86af3325b12012775af6b`;
- v13 SHA-256: `d406062e4ba8c3120ac501159121ea07e09f87f3ade5f39c346b671de71d653d`.

The v9 output reported successful config/labware preflight, 42 planned 5 uL
deposits, 45 minutes of configured rests, and a dry-run completion. The v13
output reported successful preflight, 4 clovers/16 planned 5 uL deposits,
0 uL pre-air chase, a 1.5 uL trailing air gap, a 6.5 uL piston dispense,
3 uL push-out, blow-out enabled, and a dry-run completion.

The initial dirty-worktree status and SHA-256 manifest for overlapping user-owned
architecture files is retained in
`experiment_01/audits/stage_0_user_owned_worktree_manifest.md`. Stage 0 added only
this audit document, that manifest, and
`tests/test_experiment_01_geometry_baseline.py`; it did not clean, stash, reset,
or overwrite the pre-existing worktree.

No real robot connection, upload, HTTP run, SSH run, or `--live` command was used.

## Stage 0 architecture conclusion

The repository already has strong print-only schemas, canonical hashing, scoped
runtime skills, bounded tools, an interpretation-only agent, and an approval-gated
local simulation workflow. Experiment 01 must extend these rather than duplicate
them. The missing capability is a strict general standard-workflow schema and
resolver for multiple liquids plus ordered preparation, mixing, transfer, print,
repeat, and delay actions. That new capability must compile to deterministic
physical actions consumed by trusted Python; it must not adapt the legacy v6 or v9
protocol by asking the model to generate robot code.
