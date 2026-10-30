"""Read-only consistency/hash verification for the Task 0.1 evidence."""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def main() -> None:
    rows = list(csv.DictReader((OUT / "files.csv").open(encoding="utf-8", newline="")))
    refs = list(csv.DictReader((OUT / "references.csv").open(encoding="utf-8", newline="")))
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    paths = {row["path"] for row in rows}
    assert len(paths) == len(rows) == summary["files"], "Missing/duplicate inventory rows"
    assert dict(Counter(row["classification"] for row in rows)) == summary["classifications"]
    assert dict(Counter(row["archive_assessment"] for row in rows)) == summary["archive_assessments"]
    assert len(refs) == summary["reference_edges"]
    assert dict(Counter(ref["kind"] for ref in refs)) == summary["reference_kinds"]
    assert all(ref["source"] in paths and ref["target"] in paths for ref in refs)
    assert all(row["archive_reason"] for row in rows)
    for row in rows:
        assert not row["path"].startswith("docs/reviews/")
        if row["read_status"] == "read":
            data = (ROOT / row["path"]).read_bytes()
            assert len(data) == int(row["bytes"]), row["path"]
            assert hashlib.sha256(data).hexdigest() == row["sha256"], row["path"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert head == summary["baseline_commit"], "Baseline HEAD changed"
    changed = subprocess.check_output(["git", "diff", "--name-only", "HEAD", "--"], cwd=ROOT, text=True).splitlines()
    assert all(path.startswith("docs/reviews/") for path in changed), changed
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, text=True).splitlines()
    assert all(path.startswith("docs/reviews/") for path in untracked), untracked
    tracked = set(subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines())
    tracked = {path for path in tracked if not path.startswith("docs/reviews/")}
    assert tracked == {row["path"] for row in rows if row["git_state"] == "tracked"}
    print(f"PASS: {len(rows)} file records and {len(refs)} references are internally consistent.")
    print("PASS: inventoried file hashes still match; no tracked application/config/protocol changes.")
    print("Not an application test, simulation, backup verification, or physical qualification.")


if __name__ == "__main__":
    main()
