# Phase 1 — Config-Driven Pipette Printing: Final Report

Date: 2026-07-22. Scope: make the flagship `vial_dilution_print` workflow fully
configuration-driven with P20 (single-spot) + P300 (8-up) pipette selection, materials,
build-time validation, examples, tests, simulation, focused organization, and LLM skills.

## 1. Files created

**Shared services / logic**
- `src/core/pipette_selection.py` — authoritative volume→pipette selection service.
- `src/core/print_groups.py` — unified print-group schema + validator + legacy migration.
- `src/core/materials.py` — materials/vial schema + conservative volume accounting.
- `src/core/workflow_config.py` — build-time normalizer + shared-validator entry point.

**Configs**
- `configs/printing/01_vial_dilution_paper_print.p20_only.yaml`,
  `configs/printing/01_vial_dilution_paper_print.p300_only.yaml`, and
  `configs/printing/01_vial_dilution_paper_print.mixed.yaml`
- `configs/printing/README.md` (numbered index)

**Tests**
- `tests/printing/test_pipette_selection.py` (28), `tests/printing/test_print_groups.py` (14),
  `tests/printing/test_materials.py` (13), `tests/printing/test_workflow_config.py` (13)
- `conftest.py` (numpy.trapz shim), `pytest.ini` (scope to tests/, ignore archive)

**Docs / skills / archive**
- `docs/REFACTOR_PIPETTE_SELECTION_PLAN.md` (design), `docs/printing/REPOSITORY_MAP.md`,
  `docs/printing/ARCHIVE_REVIEW_REQUIRED.md`, `docs/printing/PHASE1_FINAL_REPORT.md` (this)
- `skills/printing_workflow/{SKILL,REPOSITORY_MAP,CONFIGURATION_GUIDE,VALIDATION_AND_SIMULATION,SAFETY_AND_HARDWARE_CONSTRAINTS}.md`
- `archive/ARCHIVE_MANIFEST.md`

## 2. Files modified
- `src/protocols/printing/01_vial_dilution_paper_print.py` — multi-pipette load; per-pipette tip racks;
  group-based print dispatch (`column_8up`→P300, `single_spot`→P20); mixing decoupled;
  tip reuse/return per pipette; preflight made schema-tolerant; dead print-branch code
  removed; imaging consolidated to one before + one after.
- `scripts/build_vial_dilution_print.py` — calls `normalize_and_validate` (shared
  validators) + deck-slot check; legacy structural validator kept for legacy configs.
- `scripts/validate_vial_print.py` — expected-text markers updated to the new comments.
- `configs/constraints/pipette_constraints.yaml` — added `p300_multi_gen2`, `p20_multi_gen2`.
- `tests/test_vial_print_agent.py` — droplet 18→30 µL (sub-20 µL on P300 now correctly rejected).
- `skills/README.md` — added the `printing_workflow` skill.

## 3. Files moved to archive (old → new)
- `src/protocols/p20_gen2_test.py` → `archive/experiments/pipette_bringup/p20_gen2_test.py`
- `src/protocols/p20_p300_gen2_alternating_test.py` → `archive/experiments/pipette_bringup/…`

Full manifest: `archive/ARCHIVE_MANIFEST.md`. Files renamed: none (labware
filename==loadName rule preserved). Physical relocation of `src/`/`protocols/` into a
`src/printing/` package was **deferred** (see §12).

## 4. Production protocol & behaviors

**Primary protocol:** `src/protocols/printing/01_vial_dilution_paper_print.py` (robot entry point; runs from
embedded CONFIG). Built from YAML by `scripts/build_vial_dilution_print.py`.

**Pipette selection:** at build time, `select_pipette` picks the smallest-capacity mounted
pipette covering the volume — ≤20 µL → `p20_single_gen2`, >20 µL → `p300_multi_gen2` —
or honours an explicit name; 21–29 µL resolves to the P300 with a low-accuracy warning;
unfittable volumes error with the mounted ranges. Selection is embedded per group; the
robot protocol never chooses a pipette.

