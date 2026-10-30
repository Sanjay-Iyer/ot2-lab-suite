# Task 0.3 local implementation

Added one standard-library collector/comparer and six temporary-fixture checks.
It hashes maintained nonsecret configuration/labware files, reports the current
interpreter's distribution names/versions, records process-variable presence and
repository `.env` presence/stat only. It never reads `.env` or external credential
contents, imports the application, connects to a robot, or invokes an SDK. The
maintained settings roots must be known nonsecret before collection. Linked/reparse paths and
network roots are excluded/refused. Comparison detects settings drift and
rejects different role/root/schema reports; it is explicitly advisory.

The full original HOME state (including credentials, data, protocols, histories
and ignored local environments) remains in the private Task 0.2 verified backup.
The actual WORK counterpart is unavailable. `work_preservation.md` gives the
private collection, backup/restore, ownership and update reconciliation steps.
No WORK settings are fabricated and no deployment is switched.

The tracked robot configuration identifies OT2CEP20220929R02 and records API
2.15/software 7.0.2 with a July 2026 discovery observation. These are historical
repository settings, not a fresh observation. NanoDrop v19, other printing
profiles and their distinct heights/tip policies are retained without merging.
Calibration comments and older P300/camera guides do not qualify the current
P20 NanoDrop method. Raman remains separate and untouched.

Runtime code/configuration and all original source bytes remain unchanged.
HOME evidence describes HOME only. See tests.txt and independent review record.

R1 reviewers independently identified an unchecked intermediate `src` junction
and inconsistent password-directory exclusion. R2 checks each base ancestor
before descendant access and uses one filename/directory exclusion predicate.
New fixtures cover both. These heuristics supplement the maintained nonsecret
roots; they are not a general secret scanner.
