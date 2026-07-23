# Archive Manifest

Files moved out of the active tree during the printing-workflow organization pass.
Nothing here is deleted; every entry can be restored. The **active** workflow is the
vial → 96-well dilution → paper-print workflow (see
[docs/printing/REPOSITORY_MAP.md](../docs/printing/REPOSITORY_MAP.md)).

Conservative policy: a file is archived only when a repository-wide search finds **no
active import, path reference, registry entry, CLI reference, test, or generated-file
dependency**. Anything uncertain stays active and is listed in
[docs/printing/ARCHIVE_REVIEW_REQUIRED.md](../docs/printing/ARCHIVE_REVIEW_REQUIRED.md).

| Original path | New archive path | Reason | Evidence it is inactive | Replacement | Date | Safe to restore? |
|---|---|---|---|---|---|---|
| `src/protocols/p20_gen2_test.py` | `archive/experiments/pipette_bringup/p20_gen2_test.py` | Experimental P20 bring-up bench test; superseded by integrated P20 support in the flagship. | `grep` across `*.py`/`*.md` found no import, registry, runner, or test reference (only self-references). Not under `tests/`, so never collected by pytest. | `configs/printing/01_vial_dilution_paper_print.p20_only.yaml` + `src/core/pipette_selection.py` | 2026-07-22 | Yes |
| `src/protocols/p20_p300_gen2_alternating_test.py` | `archive/experiments/pipette_bringup/p20_p300_gen2_alternating_test.py` | Experimental two-pipette (P20+P300) alternation bench test; the mixed-pipette behaviour it prototyped is now first-class in the flagship. | Same search: no active references anywhere. | `configs/printing/01_vial_dilution_paper_print.mixed.yaml` | 2026-07-22 | Yes |
| `src/protocols/vial_rack_clearance_test.py` | `archive/protocols/vial_rack_clearance_test.py` | One-off clearance test protocol for the v2 rack; not part of Workflow 01. | `grep` for `vial_rack_clearance_test` across `*.py/*.md/*.json/*.yaml` returned **zero** references outside the file itself. | none (rack geometry validated by `3d_print_labware_validate.py`, still active) | 2026-07-22 | Yes |
| `src/printing/` (whole tree: `tools/`, `protocols/`, `configs/`, `logs/`, `README.md`, `test_workflow.py`) | `archive/legacy_src_printing/` | Legacy "v2.0" nanoparticle-printing suite; a self-contained island superseded by `src/core/*` + the flagship. Contained the old `select_pipette` (superseded by `src/core/pipette_selection.py`). | **Zero** `from src.printing` / `import src.printing` in any active `.py`; no test imports it; `pytest.ini` already excluded it from collection; only mention was one docstring line (updated to note the archive). | `src/core/pipette_selection.py` (selection); the flagship (protocols) | 2026-07-22 | Yes — but its `logs/` are historical; restore the tree, don't merge logs |
| `agent_protocol_config.json` | `archive/legacy_configs/agent_protocol_config.json` | Orphaned pre-unified printing job config at repository root. | Repository-wide search found zero imports, path constants, CLI references, builders, validators, registry entries, agent tools, tests, docs, YAML references, workflow references, or consumers of its unique keys (`total_mix_volume`, `print_volume`). | `configs/printing/01_vial_dilution_paper_print.<variant>.yaml` | 2026-07-22 | Yes |
| `scripts/validate_protocol.py` | `archive/validators/validate_protocol.py` | **Confirmed unused / broken validator.** It targeted the obsolete, absent `src/protocols/verify_tuberack.py`; historical significance is limited to preserving the old six-case tube-rack validation matrix. It had previously stayed active only because current docs still named it. | Exact searches for `validate_protocol`, `validate_protocol.py`, and `scripts/validate_protocol` found no Python import, CLI dispatcher, test, registry, skill, builder, automation, YAML, or GitHub-workflow consumer; only `docs/README.md`, `docs/REPO_ARCHITECTURE_FOR_LLM.md`, and `docs/SOP_Simulation_Testing.md`, which were migrated before the move. | `scripts/build_vial_dilution_print.py` + `scripts/validate_vial_print.py` | 2026-07-22 | Yes — restore only with its obsolete target or retarget intentionally. |
| `scripts/run_paper_print_motion_test.py`, `src/protocols/paper_print_motion_test.py`, `labware/paper_print_96_flat.json` | `archive/experiments/paper_print_motion_test/{scripts/run_paper_print_motion_test.py,src/protocols/paper_print_motion_test.py,labware/paper_print_96_flat.json}` | **Confirmed unused / retired manual hardware experiment.** Preserved as a self-contained record of an earlier dry P300 fixture-clearance sweep; its useful sweep-and-stop procedure was transferred to the active physical-validation checklist. | Exact searches for `paper_print_motion_test` and `paper_print_96_flat` found only the three family members and the review note: no external import, runner consumer, registry, test, skill, builder, active config, documentation command, YAML, automation, or GitHub workflow. Workflow 01 loads `corning_96_wellplate_360ul_custom`, not `paper_print_96_flat`; geometry comparison showed different `loadName`, well depth/bottom, z-dimension, and fixture assumptions. | `docs/printing/WORK_LAPTOP_PHYSICAL_VALIDATION.md` + Workflow 01 `column_8up` dry/limited checks | 2026-07-22 | Yes — restore the whole family together; never apply its slot-8/6-mm/1-mm assumptions to the active fixture. |
| `configs/labware/printed_6vial_mold_20ml.yaml`, `labware/printed_6vial_mold_20ml.json` | `archive/labware_examples/six_vial_mold/{configs/labware/printed_6vial_mold_20ml.yaml,labware/printed_6vial_mold_20ml.json}` | **Confirmed unused / placeholder labware example.** The YAML explicitly says its six-vial geometry is only a plausible starting point, not a measured physical part; the JSON is preserved with its original filename and internal `loadName`. | Exact searches for `printed_6vial_mold_20ml` and six-vial variants found only the YAML, JSON, and review note: no active config, protocol, builder, validator, test, doc, registry, skill, automation, YAML reference, or GitHub workflow. It was exposed only because `src/agents/labware_tools.py` dynamically lists every top-level `configs/labware/*.yaml` and `labware/*.json`; moving both files removes them from that active catalog without code changes. | Active physical rack: `labware/tuberack_3dprint_20ml_8vials_v2.json` and its measured YAML config | 2026-07-22 | Yes — restore both files together if the example catalog is intentionally expanded. |

