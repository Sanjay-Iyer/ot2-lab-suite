from __future__ import annotations

import ast
import importlib.util
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
RUNNER_PATH = REPO / "scripts" / "run_p300_smoke_test.py"
PROTOCOL_PATH = (
    REPO / "src" / "protocols" / "generated" / "p300_smoke_test.py"
)


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_p300_smoke_test", RUNNER_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _protocol_constants() -> dict[str, object]:
    tree = ast.parse(PROTOCOL_PATH.read_text(encoding="utf-8"))
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                try:
                    values[target.id] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
    return values


def test_protocol_is_api_215_p300_only_with_expected_deck() -> None:
    values = _protocol_constants()
    source = PROTOCOL_PATH.read_text(encoding="utf-8").lower()

    assert values["metadata"]["apiLevel"] == "2.15"
    assert values["PIPETTE_NAME"] == "p300_multi_gen2"
    assert values["PIPETTE_MOUNT"] == "right"
    assert values["TIPRACK_LOAD_NAME"] == "opentrons_96_tiprack_300ul"
    assert values["TIPRACK_SLOT"] == "8"
    assert values["PLATE_SLOT"] == "4"
    assert values["PAPER_SLOT"] == "5"
    assert values["DRY_AIR_VOLUME_UL"] == 30.0
    assert values["COMPARISON_DWELL_SECONDS"] == 5.0
    assert "p20" not in source
    assert "configure_nozzle_layout" not in source


def test_default_invocation_only_simulates(monkeypatch) -> None:
    runner = _load_runner()
    called: dict[str, object] = {}

    def fail_network(*args, **kwargs):
        raise AssertionError("default invocation must not make HTTP requests")

    def fake_run(command, cwd, check, capture_output, text):
        called["command"] = command
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="P300 dry-motion smoke test complete\n",
            stderr="",
        )

    monkeypatch.setattr(runner.requests, "request", fail_network)
    monkeypatch.setattr(runner.requests, "post", fail_network)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    assert runner.main([]) == 0
    assert "opentrons.simulate" in " ".join(called["command"])


def test_execute_flag_is_required_for_http_path(monkeypatch) -> None:
    runner = _load_runner()
    called: dict[str, object] = {}

    def fake_execute(robot_ip, protocol_path, labware_path, poll_seconds):
        called["execute"] = (
            robot_ip,
            protocol_path,
            labware_path,
            poll_seconds,
        )
        return 0

    def fail_simulate(*args, **kwargs):
        raise AssertionError("--execute should select the explicit HTTP path")

    monkeypatch.setattr(runner, "execute_protocol", fake_execute)
    monkeypatch.setattr(runner, "simulate_protocol", fail_simulate)

    assert runner.main(["--robot-ip", "192.0.2.10", "--execute"]) == 0
    assert called["execute"][0] == "192.0.2.10"
