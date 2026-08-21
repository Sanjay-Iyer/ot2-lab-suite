from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from src.agents import robot_http_tools as rht
from src.agents import robot_protocol_registry as rpr
from src.agents.robot_protocol_registry import RobotProtocol
from src.agents.vial_print_agent import SYSTEM_PROMPT
from src.utils.hashing import hash_file


@pytest.fixture()
def workspace_tmp():
    root = Path(__file__).resolve().parent.parent / ".test_tmp" / "robot_http_tools"
    base = root / uuid.uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    try:
        yield base
    finally:
        if base.resolve().is_relative_to(root.resolve()):
            shutil.rmtree(base, ignore_errors=True)


def _patch_vial_protocol(monkeypatch, workspace_tmp):
    runner = workspace_tmp / "run_vial_print_robot.py"
    protocol = workspace_tmp / "vial_dilution_print_latest.py"
    runner.write_text("print('runner')\n", encoding="utf-8")
    protocol.write_text("metadata = {}\n", encoding="utf-8")
    entry = RobotProtocol(
        key="vial_dilution_print",
        display_name="Vial dilution -> paper print",
        description="test entry",
        runner_script=runner,
        protocol_path=protocol,
        runner_transport="http_api",
        exposed_to_agent=True,
    )
    monkeypatch.setattr(rpr, "ROBOT_PROTOCOLS", {"vial_dilution_print": entry})
    return entry


def test_protocol_registry_lists_vial_print():
    out = rht.list_robot_http_protocols.invoke({})
    assert "vial_dilution_print" in out
    assert "http_api" in out


def test_vial_print_http_refuses_live_without_an_ai_confirmation_escape_hatch(monkeypatch, workspace_tmp):
    _patch_vial_protocol(monkeypatch, workspace_tmp)

    def fail_run(*args, **kwargs):
        raise AssertionError("runner should not be called for AI-requested live motion")

    monkeypatch.setattr(rht.subprocess, "run", fail_run)
    out = rht.run_vial_print_robot_http.invoke(
        {"robot_ip": "169.254.46.57", "live": True}
    )
    assert "REFUSED" in out
    assert "AI tools cannot authorize live OT-2 motion" in out


def test_vial_print_http_refuses_without_passing_simulation(monkeypatch, workspace_tmp):
    _patch_vial_protocol(monkeypatch, workspace_tmp)
    sim_path = workspace_tmp / "simulations.json"
    sim_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(rht, "SIMULATION_RECORDS_PATH", sim_path)
    monkeypatch.setattr(rht.Config, "live_robot_llm_auth_error", lambda: None)

    def fail_run(*args, **kwargs):
        raise AssertionError("runner should not be called without a PASS simulation")

    monkeypatch.setattr(rht.subprocess, "run", fail_run)
    out = rht.run_vial_print_robot_http.invoke(
        {"robot_ip": "169.254.46.57"}
    )
    assert "REFUSED" in out
    assert "No PASS simulation record" in out


def test_vial_print_http_builds_nonlive_runner_command(monkeypatch, workspace_tmp):
    entry = _patch_vial_protocol(monkeypatch, workspace_tmp)
    sha = hash_file(entry.protocol_path)
    sim_path = workspace_tmp / "simulations.json"
    sim_path.write_text(json.dumps({sha: {"status": "PASS"}}), encoding="utf-8")
    monkeypatch.setattr(rht, "SIMULATION_RECORDS_PATH", sim_path)
    monkeypatch.setattr(rht.Config, "live_robot_llm_auth_error", lambda: None)

    captured = {}

    def fake_run(cmd, cwd, capture_output, text):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["capture_output"] = capture_output
        captured["text"] = text
        return subprocess.CompletedProcess(cmd, 0, stdout="Run finished with status: succeeded\n", stderr="")

    monkeypatch.setattr(rht.subprocess, "run", fake_run)
    out = rht.run_vial_print_robot_http.invoke(
        {
            "robot_ip": "169.254.46.57",
            "live": False,
            "paper_start_column": 3,
            "do_dilution": False,
            "do_print": True,
            "poll_seconds": 2.5,
        }
    )

    cmd = captured["cmd"]
    assert out.startswith("Vial-print HTTP robot run SUCCESS")
    assert cmd[0] == sys.executable
    assert str(entry.runner_script) in cmd
    assert "--robot-host" in cmd and "169.254.46.57" in cmd
    assert "--protocol" in cmd and str(entry.protocol_path) in cmd
    assert "--live" not in cmd
    assert "--skip-build" in cmd
    assert "--skip-validate" in cmd
    assert "--no-dilution" in cmd
    assert "--no-print" not in cmd
    assert "--paper-start-column" in cmd and "3" in cmd
    assert "--poll-seconds" in cmd and "2.5" in cmd
    assert captured["capture_output"] is True
    assert captured["text"] is True


def test_vial_print_agent_prompt_requires_manual_live_handoff():
    assert "Agent tools cannot authorize or start live liquid handling" in SYSTEM_PROMPT
    assert "Stop at the handoff" in SYSTEM_PROMPT
    assert "deploy_protocol_to_robot() or execute_protocol_on_robot()" in SYSTEM_PROMPT
