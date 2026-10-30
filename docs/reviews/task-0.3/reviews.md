# Task 0.3 — independent reviews

Review records are outside the immutable implementation packet.

## R1 independent review

Python Engineer (`/root/python_reviewer`): **CHANGES REQUIRED**. Found an
intermediate `src` link bypass for `src/labware` and missing `password` directory
exclusion. Required regressions and a known-nonsecret settings-root contract.
R2 edits had started during Python's R1 hash check, so no unchanged-R1 packet
verification is claimed for that reviewer.

Laboratory Expert (`/root/lab_reviewer`): **CHANGES REQUIRED**. Independently
identified the same intermediate-link defect and corroborated the password-dir
finding. Verified R1 packet/all seven members, all402 HOME settings hashes,
all1196 original file hashes, robot/demo/four machine profiles/six custom labware
coverage. Confirmed methods untouched and honest WORK pending limitations.

## Cross-review and corrections

Both reviewers explicitly compared and agreed on both findings. Both required
precise wording: filename exclusions do not detect arbitrary secrets. Corrected
helper checks each base ancestor before descendants, shares one exclusion
predicate, and adds intermediate-link and nested password-canary regressions.
WORK procedure now requires confirming nonsecret settings roots before collection.
All six fixture checks passed in ai after the last code correction.

## R2 final independent rechecks

Packet: `07ab7986a4c8aa6ff7ba316b586bad39d3e799311f75f136c325c6aa5a83b8e3`.

Python Engineer: **PASS — local deliverables**. Independently verified all seven
members/aggregate, all402 HOME settings and all1196 unchanged original files;
rechecked both corrections/regressions and the precollection trust contract.
Reviewed coordinator's six-test/identity-comparison evidence; did not claim a
separate test execution. No new required corrections. WORK remains pending.

Laboratory Expert: **PASS — local deliverables**. Independently verified all
seven members/aggregate and402 HOME hashes; exercised read-only mocked probes
for intermediate-src non-traversal and password-directory pruning. Confirmed
the precise credential limitations and precollection trust contract. Explicit
cross-agreement with Python's final PASS. No method/config/labware changes.
Actual WORK backup/metadata/reconciliation/qualification remains pending.
