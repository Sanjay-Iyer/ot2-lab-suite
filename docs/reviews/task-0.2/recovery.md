# Local recovery procedure

The verified snapshot, when its `verification.json` reports success, is at:

`C:\code\opentrons_home\ot2-lab-suite-preservation\20261030_task02_v2`

It is private and outside the checkout. It contains local secrets and scientific
data. Never add it to Git, share it as a public artifact, or copy its `.env` to WORK.
Folder access is restricted to the current Windows account, SYSTEM and administrators.

- `source-history.bundle`: all reachable local Git refs/history, including the
  baseline branch/tag and reviewed inventory checkpoint.
- `working-tree.zip`: actual readable files, including ignored/untracked files,
  scientific data, local settings and installed project simulator environments.
- `manifest.private.json`: file hashes/paths, refs/status, ignored inventory,
  cache access exclusions and scratch-link metadata. Keep private.
- `restore-check/`: extracted files verified against every recorded digest.
- `git-restore-check/`: local bundle clone with verified HEAD and Git fsck.
- `verification.json`: non-secret aggregate verification result.

To recheck the archive without touching the project:

```powershell
python -B docs/reviews/task-0.2/preserve.py verify C:\code\opentrons_home\ot2-lab-suite-preservation\20261030_task02_v2
```

Actual recovery must start in a NEW private destination, never by overwriting the
current checkout. Clone the bundle locally there, check the baseline commit, and
overlay only the required verified working files from the snapshot. Preserve any
newer work first. No reset, clean, deletion or automatic original-tree overwrite is
part of this task. A copied installed environment is byte-preserved, not a claim
that it runs at a different absolute path.

The first attempt, `20261030_task02`, stopped on a pytest scratch symlink. It is
retained privately as **INCOMPLETE — DO NOT USE FOR RECOVERY**. Only `_v2` may be
treated as verified after its completion marker and hash checks pass.

Known limits: 82 exact inaccessible test-scratch directories are recorded in
`cache_access_allowlist.json`; their contents are not backed up or claimed empty.
Pytest links under `.test_tmp/` are metadata-only, not followed/recreated. Any
unexpected inaccessible data or non-scratch symlink causes preservation to fail.
There is no authorization to delete excluded caches. Actual WORK machine state
is unavailable on HOME and is separately WORK VALIDATION PENDING.
