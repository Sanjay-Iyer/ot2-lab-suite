# Task 0.4 implementation

`tests/test_gemini.py` now imports no application/provider during collection.
Its manual connectivity probe requires an explicit command-line opt-in; it was
not run. Robot-automation tests use per-test temporary protocol/key/record/deploy
paths and patch the consuming module aliases plus host resolution. Preflight
fixtures use a temporary directory. Vial template/log tests use temporary paths;
legacy build/validate/CV integration requires an explicitly marked disposable
checkout. Existing live-auth gates and stale assertions are intentionally retained
for baseline classification.

The task-local `run_baseline.py` creates a retained detached Git worktree and
copies current tracked bytes plus the new hygiene test. It also copies two ignored,
non-secret inputs the code requires: the page's three reference photos and a private
copy of the pinned simulator (no link back to the original). Other ignored data,
`.env` and local settings are not copied. It scrubs model/ADC/proxy/endpoint overrides,
disables dotenv/plugin autoload/bytecode/cache writing, and sets isolated ADC,
Opentrons and temporary paths. A stdlib sitecustomize guard loads before pytest
and approved simulator children; a pytest plugin blocks real model factories
before collection. An explicit startup assertion fails closed on guard errors.

The guard rejects non-loopback CPython socket DNS/connect/datagram operations,
shell/SSH/cloud/unreviewed Python processes and actual robot runner children.
Only reviewed builder/validator/mock-CV scripts and exact simulator/approval
verification code are allowed, with inherited guard environment. Mocked in-process
runner tests remain legitimate. Loopback fixtures are allowed; inherited proxy
settings are scrubbed. These are reviewed Python-code controls, **not OS egress
enforcement** or protection against arbitrary native/unreviewed code.

Tracked source and selected ignored settings/science/history roots are hashed
before and after; the final runner re-enumerates roots to detect additions too.
It excludes __pycache__, .pytest_tmp scratch dirs, links, `.venv`, `.env` and
unlisted external files. Recovery for the full original state remains Task0.2's
private verified backup. Integrity evidence is bounded by this declared coverage.

An earlier coordinator's mid-task reviewers cleared default collection after reading
the runner/guard and fixture changes. Python identified a template PROJECT_ROOT
mismatch; lab identified target escape and hash-coverage limitations. Corrections
include consistent temporary roots, checkout-relative test targets, reverse-DNS
blocking, broader re-enumerated scientific roots and precise coverage limits. Those
reviews have no surviving written record, so they are not counted as acceptance. The
final record is in `reviews.md`.

Results and the failure classification are in `baseline_report.md`.

R2 corrections from this task's reviews: both reviewers found two failures wrongly called outdated, now existing
defects. The guard records each blocked call with its test. The runner compares `git status --ignored` before and
after, copies cited output into `evidence/`, accepts the `llm` environment for WORK, and gains `--no-simulator` and
`--discard` so routine runs stop leaving large copies behind. `packet.py` now detects added files, an empty packet and a
changed revision. The hygiene test now checks that the Gemini probe refuses without `--connect`, and that the live guard
stops a real socket and process. `pytest.ini` no longer recurses into `.test_tmp` or `.venv`. The guard allows pinned
`opentrons.cli analyze`, which Task 0.5 needs.

No runtime liquid-handling behavior, WORK configuration or credentials changed.
Raman's separate test suite is not included by root pytest; its source/data stay
separate and protected. WORK instrument checks remain pending.
