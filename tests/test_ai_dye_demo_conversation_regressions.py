"""Replays every conversation in tests/conversation_regressions/ through the real DemoSession.

Each YAML file is a minimal conversation that once broke a conversational invariant
(found by scripts/ai_dye_demo_redteam.py or by hand). Messages carry the ground-truth label
the invariants check, and optionally the model replies recorded when the failure was found,
so the replay is deterministic without a model. Simulation only: the session's executor is
a recording function.

    expected:
      violations: []              # invariant names still allowed (normally none)
      final: {revision: 1, pending: null, clarifying: false, runs: 0}
      final_config: {deck.tuberack.slot: 6}
      final_physical: {dilutions_prepared: null}
      turns:
        - turn: 2
          classification: run
          output_contains: ["Nothing has started"]
          output_excludes: ["STARTING"]
          proposal_paths: [deck.tuberack.slot]     # a proposal with exactly these paths was shown
          unverified: [deck.paper.slot]            # ... and these were flagged
          no_proposal: true
          pending_after: 1
          clarifying_after: true
          run: false
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.agents.dye_demo.model import get_path, resolve_path
from src.agents.dye_demo.redteam.harness import replay

CASES = sorted((Path(__file__).parent / "conversation_regressions").glob("*.yaml"))


def _events(row, kind):
    return [event for event in row["events"] if event["type"] == kind]


@pytest.mark.parametrize("path", CASES, ids=[case.stem for case in CASES])
def test_conversation_regression(path, tmp_path):
    case = yaml.safe_load(path.read_text(encoding="utf-8"))
    settings = case.get("settings") or {}
    expected = case.get("expected") or {}
    result = replay(case["messages"], workdir=tmp_path, live_logic=bool(settings.get("live_logic")),
                    soft_labels=bool(settings.get("soft_labels")), keep_transcript=True, return_session=True)
    allowed = set(expected.get("violations") or [])
    unexpected = [violation for violation in result["violations"] if violation["invariant"] not in allowed]
    assert not unexpected, f"{case['name']}: {unexpected}"

    final = expected.get("final") or {}
    for key, value in final.items():
        assert result["final"][key] == value, f"{case['name']}: final {key} is {result['final'][key]!r}"
    session = result["session"]
    for dotted, value in (expected.get("final_config") or {}).items():
        config = session.state.config
        assert get_path(config, resolve_path(config, dotted)) == value, f"{case['name']}: {dotted}"
    for key, value in (expected.get("final_physical") or {}).items():
        assert session.state.physical.get(key) == value, f"{case['name']}: physical {key}"

    rows = result["transcript"]
    for check in expected.get("turns") or []:
        row = rows[check["turn"] - 1]
        where = f"{case['name']} turn {check['turn']} ({row['text']!r})"
        if "classification" in check:
            assert row["classification"] == check["classification"], where
        for text in check.get("output_contains", []):
            assert text in row["output"], f"{where}: missing {text!r} in\n{row['output']}"
        for text in check.get("output_excludes", []):
            assert text not in row["output"], f"{where}: unexpected {text!r} in\n{row['output']}"
        proposals = _events(row, "proposal")
        if "proposal_paths" in check:
            assert any(sorted(event["paths"]) == sorted(check["proposal_paths"]) for event in proposals), \
                f"{where}: proposals {[event['paths'] for event in proposals]}"
        if "unverified" in check:
            assert any(sorted(event["unverified"]) == sorted(check["unverified"]) for event in proposals), \
                f"{where}: flagged {[event['unverified'] for event in proposals]}"
        if check.get("no_proposal"):
            assert not proposals, f"{where}: unexpected proposal {proposals}"
        if "pending_after" in check:
            assert row["pending_after"] == check["pending_after"], where
        if "clarifying_after" in check:
            assert session.turns[check["turn"] - 1]["clarifying_after"] == check["clarifying_after"], where
        if "run" in check:
            assert bool(_events(row, "run")) == check["run"], where


def test_every_regression_file_names_the_invariant_and_its_messages():
    assert CASES, "tests/conversation_regressions/ is empty"
    for path in CASES:
        raw = path.read_text(encoding="utf-8")
        case = yaml.safe_load(raw)
        assert case.get("name") == path.stem
        assert case.get("messages") and case.get("invariant") and case.get("description")
        # In YAML " #" starts a comment: an unquoted "I approve proposal #1." silently becomes "I approve proposal".
        unquoted = [line for line in raw.splitlines() if re.match(r"^\s*-?\s*text: [^'\"|>].* #", line)]
        assert not unquoted, f"{path.name}: quote message texts that contain ' #': {unquoted}"
