# Stage 0 user-owned worktree manifest

This snapshot records the dirty worktree observed before Experiment 01 changes.
All listed paths are user-owned and must not be reset or overwritten merely to
obtain a clean baseline.

## Initial `git status --short`

```text
 M configs/labware/tube_rack_15ml_15tubes.yaml
 M configs/printing/four_clover_air_chase_v12.yaml
 M configs/workflows/defaults/vial_dilution_print.yaml
 M scripts/build_vial_dilution_print.py
 M scripts/generate_labware.py
 M scripts/run_vial_print_robot.py
 M skills/README.md
 M skills/ot2-labware/SKILL.md
 M src/agents/labware_tools.py
 M src/agents/main.py
 M src/agents/robot_http_tools.py
 M src/agents/vial_print_agent.py
 M src/agents/vial_print_tools.py
 M src/protocols/generated/complementary_bp_print_v10a_latest.py
 M src/protocols/generated/four_clover_air_chase_v12_latest.py
 M src/protocols/generated/vial_dilution_print_latest.py
 M src/protocols/generated/vial_dilution_print_v2_latest.py
 M src/protocols/printing/01_vial_dilution_paper_print.py
 M src/protocols/printing/02_vial_dilution_paper_print_p20_dilution.py
 M src/protocols/printing/12_four_clover_paper_print.py
 M tests/test_robot_http_tools.py
 M vision_tests/outputs/cv_report.txt
 M vision_tests/outputs/cv_summary.json
?? configs/experiments/
?? configs/labware/paper_print_96_flat.yaml
?? configs/labware/well_plate_96/
?? configs/printing/INDEX.md
?? configs/templates/
?? docs/OT2_PRINTING_GUIDE.md
?? docs/architecture/
?? examples/
?? labware/paper_test_01.json
?? skills/96-well-labware/
?? skills/design-paper-printing/
?? skills/four-clover-printing/
?? skills/standard-paper-printing/
?? src/agents/custom_labware_agent.py
?? src/agents/printing_agent.py
?? src/agents/printing_tools.py
?? src/labware/
?? src/printing/
?? tests/fixtures/
?? tests/test_labware_96_v1.py
?? tests/test_labware_geometry.py
?? tests/test_labware_schema.py
?? tests/test_labware_validation.py
?? tests/test_main_agent_mock.py
?? tests/test_print_jobs_v1.py
?? tests/test_printing_agent_print_jobs.py
?? tests/test_printing_cli.py
?? tests/test_printing_experiment_workflow.py
?? tests/test_printing_golden_baselines.py
?? tests/test_printing_registry.py
?? tests/test_printing_schemas.py
?? tests/test_printing_skills.py
?? tests/test_printing_tools.py
?? tests/test_resolved_print_plans.py
```

## SHA-256 of overlapping architecture evidence

```text
f27cf03a764c6ce39e2e5761857241303b20f6ccc0e0958eaa98e6686da0d3b8  docs/architecture/STAGE0_BASELINE.md
a92cecb9bb6d224d52914c9d8785a844f6350fef720688dc1ddfbf0c5adb8f66  docs/architecture/ARCHITECTURE.md
413ec524d1d503266fb6f1ab3602354bd04e9728da2d2a5ac01190ab936af2ce  src/printing/schemas/jobs.py
c8c5a9fc03ab307410cf0d721f1eeda3fd32faf25b91b08bc2bc05b5de4b0683  src/printing/schemas/plans.py
a533d8b81c07ca077132dcf5b8ea6b6d25410c14cee476455255e66ac6a4725b  src/printing/job_compiler.py
a9fb6e858c4266706015bf31deae48dfcf55460b83d79c4a780903b2e94b7e0d  src/printing/plans.py
a6bfb29ee892576198868efa783f6701d04a856a4712b7330f67609edea9464b  src/printing/canonical.py
df6d93e003d30327c702d37f3c356ab96a1792217d12837264a34afc5c8b4aff  src/printing/experiment_configs.py
66d342d0ff95f9da9762cd0d5a81ad6dc3efa5c0aff5dde92ff3b779a100c391  src/printing/experiment_workflow.py
f915ae64a6d3a0105d4ebec59ddd087481e22e6d8e9a86df72a099ab0aea8b44  src/printing/skills.py
a0a4008969dd21cf5e1b276391fad291f67933c6abab79ed84c74512fb01cdd0  src/printing/workflows/registry.py
caa7102788cecee0ac0242e3559299196b3db66c524ccb4f8d761175434f5687  src/agents/printing_agent.py
e7a655f22472254409cadd084396b626bccd2deada151b1a6cdec981cc405b90  src/agents/printing_tools.py
543ed90e06f9d4e219a4c7972f20708ff7d5e23230ad46ea3249d01bd3171563  skills/standard-paper-printing/SKILL.md
914dd013b47966fac297310f5d1353d55905ca8c99a1db56edec4bc441194d77  configs/templates/printing/standard_paper_printing.yaml
a05c7a7b82fa2622ed47d5905b4027344b2dba3eb7f93fb68a663424d0cf065b  configs/experiments/nanoparticle_drop_series_triplicate_v1.yaml
068e451fbcc49da34197c8b89b0cd18027a608c0cb4edd73defda4d14979092b  tests/test_print_jobs_v1.py
c38cacaab9ebc153ab66c153b32277dd9e83a7c4c0ead8f12076c74a6904dee7  tests/test_resolved_print_plans.py
3eb37e786836fbf99afcc2eaf44336f88cfa399eac5308d4f9b13a260980ee69  tests/test_printing_golden_baselines.py
7833262a6e1d7664f887014d6a9c4e8cf2901bc401fb5813df9777bc7d4a12cf  tests/test_printing_experiment_workflow.py
d0caad7e9dfc789d4024147b0bb1529d70bc9973c4b86af3325b12012775af6b  src/protocols/generated/plate_well_direct_print_v9_latest.py
d406062e4ba8c3120ac501159121ea07e09f87f3ade5f39c346b671de71d653d  src/protocols/generated/four_clover_spacing_v13_latest.py
```