**Dilution & mixing:** P300 single-nozzle water + per-series stock transfers
(stock=total/fold, water=total−stock); 8-up P300 mixing (`mix_reps`×`mix_volume_ul`).

**Tip reuse/return:** each group reuses its assigned tip (`tips.well`, P20) or 8-tip block
(`tips.block_column`, P300) and returns it to its own rack (`return_tip`, `tip_policy`).
P20 and P300 are separate InstrumentContext objects with separate racks (20 µL / 300 µL)
→ fully independent tip state.

**Imaging:** one image before the workflow and one after (mocked in simulation).

## 5. Material & solvent schema (with water/ethanol example)
```yaml
materials:
  water:      {role: solvent, labware: tuberack_3dprint_20ml_8vials_v2, vial: A1, initial_volume_ul: 15000}
  ethanol:    {role: solvent, labware: tuberack_3dprint_20ml_8vials_v2, vial: A2, initial_volume_ul: 15000}
  orange_dye: {role: dye,     labware: tuberack_3dprint_20ml_8vials_v2, vial: A3, initial_volume_ul: 8000}
  blue_dye:   {role: dye,     labware: tuberack_3dprint_20ml_8vials_v2, vial: A4, initial_volume_ul: 8000}
```
Validated: unknown material, invalid vial, duplicate vial (unless `allow_shared_vial`),
insufficient volume (consumed×overage + dead volume), aspiration height, missing labware,
filename==loadName. The active rack loadName is `tuberack_3dprint_20ml_8vials_v2` (the
spec's un-versioned name does not exist and is caught with a "did you mean" hint).

## 6. Example configs
- `01_vial_dilution_paper_print.p20_only` — 5/10 µL single_spot on the P20 (P300 mounted only to prep dilutions).
- `01_vial_dilution_paper_print.p300_only` — 30/35 µL column_8up on the P300.
- `01_vial_dilution_paper_print.mixed` — P300 30/35 µL (paper 1–4) + P20 5/10 µL (paper 5–8), water+ethanol.

## 7. Build-time validation (one authoritative rule each)
`normalize_and_validate` runs before generation; rules live only in `src/core/*`:
pipette range/mount/not-mounted (pipette_selection), layout↔channels + source/dest +
duplicate/out-of-bounds paper + per-well volume (print_groups), material/vial/volume/
filename==loadName (materials), plus deck-slot uniqueness. Errors name section, group/step,
field, value, mounted pipettes/labware, and a correction.

## 12. Deferred / requires physical OT-2 verification
**Requires the real robot (simulation cannot confirm):** dispense Z / paper standoff
(`dispense.z_mm`), real vial liquid levels vs `aspirate_height_mm`, any new deck-slot
placement, the paper fixture's true height. **Deferred work:** physical relocation of
`src/`/`protocols/` into a `src/printing/` package (≈400-reference blast radius);
AI-agent subsystem reorganization; retiring Family B (`printing_demo_*`) onto the unified
schema. See `docs/printing/ARCHIVE_REVIEW_REQUIRED.md`.

---
_Sections 8–11 (test/sim commands + exact results, and the two audit reports) are filled
in the delivered chat report and below once the functional audit completes._

## Organization audit (Agent 3) — result: SOUND
Zero broken references from the archive move; labware filename==loadName holds for all 5
custom JSONs; every path named in the new docs exists; docs match code (selection
thresholds, slot-2 rack, config table); active/archived clearly separated; history
untouched; the printing layer is understandable without `src/agents/`; `pytest
--collect-only` = 201 tests, 0 errors. Two LOW doc nits found and FIXED:
`configs/printing/README.md` safety pointer repointed to the skills safety doc; the
standard tip racks separated from the custom-labware table in `docs/printing/REPOSITORY_MAP.md`.
