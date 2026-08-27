"""Collect one session's record into a self-describing SI package.

Export never touches the original session directory.  It copies, verifies every
hash the manifest claims, and writes a README so the package explains itself to
a reviewer who has never seen this repository.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from .logger import SESSIONS_ROOT
from .models import now_iso, sha256_path

# Copied verbatim when present. Everything else in the session directory is
# copied too; this list only fixes the order the README describes.
CORE_FILES = (
    "metadata.json",
    "manifest.json",
    "conversation.jsonl",
    "conversation.md",
    "tool_calls.jsonl",
    "events.jsonl",
    "final_experiment.yaml",
    "resolved_workflow.json",
    "execution_config.yaml",
    "validation_report.json",
    "simulation_report.json",
    "generated_protocol.py",
    "input_config.yaml",
)

CORE_DIRECTORIES = ("revisions", "resolved", "validation", "simulation", "protocols", "robot_runs")

FILE_NOTES = {
    "metadata.json": "Session identity plus the software, model and machine profile that produced it.",
    "manifest.json": "Index of every artifact with its SHA-256, and the final revision and hashes.",
    "conversation.jsonl": "Canonical append-only record of every researcher and agent turn.",
    "conversation.md": "Human-readable rendering of the conversation. Convenience only.",
    "tool_calls.jsonl": "Every agent tool invocation with its arguments and its result.",
    "events.jsonl": "Lifecycle events: creation, revision, validation, approvals, simulation, execution.",
    "final_experiment.yaml": "The exact SERSExperimentV1 the session finished on.",
    "resolved_workflow.json": "The deterministic physical plan: volumes, chunks, locations, tips, waits.",
    "execution_config.yaml": "The low-level execution contract the resolver produced.",
    "validation_report.json": "The final validation result, with checks, errors and warnings.",
    "simulation_report.json": "The final Opentrons simulation result, bound to a resolved hash.",
    "generated_protocol.py": "The exact OT-2 protocol emitted from the final resolved plan.",
    "input_config.yaml": "The YAML config supplied to the manual runner, when that path was used.",
    "revisions/": "One immutable YAML snapshot per experiment revision, plus sidecars and diffs.",
    "resolved/": "The resolved physical plan for each revision.",
    "validation/": "The validation report for each revision.",
    "simulation/": "The simulation report for each simulated revision.",
    "protocols/": "The generated OT-2 protocol for each simulated revision.",
    "robot_runs/": "One record per physical OT-2 execution. Replicates never overwrite each other.",
}


class ExportError(RuntimeError):
    """The session could not be exported."""


def find_session(identifier: str, root: Path | None = None) -> Path:
    """Locate a session directory by id, directory name, or path."""
    candidate = Path(identifier)
    if candidate.is_dir() and (candidate / "metadata.json").is_file():
        return candidate
    base = Path(root) if root is not None else SESSIONS_ROOT
    if not base.is_dir():
        raise ExportError(f"no session directory exists yet at {base}")
    matches = [
        item
        for item in sorted(base.iterdir())
        if item.is_dir()
        and item.name != "exports"
        and (item.name == identifier or item.name.endswith(identifier))
    ]
    if not matches:
        for item in sorted(base.iterdir()):
            metadata = item / "metadata.json"
            if not metadata.is_file():
                continue
            try:
                payload = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if identifier in {payload.get("session_id"), payload.get("experiment_id")}:
                matches.append(item)
    if not matches:
        raise ExportError(f"no session matching {identifier!r} under {base}")
    return matches[-1]


def list_sessions(root: Path | None = None) -> list[dict[str, Any]]:
    """Every recorded session, newest last, with enough to pick one."""
    base = Path(root) if root is not None else SESSIONS_ROOT
    if not base.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for item in sorted(base.iterdir()):
        metadata = item / "metadata.json"
        if not metadata.is_file():
            continue
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        manifest: dict[str, Any] = {}
        manifest_path = item / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = {}
        rows.append(
            {
                "session_id": payload.get("session_id"),
                "directory": str(item),
                "name": item.name,
                "mode": payload.get("mode"),
                "created_at": payload.get("created_at"),
                "experiment_id": payload.get("experiment_id"),
                "experiment_name": payload.get("experiment_name"),
                "revisions": manifest.get("revision_count"),
                "robot_runs": manifest.get("robot_run_count"),
                "degraded": payload.get("degraded", False),
            }
        )
    return rows


def verify(session_dir: Path) -> list[str]:
    """Re-check every SHA-256 the manifest claims. Returns the mismatches."""
    manifest_path = session_dir / "manifest.json"
    if not manifest_path.is_file():
        return ["manifest.json is missing"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"manifest.json is unreadable: {exc}"]
    problems: list[str] = []
    for name, entry in (manifest.get("artifacts") or {}).items():
        relative = entry.get("path")
        if not relative:
            continue
        target = session_dir / relative
        if not target.is_file():
            # The machine profile lives in the repository, not the session.
            target = Path(relative)
            if not target.is_file():
                problems.append(f"{name}: {relative} is missing")
                continue
        expected = entry.get("sha256")
        if expected and sha256_path(target) != expected:
            problems.append(f"{name}: {relative} does not match its recorded sha256")
    return problems


def _readme(session_dir: Path, metadata: dict[str, Any], manifest: dict[str, Any]) -> str:
    lines = [
        f"# SERS experiment record — {metadata.get('experiment_name') or 'session'}",
        "",
        "Supporting Information export produced by `scripts/export_sers_run.py`.",
        "It is a copy; the original session directory was not modified.",
        "",
        "## Identity",
        "",
        f"- session_id: `{metadata.get('session_id')}`",
        f"- experiment_id: `{metadata.get('experiment_id')}`",
        f"- experiment_name: {metadata.get('experiment_name')}",
        f"- mode: {metadata.get('mode')}",
        f"- created: {metadata.get('created_at')}",
        f"- closed: {metadata.get('closed_at')}",
        f"- exported: {now_iso()}",
        f"- source directory: `{session_dir}`",
        "",
        "## Final state",
        "",
        f"- final revision: {manifest.get('final_revision')} "
        f"(snapshot {manifest.get('final_revision_index')})",
        f"- config hash: `{manifest.get('final_config_hash')}`",
        f"- resolved hash: `{manifest.get('final_resolved_hash')}`",
        f"- simulated hash: `{manifest.get('simulated_hash')}`",
        f"- revisions recorded: {manifest.get('revision_count')}",
        f"- physical robot runs: {manifest.get('robot_run_count')}",
        "",
        "## Software and model",
        "",
        f"- git commit: `{metadata.get('git_commit')}` on `{metadata.get('git_branch')}`",
        f"- working tree dirty at run time: {metadata.get('git_dirty')}",
        f"- python: {metadata.get('python_version')} on {metadata.get('platform')}",
    ]
    # Unknown values are left out rather than printed as None, so the reader can
    # tell what was actually recorded from what was never available.
    if metadata.get("model_provider"):
        lines.append(
            f"- model: {metadata.get('model_name')} via {metadata.get('model_provider')} "
            f"({metadata.get('model_name_source') or 'unrecorded'} name, not a resolved "
            "provider version)"
        )
        if metadata.get("model_configuration"):
            lines.append(f"- model configuration: {metadata['model_configuration']}")
    else:
        lines.append("- model: none; this workflow was produced without a language model")
    for label, key in (
        ("system prompt sha256", "system_prompt_sha256"),
        ("tool schema sha256", "tool_schema_sha256"),
        ("machine profile sha256", "machine_profile_sha256"),
    ):
        if metadata.get(key):
            lines.append(f"- {label}: `{metadata[key]}`")
    if metadata.get("machine_profile"):
        lines.append(f"- machine profile: `{metadata['machine_profile']}`")
    if metadata.get("opentrons_api_level"):
        lines.append(f"- Opentrons API level: {metadata['opentrons_api_level']}")
    if metadata.get("tool_names"):
        lines.append(f"- tools the agent could call: {', '.join(metadata['tool_names'])}")
    lines += ["", "### Package versions", ""]
    for name, version in sorted((metadata.get("packages") or {}).items()):
        lines.append(f"- {name}: {version}")
    lines += ["", "## Files", ""]
    for name in CORE_FILES:
        if (session_dir / name).is_file():
            lines.append(f"- `{name}` — {FILE_NOTES.get(name, '')}")
    for name in CORE_DIRECTORIES:
        if (session_dir / name).is_dir():
            lines.append(f"- `{name}/` — {FILE_NOTES.get(name + '/', '')}")
    lines += [
        "",
        "## How to read this record",
        "",
        "1. `conversation.jsonl` — what the researcher asked, in their own words.",
        "2. `tool_calls.jsonl` — what the agent did about it, with exact arguments.",
        "3. `revisions/` — the experiment after every change, oldest first.",
        "4. `resolved/` — the deterministic physical plan each revision produced.",
        "5. `simulation/` and `protocols/` — what was simulated and the exact protocol.",
        "6. `events.jsonl` — approvals, invalidations and execution, with timestamps.",
        "7. `robot_runs/` — what the OT-2 actually did.",
        "",
        "Hashes bind these together: a simulation authorizes only the resolved hash",
        "it was run against, and a robot run records the resolved hash it executed.",
        "",
        "No credentials, tokens or keys are recorded anywhere in this package.",
        "",
    ]
    if metadata.get("degraded"):
        lines += [
            "## WARNING: incomplete record",
            "",
            "Provenance logging reported failures during this session:",
            "",
        ]
        lines += [f"- {reason}" for reason in metadata.get("degraded_reasons") or []]
        lines.append("")
    return "\n".join(lines)


def export_session(
    identifier: str,
    destination: Path | None = None,
    root: Path | None = None,
    archive: bool = False,
) -> dict[str, Any]:
    """Copy one session's record into an SI package. The original is untouched."""
    session_dir = find_session(identifier, root=root)
    metadata_path = session_dir / "metadata.json"
    if not metadata_path.is_file():
        raise ExportError(f"{session_dir} has no metadata.json; it is not a session directory")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    manifest: dict[str, Any] = {}
    if (session_dir / "manifest.json").is_file():
        manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))

    name = f"SI_sers_{metadata.get('experiment_name') or 'session'}_{metadata.get('session_id')}"
    name = "".join(character if character.isalnum() or character in "._-" else "_" for character in name)
    if destination is not None:
        out_root = Path(destination)
    elif session_dir.parent == SESSIONS_ROOT:
        # Beside the sessions directory, never inside it, so an export is never
        # mistaken for a session.
        out_root = SESSIONS_ROOT.parent / "exports"
    else:
        out_root = session_dir.parent / "exports"
    target = out_root / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for item in sorted(session_dir.iterdir()):
        if item.is_dir():
            shutil.copytree(item, target / item.name)
            copied.extend(
                f"{item.name}/{child.relative_to(item)}".replace("\\", "/")
                for child in sorted(item.rglob("*"))
                if child.is_file()
            )
        else:
            shutil.copy2(item, target / item.name)
            copied.append(item.name)

    profile = metadata.get("machine_profile")
    if profile:
        from ..schema import REPO_ROOT

        candidate = Path(profile)
        absolute = candidate if candidate.is_absolute() else REPO_ROOT / candidate
        if absolute.is_file():
            (target / "machine_profile").mkdir(exist_ok=True)
            shutil.copy2(absolute, target / "machine_profile" / absolute.name)
            copied.append(f"machine_profile/{absolute.name}")

    readme = target / "README.md"
    readme.write_text(_readme(session_dir, metadata, manifest), encoding="utf-8")
    copied.append("README.md")

    result: dict[str, Any] = {
        "session_id": metadata.get("session_id"),
        "session_dir": str(session_dir),
        "export_dir": str(target),
        "files": sorted(copied),
        "file_count": len(copied),
        "verification": verify(session_dir),
        "degraded": bool(metadata.get("degraded")),
    }
    if archive:
        archive_path = target.with_suffix(".zip")
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for child in sorted(target.rglob("*")):
                if child.is_file():
                    bundle.write(child, f"{target.name}/{child.relative_to(target)}")
        result["archive"] = str(archive_path)
    return result
