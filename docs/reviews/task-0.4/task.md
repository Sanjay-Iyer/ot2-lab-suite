# Task 0.4 — Controlled baseline tests

Run existing tests on HOME in conda ai, without unintended model or hardware
calls. Remove Gemini's import-time connectivity request, isolate destructive
test fixtures, record actual test outcomes and classify baseline failures.
Do not fix application defects or weaken live authorization to obtain green tests.

Scope: four legacy test modules, a collection/guard regression test, task-local
disposable-checkout runner/guards and documented results. Production application,
protocols and maintained scientific configuration remain unchanged.

Acceptance: reproducible collection and baseline results, documented failure/skip
classification, no unintended model/hardware calls, integrity check for declared
source/scientific coverage, independent Python/laboratory review and cross-review.
Physical qualification remains WORK VALIDATION PENDING.
