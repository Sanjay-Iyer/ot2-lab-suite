"""Local preservation for cleanup batches; never deletes or restores source files."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT.parent / "ot2-lab-suite-archive" / "20261031"
EXCLUDED = {".git", ".venv", ".test_tmp", "__pycache__", ".pytest_cache", ".ruff_cache"}
KNOWN_LOCKED_SCRATCH = {f"raman/results/.pytest_tmp_20260728_{i:02d}" for i in range(2, 8)}


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def git(*args):
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(ROOT), *args])


def protect(directory):
    directory.mkdir(parents=True, exist_ok=False)
    user = os.environ["USERDOMAIN"] + "\\" + os.environ["USERNAME"]
    subprocess.run(["icacls", str(directory), "/inheritance:r", "/grant:r",
                    user + ":(OI)(CI)F", "SYSTEM:(OI)(CI)F", "Administrators:(OI)(CI)F"],
                   check=True, capture_output=True)


def store(batch, paths):
    directory = ARCHIVE / batch
    protect(directory)
    records = []
    with zipfile.ZipFile(directory / "files.zip", "x", zipfile.ZIP_STORED, allowZip64=True) as output:
        for source in sorted(set(Path(p).absolute() for p in paths)):
            info = source.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
                raise RuntimeError(f"Link must be separately reviewed: {source}")
            source.relative_to(ROOT)
            parent = source.parent
            while parent != ROOT:
                if parent.is_symlink() or parent.is_junction():
                    raise RuntimeError(f"Ancestor link must be separately reviewed: {parent}")
                parent = parent.parent
            path = source.resolve()
            relative = path.relative_to(ROOT).as_posix()
            digest = sha(path)
            output.write(path, relative)
            records.append({"path": relative, "sha256": digest, "bytes": info.st_size})
    with zipfile.ZipFile(directory / "files.zip") as saved:
        assert saved.testzip() is None
        for item in records:
            assert hashlib.sha256(saved.read(item["path"])).hexdigest() == item["sha256"]
            assert sha(ROOT / item["path"]) == item["sha256"]
    record = {"source_commit": git("rev-parse", "HEAD").decode().strip(),
              "files": records, "zip_sha256": sha(directory / "files.zip"),
              "restore": "Extract only selected named members into a NEW destination; compare hashes. Never overwrite a newer checkout."}
    (directory / "manifest.private.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"{batch}: {len(records)} source/archive hashes verified; CRC PASS")
    return directory


def baseline():
    paths = []
    excluded = []
    def fail(error):
        raise error
    for base, dirs, files in os.walk(ROOT, followlinks=False, onerror=fail):
        for name in list(dirs):
            p = Path(base) / name
            if name in EXCLUDED or p.relative_to(ROOT).as_posix() in KNOWN_LOCKED_SCRATCH:
                excluded.append(p.relative_to(ROOT).as_posix()); dirs.remove(name)
            elif p.is_symlink() or p.is_junction():
                raise RuntimeError(f"Unexpected noncache directory link: {p}")
        paths.extend(Path(base) / name for name in files if not name.endswith(".pyc"))
    directory = store("C0-before-cleanup", paths)
    (directory / "status-before.txt").write_bytes(git("status", "--porcelain=v1", "-uall"))
    (directory / "working.patch").write_bytes(git("diff", "--binary", "HEAD"))
    (directory / "worktrees.txt").write_bytes(git("worktree", "list", "--porcelain"))
    (directory / "exclusions.json").write_text(json.dumps(excluded, indent=2))
    git("bundle", "create", str(directory / "history.bundle"), "--all")
    subprocess.run(["git", "bundle", "verify", str(directory / "history.bundle")], check=True, capture_output=True)
    print("Git bundle verified. Excluded caches/worktrees and pinned environment retained in place; previous independent preservation snapshots also retained.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch")
    parser.add_argument("--paths-json", type=Path)
    args = parser.parse_args()
    if args.batch == "C0-before-cleanup":
        baseline()
    else:
        store(args.batch, [ROOT / p for p in json.loads(args.paths_json.read_text(encoding="utf-8"))])
