"""Deterministic answers about what changed, read from stored revisions.

History questions ("what have I changed?", "what was the original plate slot?",
"what changed after revision 4?") are answered from ExperimentState's snapshots and
history records, never reconstructed by the LLM from conversation memory.
"""
from __future__ import annotations

import re
from typing import Any

from src.agents.dye_demo.intent import fields_mentioned, labware_mentions
from src.agents.dye_demo.model import EDITABLE_FIELDS, FieldError, field_label, get_path, resolve_path

_ORIGINAL = re.compile(r"\b(?:original|initial|starting|startup|first|old|previous|used\s+to\s+be)\b", re.I)
_AFTER_REVISION = re.compile(r"\b(?:after|since)\s+revision\s+(\d+)\b", re.I)
_UNCHANGED = re.compile(r"\b(?:still\s+the\s+same|remain(?:s|ed)?\s+the\s+same|unchanged|same\s+as\s+(?:at\s+)?"
                        r"(?:startup|the\s+start|the\s+beginning))\b", re.I)
_WHAT_REVISION = re.compile(r"\b(?:what|which)\s+revision\b", re.I)
_CHANGED = re.compile(r"\b(?:what\s+(?:have|did)\s+(?:i|we)\s+chang|what(?:'s|\s+has|\s+have)?\s+(?:been\s+)?changed|"
                      r"history|(?:list|show)\s+(?:me\s+)?(?:the\s+|my\s+|all\s+)?changes)\b", re.I)


def _fmt(path: str, value: Any) -> str:
    from src.agents.dye_demo.render import format_value

    return format_value(path, value)


def editable_values(config: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for canonical in EDITABLE_FIELDS:
        try:
            values[canonical] = get_path(config, resolve_path(config, canonical))
        except FieldError:
            continue
    return values


def differences(before: dict[str, Any], after: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    old, new = editable_values(before), editable_values(after)
    return [(path, old.get(path), new.get(path)) for path in EDITABLE_FIELDS
            if old.get(path) != new.get(path)]


def previous_slots(state) -> dict[str, Any]:
    """Where each labware was before its most recent recorded move in this session."""
    result: dict[str, Any] = {}
    for record in state.history:
        for change in record["changes"]:
            parts = change["path"].split(".")
            if parts[0] == "deck" and parts[-1] == "slot":
                result[parts[1]] = change["before"]
    return result


def _paths_in(text: str) -> list[str]:
    paths = fields_mentioned(text)
    for role, _, _ in labware_mentions(text):
        path = f"deck.{role}.slot"
        if path not in paths:
            paths.append(path)
    return [path for path in paths if path in EDITABLE_FIELDS]


def answer_history_question(text: str, state) -> str | None:
    start = state.snapshots[0]["config"]
    now = state.config
    after = _AFTER_REVISION.search(text)
    if after:
        revision = int(after.group(1))
        records = [record for record in state.history if record["revision"] > revision]
        if revision > state.revision:
            return f"There is no revision {revision} yet; the experiment is at revision {state.revision}."
        if not records:
            return f"Nothing has changed after revision {revision} (the experiment is at revision {state.revision})."
        return "HISTORY\n" + "\n".join(_record_lines(records))
    if _UNCHANGED.search(text):
        changed = {path for path, _, _ in differences(start, now)}
        same = [field_label(path) for path in EDITABLE_FIELDS if path not in changed
                and path in editable_values(now)]
        lines = ["STILL THE SAME AS AT STARTUP (revision 0)"] + [f"  {label}" for label in same]
        diff = differences(start, now)
        if diff:
            lines += ["CHANGED SINCE STARTUP"] + [f"  {field_label(p)}: {_fmt(p, a)} -> {_fmt(p, b)}" for p, a, b in diff]
        return "\n".join(lines)
    if _WHAT_REVISION.search(text):
        return (f"The experiment is at revision {state.revision} "
                f"({len(state.history)} applied change set(s) this session).")
    if _ORIGINAL.search(text):
        paths = _paths_in(text)
        if paths:
            startup, current = editable_values(start), editable_values(now)
            return "\n".join(f"{field_label(path)}: {_fmt(path, startup.get(path))} at startup (revision 0); "
                             f"now {_fmt(path, current.get(path))}." for path in paths)
    if _CHANGED.search(text) or _ORIGINAL.search(text):
        if not state.history:
            return "Nothing has been changed yet in this session (revision 0)."
        lines = ["HISTORY"] + _record_lines(state.history)
        diff = differences(start, now)
        lines += ["COMPARED WITH STARTUP"] + ([f"  {field_label(p)}: {_fmt(p, a)} -> {_fmt(p, b)}" for p, a, b in diff]
                                             or ["  every setting is back to its startup value"])
        return "\n".join(lines)
    return None


def _record_lines(records: list[dict[str, Any]]) -> list[str]:
    lines = []
    for record in records:
        lines.append(f"  revision {record['revision']} by {record['operator']} ({record.get('source', 'conversation')})")
        for change in record["changes"]:
            lines.append(f"    {field_label(change['path'])}: {_fmt(change['path'], change['before'])} -> "
                         f"{_fmt(change['path'], change['after'])}")
        for key, value in (record.get("physical") or {}).items():
            lines.append(f"    recorded physical state: {key.replace('_', ' ')} = "
                         f"{'cleared' if value is None else value.get('wells', value)}")
    return lines
