"""Local-only full working-tree preservation; standard library plus Git.

The caller creates a NEW, access-restricted destination outside the checkout.
No source files are modified. No application imports, cloud or robot calls.
The archive includes secrets: keep this folder private and never commit it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-c", "core.fsmonitor=false", *args], cwd=ROOT, text=True).strip()


def cache_path(path: Path) -> bool:
    allowed = json.loads(Path(__file__).with_name("cache_access_allowlist.json").read_text(encoding="utf-8"))["paths"]
    return path.relative_to(ROOT).as_posix() in allowed


def verify(destination: Path, *, restore: bool) -> dict:
    manifest = json.loads((destination / "manifest.private.json").read_text(encoding="utf-8"))
    archive = destination / "working-tree.zip"
    assert sha(archive) == manifest["archive_sha256"], "Archive digest mismatch"
    assert sha(destination / "source-history.bundle") == manifest["bundle_sha256"], "Bundle digest mismatch"
    recovered = destination / "restore-check"
    if restore:
        recovered.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None, "ZIP CRC check failed"
        assert set(z.namelist()) == {row["path"] for row in manifest["files"]}
        for row in manifest["files"]:
            relative = Path(row["path"])
            assert not relative.is_absolute() and ".." not in relative.parts
            digest = hashlib.sha256()
            target = recovered / relative
            if restore:
                target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(row["path"]) as src:
                if restore:
                    with target.open("xb") as dst:
                        for block in iter(lambda: src.read(1024 * 1024), b""):
                            digest.update(block)
                            dst.write(block)
                else:
                    for block in iter(lambda: src.read(1024 * 1024), b""):
                        digest.update(block)
            assert digest.hexdigest() == row["sha256"], row["path"]
            if restore:
                assert sha(target) == row["sha256"], row["path"]
    return {"files_verified": len(manifest["files"]), "bytes": sum(r["bytes"] for r in manifest["files"]),
            "restore_verified": restore, "excluded_cache_errors": len(manifest["cache_access_errors"]),
            "cache_links_recorded": len(manifest.get("cache_links", []))}


def create(destination: Path) -> dict:
    assert destination.is_dir(), "Caller must create the restricted backup folder"
    assert destination != ROOT and ROOT not in destination.parents, "Backup must be outside checkout"
    assert not any(destination.iterdir()), "Destination must be new and empty"
    baseline = git("rev-parse", "HEAD")
    if git("diff", "--cached", "--name-only"):
        raise RuntimeError("Staged-only versions require separate preservation; this snapshot requires an empty index")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    ignored = git("ls-files", "--others", "--ignored", "--exclude-standard", "--directory")
    subprocess.run(["git", "bundle", "create", str(destination / "source-history.bundle"), "--all"], cwd=ROOT, check=True)
    subprocess.run(["git", "bundle", "verify", str(destination / "source-history.bundle")], cwd=ROOT, check=True, capture_output=True)
    files = []
    cache_errors = []
    cache_links = []

    def record_cache_link(path: Path) -> None:
        relative = path.relative_to(ROOT).as_posix()
        if not relative.startswith(".test_tmp/"):
            raise RuntimeError(f"Non-scratch link requires explicit preservation: {relative}")
        cache_links.append({"path": relative, "target": os.readlink(path),
                            "note": "pytest scratch link metadata only; never followed or restored into project"})

    def walk_error(error: OSError) -> None:
        p = Path(error.filename)
        if cache_path(p):
            cache_errors.append(p.relative_to(ROOT).as_posix())
        else:
            raise error

    with zipfile.ZipFile(destination / "working-tree.zip", "x", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for parent, dirs, names in os.walk(ROOT, followlinks=False, onerror=walk_error):
            if Path(parent) == ROOT:
                dirs[:] = [d for d in dirs if d != ".git"]
            for d in list(dirs):
                if (Path(parent) / d).is_symlink():
                    record_cache_link(Path(parent) / d)
                    dirs.remove(d)
            for name in names:
                p = Path(parent) / name
                if p.is_symlink():
                    record_cache_link(p)
                    continue
                relative = p.relative_to(ROOT).as_posix()
                try:
                    before = sha(p)
                    z.write(p, relative)
                    if sha(p) != before:
                        raise RuntimeError(f"File changed during backup: {relative}")
                    files.append({"path": relative, "sha256": before, "bytes": p.stat().st_size})
                except OSError:
                    if cache_path(p):
                        # A write failure after adding a member is not safely skippable.
                        if relative in z.namelist():
                            raise
                        cache_errors.append(relative)
                    else:
                        raise
    manifest = {"baseline_commit": baseline, "source_root": str(ROOT), "files": files,
                "git_status_before": status, "ignored_paths_before": ignored.splitlines(),
                "refs_before": git("show-ref"), "cache_access_errors": cache_errors,
                "cache_links": cache_links,
                "archive_sha256": sha(destination / "working-tree.zip"),
                "bundle_sha256": sha(destination / "source-history.bundle")}
    (destination / "manifest.private.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    result = verify(destination, restore=True)
    if git("diff", "--cached", "--name-only") or git("rev-parse", "HEAD") != baseline:
        raise RuntimeError("Git index or HEAD changed during preservation")
    # Verify historical Git objects from the local bundle, without external network.
    git_restore = destination / "git-restore-check"
    subprocess.run(["git", "clone", "--no-checkout", str(destination / "source-history.bundle"), str(git_restore)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(git_restore), "fsck", "--full"], check=True, capture_output=True)
    assert subprocess.check_output(["git", "-C", str(git_restore), "rev-parse", "HEAD"], text=True).strip() == baseline
    result.update({"baseline_commit": baseline, "bundle_restore_verified": True, "backup_path": str(destination)})
    (destination / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["create", "verify"])
    p.add_argument("destination", type=Path)
    args = p.parse_args()
    result = create(args.destination.resolve()) if args.action == "create" else verify(args.destination.resolve(), restore=False)
    print(json.dumps(result, indent=2))
