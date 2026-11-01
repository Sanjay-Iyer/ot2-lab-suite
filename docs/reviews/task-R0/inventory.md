# R0.1 inventory and recovery decisions

Initial root: clean `01_paper` at1a4fd19, matching origin. Eleven registered
worktrees: root, eight retained `.test_tmp/b04-*/repo` checkouts at0129290, and
`ot2-wt-phase1` / `ot2-wt-1-2` at1838b3a. No new ref or worktree was created.

| Work | Actual evidence | Recovery decision |
|---|---|---|
|0.1|f8cfc5d, accepted independent review records|Fast-forward accepted commit onto01_paper|
|0.2|a2f8dba, verified original backup and accepted reviews|Fast-forward accepted commit|
|0.3|0129290, local PASS/PASS, actual WORK pending|Fast-forward accepted commit|
|0.4|06c0bdd contains test hygiene and historical2,227 passed/25 failed/5 skipped evidence, no final reviews|Recover inspected paths as candidate; retire copying runner, review in place next|
|0.5|Same06c0bdd contains29 traces; packet lists20 missing data paths and four changed code hashes; no final reviews|Preserve all 29 unchanged; fix evidence/dry-run mismatch in0.5, never regenerate to conceal drift|
|1.1|1838b3a is sibling of06c0bdd, based on0129290;49 passes reported, no acceptance|Preserve exact patch and source commit; inactive until ordered review|
|1.2|15 tracked dirty files+2 new in external worktree, based on1838b3a; no staged changes|Preserve all17 privately; public patch contains15 source files, CV outputs remain private evidence|

Both external task branches point to1838b3a;1.1 worktree is clean. Eight baseline
worktrees are dirty (EOL noise, four fixture files, two runs' generated CV files).
They remain intact. Existing unrelated `bp`, `main`, `paper`, `vision`, Claude
branch, baseline tag/branch, remote tracking refs and old task refs are untouched.

Accepted-packet byte mismatches from Git blobs were independently explained by
LF/CRLF normalization; historical manifests/verdicts are not rewritten. Task 0.5
also has substantive differences and is explicitly unaccepted. Its dry-run fake
records five load events while analysis forces motion (506events); old20case/67
pass evidence does not validate the current29case corpus.

Historical0.4 JUnit was independently parsed:2,257 cases,2,227 passed,25 failed,5 skipped,
0 errors. The earlier2204/36/15 run lacks photos/pinned simulation and is superseded.
Eight auth failures are HOME refusals, not failed robot runs. Guard-blocked tests
include Windows platform probes; a passing guarded test does not prove network
success. Physical-height/clover/abort uncertainties remain unresolved.
