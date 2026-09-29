"""Per-user experiment memory: each successful run saved as small YAML files, found again by who ran it and when.

    experiment_history/<user>/index.yaml       one entry per run: number, time, mode, summary, file
    experiment_history/<user>/run_0001.yaml    the run: who, when, a readable summary of its settings, and the full
                                               configuration it ran (what loading proposes again)

The LLM reads what the scientist asks ("load my last experiment", "what did I run yesterday?", "load Sanjay run 12",
"load my experiment from this morning") into a HistoryQuery; this module resolves it deterministically against the
saved files. Several matches are never guessed: the session asks which one. Loading only proposes the saved plan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from src.agents.dye_demo.model import REPO, fmt_factor, liquid_handling, positions_text
from src.agents.dye_demo.plan import build_plan

HISTORY_DIR = REPO / "experiment_history"
PARTS_OF_DAY = {"morning": (5, 12), "afternoon": (12, 17), "evening": (17, 24), "night": (0, 5)}
_SAME_TIME_MIN = 45                     # "the 1:30 one": a run within 45 minutes of the time named


def user_id(name: str) -> str:
    """The folder a person's runs are saved in (the session's operator_id: "Sanjay Iyer" -> sanjay_iyer)."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "unknown"


@dataclass
class HistoryQuery:
    """What the scientist asked about saved runs, as the router read it. Empty fields were not said."""
    action: str = "load"                # load (propose it again) | list (say what was run)
    user: str = ""                      # someone else ("load Sanjay run 12"); empty: the operator
    run: int | None = None
    date: str = ""                      # YYYY-MM-DD
    days_ago: int | None = None         # 0 today, 1 yesterday
    part_of_day: str = ""               # morning | afternoon | evening | night
    time: str = ""                      # HH:MM, 24 h ("the 1:30 one", read in context)
    which: str = ""                     # last | all

    @classmethod
    def from_router(cls, data: dict[str, Any]) -> "HistoryQuery":
        def number(value: Any) -> int | None:
            try:
                return int(str(value).strip().lstrip("#"))
            except (TypeError, ValueError):
                return None

        action = str(data.get("action") or "load").strip().lower()
        return cls(action="list" if action in {"list", "show", "describe", "what"} else "load",
                   user=str(data.get("user") or "").strip(), run=number(data.get("run")),
                   date=str(data.get("date") or "").strip(), days_ago=number(data.get("days_ago")),
                   part_of_day=str(data.get("part_of_day") or "").strip().lower(),
                   time=str(data.get("time") or "").strip(), which=str(data.get("which") or "").strip().lower())

    @property
    def narrows_by_time(self) -> bool:
        return bool(self.date or self.days_ago is not None or self.part_of_day)


# ── saving ────────────────────────────────────────────────────────────────────────

def summarize(config: dict[str, Any]) -> str:
    """One line a scientist recognises the run by."""
    plan = build_plan(config)
    parts = []
    if plan.do_dilution and plan.wells:
        factors = ", ".join(fmt_factor(well.factor) for well in plan.wells)
        parts.append(f"{len(plan.wells)} dilution{'s' if len(plan.wells) != 1 else ''} ({factors}) in plate column "
                     f"{plan.plate_column}")
    elif plan.do_print:
        parts.append("print only (no dilutions made)")
    if plan.do_print:
        replicates = int((config.get("print") or {}).get("replicates", 1))
        positions = plan.print_positions
        parts.append(f"printed on {len(positions)} paper position{'s' if len(positions) != 1 else ''} "
                     f"({positions_text(positions, limit=6)}), {replicates} total replicate"
                     f"{'s' if replicates != 1 else ''}, {plan.total_drops} drop{'s' if plan.total_drops != 1 else ''}")
    policy = {"single_tip": "one tip for the whole run", "per_liquid": "one tip per liquid",
              "new_tip_every_transfer": "a new tip every transfer"}.get(plan.policy, plan.policy)
    parts.append(policy)
    return "; ".join(parts)


def _settings(config: dict[str, Any]) -> dict[str, Any]:
    """The settings a scientist reads in the file (loading uses the full configuration next to it)."""
    dilution, printing, tips = config.get("dilution") or {}, config.get("print") or {}, config.get("tips") or {}
    plan = build_plan(config)
    return {
        "dilution": {key: dilution.get(key) for key in ("enabled", "factors", "plate_column", "start_row", "rows",
                                                        "total_volume_ul", "prepared_volume_ul") if key in dilution},
        "print": {
            "enabled": printing.get("enabled", True),
            "droplet_volume_ul": printing.get("droplet_volume_ul"),
            "drops_per_position": printing.get("droplets_per_spot", 1),
            "total_replicates": printing.get("replicates", 1),
            "paper_start_column": printing.get("paper_start_column", 1),
            "print_map": [{"from": op.source, "to": op.destination, "drops": op.droplets}
                          for op in plan.operations if op.kind == "print"],
        },
        "tips": dict(tips),
        "mixing": dict(config.get("mixing") or {}),
        "liquid_handling": liquid_handling(config),
        "deck": {role: (spec or {}).get("slot") for role, spec in (config.get("deck") or {}).items()},
    }


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def load_index(user: str, directory: Path = HISTORY_DIR) -> list[dict[str, Any]]:
    data = _read_yaml(Path(directory) / user_id(user) / "index.yaml")
    runs = data.get("runs") if isinstance(data, dict) else None
    return [entry for entry in runs or [] if isinstance(entry, dict) and "run" in entry]


