# Task 0.2 implementation

Local baseline branch/tag were created at source commit `1a4fd19` as approved.
The reviewed Task0.1 records were committed separately as `f8cfc5d`; no application
files changed. Task0.2 is isolated on `codex/task-0-2-preservation`.

The preservation helper writes only to a NEW private external folder. It creates
an all-refs Git bundle and an actual-working-files ZIP, without using Git ignore
rules to omit untracked/ignored source, data, local settings or installed
simulators. It verifies archive and per-file digests, extracts to a separate
private tree and rehashes it, and clones/fscks the bundle locally. An empty index
and unchanged HEAD are required; staged-only versions are not silently omitted.

The snapshot contains secrets and is not a shareable release package. No secret
values or individual secret-file digests are copied into these public records.
Original credentials are not modified. Backup ACLs restrict access to the owner,
SYSTEM and administrators. No network clone/push or physical command is used.

Preflight reviewers independently identified an overly broad cache-error rule.
It was replaced before execution by exact membership in an 82-path allowlist.
Any unexpected/non-cache read failure blocks preservation.

The first attempt stopped safely on a pytest `current` symlink. It remains
privately marked incomplete. The corrected helper records only `.test_tmp/`
links as metadata without following/recreating them; any non-scratch link fails.
The `_v2` snapshot is the recovery candidate. Inaccessible scratch contents are
not claimed backed up or empty. No original cache/data is deleted.

The operator's new standing authorization/two-reviewer process is recorded in
`../PROCESS.md`. The original master-plan input and accepted Task0.1 packet are
not rewritten. Full snapshot/recovery completion and final review results are
recorded separately once their checks finish.
