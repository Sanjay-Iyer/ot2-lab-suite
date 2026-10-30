# Task 0.3 — Preserve WORK-specific information

Inventory and retain robot identity, environments, cloud/network settings,
labware/offsets, records, generated protocols and calibration evidence without
committing credentials. No runtime/configuration changes are made in this task.

Local deliverables are a read-only offline evidence helper, temporary-fixture
checks, HOME settings inventory and a private WORK preservation procedure.
The helper uses only the standard library and distribution metadata; no SDK,
application, credential-content or network access. It detects drift when invoked;
it is not an update lock. Profiles/ownership enforcement belongs to Task 2.4.

Acceptance: local helper/procedure independently reviewed; all actual WORK
collection, recovery verification and update reconciliation explicitly remain
WORK VALIDATION PENDING. HOME mirrors cannot establish the WORK laptop state.
The operator authorizes continuing independent software tasks despite this
unavailable physical/machine evidence.
