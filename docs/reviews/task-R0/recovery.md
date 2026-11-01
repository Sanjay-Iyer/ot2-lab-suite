# R0.2 private recovery and exact restore

Private folder: `C:\code\opentrons_home\ot2-lab-suite-preservation\20261030_R0_recovery`.
It depends on the immutable verified original base in `20261030_task02_v2`.
Both archives are restricted to owner/SYSTEM/Administrators. Credentials and
private per-file digests remain outside Git. Preserve both folders together.

Main:70,948 file records across11 trees;52,089 overlay files and18,859 base references.
Supplement:9,093 scratch records,3,793 base references. Archive CRC/per-file hashes,
original base artifact hashes, complete43-ref Git bundle and unchanged initial
statuses/refs passed. The95 outer baseline logs/JUnit/summary files received a
separate source-to-ZIP hash audit;13,234 mutable files were rehashed.44 Git config,
HEAD, index and worktree-binding files are privately byte-verified too.

Main excludes cache folders and records zero noncache links. Supplement fills
readable `.test_tmp` coverage without following links:887 link targets have byte
coverage,875 are empty dirs,18 are recursively empty trees. The only76 unreadable
scratch paths exactly match Task 0.2's allowlist; content remains unknown and
sources remain intact. Empty directory/link metadata is private. No unknown
read error or meaningful uncovered target was accepted.

**Restore to a NEW, private, empty destination using the exact current-file
manifest. Do not extract the entire old base then overlay it.** For each current
record: copy only its named `base:` ZIP member or its overlay/supplement member;
verify size/hash. This preserves absence/deletion state instead of resurrecting
old root docs. Restore source-control history/refs from recovery.bundle, recover
HEAD/branch bindings deliberately, and reconcile staged/working patches/index
metadata. Never overwrite credentials, science or an existing checkout blindly.

`manifest.private.json`, `scratch-manifest.private.json`, private link audit,
`post-audit.private.json` and Git metadata manifest supply exact mappings. The
original base already passed full extracted restoration; R0 validated current
whitelist storage/content without creating another project checkout. Retained
original directories/refs provide additional recovery until candidate acceptance.

Coordinator test writers were stopped and reviewers were read-only. Unchanged
porcelain alone is insufficient for already-dirty files; the mutable-file rehash
and independent source/archive checks address this limitation. External WORK
machine/calibration/ADC state remains unavailable, WORK VALIDATION PENDING.
