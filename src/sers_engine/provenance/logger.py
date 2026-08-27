"""The provenance service: one session directory, written append-only.

This module is the only thing in the SERS engine that writes the scientific
record.  Nothing here decides anything about an experiment -- it observes the
deterministic session, the agent conversation, and the execution layer, and
writes down what happened.

Two rules shape the design:

*   **Append-only where it matters.**  Conversation turns, tool calls, events
    and revision snapshots are never rewritten.  ``final_experiment.yaml``,
    ``resolved_workflow.json``, ``manifest.json`` and friends are convenience
    copies of the current final state, and the history behind them stays.
*   **A failed write is never silent.**  If the record cannot be completed the
    session is marked degraded, the reason is surfaced, and
    :func:`live_execution_readiness` refuses to let the robot move.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml

from ..schema import REPO_ROOT
from .models import (
    ArtifactEntry,
    ConversationRecord,
    Event,
    EventRecord,
    Manifest,
    RevisionSidecar,
    RobotRunRecord,
    SessionMetadata,
    ToolCallRecord,
    directory_stamp,
    jsonable,
    now_iso,
    sha256_path,
    sha256_text,
    slugify,
)
from .software import (
    git_provenance,
    interpreter_provenance,
    model_provenance,
    package_versions,
    safe_environment,
)

SESSIONS_ROOT = REPO_ROOT / "runs" / "sers" / "sessions"


class ProvenanceError(RuntimeError):
    """The scientific record could not be written."""


def _text_of(content: Any) -> str:
    """Flatten a message body to plain text without discarding the original."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "".join(parts)
    return "" if content is None else str(content)


