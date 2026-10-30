"""Run tests in a disposable Git worktree, retained unless --discard is given.

Activate conda ai (HOME) or llm (WORK) first. Only this coordinator invokes Git/worktree creation.
The child is guarded before collection. Original source/data are hash checked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

SOURCE = Path(__file__).resolve().parents[3]
PROTECTED_ROOTS = ("configs/workflows/user", "robot_data", "experiment_history",
                   "raman", "runs", "vision_runs", "vision/raw", "vision_tests/raw")
# Ignored but required, non-secret test inputs. Without them the page cannot start
# and pinned-simulator tests skip, which measures the checkout and not the code.
REQUIRED_IGNORED_FILES = ("visualization/96_well_plate.jpg", "visualization/paper.jpg",
                          "visualization/20ml_vial_rack.jpg")
PINNED_SIMULATOR = ".venv/ot2-api-2.15-py310"


def hashes(paths):
    return {path.relative_to(SOURCE).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


def protected_paths(tracked):
    paths = [SOURCE / name for name in tracked]
    for folder in PROTECTED_ROOTS:
        base = SOURCE / folder
        if base.exists():
            for current, dirs, files in os.walk(base, followlinks=False):
                dirs[:] = [name for name in dirs if name != "__pycache__" and
                           not name.startswith(".pytest_tmp") and
                           not (Path(current) / name).is_symlink()]
                paths.extend(Path(current) / name for name in files if
                             not (Path(current) / name).is_symlink())
    return paths


def git_status():
    """Also catches additions outside the hashed roots; collapsed ignored folders are not looked inside."""
    return subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=SOURCE, capture_output=True,
                          text=True, check=True).stdout.splitlines()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--work-auth-fixture", action="store_true",
                        help="Classify auth-gated tests: Vertex settings with a fake project. Model factories stay blocked.")
    parser.add_argument("--no-simulator", action="store_true",
                        help="Skip the private simulator copy, for targeted runs that do not need it.")
    parser.add_argument("--discard", action="store_true", help="Remove the disposable worktree afterward.")
    parser.add_argument("--evidence", type=Path, help="Copy junit, pytest output and summary into this folder.")
    parser.add_argument("targets", nargs="*", default=["tests"])
    args = parser.parse_args()
    for target in args.targets:
        path = Path(target.split("::", 1)[0])
        if path.is_absolute() or not (SOURCE / path).resolve().is_relative_to(SOURCE / "tests"):
            parser.error("Targets must be relative paths inside tests/")
    if os.environ.get("CONDA_DEFAULT_ENV") not in {"ai", "llm"}:
        raise SystemExit("Activate conda ai (HOME) or llm (WORK) before the offline baseline")
    tracked = subprocess.run(["git", "ls-files"], cwd=SOURCE, capture_output=True,
                             text=True, check=True).stdout.splitlines()
    # This task's new collection/guard regression is not committed yet.
    if "tests/test_offline_baseline_hygiene.py" not in tracked:
        tracked.append("tests/test_offline_baseline_hygiene.py")
    before = hashes(protected_paths(tracked))
    status_before = git_status()
    run = SOURCE / ".test_tmp" / ("b04-" + uuid.uuid4().hex[:8])
    run.mkdir(parents=True)
    checkout = run / "repo"
    subprocess.run(["git", "worktree", "add", "--detach", str(checkout), "HEAD"],
                   cwd=SOURCE, check=True, capture_output=True, text=True)
    # Copy current tracked bytes, including the reviewed pending fixture fixes.
    # Ignored data/.env/local settings are deliberately not copied.
    for name in tracked:
        path = SOURCE / name
        if path.is_file():
            destination = checkout / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    for name in REQUIRED_IGNORED_FILES:
        destination = checkout / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / name, destination)
    # A private copy at the path the code expects: no link back to the original
    # environment, so a later scratch cleanup cannot reach through it.
    if not args.no_simulator:
        shutil.copytree(SOURCE / PINNED_SIMULATOR, checkout / PINNED_SIMULATOR, symlinks=True)
    (checkout / ".ot2-disposable-baseline").write_text("Offline baseline only\n")
    guard = str(Path(__file__).with_name("offline_guard"))
    environment = os.environ.copy()
    for key in list(environment):
        if any(word in key.upper() for word in ("PROXY", "GOOGLE", "GEMINI", "VERTEX", "GCP_", "ROBOT_", "OT2_ROBOT", "LLM_", "CLOUDSDK", "APPROVAL_KEY")):
            environment.pop(key)
    environment.update({
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_DOTENV_DISABLED": "1", "OT2_BASELINE_ROOT": str(checkout),
        "OT2_RUN_ISOLATED_PIPELINE": "1", "LLM_PROVIDER": "api-key",
        "OT_API_CONFIG_DIR": str(run / "opentrons"),
        "CLOUDSDK_CONFIG": str(run / "gcloud"),
        "GOOGLE_APPLICATION_CREDENTIALS": str(run / "no-adc.json"),
        "PYTHONPATH": os.pathsep.join((guard, str(checkout))),
        "TEMP": str(run / "temp"), "TMP": str(run / "temp"),
    })
    if args.work_auth_fixture:
        environment.update({"LLM_PROVIDER": "vertexai", "GOOGLE_CLOUD_PROJECT": "offline-baseline-fixture"})
    (run / "temp").mkdir()
    # Explicit assertion fails closed if Python's optional sitecustomize startup
    # swallowed an import error. The guard itself uses stdlib only.
    command = [sys.executable, "-B", "-c", "import sitecustomize; assert sitecustomize.GUARD_ACTIVE; import pytest,sys; sys.exit(pytest.main(sys.argv[1:]))",
               *args.targets, "-q", "-p", "baseline_plugin", "-p", "no:cacheprovider",
               "--basetemp", str(run / "pytest"), "--junitxml", str(run / "junit.xml")]
    if args.collect_only:
        command.append("--collect-only")
    with (run / "pytest.txt").open("w", encoding="utf-8") as output:
        result = subprocess.run(command, cwd=checkout, env=environment, stdout=output,
                                stderr=subprocess.STDOUT, text=True)
    after = hashes(protected_paths(tracked))
    changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
    status_after = git_status()
    status_changed = sorted(set(status_before) ^ set(status_after))
    guards = [json.loads(p.read_text()) for p in run.glob("guard-*.json")]
    blocked_tests = sorted({record["test"].split(" ")[0] for guard_run in guards
                            for record in guard_run.get("records", []) if record["test"]})
    summary = {"source": str(SOURCE), "checkout": str(checkout), "python": sys.executable,
               "command": command, "exit_code": result.returncode,
               "work_auth_fixture": args.work_auth_fixture,
               "protected_files": len(before), "protected_changed": changed,
               "git_status_changed": status_changed, "tests_with_guard_blocks": blocked_tests,
               "protected_roots": PROTECTED_ROOTS,
               "protection_limits": "Tracked paths plus selected ignored science/config/history roots, re-enumerated after run. Excludes cache dirs __pycache__/.pytest_tmp*, links, .venv, .env and unlisted external files; full recovery is Task0.2 backup.",
               "guard_processes": guards,
               "limitations": "Reviewed Python code guards, not OS egress enforcement. No WORK or physical validation."}
    (run / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run": str(run), "exit_code": result.returncode, "protected_files": len(before),
                      "protected_changed": changed, "git_status_changed": status_changed}, indent=2))
    if args.evidence:
        args.evidence.mkdir(parents=True, exist_ok=True)
        for name in ("junit.xml", "pytest.txt", "summary.json"):
            shutil.copy2(run / name, args.evidence / name)
    if args.discard:
        subprocess.run(["git", "worktree", "remove", "--force", str(checkout)], cwd=SOURCE, check=True,
                       capture_output=True, text=True)
    return 2 if changed or status_changed else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
