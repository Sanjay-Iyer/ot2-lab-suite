# Archive — Review Required

Files that *look* inactive or superseded but were **kept active** because archiving
them is uncertain or would break a reference. Each needs a human decision before moving.
(Policy: when in doubt, stay active — see archive/ARCHIVE_MANIFEST.md.)

| File | Why it looks archivable | Why it was NOT archived | Suggested action |
|---|---|---|---|
| `_legacy_run_single_tip_print()` in `src/protocols/printing/01_vial_dilution_paper_print.py` | Dead code: never called; references undefined names if it ever were. | It lives *inside* the active flagship file; removing it is a code edit, not a file move. | Delete the function in a focused cleanup PR (low risk, improves readability). |
| `src/protocols/printing.py`, `src/protocols/dilution.py` | Older registry generators (`generate_printing_protocol` / `generate_dilution_protocol`), apiLevel 2.13/2.15. | Still wired into `src/core/workflows/registry.py` and covered by `tests/test_workflows/` (currently skipped). | Confirm the registry path is unused, then archive with its tests. |
| `src/protocols/printing_demo_protocol.py` + `configs/workflows/defaults/printing_{demo,12,96}.yaml` | Family B (demo generator) — parallel to the flagship. | Actively tested (`tests/test_printing_demo_*`) and documented. Not part of THIS workflow but not dead. | Decide whether Family B is retired; if so, migrate onto the unified schema (Phase 2) before archiving. |
| `src/protocols/mock_test_protocol.py`, `simulate_protocol.py` | Generic/legacy helpers. | Referenced by SOPs and the AI-agent overview. | Keep until the SOPs are rewritten. |
| `vision_tests/` (large raw image trees) | CV is deprioritized to begin/end capture only. | Separate subsystem; raw images are git-ignored now (`*.jpg/*.png`). | Out of scope for this pass; review under a CV cleanup. |
| `configs/labware/{reservoir_12col_21ml,standard_24_wellplate_3400ul,standard_6_wellplate_16800ul,tube_rack_15ml_15tubes}.yaml` | No protocol or workflow names these individual configs. | The labware agent enumerates `configs/labware/*.yaml` dynamically, so these are available catalog inputs rather than orphans. | Keep as shared labware catalog unless the supported catalog is intentionally reduced. |
| `vision/config.py`, `vision/transfer_images.py`, `vision/image_inventory.py`, `vision/validate_images.py`, `vision/physical_setup_image_validation.py` | `docs/computer_vision.md` labels parts of this path parallel/legacy; the pre-flight gate is not wired into a launcher. | They remain documented manual tools in the CV subsystem, and CV cleanup is separate from Workflow 01 archival. | Review as one CV acquisition/pre-flight cleanup; do not move piecemeal. |
| `vision_tests/scripts/build_blue_orange_audit_report.py` | No code or docs invoke the filename directly. | It deterministically regenerates the tracked `BLUE_ORANGE_DROPLET_AUDIT.md` from historical analysis CSV data, so it supports reproducibility. | Keep with the historical CV study unless that study is archived as a unit. |
| `configs/workflows/defaults/{austar,cleanup}.yaml` | Both workflows use `stub_generator` and cannot generate protocols. | They are explicit deferred registry entries and therefore part of the AI-agent architecture, which is out of scope. | Leave for the later AI-agent/workflow-registry reorganization. |

## Final unused-file census — 2026-07-22

The repository-wide census covered tracked scripts, protocols, configs, labware, core
validators/tools, tests, documentation, skills, vision code, GitHub workflow paths,
generated artifacts, and the robot-data mirror. Searches included Python imports,
dynamic directory enumeration, path constants, CLI examples, builders, validators,
workflow and robot registries, agent tools, pytest collection, Markdown/YAML references,
and generated-protocol dependencies.

Classification summary:

- **Active Workflow 01:** the numbered protocol/configs/docs/skills, shared Workflow 01
  validators, vial builder/matrix validator, active labware, and `tests/printing/`.
- **Shared supporting infrastructure:** registered workflows, generic config/constraint
  loaders, labware generator/catalog, simulation/deployment utilities, Family B, plate
  waste disposal, and documented CV acquisition/analysis tools.
- **AI-agent subsystem — deferred:** `src/agents/**`, agent registries/auth checks, and
  registered deferred workflow stubs. None were archived by this census.
- **Historical record:** `robot_data/**`, generated run protocols, user run configs,
  CV study inputs/results, and planning/audit documents. None were altered.
- **Confirmed unused:** the former root `agent_protocol_config.json`, broken legacy
  validator, retired paper-motion family, and placeholder six-vial mold pair; all are
  recorded with exact destinations and replacements in `archive/ARCHIVE_MANIFEST.md`.
- **Uncertain:** the rows above. They remain in place pending an explicit decision.

Pytest collection (excluding the live-network `tests/test_gemini.py`) found 201 tests
across every other test module and zero items under `archive/`; no orphaned collected
test module was found.

## Notes
- Historical run snapshots (`configs/workflows/user/*.yaml`) and timestamped generated
  protocols are **intentionally left untouched** (immutable experimental history).
- The AI-agent subsystem (`src/agents/…`) is deferred; only references that would break
  from path changes were to be touched, and no active path changed in this pass.