def _parse_result(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (TypeError, ValueError):
            return content
    return jsonable(content)


class ProvenanceSession:
    """One durable experiment-session directory.

    A session is a design conversation (or one manual config run).  It may hold
    more than one experiment and many revisions of each, and an approved
    experiment may be executed on the robot more than once.
    """

    def __init__(
        self,
        directory: Path,
        session_id: str,
        mode: str = "agent",
        thread_id: str | None = None,
    ) -> None:
        self.directory = directory
        self.session_id = session_id
        self.mode = mode
        self.thread_id = thread_id
        self.degraded = False
        self.degraded_reasons: list[str] = []

        self._sequence = 0
        self._revision_index = 0
        self._run_index = 0
        self._captured: set[tuple[str, int]] = set()
        self._seen_messages: set[str] = set()
        self._pending_tool_calls: dict[str, dict[str, Any]] = {}
        self._last_revision: int | None = None
        self._last_user_text: str | None = None
        self._status: str | None = None
        self._experiment_ids: list[str] = []
        self._revision_rows: list[dict[str, Any]] = []
        self._run_rows: list[dict[str, Any]] = []
        self._counts = {"conversation": 0, "tool_calls": 0, "events": 0}
        self._manifest_artifacts: dict[str, ArtifactEntry] = {}
        self._closed = False

        self.metadata = SessionMetadata(
            session_id=session_id,
            session_dir=str(directory),
            mode=mode,
            thread_id=thread_id,
            created_at=now_iso(),
        )

    # ---- paths -------------------------------------------------------------
    @property
    def conversation_path(self) -> Path:
        return self.directory / "conversation.jsonl"

    @property
    def tool_calls_path(self) -> Path:
        return self.directory / "tool_calls.jsonl"

    @property
    def events_path(self) -> Path:
        return self.directory / "events.jsonl"

    @property
    def metadata_path(self) -> Path:
        return self.directory / "metadata.json"

    @property
    def manifest_path(self) -> Path:
        return self.directory / "manifest.json"

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.directory)).replace(os.sep, "/")
        except ValueError:
            return str(path)

    # ---- failure handling --------------------------------------------------
    def _degrade(self, reason: str) -> None:
        """Record that the scientific record is incomplete, loudly."""
        if reason not in self.degraded_reasons:
            self.degraded_reasons.append(reason)
        self.degraded = True
        self.metadata.degraded = True
        self.metadata.degraded_reasons = list(self.degraded_reasons)
        print(f"PROVENANCE ERROR: {reason}", file=sys.stderr)

    def _append(self, path: Path, record: Any) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(jsonable(record), ensure_ascii=False)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            self._degrade(f"could not append to {path.name}: {exc}")

    def _write(self, path: Path, text: str) -> Path | None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return path
        except OSError as exc:
            self._degrade(f"could not write {path.name}: {exc}")
            return None

    def _write_json(self, path: Path, payload: Any) -> Path | None:
        return self._write(path, json.dumps(jsonable(payload), indent=2, ensure_ascii=False) + "\n")

    def _next(self) -> int:
        self._sequence += 1
        return self._sequence

    # ---- session lifecycle -------------------------------------------------
    def start(self, **metadata: Any) -> "ProvenanceSession":
        self.describe(**metadata)
        self.log_event(
            Event.SESSION_CREATED,
            details={"mode": self.mode, "session_dir": str(self.directory)},
        )
        return self

    def describe(self, **fields: Any) -> None:
        """Merge software, model, and experiment metadata into ``metadata.json``."""
        for key, value in fields.items():
            if value is None or not hasattr(self.metadata, key):
                continue
            setattr(self.metadata, key, value)
        self._write_metadata()

    def describe_model(self, llm: Any) -> None:
        self.describe(**model_provenance(llm))

    def _write_metadata(self) -> None:
        self.metadata.experiment_ids = list(self._experiment_ids)
        self.metadata.degraded = self.degraded
        self.metadata.degraded_reasons = list(self.degraded_reasons)
        self._write_json(self.metadata_path, self.metadata.model_dump(mode="json"))

    def close(self, status: str | None = None) -> Path:
        """Finish the record: last events, transcript, manifest."""
        if self._closed:
            return self.directory
        self._closed = True
        self.log_event(Event.SESSION_CLOSED, details={"status": status} if status else {})
        self.metadata.closed_at = now_iso()
        self._write_metadata()
        self.write_transcript()
        return self.write_manifest()

    # ---- conversation ------------------------------------------------------
    def log_message(
        self,
        role: str,
        content: Any,
        message_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        """Append one conversational turn, verbatim and never overwritten."""
        if message_id and message_id in self._seen_messages:
            return
        if message_id:
            self._seen_messages.add(message_id)
        text = _text_of(content)
        if role == "user":
            self._last_user_text = text
        record = ConversationRecord(
            timestamp=now_iso(),
            sequence=self._next(),
            role=role,
            content=jsonable(content),
            text=text,
            message_id=message_id,
            thread_id=self.thread_id,
            experiment_id=self.metadata.experiment_id,
            revision=self._last_revision,
            model=self.metadata.model_name if role == "assistant" else None,
            model_provider=self.metadata.model_provider if role == "assistant" else None,
            tool_calls=tool_calls or [],
        )
        self._append(self.conversation_path, record)
        self._counts["conversation"] += 1

    def log_tool_call(
        self,
        tool_name: str,
        arguments: Any = None,
        result: Any = None,
        tool_call_id: str | None = None,
        revision_before: int | None = None,
    ) -> None:
        """Append one tool invocation with its arguments and its result."""
        parsed = _parse_result(result)
        ok = parsed.get("ok") if isinstance(parsed, dict) else None
        before = self._last_revision if revision_before is None else revision_before
        after = before
        if isinstance(parsed, dict):
            state = parsed.get("state")
            if isinstance(state, dict):
                if isinstance(state.get("revision"), int):
                    after = state["revision"]
                    self._last_revision = after
                if state.get("experiment_id"):
                    self._note_experiment(state["experiment_id"])
        record = ToolCallRecord(
            timestamp=now_iso(),
            sequence=self._next(),
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            arguments=jsonable(arguments),
            result=jsonable(parsed),
            ok=ok if isinstance(ok, bool) else None,
            experiment_id=self.metadata.experiment_id,
            revision_before=before,
            revision_after=after,
            thread_id=self.thread_id,
        )
        self._append(self.tool_calls_path, record)
        self._counts["tool_calls"] += 1

    def record_messages(self, messages: list[Any]) -> None:
        """Log every message the graph has produced that has not been logged yet.

        The LangGraph checkpointer is runtime state and disappears with the
        process; this turns it into the permanent record.  Pairing an assistant
        tool call with its ``ToolMessage`` result happens here, so a tool call
        and what it returned end up on one line together.
        """
        for message in messages:
            identifier = getattr(message, "id", None)
            kind = getattr(message, "type", None)
            if kind == "human":
                self.log_message("user", message.content, message_id=identifier)
            elif kind == "ai":
                calls = list(getattr(message, "tool_calls", None) or [])
                if identifier and identifier not in self._seen_messages:
                    self.log_message(
                        "assistant",
                        message.content,
                        message_id=identifier,
                        tool_calls=[
                            {"name": call.get("name"), "id": call.get("id"), "args": call.get("args")}
                            for call in calls
                        ],
                    )
                for call in calls:
                    call_id = call.get("id")
                    if call_id and call_id not in self._pending_tool_calls:
                        self._pending_tool_calls[call_id] = {
                            "name": call.get("name"),
                            "args": call.get("args"),
                            "revision_before": self._last_revision,
                        }
            elif kind == "tool":
                if identifier and identifier in self._seen_messages:
                    continue
                if identifier:
                    self._seen_messages.add(identifier)
                call_id = getattr(message, "tool_call_id", None)
                pending = self._pending_tool_calls.pop(call_id, {}) if call_id else {}
                self.log_tool_call(
                    tool_name=pending.get("name") or getattr(message, "name", "unknown"),
                    arguments=pending.get("args"),
                    result=message.content,
                    tool_call_id=call_id,
                    revision_before=pending.get("revision_before"),
                )

    def last_user_text(self) -> str | None:
        """The researcher's own most recent words, for quoting an approval."""
        return self._last_user_text

    # ---- events ------------------------------------------------------------
    def log_event(
        self,
        event: str,
        experiment_id: str | None = None,
        revision: int | None = None,
        revision_index: int | None = None,
        config_hash: str | None = None,
        resolved_hash: str | None = None,
        approval_text: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        record = EventRecord(
            timestamp=now_iso(),
            sequence=self._next(),
            event=event,
            experiment_id=experiment_id or self.metadata.experiment_id,
            revision=revision if revision is not None else self._last_revision,
            revision_index=revision_index,
            config_hash=config_hash,
            resolved_hash=resolved_hash,
            approval_text=approval_text,
            details=jsonable(details or {}),
        )
        self._append(self.events_path, record)
        self._counts["events"] += 1

    def _note_experiment(self, experiment_id: str | None) -> None:
        if experiment_id and experiment_id not in self._experiment_ids:
            self._experiment_ids.append(experiment_id)

    # ---- observing the deterministic session -------------------------------
    def record_revision(self, session: Any, report: Any = None, plan: Any = None) -> int | None:
        """Snapshot one experiment revision immutably.

        Called every time the engine resolves, but a snapshot is written only
        once per (experiment, revision): repeatedly re-validating an unchanged
        experiment must not manufacture revision history.
        """
        from ..intent import intent_as_dict

        experiment = session.experiment
        experiment_id = experiment.experiment_id
        self._note_experiment(experiment_id)
        if self.metadata.experiment_id is None:
            self.metadata.experiment_id = experiment_id
            self.metadata.experiment_name = experiment.experiment_name
        self._last_revision = session.revision

        key = (experiment_id, session.revision)
        first_time = key not in self._captured
        plan = plan if plan is not None else session.resolved
        report = report if report is not None else session.validation

        index: int | None = None
        if first_time:
            self._captured.add(key)
            self._revision_index += 1
            index = self._revision_index
            stem = f"revision_{index:03d}"

            experiment_file = self._write(
                self.directory / "revisions" / f"{stem}.yaml",
                yaml.safe_dump(intent_as_dict(experiment), sort_keys=False, allow_unicode=True),
            )
            resolved_file = None
            if plan is not None:
                resolved_file = self._write_json(
                    self.directory / "resolved" / f"{stem}.json", plan.model_dump(mode="json")
                )
            validation_file = None
            if report is not None:
                validation_file = self._write_json(
                    self.directory / "validation" / f"{stem}.json",
                    self._validation_payload(session, report, plan, index),
                )

            if experiment_file is not None:
                sidecar = RevisionSidecar(
                    revision_index=index,
                    revision=session.revision,
                    experiment_id=experiment_id,
                    experiment_name=experiment.experiment_name,
                    timestamp=now_iso(),
                    reason=session.last_change,
                    changes=list(session.history),
                    config_hash=plan.config_hash if plan is not None else None,
                    resolved_hash=plan.resolved_hash if plan is not None else None,
                    status=getattr(session.status, "value", str(session.status)),
                    validation_status=getattr(report, "status", None),
                    experiment_file=self.relative(experiment_file),
                    experiment_sha256=sha256_path(experiment_file) or "",
                    resolved_file=self.relative(resolved_file) if resolved_file else None,
                    validation_file=self.relative(validation_file) if validation_file else None,
                )
                self._write_json(self.directory / "revisions" / f"{stem}.json", sidecar)
                self._revision_rows.append(sidecar.model_dump(mode="json"))
                self._write_diff(index, experiment)

            self.log_event(
                Event.EXPERIMENT_CREATED if session.revision == 0 else Event.EXPERIMENT_UPDATED,
                experiment_id=experiment_id,
                revision=session.revision,
                revision_index=index,
                config_hash=plan.config_hash if plan is not None else None,
                resolved_hash=plan.resolved_hash if plan is not None else None,
                details={"reason": session.last_change, "snapshot": f"revisions/{stem}.yaml"},
            )
        else:
            index = self._index_for(experiment_id, session.revision)

        if report is not None:
            self.log_event(
                Event.VALIDATION_PASSED if report.ok else Event.VALIDATION_FAILED,
                experiment_id=experiment_id,
                revision=session.revision,
                revision_index=index,
                config_hash=plan.config_hash if plan is not None else None,
                resolved_hash=plan.resolved_hash if plan is not None else None,
                details={
                    "errors": list(report.errors),
                    "warnings": list(report.warnings),
                    "checks_run": list(report.checks_run),
                },
            )
        self._refresh_final(session, plan, report)
        return index

    def _validation_payload(
        self, session: Any, report: Any, plan: Any, index: int
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "timestamp": now_iso(),
            "session_id": self.session_id,
            "experiment_id": session.experiment.experiment_id,
            "revision": session.revision,
            "revision_index": index,
            "config_hash": plan.config_hash if plan is not None else None,
            "resolved_hash": plan.resolved_hash if plan is not None else None,
            "status": report.status,
            "ok": report.ok,
            "errors": list(report.errors),
            "warnings": list(report.warnings),
            "checks_run": list(report.checks_run),
        }
        if plan is not None:
            config = plan.execution_config
            payload["requirements"] = {
                "tips_required": plan.totals.tips_required,
                "start_tip": config.get("tips", {}).get("start_tip"),
                "tip_racks": list(config.get("deck_layout", {}).get("tip_racks", {})),
                "deck_slots": dict(plan.deck),
                "liquid_requirements": [
                    item.model_dump(mode="json") for item in plan.totals.liquid_requirements
                ],
                "final_well_volumes": dict(plan.totals.final_well_volumes),
                "printed_volume_ul": plan.totals.printed_volume_ul,
                "deposits": plan.totals.deposits,
            }
            payload["depth_warnings"] = [
                warning for warning in report.warnings if "submer" in warning or "depth" in warning
            ]
        return payload

    def _index_for(self, experiment_id: str, revision: int) -> int | None:
        for row in reversed(self._revision_rows):
            if row["experiment_id"] == experiment_id and row["revision"] == revision:
                return row["revision_index"]
        return None

    def _write_diff(self, index: int, experiment: Any) -> None:
        """A structured diff against the previous snapshot. The snapshot is the record."""
        if index < 2:
            return
        from ..intent import intent_as_dict

        previous_path = self.directory / "revisions" / f"revision_{index - 1:03d}.yaml"
        if not previous_path.is_file():
            return
        try:
            before = yaml.safe_load(previous_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return
        after = intent_as_dict(experiment)
        changes: dict[str, Any] = {}
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                changes[key] = {"before": before.get(key), "after": after.get(key)}
        self._write_json(
            self.directory / "revisions" / f"revision_{index:03d}.diff.json",
            {
                "timestamp": now_iso(),
                "from_revision_index": index - 1,
                "to_revision_index": index,
                "changed_fields": sorted(changes),
                "changes": changes,
            },
        )

    def record_invalidation(self, session: Any, reason: str, had_simulation: bool) -> None:
        """A change landed; note what it threw away before the new revision resolves."""
        if not had_simulation:
            return
        superseded = self._index_for(session.experiment.experiment_id, session.revision - 1)
        self.log_event(
            Event.SIMULATION_INVALIDATED,
            experiment_id=session.experiment.experiment_id,
            revision=session.revision,
            details={
                "reason": reason,
                "superseded_revision": session.revision - 1,
                "superseded_revision_index": superseded,
                "superseded_simulation": (
                    f"simulation/revision_{superseded:03d}.json" if superseded else None
                ),
                "invalidated": [
                    "resolved plan",
                    "simulation",
                    "plan approval",
                    "live approval",
                ],
                "note": (
                    "the superseded simulation report is preserved under "
                    "simulation/ and is no longer authorized for execution"
                ),
            },
        )

    def record_plan_approval(self, session: Any) -> None:
        plan = session.resolved
        self.log_event(
            Event.PLAN_APPROVED,
            experiment_id=session.experiment.experiment_id,
            revision=session.revision,
            revision_index=self._index_for(session.experiment.experiment_id, session.revision),
            config_hash=plan.config_hash if plan else None,
            resolved_hash=plan.resolved_hash if plan else None,
            approval_text=self._last_user_text,
            details={"gate": "1 of 2", "authorizes": "simulation only"},
        )

    def record_plan_refusal(self, session: Any, reason: str) -> None:
        self.log_event(
            Event.PLAN_REFUSED,
            experiment_id=session.experiment.experiment_id if session else None,
            approval_text=self._last_user_text,
            details={"reason": reason},
        )

    def record_simulation_start(self, session: Any) -> None:
        plan = session.resolved
        self.log_event(
            Event.SIMULATION_STARTED,
            experiment_id=session.experiment.experiment_id,
            revision=session.revision,
            revision_index=self._index_for(session.experiment.experiment_id, session.revision),
            config_hash=plan.config_hash if plan else None,
            resolved_hash=plan.resolved_hash if plan else None,
        )

    def record_simulation(self, session: Any, report: Any) -> None:
        """Persist the simulation report and the exact protocol it describes."""
        experiment_id = session.experiment.experiment_id
        index = self._index_for(experiment_id, session.revision)
        stem = f"revision_{index:03d}" if index else f"rev_{session.revision:03d}"
        plan = session.resolved

        payload = report.model_dump(mode="json")
        payload.update(
            {
                "session_id": self.session_id,
                "timestamp": now_iso(),
                "revision": session.revision,
                "revision_index": index,
                "opentrons_api_level": self._api_level(plan),
                "opentrons_version": self.metadata.packages.get("opentrons"),
            }
        )
        protocol_path = self.save_protocol(session, stem)
        if protocol_path is not None:
            payload["protocol_file"] = self.relative(protocol_path)
            payload["protocol_sha256"] = sha256_path(protocol_path)
        self._write_json(self.directory / "simulation" / f"{stem}.json", payload)

        self.log_event(
            Event.SIMULATION_PASSED if report.passed else Event.SIMULATION_FAILED,
            experiment_id=experiment_id,
            revision=session.revision,
            revision_index=index,
            config_hash=report.config_hash,
            resolved_hash=report.resolved_hash,
            details={
                "command_count": report.command_count,
                "deposits": report.deposits,
                "tips_used": report.tips_used,
                "errors": list(report.errors),
                "report": f"simulation/{stem}.json",
            },
        )
        self._refresh_final(session, plan, session.validation, report)

    def save_protocol(self, session: Any, stem: str) -> Path | None:
        """Write the exact generated OT-2 protocol for the current resolved plan."""
        plan = session.resolved
        if plan is None:
            return None
        from ..emitter import emit_protocol

        try:
            source = emit_protocol(plan)
        except Exception as exc:  # emission must never break the record
            self._degrade(f"could not emit the protocol for {stem}: {exc}")
            return None
        return self._write(self.directory / "protocols" / f"{stem}.py", source)

    def record_live_approval_requested(self, session: Any, pending: list[dict[str, Any]]) -> None:
        self.log_event(
            Event.LIVE_APPROVAL_REQUESTED,
            experiment_id=session.experiment.experiment_id if session else None,
            details={"pending_tools": jsonable(pending)},
        )

    def record_live_approval(self, session: Any, confirmation: str | None) -> None:
        plan = session.resolved
        self.log_event(
            Event.LIVE_APPROVED,
            experiment_id=session.experiment.experiment_id,
            revision=session.revision,
            revision_index=self._index_for(session.experiment.experiment_id, session.revision),
            config_hash=plan.config_hash if plan else None,
            resolved_hash=plan.resolved_hash if plan else None,
            approval_text=confirmation or self._last_user_text,
            details={"gate": "2 of 2", "simulated_hash": session.simulated_hash},
        )

    def record_live_refusal(self, session: Any, reason: str, pending: Any = None) -> None:
        self.log_event(
            Event.LIVE_REFUSED,
            experiment_id=session.experiment.experiment_id if session else None,
            approval_text=self._last_user_text,
            details={"reason": reason, "pending_tools": jsonable(pending or [])},
        )

    # ---- physical runs -----------------------------------------------------
    def start_robot_run(
        self,
        session: Any,
        robot_host: str | None = None,
        robot_run_id: str | None = None,
        robot_protocol_id: str | None = None,
        protocol_path: str | Path | None = None,
        approval_text: str | None = None,
    ) -> RobotRunRecord:
        """Open a new physical-run record. Repeat runs never overwrite each other."""
        self._run_index += 1
        plan = session.resolved
        profile_path = getattr(plan, "machine_profile_path", None) if plan else None
        absolute_profile = None
        if profile_path:
            candidate = Path(profile_path)
            absolute_profile = candidate if candidate.is_absolute() else REPO_ROOT / candidate
        record = RobotRunRecord(
            run_index=self._run_index,
            run_id=f"{self.session_id}-run{self._run_index:03d}",
            session_id=self.session_id,
            experiment_id=session.experiment.experiment_id,
            experiment_name=session.experiment.experiment_name,
            revision=session.revision,
            revision_index=self._index_for(session.experiment.experiment_id, session.revision),
            config_hash=plan.config_hash if plan else None,
            resolved_hash=plan.resolved_hash if plan else None,
            simulated_hash=session.simulated_hash,
            protocol_file=str(protocol_path) if protocol_path else None,
            protocol_sha256=sha256_path(protocol_path) if protocol_path else None,
            robot_run_id=robot_run_id,
            robot_protocol_id=robot_protocol_id,
            robot_host=robot_host,
            started_at=now_iso(),
            status="running",
            operator_approval_text=approval_text,
            machine_profile_path=profile_path,
            machine_profile_id=plan.machine_profile_id if plan else None,
            machine_profile_sha256=sha256_path(absolute_profile) if absolute_profile else None,
            opentrons_api_level=self._api_level(plan),
            environment=safe_environment(),
        )
        self._write_run(record)
        self.log_event(
            Event.EXECUTION_STARTED,
            experiment_id=record.experiment_id,
            revision=record.revision,
            revision_index=record.revision_index,
            config_hash=record.config_hash,
            resolved_hash=record.resolved_hash,
            approval_text=approval_text,
            details={
                "run_index": record.run_index,
                "robot_run_id": robot_run_id,
                "robot_host": robot_host,
                "record": f"robot_runs/run_{record.run_index:03d}.json",
            },
        )
        return record

    def finish_robot_run(
        self,
        record: RobotRunRecord,
        status: str,
        errors: Any = None,
        run_log: dict[str, Any] | None = None,
    ) -> None:
        record.status = status
        record.finished_at = now_iso()
        if errors:
            record.errors = list(errors) if isinstance(errors, list) else [errors]
        if run_log is not None:
            record.run_log = jsonable(run_log)
        self._write_run(record)
        self.log_event(
            Event.EXECUTION_COMPLETE if status == "succeeded" else Event.EXECUTION_FAILED,
            experiment_id=record.experiment_id,
            revision=record.revision,
            resolved_hash=record.resolved_hash,
            details={
                "run_index": record.run_index,
                "robot_run_id": record.robot_run_id,
                "status": status,
                "errors": jsonable(record.errors),
            },
        )

    def _write_run(self, record: RobotRunRecord) -> None:
        path = self.directory / "robot_runs" / f"run_{record.run_index:03d}.json"
        self._write_json(path, record.model_dump(mode="json"))
        row = {
            "run_index": record.run_index,
            "run_id": record.run_id,
            "robot_run_id": record.robot_run_id,
            "status": record.status,
            "resolved_hash": record.resolved_hash,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "file": self.relative(path),
        }
        self._run_rows = [item for item in self._run_rows if item["run_index"] != record.run_index]
        self._run_rows.append(row)
        self._run_rows.sort(key=lambda item: item["run_index"])

    def robot_run_count(self) -> int:
        return self._run_index

    # ---- final convenience copies -----------------------------------------
    def _api_level(self, plan: Any) -> str | None:
        if plan is None:
            return None
        try:
            return str(plan.execution_config.get("api_level"))
        except Exception:
            return None

    def _refresh_final(
        self, session: Any, plan: Any = None, report: Any = None, simulation: Any = None
    ) -> None:
        """Rewrite the convenience pointers to the current final state.

        These are copies. Every historical revision, resolution, validation and
        simulation stays exactly where it was written.
        """
        from ..intent import intent_as_dict

        # Where the session actually stands now, not where it stood when the
        # revision was first snapshotted.
        self._status = getattr(session.status, "value", str(session.status))

        self._write(
            self.directory / "final_experiment.yaml",
            yaml.safe_dump(intent_as_dict(session.experiment), sort_keys=False, allow_unicode=True),
        )
        plan = plan if plan is not None else session.resolved
        if plan is not None:
            self._write_json(
                self.directory / "resolved_workflow.json", plan.model_dump(mode="json")
            )
            self._write(
                self.directory / "execution_config.yaml",
                yaml.safe_dump(plan.execution_config, sort_keys=False, allow_unicode=True),
            )
            if self.metadata.opentrons_api_level is None:
                self.metadata.opentrons_api_level = self._api_level(plan)
                self.metadata.machine_profile = plan.machine_profile_path
                self.metadata.machine_profile_id = plan.machine_profile_id
                if plan.machine_profile_path:
                    candidate = Path(plan.machine_profile_path)
                    absolute = candidate if candidate.is_absolute() else REPO_ROOT / candidate
                    self.metadata.machine_profile_sha256 = sha256_path(absolute)
                self.metadata.resolver_version = plan.resolver_version
                self._write_metadata()
        report = report if report is not None else session.validation
        if report is not None:
            index = self._index_for(session.experiment.experiment_id, session.revision)
            self._write_json(
                self.directory / "validation_report.json",
                self._validation_payload(session, report, plan, index or 0),
            )
        simulation = simulation if simulation is not None else session.simulation
        if simulation is not None:
            index = self._index_for(session.experiment.experiment_id, session.revision)
            stem = f"revision_{index:03d}" if index else f"rev_{session.revision:03d}"
            source = self.directory / "simulation" / f"{stem}.json"
            if source.is_file():
                self._write(
                    self.directory / "simulation_report.json",
                    source.read_text(encoding="utf-8"),
                )
            protocol = self.directory / "protocols" / f"{stem}.py"
            if protocol.is_file():
                self._write(
                    self.directory / "generated_protocol.py",
                    protocol.read_text(encoding="utf-8"),
                )

    # ---- transcript and manifest ------------------------------------------
    def write_transcript(self) -> Path | None:
        """Render conversation.md for reading. conversation.jsonl stays canonical."""
        if not self.conversation_path.is_file():
            return None
        try:
            turns = [
                json.loads(line)
                for line in self.conversation_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            calls = []
            if self.tool_calls_path.is_file():
                calls = [
                    json.loads(line)
                    for line in self.tool_calls_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
        except (OSError, ValueError) as exc:
            self._degrade(f"could not render conversation.md: {exc}")
            return None

        merged = sorted(
            [("turn", item) for item in turns] + [("call", item) for item in calls],
            key=lambda pair: pair[1].get("sequence", 0),
        )
        lines = [
            "# SERS Agent Conversation",
            "",
            f"Session `{self.session_id}`  —  {self.metadata.created_at}",
            "",
            "This file is a convenience rendering. The canonical record is",
            "`conversation.jsonl` and `tool_calls.jsonl`.",
            "",
        ]
        for kind, item in merged:
            if kind == "turn":
                who = {"user": "User", "assistant": "Agent"}.get(item["role"], item["role"].title())
                lines.append(f"## {item['timestamp']} — {who}")
                lines.append("")
                lines.append(item.get("text") or "_(tool call only)_")
                lines.append("")
            else:
                lines.append(f"### Tool call: {item['tool_name']}")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(item.get("arguments"), indent=2, ensure_ascii=False))
                lines.append("```")
                outcome = "ok" if item.get("ok") else ("error" if item.get("ok") is False else "-")
                lines.append(
                    f"Result: **{outcome}**  —  revision "
                    f"{item.get('revision_before')} → {item.get('revision_after')}"
                )
                lines.append("")
        return self._write(self.directory / "conversation.md", "\n".join(lines) + "\n")

    def _artifact(self, name: str, relative_path: str, records: int | None = None) -> None:
        path = self.directory / relative_path
        if not path.is_file():
            return
        self._manifest_artifacts[name] = ArtifactEntry(
            path=relative_path,
            sha256=sha256_path(path),
            bytes=path.stat().st_size,
            records=records,
        )

    def write_manifest(self) -> Path:
        """Index every artifact with its SHA-256, for SI export and audit."""
        self._manifest_artifacts: dict[str, ArtifactEntry] = {}
        self._artifact("metadata", "metadata.json")
        self._artifact("conversation", "conversation.jsonl", self._counts["conversation"])
        self._artifact("conversation_markdown", "conversation.md")
        self._artifact("tools", "tool_calls.jsonl", self._counts["tool_calls"])
        self._artifact("events", "events.jsonl", self._counts["events"])
        self._artifact("final_experiment", "final_experiment.yaml")
        self._artifact("resolved_workflow", "resolved_workflow.json")
        self._artifact("execution_config", "execution_config.yaml")
        self._artifact("validation_report", "validation_report.json")
        self._artifact("simulation_report", "simulation_report.json")
        self._artifact("protocol", "generated_protocol.py")
        self._artifact("input_config", "input_config.yaml")
        for row in self._revision_rows:
            index = row["revision_index"]
            self._artifact(f"revision_{index:03d}", f"revisions/revision_{index:03d}.yaml")
        for row in self._run_rows:
            self._artifact(f"robot_run_{row['run_index']:03d}", row["file"])
        if self.metadata.machine_profile:
            candidate = Path(self.metadata.machine_profile)
            absolute = candidate if candidate.is_absolute() else REPO_ROOT / candidate
            if absolute.is_file():
                self._manifest_artifacts["machine_profile"] = ArtifactEntry(
                    path=str(self.metadata.machine_profile),
                    sha256=sha256_path(absolute),
                    bytes=absolute.stat().st_size,
                )

        final = self._revision_rows[-1] if self._revision_rows else {}
        manifest = Manifest(
            session_id=self.session_id,
            written_at=now_iso(),
            mode=self.mode,
            experiment_id=self.metadata.experiment_id,
            experiment_name=self.metadata.experiment_name,
            final_revision=final.get("revision"),
            final_revision_index=final.get("revision_index"),
            final_config_hash=final.get("config_hash"),
            final_resolved_hash=final.get("resolved_hash"),
            status=self._status or final.get("status"),
            revision_count=len(self._revision_rows),
            conversation_turns=self._counts["conversation"],
            tool_calls=self._counts["tool_calls"],
            events=self._counts["events"],
            robot_run_count=len(self._run_rows),
            degraded=self.degraded,
            degraded_reasons=list(self.degraded_reasons),
            artifacts=self._manifest_artifacts,
            revisions=list(self._revision_rows),
            robot_runs=list(self._run_rows),
        )
        simulation_path = self.directory / "simulation_report.json"
        if simulation_path.is_file():
            try:
                manifest.simulated_hash = json.loads(
                    simulation_path.read_text(encoding="utf-8")
                ).get("resolved_hash")
            except (OSError, ValueError):
                pass
        self._write_json(self.manifest_path, manifest.model_dump(mode="json"))
        return self.manifest_path

    # ---- live-execution readiness -----------------------------------------
    def live_readiness(self, session: Any) -> tuple[bool, list[str]]:
        """What is missing from the record before the robot may be allowed to move."""
        missing: list[str] = []
        if self.degraded:
            missing.append(f"provenance writes failed: {'; '.join(self.degraded_reasons)}")
        if not self.metadata_path.is_file():
            missing.append("metadata.json was never written")
        experiment_id = session.experiment.experiment_id
        index = self._index_for(experiment_id, session.revision)
        if index is None:
            missing.append(f"revision {session.revision} was never snapshotted")
        else:
            for relative_path in (
                f"revisions/revision_{index:03d}.yaml",
                f"resolved/revision_{index:03d}.json",
                f"simulation/revision_{index:03d}.json",
                f"protocols/revision_{index:03d}.py",
            ):
                if not (self.directory / relative_path).is_file():
                    missing.append(f"{relative_path} is missing")
        if not (self.directory / "final_experiment.yaml").is_file():
            missing.append("final_experiment.yaml is missing")
        if not self.events_path.is_file():
            missing.append("events.jsonl is missing")
        else:
            try:
                text = self.events_path.read_text(encoding="utf-8")
            except OSError as exc:
                missing.append(f"events.jsonl is unreadable: {exc}")
                text = ""
            if f'"{Event.LIVE_APPROVED}"' not in text:
                missing.append("no LIVE_APPROVED event is on file")
        if not os.access(self.directory, os.W_OK):
            missing.append(f"{self.directory} is not writable")
        return (not missing), missing


# ---------------------------------------------------------------------------
# Module-level active session
# ---------------------------------------------------------------------------

_ACTIVE: ProvenanceSession | None = None


def active_session() -> ProvenanceSession | None:
    """The session currently recording, if provenance logging is switched on."""
    return _ACTIVE


def set_active_session(session: ProvenanceSession | None) -> None:
    global _ACTIVE
    _ACTIVE = session


def create_session(
    label: str = "agent",
    mode: str = "agent",
    thread_id: str | None = None,
    root: Path | None = None,
    activate: bool = True,
    **metadata: Any,
) -> ProvenanceSession:
    """Open one durable experiment-session directory and start recording."""
    base = Path(root) if root is not None else SESSIONS_ROOT
    session_id = uuid.uuid4().hex[:8]
    directory = base / f"{directory_stamp()}_{slugify(label)}_{session_id}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ProvenanceError(
            f"could not create the provenance session directory {directory}: {exc}"
        ) from exc

    session = ProvenanceSession(directory, session_id, mode=mode, thread_id=thread_id)
    if activate:
        set_active_session(session)
    session.start(
        intent_schema_version=_intent_version(),
        packages=package_versions(),
        environment=safe_environment(),
        system_prompt_sha256=_system_prompt_hash(),
        **git_provenance(),
        **interpreter_provenance(),
        **metadata,
    )
    return session


def close_session(session: ProvenanceSession | None = None, status: str | None = None) -> None:
    target = session or active_session()
    if target is None:
        return
    target.close(status=status)
    if target is active_session():
        set_active_session(None)


def _intent_version() -> str | None:
    try:
        from ..intent import INTENT_SCHEMA_VERSION

        return INTENT_SCHEMA_VERSION
    except Exception:
        return None


def _system_prompt_hash() -> str | None:
    try:
        from ..agent.prompts import SYSTEM_PROMPT

        return sha256_text(SYSTEM_PROMPT)
    except Exception:
        return None


def tool_schema_provenance(tools: Any) -> dict[str, Any]:
    """Hash the tool contract the model was given, so SI can state what it could call."""
    described = []
    for item in tools or []:
        described.append(
            {
                "name": getattr(item, "name", str(item)),
                "description": (getattr(item, "description", "") or "").strip(),
                "args": jsonable(getattr(item, "args", {}) or {}),
            }
        )
    described.sort(key=lambda entry: entry["name"])
    return {
        "tool_names": [entry["name"] for entry in described],
        "tool_schema_sha256": sha256_text(
            json.dumps(described, sort_keys=True, separators=(",", ":"))
        ),
    }


def live_execution_readiness(session: Any) -> tuple[bool, str]:
    """Whether the scientific record is complete enough to allow physical motion.

    An unlogged run cannot be reconstructed, so it is refused. This gate reports
    only on the record; every safety gate in :mod:`sers_engine.execution` is
    unchanged and still has to pass on its own.
    """
    recorder = getattr(session, "provenance", None) or active_session()
    if recorder is None:
        return False, (
            "no provenance session is recording this experiment; live execution is "
            "blocked because the run could not be reconstructed afterwards. Run it "
            "through scripts/sers_agent.py or scripts/run_sers_experiment.py."
        )
    ok, missing = recorder.live_readiness(session)
    if ok:
        return True, f"the SI record is complete at {recorder.directory}"
    return False, "the SI record is incomplete: " + "; ".join(missing)
