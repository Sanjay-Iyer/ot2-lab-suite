"""Private incremental recovery snapshot. No checkout, cleanup or application import.

The verified Task0.2 base plus this overlay restore readable current files.
All registered worktrees are captured before any integration. Cache links are
recorded, not followed. Private manifests may contain credential file hashes.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT.parent / "ot2-lab-suite-preservation/20261030_task02_v2"
DEST = ROOT.parent / "ot2-lab-suite-preservation/20261030_R0_recovery"


def git(root, *args):
    return subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args],
                          check=True, capture_output=True).stdout


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 1024)


def main():
    if not (BASE / "verification.json").is_file():
        raise RuntimeError("Verified private base is required")
    original = json.loads((BASE / "manifest.private.json").read_text(encoding="utf-8"))
    # Verify the complete immutable base artifacts before depending on them.
    if digest(BASE / "working-tree.zip") != original["archive_sha256"]:
        raise RuntimeError("Base archive changed")
    if digest(BASE / "source-history.bundle") != original["bundle_sha256"]:
        raise RuntimeError("Base bundle changed")
    prior = {item["path"]: item for item in original["files"]}
    DEST.mkdir(exist_ok=False)
    identity = os.environ.get("USERDOMAIN", ".") + "\\" + os.environ["USERNAME"]
    subprocess.run(["icacls", str(DEST), "/inheritance:r", "/grant:r",
                    identity + ":(OI)(CI)F", "SYSTEM:(OI)(CI)F",
                    "Administrators:(OI)(CI)F"], check=True, capture_output=True)
    worktrees = []
    for line in git(ROOT, "worktree", "list", "--porcelain").decode().splitlines():
        if line.startswith("worktree "):
            worktrees.append(Path(line[9:]))
    initial = {str(path): git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all").hex()
               for path in worktrees}
    refs = git(ROOT, "show-ref").decode()
    git(ROOT, "bundle", "create", str(DEST / "recovery.bundle"), "--all")
    git(ROOT, "bundle", "verify", str(DEST / "recovery.bundle"))
    records, links, exclusions = [], [], []
    with zipfile.ZipFile(DEST / "overlay.zip", "x", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for number, tree in enumerate(worktrees):
            label = "root" if tree == ROOT else "worktree-" + str(number)
            archive.writestr(label + "/status.hex", initial[str(tree)])
            for name, words in (("head.txt", ("rev-parse", "HEAD")),
                                ("working.patch", ("diff", "--binary", "HEAD")),
                                ("staged.patch", ("diff", "--binary", "--cached"))):
                archive.writestr(label + "/" + name, git(tree, *words))
            def error(exc):
                rel = Path(exc.filename).relative_to(tree).as_posix()
                if tree == ROOT and rel in original["cache_access_errors"]:
                    exclusions.append({"tree": str(tree), "path": rel, "reason": "existing inaccessible test cache"})
                else:
                    raise exc
            for current, dirs, names in os.walk(tree, followlinks=False, onerror=error):
                for name in list(dirs):
                    child = Path(current) / name
                    rel = child.relative_to(tree).as_posix()
                    if name == ".git" or name in {"__pycache__", ".pytest_cache", ".test_tmp"} or name.startswith(".pytest_tmp"):
                        dirs.remove(name)
                        exclusions.append({"tree": str(tree), "path": rel, "reason": "cache or Git metadata; registered worktrees separately protected"})
                    elif linked(child):
                        dirs.remove(name)
                        links.append({"tree": str(tree), "path": rel, "target": os.readlink(child)})
                for name in sorted(names):
                    path = Path(current) / name
                    rel = path.relative_to(tree).as_posix()
                    if name == ".git":
                        continue
                    if linked(path):
                        links.append({"tree": str(tree), "path": rel, "target": os.readlink(path)})
                        continue
                    sha = digest(path)
                    record = {"tree": str(tree), "path": rel, "sha256": sha, "bytes": path.stat().st_size}
                    if tree == ROOT and rel in prior and sha == prior[rel]["sha256"]:
                        record["storage"] = "base:" + rel
                    else:
                        record["storage"] = label + "/files/" + rel
                        archive.write(path, record["storage"])
                        if digest(path) != sha:
                            raise RuntimeError("Source changed during backup")
                    records.append(record)
            # Preserve non-repo run logs/JUnit/summary for registered baseline copies.
            if tree.parent.name.startswith("b04-"):
                for path in tree.parent.iterdir():
                    if path.is_file():
                        archive.write(path, label + "/baseline-evidence/" + path.name)
            print("Protected tree", number + 1, "of", len(worktrees), flush=True)
    for tree in worktrees:
        if git(tree, "status", "--porcelain=v1", "-z", "--untracked-files=all").hex() != initial[str(tree)]:
            raise RuntimeError("Worktree status changed during snapshot")
    if git(ROOT, "show-ref").decode() != refs:
        raise RuntimeError("Refs changed during snapshot")
    with zipfile.ZipFile(DEST / "overlay.zip") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Overlay CRC failure")
        for record in records:
            if not record["storage"].startswith("base:"):
                h = hashlib.sha256()
                with archive.open(record["storage"]) as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        h.update(chunk)
                if h.hexdigest() != record["sha256"]:
                    raise RuntimeError("Overlay content mismatch")
    manifest = {"base": str(BASE), "source": str(ROOT), "worktrees": [str(p) for p in worktrees],
                "refs": refs, "status_before_hex": initial, "files": records,
                "links_metadata_only": links, "cache_exclusions": exclusions,
                "overlay_sha256": digest(DEST / "overlay.zip"),
                "bundle_sha256": digest(DEST / "recovery.bundle")}
    (DEST / "manifest.private.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    summary = {"status": "verified", "worktrees": len(worktrees), "files": len(records),
               "overlay_files": sum(not x["storage"].startswith("base:") for x in records),
               "base_files": sum(x["storage"].startswith("base:") for x in records),
               "links_metadata_only": len(links), "cache_exclusions": len(exclusions),
               "archive_crc_and_file_sha": "PASS", "base_artifact_hashes": "PASS",
               "bundle_verify": "PASS", "source_status_refs_unchanged": "PASS"}
    (DEST / "verification.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
