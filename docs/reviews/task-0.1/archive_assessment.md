# Archival and retention assessment

This is a dependency assessment, not approval to archive. No file is moved or
deleted. All 1,196 in-scope files have individual roles, hashes and retention
reasons in `files.csv`; incoming static evidence is in `references.csv`.
Zero imports never establishes that a CLI, registered protocol, YAML, skill or
manually used experiment is unused.

## Explicit replacement-review candidates (14 files)

These remain shared/supported compatibility paths. Their replacement is not
implemented and WORK qualification is pending. Future archival requires removal
or migration of all listed consumers, dynamic catalogue checks, equivalent
scientific features and physical-method evidence where applicable.

| Candidate | Known consumer/dependency evidence | Required replacement and decision |
|---|---|---|
| `scripts/experiment_agent.py` | Manual entry point; configs/templates and generated current YAML; robot runner | Unified CLI/agent must cover its dilution/standard/clover features; retain |
| `scripts/11_experiment_agent.py` | Manual v11 entry point; `src/printing/v11/agent_core.py`, v11 templates/loaders and runner aliases | Preserve offline parser/manual features and v11 method behavior before retirement; retain |
| `scripts/11_test_agent_configs.py` | v11 agent case configs and `experiment_01/11_validation_report.md` | Equivalent reproducible config/negative-case tests required; retain |
| `scripts/11_run_simulation_matrix.py` | v11 matrix YAML and validation report; simulator scratch setup | Equivalent simulation scenarios/coverage required; retain |
| `src/agents/main.py` | Stress/simulation tools, mock-agent/printing tests, documented module launch | Shared service/manual interface and labware/tool behavior replacement required; retain |
| `src/agents/vial_print_agent.py` | `tests/test_vial_print_agent.py`, agent overview/skills, legacy workflow specialist | Unified agent + workflow-01 feature mapping required; retain |
| `src/agents/vial_print_tools.py` | Vial specialist, printing tools and vial-agent tests | Keep gates and shared tool consumers until explicitly migrated; retain |
| `src/protocols/printing/01_vial_dilution_paper_print.py` | Registry v1, builder default, default/printing configs, validators/tests and current guides | P300/P20 workflow coverage and qualified replacement required; retain |
| `src/protocols/printing/02_vial_dilution_paper_print_p20_dilution.py` | Registry v2, `bp_20260723_v2.yaml`, validator and guide | Small-volume pipette selection/bring-up compatibility required; retain |
| `src/protocols/printing/03_vial_dilution_paper_print_v3.py` | Registry v3, pinned validator, `tests/printing/test_v3_api_215.py` | API-2.15/P300-specific constraints and qualified method replacement required; retain |
| `src/protocols/printing/04_vial_dilution_paper_print_v4_quicktest.py` | Registry v4 remains builder-addressable, even if no direct Python imports | Check existing/operator configs and reproduce requested quick-test behavior; retain |
| `src/protocols/printing/06_vial_dilution_paper_print_v6_p20only.py` | Registry v6, experiment audit references; NanoDrop protocol history mentions | Preserve distinct P20 dilution mechanics and manual users before retirement; retain |
| `src/protocols/printing/07_paper_print_v7_print_only.py` | Registry v7, historical configs/version selection | Explicit print-only method/experiment replacement required; retain |
| `src/protocols/printing/08_vial_direct_paper_print_v8.py` | Registry v8, historical configs/version selection | Vial-direct printing compatibility and WORK evidence required; retain |

The registry labels these protocol entries deprecated, but they are not removed
from the builder. There is no v5 entry to infer from the numeric range. Config
ownership is version-based, so source-only moves would break historical workflows.

## Other assessment groups

| Group | Files | Role and dependency assessment | Decision |
|---|---:|---|---|
| Existing historical archives | 86 | 82 under `archive/` plus four `src/agents/archive/` snapshots; preserve manifests and provenance. Some guides refer to them. Existing archive/test exclusions do not prove absence of manual use. | Preserve in place now; later move whole families only with verified recovery and consumer checks |
| Generated protocol/config records | 50 | 36 tracked generated Python artifacts and 14 generated YAML files. Runner aliases, builder-version mapping, tests and operator commands can target them. | Retention review, not disposable cache; current runner targets remain |
| Robot mirror | 42 | Identity/config/calibration/database/history snapshots. Current Config/paths and simulation helpers reference this tree. | Preserve; separate future runtime simulator scratch only after path migration |
| Experiment 01 evidence | 29 | Staged audits and ground-truth protocol/config/trace artifacts; regression/documentation consumers exist | Preserve referenced fixtures and scientific records; archive excess records only with retention assessment |
| Remaining files with no archive proposal | 975 | Active NanoDrop, shared code/configs/skills, independent Raman/vision and supporting docs/tests. Some may later be superseded, but no specific archive proposal is made here. | Retain; candidate status must be established by later feature/dependency review |

The 14 explicit candidates plus 86 existing-historical and 121 record-retention
paths (50 generated + 42 robot mirror + 29 experiment evidence) leave 975 paths
with no archive proposal. Classification and retention are different axes.

## Ignored/local material

`local_artifacts.json` lists ignored paths only. It is not an archive manifest or
a verified backup. Important families include:

- `.env` and local workstation settings: preserve without exposing values.
- `.venv/`: installed simulator environments; preserve before environment changes.
- `runs/`, `experiment_history/`, user workflow YAML and generated run snapshots:
  potentially unique experiment/session history; preserve until retention decided.
- Raman/vision raw images/data/results: scientific records, separate from cache.
- `.test_tmp/`, `__pycache__/`, pytest/Ruff caches: regenerable scratch candidates
  after confirming no active job needs them; no cleanup in Batch A inventory.
- `.claude/launch.json` and empty/local agent metadata: developer setup, not proof
  of scientific disuse; review independently rather than blanket-delete dotfolders.

## Conditions for a later move

1. Match current imports, path loading, registered versions/aliases, skill/config
   discovery, YAML includes, tests, docs and known operator consumers.
2. Demonstrate replacement features and exact NanoDrop motion parity where used.
3. Preserve original paths, hashes, source commit, reason and recovery procedure.
4. Preserve WORK settings, labware identities, calibration and experiment history.
5. Obtain relevant WORK physical evidence before retiring a physical method.
6. Exclude the later external archive from active discovery/imports/pytest.

No present assessment satisfies these future retirement gates by itself.
