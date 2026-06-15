"""Local JSON run history for scripts that talk to the OT-2."""
from __future__ import annotations

import getpass
import json
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
DEFAULT_LOG_DIR = REPO / "src" / "printing" / "logs" / "robot_runs"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _local_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def repo_relative(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(REPO))
    except ValueError:
        return str(resolved)


class RobotRunLog:
    """Append-style run record that is rewritten as each script progresses."""

    def __init__(self, script: str, *, log_dir: Path = DEFAULT_LOG_DIR) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(script).stem
        self.path = log_dir / f"{stem}_{_local_stamp()}_{uuid.uuid4().hex[:8]}.json"
        self.record: dict[str, Any] = {
            "schema_version": 1,
            "started_at": _utc_now(),
            "finished_at": None,
            "status": "started",
            "script": script,
            "command": [sys.executable, *sys.argv],
            "cwd": str(Path.cwd()),
            "user": getpass.getuser(),
            "host": platform.node(),
            "events": [],
        }
        self.write()

    def update(self, **fields: Any) -> None:
        self.record.update(_jsonable(fields))
        self.write()

    def event(self, name: str, **details: Any) -> None:
        self.record["events"].append({
            "time": _utc_now(),
            "name": name,
            "details": _jsonable(details),
        })
        self.write()

    def finish(self, status: str, *, exit_code: int | None = None, error: str | None = None) -> None:
        self.record["status"] = status
        self.record["finished_at"] = _utc_now()
        if exit_code is not None:
            self.record["exit_code"] = exit_code
        if error:
            self.record["error"] = error
        self.write()

    def write(self) -> None:
        self.path.write_text(json.dumps(self.record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
