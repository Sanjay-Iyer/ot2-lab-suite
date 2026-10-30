"""Read-only, offline settings evidence. Never import the application or an SDK.

This is an advisory inventory, not an update lock or a credential backup.
Use the private full-state preservation procedure for recovery.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import sys

SCHEMA = 1
ROOTS = ("configs", "labware", "src/labware")
SUFFIXES = {".yaml", ".yml", ".json", ".py"}
PACKAGES = ("opentrons", "pydantic", "numpy", "pytest", "nicegui", "PyYAML",
            "langchain", "langchain-google-genai", "langchain-google-vertexai")
PRESENCE_KEYS = ("GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
                 "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "LLM_PROVIDER",
                 "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                 "http_proxy", "https_proxy", "all_proxy", "no_proxy")


def excluded(name: str) -> bool:
    return name.startswith(".") or any(
        word in name.lower() for word in ("secret", "credential", "token", "private", "password")
    )


def is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 1024)


def local_root(value: str) -> Path:
    path = Path(value).absolute()
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("Network roots are not inspected")
    # Check every ancestor before resolving; do not follow directory junctions.
    for part in (*reversed(path.parents), path):
        if is_link(part.lstat()):
            raise ValueError("Linked roots are not inspected")
    if not path.is_dir():
        raise ValueError("Repository root is not a directory")
    return path


def presence(path: Path) -> dict:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"status": "missing"}
    except OSError:
        return {"status": "inaccessible"}
    if is_link(info):
        return {"status": "link-not-inspected"}
    return {"status": "present", "size": info.st_size, "mtime_ns": info.st_mtime_ns}


def collect(root: Path, role: str) -> dict:
    if role not in {"HOME", "WORK"}:
        raise ValueError("Declare HOME or WORK explicitly")
    root = local_root(str(root))
    files, issues = {}, []
    for folder in ROOTS:
        base = root / folder
        # Check each component before touching descendants. A src junction can
        # otherwise escape even when src/labware is an ordinary directory.
        blocked = False
        current = root
        for component in Path(folder).parts:
            current = current / component
            try:
                info = current.lstat()
            except FileNotFoundError:
                issues.append({"path": folder, "status": "missing"})
                blocked = True
                break
            except OSError:
                issues.append({"path": folder, "status": "inaccessible"})
                blocked = True
                break
            if is_link(info):
                issues.append({"path": current.relative_to(root).as_posix(), "status": "link-not-inspected"})
                blocked = True
                break
        if blocked:
            continue
        def error(exc: OSError) -> None:
            issues.append({"path": str(Path(exc.filename).relative_to(root)),
                           "status": "inaccessible"})
        for current, dirs, names in os.walk(base, followlinks=False, onerror=error):
            # Exclude credential-looking paths even inside these nonsecret roots.
            for name in list(dirs):
                child = Path(current) / name
                if excluded(name):
                    dirs.remove(name)
                elif is_link(child.lstat()):
                    dirs.remove(name)
                    issues.append({"path": child.relative_to(root).as_posix(), "status": "link-not-inspected"})
            for name in sorted(names):
                path = Path(current) / name
                if excluded(name) or path.suffix.lower() not in SUFFIXES:
                    continue
                key = path.relative_to(root).as_posix()
                try:
                    if is_link(path.lstat()):
                        issues.append({"path": key, "status": "link-not-inspected"})
                    else:
                        digest = hashlib.sha256()
                        with path.open("rb") as handle:
                            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                                digest.update(chunk)
                        files[key] = digest.hexdigest()
                except OSError:
                    issues.append({"path": key, "status": "inaccessible"})
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"schema": SCHEMA, "declared_role": role, "root": str(root),
            "python": {"version": sys.version.split()[0], "executable": sys.executable},
            "packages": packages, "files": dict(sorted(files.items())), "issues": issues,
            "credential_evidence": {"repo_dotenv": presence(root / ".env")},
            "process_variable_presence": {key: key in os.environ for key in PRESENCE_KEYS},
            "external_credentials": "Not inspected: SSH, ADC and env-specified paths; private backup required",
            "limitations": "Declared role only; HOME is not WORK evidence. Presence/stat cannot prove credential integrity. No network or physical validation."}


def compare(before: dict, after: dict) -> dict:
    for key in ("schema", "declared_role", "root"):
        if before.get(key) != after.get(key) or before.get(key) is None:
            raise ValueError(f"Incompatible evidence: {key}")
    old, new = before["files"], after["files"]
    return {"changed": sorted(k for k in old.keys() & new.keys() if old[k] != new[k]),
            "missing": sorted(old.keys() - new.keys()), "added": sorted(new.keys() - old.keys()),
            "environment_changed": any(before.get(k) != after.get(k) for k in
                                       ("python", "packages", "credential_evidence", "process_variable_presence")),
            "inspection_issues": before.get("issues", []) + after.get("issues", []),
            "limitations": "Advisory comparison; private backup and operator reconciliation required before a WORK update."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("collect")
    inventory.add_argument("--root", required=True)
    inventory.add_argument("--role", choices=("HOME", "WORK"), required=True)
    inventory.add_argument("--output", required=True)
    diff = commands.add_parser("compare")
    diff.add_argument("before")
    diff.add_argument("after")
    args = parser.parse_args()
    if args.command == "collect":
        report = collect(Path(args.root), args.role)
        with Path(args.output).open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
        print(f"Collected {len(report['files'])} nonsecret settings files; {len(report['issues'])} inspection issues; declared role {args.role}")
        return 0
    result = compare(json.loads(Path(args.before).read_text(encoding="utf-8")),
                     json.loads(Path(args.after).read_text(encoding="utf-8")))
    print(json.dumps(result, indent=2))
    return int(bool(result["changed"] or result["missing"] or result["added"] or
                    result["environment_changed"] or result["inspection_issues"]))


if __name__ == "__main__":
    raise SystemExit(main())
