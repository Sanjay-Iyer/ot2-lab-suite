# Archive — Review Required

Files that *look* inactive or superseded but were **kept active** because archiving
them is uncertain or would break a reference. Each needs a human decision before moving.
(Policy: when in doubt, stay active — see archive/ARCHIVE_MANIFEST.md.)

| File | Why it looks archivable | Why it was NOT archived | Suggested action |
|---|---|---|---|
| `scripts/validate_protocol.py` | Broken: points at `src/protocols/verify_tuberack.py`, which does not exist (lives only under `archive/tuberack_3dprint_20ml_8vials_v1/`). | Referenced by `docs/README.md`, `docs/REPO_ARCHITECTURE_FOR_LLM.md`, `docs/SOP_Simulation_Testing.md`. Archiving needs those docs updated too. | Fix the path or archive together with the three doc references. |
| `_legacy_run_single_tip_print()` in `src/protocols/printing/01_vial_dilution_paper_print.py` | Dead code: never called; references undefined names if it ever were. | It lives *inside* the active flagship file; removing it is a code edit, not a file move. | Delete the function in a focused cleanup PR (low risk, improves readability). |
| `src/protocols/printing.py`, `src/protocols/dilution.py` | Older registry generators (`generate_printing_protocol` / `generate_dilution_protocol`), apiLevel 2.13/2.15. | Still wired into `src/core/workflows/registry.py` and covered by `tests/test_workflows/` (currently skipped). | Confirm the registry path is unused, then archive with its tests. |
| `src/protocols/printing_demo_protocol.py` + `configs/workflows/defaults/printing_{demo,12,96}.yaml` | Family B (demo generator) — parallel to the flagship. | Actively tested (`tests/test_printing_demo_*`) and documented. Not part of THIS workflow but not dead. | Decide whether Family B is retired; if so, migrate onto the unified schema (Phase 2) before archiving. |
| `src/protocols/mock_test_protocol.py`, `simulate_protocol.py` | Generic/legacy helpers. | Referenced by SOPs and the AI-agent overview. | Keep until the SOPs are rewritten. |
| `vision_tests/` (large raw image trees) | CV is deprioritized to begin/end capture only. | Separate subsystem; raw images are git-ignored now (`*.jpg/*.png`). | Out of scope for this pass; review under a CV cleanup. |

## Notes
- Historical run snapshots (`configs/workflows/user/*.yaml`) and timestamped generated
  protocols are **intentionally left untouched** (immutable experimental history).
- The AI-agent subsystem (`src/agents/…`) is deferred; only references that would break
  from path changes were to be touched, and no active path changed in this pass.
