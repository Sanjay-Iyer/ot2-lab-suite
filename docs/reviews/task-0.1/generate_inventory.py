"""Reproduce Task 0.1 inventory using only standard-library, read-only inspection.

Writes evidence only beside this script. Never imports repository modules,
runs tests/builds, or contacts a robot/model/network service. Known secret paths
are excluded; this is not a general secret detector.
Static references are evidence, not a proof that an unreferenced file is unused.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import posixpath
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
EVIDENCE_PREFIX = "docs/reviews/"
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".ini", ".txt", ".ps1"}
LEGACY_PROTOCOLS = {
    "01_vial_dilution_paper_print.py", "02_vial_dilution_paper_print_p20_dilution.py",
    "03_vial_dilution_paper_print_v3.py", "04_vial_dilution_paper_print_v4_quicktest.py",
    "06_vial_dilution_paper_print_v6_p20only.py", "07_paper_print_v7_print_only.py",
    "08_vial_direct_paper_print_v8.py",
}
OLDER_FRONTENDS = {
    "scripts/experiment_agent.py", "scripts/11_experiment_agent.py",
    "scripts/11_run_simulation_matrix.py", "scripts/11_test_agent_configs.py",
    "src/agents/main.py", "src/agents/vial_print_agent.py", "src/agents/vial_print_tools.py",
}


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-c", "core.fsmonitor=false", *args], cwd=ROOT).decode("utf-8")


def secret(path: str) -> bool:
    parts = Path(path).parts
    return Path(path).name == ".env" or any(p in {"keys", ".aws"} for p in parts)


def classification(path: str) -> tuple[str, str, str, str]:
    """Class, role, archival assessment, and reason for the assessment."""
    name = Path(path).name
    if path.startswith(("archive/", "src/agents/archive/")):
        return "historical", "historical implementation/evidence", "existing_historical", "Preserve; check consumers and archive manifest before any further move."
    if path.startswith("src/protocols/generated/") or path.startswith("configs/generated/"):
        return "generated", "generated protocol/configuration", "record_retention_review", "May be a runner target or historical experiment; not disposable solely because generated."
    if path.startswith("robot_data/"):
        return "historical", "local robot mirror/configuration/calibration evidence", "record_retention_review", "Preserve identity/calibration/history; current code also uses paths in this tree."
    if path.startswith("raman/"):
        role = "independent Raman analysis"
        if "/raw/" in path or "/results/" in path or "/plates/" in path:
            role = "Raman scientific inputs/results/layouts"
        return "shared", role, "not_proposed", "Independent analysis domain; preserve scientific data and numerical behavior."
    if path.startswith("src/protocols/printing/") and name in LEGACY_PROTOCOLS:
        return "shared", "deprecated but builder-compatible protocol", "after_replacement_review", "Registry versions 1-8 remain buildable; requires consumer/config/physical-method replacement coverage."
    if path in OLDER_FRONTENDS:
        return "shared", "older/compatibility workflow frontend", "after_replacement_review", "Different supported paths may depend on it; replacement and dynamic-discovery review required."
    if path.startswith(("vision/", "vision_tests/")):
        return "shared", "manual vision acquisition/analysis or study evidence", "not_proposed", "Not in NanoDrop v19 imaging path; still separate manual tooling/evidence."
    if path.startswith("experiment_01/"):
        return "historical", "ground truth, experiment artifacts and staged audits", "record_retention_review", "Retain regression fixtures and reproducibility evidence; not cache."
    if path == "src/protocols/printing/13_ai_agent_dilution_print_demo.py":
        return "active", "NanoDrop v19 deterministic robot executor", "not_proposed", "Registry-selected main-demo source protocol; preserve method/motion until qualified replacement."
    if path in {"scripts/ai_dye_demo_redteam.py", "scripts/check_ai_dye_demo_sop.py"}:
        return "active", "NanoDrop red-team/SOP verification entry point", "not_proposed", "Current verification tooling; inventory does not establish production-route coverage or passing tests."
    if path.startswith("src/agents/dye_demo/") or path in {"scripts/ai_dye_demo.py", "scripts/ai_dye_demo_gui.py"}:
        return "active", "NanoDrop implementation/launcher", "not_proposed", "Preserve complete current demo, including semantic and legacy branches, until verified replacement."
    if path.startswith("skills/nanodrop-") or path == "configs/workflows/defaults/ai_agent_dilution_print_demo.yaml":
        return "active", "NanoDrop runtime skill/default configuration", "not_proposed", "Runtime-read configuration/procedural knowledge; file discovery is an active dependency."
    if path.startswith("docs/ai_dye_demo/") or path.startswith("tests/test_ai_dye_demo") or path.startswith("tests/conversation_regressions/"):
        return "active", "NanoDrop documentation/regression evidence", "not_proposed", "Retain; historical claims and semantic-vs-legacy test routing need separate baseline assessment."
    if path.startswith(("src/printing/", "src/sers_engine/", "src/labware/", "src/core/", "src/utils/", "src/lab/")):
        return "shared", "planning/validation/transport/configuration infrastructure or parallel workflow", "not_proposed", "Candidate for reuse/consolidation, not safe removal; imports and dynamic entry points matter."
    if path.startswith(("configs/", "labware/", "skills/", "tests/", "scripts/", "src/protocols/", "src/agents/", "examples/", "docs/")):
        return "shared", "configuration, tooling, tests, documentation or supporting workflow", "not_proposed", "Retain pending specific consumer/feature review; no zero-reference deletion inference."
    if path in {"AGENTS.md", "AI_AGENTS_SKILLS_OVERVIEW.md", "ACTIVE_WORKFLOWS.md", "README_PATHS.md", "requirements.txt", "requirements-ot2-api-2.15.txt", ".gitignore", ".env.template", "pytest.ini", "conftest.py"}:
        return "shared", "repository guidance/environment/test configuration", "not_proposed", "Preserve local/workstation distinction and test/import boundaries."
    return "unknown", "unclassified path requiring manual assessment", "not_proposed", "Unknown is not evidence of disuse; do not archive without assessment."


def module_name(path: str) -> str:
    name = path[:-3].replace("/", ".")
    return name[:-9] if name.endswith(".__init__") else name


def main() -> None:
    baseline = git("rev-parse", "HEAD").strip()
    tracked = set(git("ls-files", "-z").split("\0")) - {""}
    untracked = set(git("ls-files", "--others", "--exclude-standard", "-z").split("\0")) - {""}
    paths = sorted(p for p in tracked | untracked if not p.startswith(EVIDENCE_PREFIX))
    modules = {module_name(p): p for p in paths if p.endswith(".py")}
    # Explicit package initializers take precedence over a same-named .py file.
    modules.update({module_name(p): p for p in paths if p.endswith("/__init__.py")})
    basenames: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        basenames[Path(path).name].append(path)
    unique = {name: values[0] for name, values in basenames.items() if len(values) == 1 and len(name) >= 8}
    name_re = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(n) for n in sorted(unique, key=len, reverse=True)) + r")(?![A-Za-z0-9_])")
    path_re = re.compile(r"(?:[A-Za-z0-9_.-]+[/\\])+[A-Za-z0-9_.-]+\.(?:py|md|yaml|yml|json|toml|ini|txt|ps1)")
    path_lookup = {p.casefold(): p for p in paths}
    edges: set[tuple[str, str, str, int]] = set()
    issues = []
    rows = []
    text_by_path = {}

    def edge(source: str, target: str, kind: str, line: int) -> None:
        if source != target:
            edges.add((source, target, kind, line))

    for path in paths:
        file = ROOT / path
        category, role, candidate, reason = classification(path)
        row = dict(path=path, git_state="tracked" if path in tracked else "untracked", classification=category, role=role, archive_assessment=candidate, archive_reason=reason)
        if secret(path) or file.is_symlink():
            row.update(bytes="", sha256="NOT_READ", read_status="excluded_sensitive_or_symlink")
            rows.append(row)
            continue
        try:
            data = file.read_bytes()
        except OSError as exc:
            row.update(bytes="", sha256="", read_status=str(exc))
            issues.append(dict(path=path, kind="read_error", detail=str(exc)))
            rows.append(row)
            continue
        row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), read_status="read")
        rows.append(row)
        if file.suffix not in TEXT_SUFFIXES or len(data) > 2_000_000:
            continue
        text = data.decode("utf-8-sig", errors="replace")
        text_by_path[path] = text
        if file.suffix == ".py":
            try:
                tree = ast.parse(text, filename=path)
            except SyntaxError as exc:
                issues.append(dict(path=path, kind="syntax_error", line=exc.lineno, detail=exc.msg))
                continue
            current = module_name(path)
            package = current if path.endswith("/__init__.py") else current.rpartition(".")[0]
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in modules:
                            edge(path, modules[alias.name], "python_import", node.lineno)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        parts = package.split(".") if package else []
                        base = ".".join(parts[:len(parts) - node.level + 1])
                        base = ".".join(x for x in (base, node.module) if x)
                    else:
                        base = node.module or ""
                    if base in modules:
                        edge(path, modules[base], "python_import", node.lineno)
                    for alias in node.names:
                        target = base + "." + alias.name
                        if target in modules:
                            edge(path, modules[target], "python_import", node.lineno)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 500:
                    literal = node.value.replace("\\", "/")
                    literal = re.sub("/+", "/", literal)
                    if literal in paths:
                        edge(path, literal, "python_path_literal", node.lineno)
                    elif node.value in unique:
                        edge(path, unique[node.value], "python_unique_filename_literal", node.lineno)
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in path_re.finditer(line):
                literal = re.sub("/+", "/", match.group().replace("\\", "/"))
                candidates = (literal, posixpath.normpath(posixpath.join(posixpath.dirname(path), literal)))
                for candidate in candidates:
                    target = path_lookup.get(candidate.casefold())
                    if target:
                        edge(path, target, "text_path_reference", lineno)
            for match in name_re.finditer(line):
                edge(path, unique[match.group(1)], "text_unique_filename", lineno)

    incoming = defaultdict(Counter)
    incoming_sources: dict[str, set[str]] = defaultdict(set)
    for source, target, kind, line in edges:
        incoming[target][kind] += 1
        incoming_sources[target].add(source)
    for row in rows:
        row["incoming_python_imports"] = incoming[row["path"]]["python_import"]
        row["incoming_path_literals"] = sum(incoming[row["path"]][k] for k in ("python_path_literal", "python_unique_filename_literal"))
        row["incoming_text_mentions"] = incoming[row["path"]]["text_unique_filename"]
        row["incoming_text_path_references"] = incoming[row["path"]]["text_path_reference"]
        row["reference_source_examples"] = "; ".join(sorted(incoming_sources[row["path"]])[:4])

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "files.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (OUT / "references.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("source", "target", "kind", "line"))
        writer.writerows(sorted(edges))

    ignored = sorted(set(git("ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z").split("\0")) - {""})
    # Names only: ignored paths include secrets, calibration mirrors and scientific
    # data. Inventory is not the backup/protection task and reads no secret values.
    (OUT / "local_artifacts.json").write_text(json.dumps({"inspection": "names only; content, size and recoverability not verified", "paths": ignored}, indent=2) + "\n", encoding="utf-8")
    (OUT / "inspection_issues.json").write_text(json.dumps(issues, indent=2) + "\n", encoding="utf-8")
    summary = {
        "baseline_commit": baseline,
        "baseline_commit_subject": git("log", "-1", "--format=%s").strip(),
        "scope": "Git-tracked and nonignored untracked files; docs/reviews/ excluded; ignored artifacts inventoried by path only",
        "files": len(rows), "tracked": sum(r["git_state"] == "tracked" for r in rows),
        "untracked": sum(r["git_state"] == "untracked" for r in rows),
        "classifications": dict(sorted(Counter(r["classification"] for r in rows).items())),
        "archive_assessments": dict(sorted(Counter(r["archive_assessment"] for r in rows).items())),
        "reference_edges": len(edges), "reference_kinds": dict(sorted(Counter(e[2] for e in edges).items())),
        "ignored_artifact_paths": len(ignored), "inspection_issues": len(issues),
        "no_runtime_modules_imported": True, "no_tests_or_simulations_run": True,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    assert len({r["path"] for r in rows}) == len(rows)
    assert all(source in paths and target in paths for source, target, _, _ in edges)
    assert all(r["archive_reason"] for r in rows)
    changed = []
    for row in rows:
        if row["read_status"] == "read" and hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest() != row["sha256"]:
            changed.append(row["path"])
    if changed:
        raise RuntimeError(f"Files changed during inspection: {changed}")
    print(json.dumps(summary, indent=2))
    print("Inventory assertions passed; all inspected file hashes remained unchanged.")


if __name__ == "__main__":
    main()
