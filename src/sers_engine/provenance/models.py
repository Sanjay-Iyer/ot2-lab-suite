"""Record shapes for the SERS provenance layer.

Every file the provenance logger writes is described here, so the on-disk
scientific record has one schema definition rather than a scattering of ad-hoc
dictionaries.  Nothing in this module writes anything or touches the robot.

The record is designed to be read years later by someone who has only the
directory: each file says what it is, which experiment revision it belongs to,
and which configuration hash it was bound to.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PROVENANCE_SCHEMA_VERSION = "sers-provenance/v1"


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """A timezone-aware ISO 8601 timestamp, e.g. ``2026-08-26T15:48:21-04:00``.

    Every timestamp in the scientific record uses this, because a bare local
    time cannot be interpreted by anyone reading the record elsewhere or later.
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


def directory_stamp() -> str:
    """A filename-safe local stamp for a session directory: ``20260826T154821``."""
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: str | Path) -> str | None:
    """Hash a file, or return None when it is not there to hash."""
    target = Path(path)
    if not target.is_file():
        return None
    return sha256_bytes(target.read_bytes())


def jsonable(value: Any) -> Any:
    """Coerce to something json.dumps will accept without losing information."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def slugify(text: str, limit: int = 40) -> str:
    """A conservative filename fragment: lowercase, alphanumeric, underscores."""
    slug = "".join(
        character if character.isalnum() else "_" for character in str(text).strip().lower()
    ).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:limit].strip("_") or "session"


# ---------------------------------------------------------------------------
# Lifecycle events
# ---------------------------------------------------------------------------


class Event:
    """The lifecycle transitions the record is required to preserve."""

    SESSION_CREATED = "SESSION_CREATED"
    SESSION_CLOSED = "SESSION_CLOSED"
    MANUAL_CONFIG_EXECUTION = "MANUAL_CONFIG_EXECUTION"
    EXPERIMENT_CREATED = "EXPERIMENT_CREATED"
    EXPERIMENT_UPDATED = "EXPERIMENT_UPDATED"
    VALIDATION_PASSED = "VALIDATION_PASSED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_REFUSED = "PLAN_REFUSED"
    SIMULATION_STARTED = "SIMULATION_STARTED"
    SIMULATION_PASSED = "SIMULATION_PASSED"
    SIMULATION_FAILED = "SIMULATION_FAILED"
    SIMULATION_INVALIDATED = "SIMULATION_INVALIDATED"
    LIVE_APPROVAL_REQUESTED = "LIVE_APPROVAL_REQUESTED"
    LIVE_APPROVED = "LIVE_APPROVED"
    LIVE_REFUSED = "LIVE_REFUSED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_COMPLETE = "EXECUTION_COMPLETE"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    PROVENANCE_DEGRADED = "PROVENANCE_DEGRADED"


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Append-only line records
# ---------------------------------------------------------------------------


class ConversationRecord(_Record):
    """One researcher or agent turn, preserved verbatim."""

    timestamp: str
    sequence: int
    role: str
    content: Any = None
    text: str = ""
    message_id: str | None = None
    thread_id: str | None = None
    experiment_id: str | None = None
    revision: int | None = None
    model: str | None = None
    model_provider: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class ToolCallRecord(_Record):
    """One agent tool invocation, its arguments, and what came back."""

    timestamp: str
    sequence: int
    tool_name: str
    tool_call_id: str | None = None
    arguments: Any = None
    result: Any = None
    ok: bool | None = None
    experiment_id: str | None = None
    revision_before: int | None = None
    revision_after: int | None = None
    thread_id: str | None = None


class EventRecord(_Record):
    """One lifecycle transition of the experiment."""

    timestamp: str
    sequence: int
    event: str
    experiment_id: str | None = None
    revision: int | None = None
    revision_index: int | None = None
    config_hash: str | None = None
    resolved_hash: str | None = None
    approval_text: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Per-revision sidecars
# ---------------------------------------------------------------------------


class RevisionSidecar(_Record):
    """What one immutable experiment snapshot is, and why it exists.

    ``revision_index`` is the position of this snapshot within the session and
    is what every filename uses.  ``revision`` is the engine's own revision
    counter for that experiment, which restarts if a second experiment is
    created in the same session.
    """

    revision_index: int
    revision: int
    experiment_id: str
    experiment_name: str
    timestamp: str
    reason: str | None = None
    changes: list[str] = Field(default_factory=list)
    config_hash: str | None = None
    resolved_hash: str | None = None
    status: str | None = None
    validation_status: str | None = None
    experiment_file: str
    experiment_sha256: str
    resolved_file: str | None = None
    validation_file: str | None = None


# ---------------------------------------------------------------------------
# Physical run records
# ---------------------------------------------------------------------------


class RobotRunRecord(_Record):
    """One physical OT-2 execution of one approved, simulated workflow."""

    run_index: int
    run_id: str
    session_id: str
    experiment_id: str
    experiment_name: str
    revision: int
    revision_index: int | None = None
    config_hash: str | None = None
    resolved_hash: str | None = None
    simulated_hash: str | None = None
    protocol_file: str | None = None
    protocol_sha256: str | None = None
    robot_run_id: str | None = None
    robot_protocol_id: str | None = None
    robot_host: str | None = None
    started_at: str
    finished_at: str | None = None
    status: str = "started"
    errors: list[Any] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    operator_approval_text: str | None = None
    machine_profile_path: str | None = None
    machine_profile_id: str | None = None
    machine_profile_sha256: str | None = None
    opentrons_api_level: str | None = None
    run_log: dict[str, Any] | None = None
    environment: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Session-level documents
# ---------------------------------------------------------------------------


class SessionMetadata(_Record):
    """Who, what, when, and with which software this session was produced."""

    provenance_schema_version: str = PROVENANCE_SCHEMA_VERSION
    session_id: str
    session_dir: str
    mode: str = "agent"
    created_at: str
    closed_at: str | None = None
    thread_id: str | None = None
    experiment_id: str | None = None
    experiment_name: str | None = None
    experiment_ids: list[str] = Field(default_factory=list)
    intent_schema_version: str | None = None
    resolver_version: str | None = None
    opentrons_api_level: str | None = None
    machine_profile: str | None = None
    machine_profile_id: str | None = None
    machine_profile_sha256: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    # "configured": the name the session was built with. Providers do not report
    # which exact weights answered a call, so this is never claimed as resolved.
    model_name_source: str | None = None
    model_configuration: dict[str, Any] = Field(default_factory=dict)
    system_prompt_sha256: str | None = None
    tool_schema_sha256: str | None = None
    tool_names: list[str] = Field(default_factory=list)
    git_commit: str | None = None
    git_branch: str | None = None
    git_dirty: bool | None = None
    python_version: str | None = None
    platform: str | None = None
    host: str | None = None
    packages: dict[str, str] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)
    input_config: str | None = None
    input_config_sha256: str | None = None
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)


class ArtifactEntry(_Record):
    path: str
    sha256: str | None = None
    bytes: int | None = None
    records: int | None = None


class Manifest(_Record):
    """The index an SI reader opens first."""

    provenance_schema_version: str = PROVENANCE_SCHEMA_VERSION
    session_id: str
    written_at: str
    mode: str = "agent"
    experiment_id: str | None = None
    experiment_name: str | None = None
    final_revision: int | None = None
    final_revision_index: int | None = None
    final_config_hash: str | None = None
    final_resolved_hash: str | None = None
    simulated_hash: str | None = None
    status: str | None = None
    revision_count: int = 0
    conversation_turns: int = 0
    tool_calls: int = 0
    events: int = 0
    robot_run_count: int = 0
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    artifacts: dict[str, ArtifactEntry] = Field(default_factory=dict)
    revisions: list[dict[str, Any]] = Field(default_factory=list)
    robot_runs: list[dict[str, Any]] = Field(default_factory=list)
