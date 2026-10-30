"""Write or verify a task's review packet: SHA-256 of each named file plus an aggregate.

    python -B docs/reviews/packet.py write docs/reviews/task-0.4 R1 tests/test_gemini.py ...
    python -B docs/reviews/packet.py verify docs/reviews/task-0.4

Every file in the task folder is a member, except the manifest and the review records written after the packet.
Files elsewhere in the repo are named on the command line. Verify fails on a changed, missing or added member,
an empty packet, or a changed revision.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SKIP_NAMES = {"packet_manifest.json", "reviews.md", "final_verdict.md"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(revision: str, files: list[dict[str, str]]) -> str:
    payload = json.dumps({"revision": revision, "files": files}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def folder_members(folder: Path) -> list[str]:
    return sorted(path.resolve().relative_to(ROOT).as_posix() for path in folder.rglob("*")
                  if path.is_file() and path.name not in SKIP_NAMES and "__pycache__" not in path.parts)


def write(folder: Path, revision: str, extra: list[str]) -> str:
    names = sorted(set(folder_members(folder)) | {(ROOT / name).resolve().relative_to(ROOT).as_posix() for name in extra})
    files = [{"path": name, "sha256": digest(ROOT / name)} for name in names]
    if not files:
        raise SystemExit("refusing to write an empty packet")
    value = aggregate(revision, files)
    manifest = {"revision": revision, "files": files, "aggregate_sha256": value}
    (folder / "packet_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return value


def verify(folder: Path) -> list[str]:
    manifest = json.loads((folder / "packet_manifest.json").read_text(encoding="utf-8"))
    files = manifest.get("files") or []
    if not files:
        return ["empty packet"]
    problems = [item["path"] for item in files
                if not (ROOT / item["path"]).is_file() or digest(ROOT / item["path"]) != item["sha256"]]
    listed = {item["path"] for item in files}
    problems += [f"not in manifest: {name}" for name in folder_members(folder) if name not in listed]
    if aggregate(manifest.get("revision", ""), files) != manifest.get("aggregate_sha256"):
        problems.append("aggregate_sha256")
    return problems


if __name__ == "__main__":
    action, target = sys.argv[1], ROOT / sys.argv[2]
    if action == "write":
        print(write(target, sys.argv[3], sys.argv[4:]))
    elif action == "verify":
        issues = verify(target)
        print("OK" if not issues else "\n".join(issues))
        raise SystemExit(1 if issues else 0)
    else:
        raise SystemExit("usage: packet.py write <folder> <revision> [files...] | verify <folder>")
