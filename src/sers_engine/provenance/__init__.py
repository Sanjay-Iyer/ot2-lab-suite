"""Durable scientific provenance for every SERS experiment session.

The engine decides what the robot does; this package writes down what happened,
so an experiment can be reconstructed months later from its directory alone:
the original request, every revision, every tool decision, the exact resolved
plan, validation, simulation, approvals, the emitted protocol, and the physical
run outcome.

It observes. It never changes an experiment, and it never relaxes a safety gate.
"""

from .export import ExportError, export_session, find_session, list_sessions, verify
from .logger import (
    SESSIONS_ROOT,
    ProvenanceError,
    ProvenanceSession,
    active_session,
    close_session,
    create_session,
    live_execution_readiness,
    set_active_session,
    tool_schema_provenance,
)
from .models import (
    PROVENANCE_SCHEMA_VERSION,
    ConversationRecord,
    Event,
    EventRecord,
    Manifest,
    RevisionSidecar,
    RobotRunRecord,
    SessionMetadata,
    ToolCallRecord,
    now_iso,
    sha256_path,
    sha256_text,
)

__all__ = [
    "ConversationRecord",
    "Event",
    "EventRecord",
    "ExportError",
    "Manifest",
    "PROVENANCE_SCHEMA_VERSION",
    "ProvenanceError",
    "ProvenanceSession",
    "RevisionSidecar",
    "RobotRunRecord",
    "SESSIONS_ROOT",
    "SessionMetadata",
    "ToolCallRecord",
    "active_session",
    "close_session",
    "create_session",
    "export_session",
    "find_session",
    "list_sessions",
    "live_execution_readiness",
    "now_iso",
    "set_active_session",
    "sha256_path",
    "sha256_text",
    "tool_schema_provenance",
    "verify",
]
