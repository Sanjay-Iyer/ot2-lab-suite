# Practical reorganization — 2026-10-31

This work follows the operator's newer lightweight organization request,
superseding the earlier multi-phase implementation/review process. Development
stays on `01_paper`; existing commits and uncommitted recovery work are retained.
No earlier approval/run-gate patches were activated.

## Changes

- Kept the primary application under `src/agents/dye_demo/`, with an explicit
  architecture map and shared `tools.py` for manual YAML workflow construction.
- Moved the existing skill loader into `skills/__init__.py` without changing its
  import path or bytes. Runtime Markdown stays in the root shared skills folder.
- Moved the existing YAML history store unchanged into `memory/__init__.py`.
  Kept the former public imports in `experiment_memory.py`; existing history is
  neither migrated nor rewritten.
- Added YAML-driven manual dilution/printing entry points and HOME/WORK launcher
  profiles, reusing the current compiler, builder, simulator and GUI/session.
  Added one optional base-YAML reference using the existing merge helper.
- Exported the one-well manual test's settings unchanged into YAML and added an
  offline preview; retained its existing physical protocol and LIVE prompt.
- Fixed a standalone builder import cycle using lazy imports in
  `src/printing/artifacts.py`. The combined test process had hidden this cycle.
- Archived four already historical agent programs and two superseded documents
  byte-for-byte. Added a root README, concise documentation index, architecture,
  configuration and copyable command guides. Existing detailed guides retain
  their paths. No broad protocol relocation or framework replacement occurred.

## Preservation and checks

Existing full private preservation snapshots remain available. Before this work,
files being edited/moved were also copied under the ignored local directory
`.test_tmp/agent-reorganization-preserve-08fd5408cc/`. Its private comparison
manifest covers 459 existing configuration, labware, robot-data and credential
files; secret contents/hashes are not published here.

Focused checks cover manual builds from fresh Python processes, YAML references
outside the working directory, memory compatibility, skills and the existing
fake-OT-2 protocol regression tests. CLI builds/previews, offline terminal launch,
NiceGUI loopback startup, and two pinned local simulations were also exercised.
Final results:

- **41 focused tests passed** in 3.75 s, including two fresh-process CLI builds,
  the existing 22 fake-protocol regressions and existing skill-loading checks.
- Nine standalone CLI import/build/preview/summary commands exited 0.
- Offline terminal launched and quit with exit 0. NiceGUI returned HTTP 200 and
  the expected page title on loopback; the test server was then stopped.
- Both representative manual workflows passed the installed local Opentrons
  simulator using `.venv/ot2-api-2.15-py310/python.exe`.
- All **459 pre-existing protected files** remained byte-identical. Eight
  moved/archived source/document copies match their saved originals exactly.
  The one-well configuration matches its former Python defaults; protocol source
  differs only in the documentation path directing operators to YAML.
- Twenty changed/new Python files parsed; twelve current documentation pages
  had no missing local link targets. Current reorganization diffs pass whitespace
  checks. A pre-existing trailing-space line in the old master-plan diff is
  retained without modifying that earlier work.

## Boundaries and remaining WORK verification

Physical execution, instrument connection/SSH diagnostics, Vertex AI/gcloud ADC,
real deck geometry, liquid behavior and stopped-run recovery were not tested.
The WORK launcher is a new profile for the existing backend, not a promotion of a
replacement robot engine. No robot run, deployment, remote push, credential edit,
scientific-data deletion, new branch or worktree was performed.

Primary user-facing paths have documented YAML profiles. Specialized historical
smoke/alignment/camera helpers retain their original CLI/embedded settings and
existing shared robot configuration; they were not all converted or revalidated.
NanoDrop's factor series is stock-to-well dilution. Serial well-to-well dilution
is not added; the older generator is a fixed-transfer example. These limitations
are explicit rather than presented as completed experimental capabilities.

The earlier R0/baseline/golden candidates remain preserved and unaccepted. This
reorganization does not certify the entire repository or close the old plan.