def save_run(*, operator: str, config: dict[str, Any], mode: str, session: dict[str, Any] | None = None,
             directory: Path = HISTORY_DIR, now: datetime | None = None) -> dict[str, Any]:
    """Save one successful run for `operator`; returns its index entry."""
    folder = Path(directory) / user_id(operator)
    folder.mkdir(parents=True, exist_ok=True)
    index = load_index(operator, directory)
    number = max((int(entry["run"]) for entry in index), default=0) + 1
    stamp = (now or datetime.now()).astimezone().isoformat(timespec="seconds")
    saved = {key: value for key, value in config.items() if key != "session"}
    summary = summarize(config)
    record = {"user": operator, "user_id": user_id(operator), "run": number, "saved_at": stamp, "mode": mode,
              "session": dict(session or {}), "summary": summary, "settings": _settings(config), "config": saved}
    name = f"run_{number:04d}.yaml"
    (folder / name).write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True), encoding="utf-8")
    entry = {"run": number, "saved_at": stamp, "mode": mode, "summary": summary, "file": name}
    (folder / "index.yaml").write_text(yaml.safe_dump({"user": operator, "runs": index + [entry]}, sort_keys=False,
                                                      allow_unicode=True), encoding="utf-8")
    return entry


def load_run(user: str, entry: dict[str, Any], directory: Path = HISTORY_DIR) -> dict[str, Any] | None:
    """The saved configuration of one index entry, or None when its file is missing or unreadable."""
    data = _read_yaml(Path(directory) / user_id(user) / str(entry.get("file") or f"run_{int(entry['run']):04d}.yaml"))
    config = data.get("config") if isinstance(data, dict) else None
    return config if isinstance(config, dict) else None


# ── finding runs ──────────────────────────────────────────────────────────────────

def saved_time(entry: dict[str, Any]) -> datetime:
    """When a run was saved, as local time."""
    moment = datetime.fromisoformat(str(entry["saved_at"]))
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


def _day(query: HistoryQuery, now: datetime) -> date | None:
    if query.date:
        try:
            return date.fromisoformat(query.date[:10])
        except ValueError:
            return None
    if query.days_ago is not None:
        return (now - timedelta(days=query.days_ago)).date()
    if query.part_of_day:
        return now.date()                       # "this morning"
    return None


def _minutes(text: str) -> int | None:
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?", text.strip().lower())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if match.group(3):
        hour = hour % 12 + (12 if match.group(3).startswith("p") else 0)
    return hour * 60 + minute if 0 <= hour < 24 and 0 <= minute < 60 else None


def _near(entries: list[dict[str, Any]], time: str) -> list[dict[str, Any]]:
    """The runs nearest the time named: "the 1:30 one" is 13:30 when the runs were in the afternoon."""
    target = _minutes(time)
    if target is None:
        return entries
    candidates = []
    for entry in entries:
        moment = saved_time(entry)
        minutes = moment.hour * 60 + moment.minute
        # a 12-hour time without am/pm may mean either half of the day
        gaps = [abs(minutes - target)] + ([abs(minutes - (target + 720))] if target < 720 else [])
        candidates.append((min(gaps), entry))
    close = [(gap, entry) for gap, entry in candidates if gap <= _SAME_TIME_MIN]
    if not close:
        return []
    best = min(gap for gap, _ in close)
    return [entry for gap, entry in close if gap == best]


def find_runs(query: HistoryQuery, operator: str, *, directory: Path = HISTORY_DIR, now: datetime | None = None,
              among: list[dict[str, Any]] | None = None) -> tuple[str, list[dict[str, Any]]]:
    """(the user whose runs were searched, the matching index entries, oldest first). `among` limits the search to
    runs just offered as choices ("the 1:30 one")."""
    now = now or datetime.now()
    user = query.user or operator
    entries = list(among) if among is not None else load_index(user, directory)
    if query.run is not None:
        entries = [entry for entry in entries if int(entry["run"]) == query.run]
    day = _day(query, now)
    if day is not None:
        entries = [entry for entry in entries if saved_time(entry).date() == day]
    if query.part_of_day in PARTS_OF_DAY:
        start, end = PARTS_OF_DAY[query.part_of_day]
        entries = [entry for entry in entries if start <= saved_time(entry).hour < end]
    if query.time:
        entries = _near(entries, query.time)
    entries.sort(key=saved_time)
    if query.which == "last" and entries:
        entries = entries[-1:]
    elif query.action == "load" and not (query.narrows_by_time or query.time or query.run is not None) and entries:
        entries = entries[-1:]                  # "load my experiment": the most recent one
    return user, entries


def clock(entry: dict[str, Any]) -> str:
    return saved_time(entry).strftime("%I:%M %p").lstrip("0")


def when_text(query: HistoryQuery, now: datetime | None = None) -> str:
    now = now or datetime.now()
    day = _day(query, now)
    if day is None:
        return ""
    words = {0: "today", 1: "yesterday"}.get((now.date() - day).days, f"on {day:%A %d %B}")
    if query.part_of_day:
        words = f"this {query.part_of_day}" if words == "today" else f"{words} {query.part_of_day}"
    return words


def describe(entries: list[dict[str, Any]]) -> list[str]:
    return [f"run {entry['run']} at {clock(entry)} on {saved_time(entry):%d %b}"
            + ("" if entry.get("mode") == "LIVE" else f" ({str(entry.get('mode', '')).lower()})")
            + f": {entry.get('summary', '')}" for entry in entries]