## Deeper unused-file audit — classification of candidates

Every candidate was searched for Python imports, dynamic imports, CLI/registry/builder
references, YAML path references, doc links, test references, agent-tool references, and
`Path` constants. Classification: **Active** (Workflow 01 / shared infra), **Supporting**
(used by active code, not an entry point), **Legacy confirmed** (archived above),
**Uncertain** (kept active, see `docs/printing/ARCHIVE_REVIEW_REQUIRED.md`), **Historical**
(immutable).

| Candidate | Class | Evidence |
|---|---|---|
| `src/protocols/printing/01_vial_dilution_paper_print.py` | Active | Workflow-01 entry point |
| `src/core/{pipette_selection,print_groups,materials,workflow_config}.py` | Active | shared validators |
| `scripts/build_vial_dilution_print.py`, `scripts/validate_vial_print.py` | Active | builder + matrix validator |
| `src/protocols/printing.py`, `src/protocols/dilution.py` | Supporting | imported by `src/core/workflows/registry.py` (lines 55–56) |
| `src/protocols/3d_print_labware_validate.py` | Supporting | 108 refs; single-nozzle pattern template + `run_3d_print_labware_validate.py` |
| `src/protocols/simulate_protocol.py`, `mock_test_protocol.py` | Supporting/Uncertain | referenced by SOP docs; generic sim helpers |
| `src/protocols/printing_demo_protocol.py` + `printing_{demo,12,96}.yaml` | Uncertain | Family B; actively tested (`tests/test_printing_demo_*`) — kept |
| `scripts/validate_protocol.py` | **Legacy confirmed** | Broken obsolete target; three documentation references migrated to the Workflow 01 builder/validator → archived |
| `scripts/run_paper_print_motion_test.py` + protocol + `paper_print_96_flat.json` | **Legacy confirmed** | Isolated manual fixture experiment; active Workflow 01 uses different paper labware and the safety procedure now lives in the work-laptop checklist → archived together |
| `printed_6vial_mold_20ml.{yaml,json}` | **Legacy confirmed** | Placeholder six-vial geometry, no explicit consumer, removed from dynamic active discovery by moving both source and generated definition → archived together |
| `src/protocols/vial_rack_clearance_test.py` | **Legacy confirmed** | 0 references → archived |
| `src/printing/` (tree) | **Legacy confirmed** | 0 external imports, not test-collected → archived |
| `agent_protocol_config.json` | **Legacy confirmed** | 0 references and no schema consumer → archived |
| `configs/workflows/user/*.yaml`, `src/protocols/generated/*_run_*.py` | Historical | immutable run snapshots / build artifacts — untouched |
| `vision_tests/` raw images | Out of scope | CV deprioritized; `*.jpg/*.png` git-ignored |

## Pre-existing archive directories (not created by this pass)
- `archive/previous_workflow_v1/` — the earlier nanoparticle-printing suite (contains a
  `tools/test_printing_simulation.py` that calls `sys.exit(1)` at import; excluded from
  pytest collection via `pytest.ini`).
- `archive/tuberack_3dprint_20ml_8vials_v1/` — the v1 tube rack and its verify protocol.

## Empty category folders (reserved for future archival)
`archive/protocols/`, `archive/configs/`, `archive/computer_vision/`, `archive/docs/`
were created for the category-specific layout requested by the organization plan but
hold nothing yet — candidates for them are listed in ARCHIVE_REVIEW_REQUIRED.md.
