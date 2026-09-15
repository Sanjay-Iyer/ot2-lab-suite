"""Ctrl-C during a live dye-demo run: the robot run is stopped, confirmed, and recorded as aborted.

Offline only. The OT-2 robot server is a recording fake (no HTTP request leaves the process), the robot runner's
build step and upload are stubbed, and the runner subprocess is a fake process. What these tests prove is the software
path: an interrupt after the run started sends the stop action for that run and waits for the robot's finished state;
an interrupt before a run exists sends nothing; the session never dies on the interrupt, records the run as aborted,
and will not start another live run until the operator confirms the robot was checked. Whether a real OT-2 stops
promptly on that request can only be checked on the robot itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import scripts.run_vial_print_robot as runner
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.session import (
    RUN_ABORTED_EXIT_CODE,
    RUN_NOT_STARTED_EXIT_CODE,
    DemoSession,
    SessionLog,
    SessionSettings,
    SubprocessExecutor,
)


# ── the robot runner ────────────────────────────────────────────────────────────

class FakeRobot:
    """The robot server's run endpoints. Records every call; the run keeps "running" until it is stopped."""

    def __init__(self, *, after_stop=("stop-requested", "stopped"), stop_failures=0, status_before_stop="running"):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.stopped = False
        self.after_stop = list(after_stop)
        self.stop_failures = stop_failures
        self.status_before_stop = status_before_stop

    def request(self, method, robot_ip, path, **kwargs):
        body = kwargs.get("json")
        self.calls.append((method, path, body))
        if method == "POST" and path == "/runs":
            return {"data": {"id": "run-1"}}
        if method == "POST" and path == "/runs/run-1/actions":
            if body["data"]["actionType"] == "stop":
                if self.stop_failures:
                    self.stop_failures -= 1
                    raise RuntimeError("POST /runs/run-1/actions failed with HTTP 409")
                self.stopped = True
            return {"data": {"actionType": body["data"]["actionType"]}}
        if method == "GET" and path == "/runs/run-1":
            if not self.stopped:
                return {"data": {"status": self.status_before_stop}}
            status = self.after_stop.pop(0) if len(self.after_stop) > 1 else self.after_stop[0]
            return {"data": {"status": status}}
        raise AssertionError(f"unexpected robot call {method} {path}")

    def actions(self):
        return [body["data"]["actionType"] for method, path, body in self.calls if path.endswith("/actions")]


class FakeRunLog:
    def __init__(self, script, *, interrupt_on: str | None = None):
        self.path, self.events, self.finished, self.interrupt_on = "(fake run log)", [], None, interrupt_on

    def update(self, **fields):
        pass

    def event(self, name, **details):
        self.events.append(name)
        if name == self.interrupt_on:
            raise KeyboardInterrupt

    def finish(self, status, *, exit_code=None, error=None):
        self.finished = (status, exit_code)


class FakeTime:
    """Replaces the runner's clock: the first sleep while the run is still going is the operator's Ctrl-C."""

    def __init__(self, robot: FakeRobot, *, interrupt=True):
        self.robot, self.interrupt, self.now = robot, interrupt, 0.0

    def sleep(self, seconds):
        if self.interrupt and not self.robot.stopped and ("POST", "/runs/run-1/actions",
                                                          {"data": {"actionType": "play"}}) in self.robot.calls:
            self.interrupt = False
            raise KeyboardInterrupt
        self.now += seconds

    def monotonic(self):
        return self.now


@pytest.fixture()
def runner_env(tmp_path, monkeypatch):
    config = tmp_path / "ai_dye_demo.yaml"
    config.write_text("protocol_version: 19\n", encoding="utf-8")
    protocol = tmp_path / "ai_agent_dilution_print_demo_latest.py"
    protocol.write_text("# generated protocol stand-in\n", encoding="utf-8")
    status = tmp_path / "robot_run_status.json"
    robot = FakeRobot()
    logs: list[FakeRunLog] = []
    built: list[list[str]] = []
    monkeypatch.setenv("NO_PROXY", "localhost")
    monkeypatch.setattr(runner, "resolve_host", lambda host=None: host or "robot.test")
    monkeypatch.setattr(runner, "connection_summary", lambda host: f"Robot: test at {host}")
    monkeypatch.setattr(runner, "_run_local_step", lambda command, label: built.append(command))
    monkeypatch.setattr(runner, "_upload_protocol", lambda robot_ip, path: "protocol-1")
    monkeypatch.setattr(runner, "_request", robot.request)
    monkeypatch.setattr(sys, "argv", ["run_vial_print_robot.py", "--config", str(config), "--protocol", str(protocol),
                                      "--live", "--robot-host", "robot.test", "--status-file", str(status),
                                      "--poll-seconds", "0.5"])

    def use_log(**options):
        def factory(script):
            log = FakeRunLog(script, **options)
            logs.append(log)
            return log
        monkeypatch.setattr(runner, "RobotRunLog", factory)

    def use_time(**options):
        clock = FakeTime(robot, **options)
        monkeypatch.setattr(runner, "time", SimpleNamespace(sleep=clock.sleep, monotonic=clock.monotonic))
        return clock

    use_log()
    use_time()
    return SimpleNamespace(robot=robot, logs=logs, built=built, status=status, use_log=use_log, use_time=use_time,
                           monkeypatch=monkeypatch)


def test_ctrl_c_after_the_run_started_asks_the_ot2_to_stop_and_records_an_abort(runner_env, capsys):
    code = runner.main()
    out = capsys.readouterr().out
    assert code == runner.EXIT_RUN_ABORTED == RUN_ABORTED_EXIT_CODE == 130
    assert runner_env.robot.actions() == ["play", "stop"]
    assert "[stop] Asking the OT-2 to stop run run-1" in out and "[stop] The OT-2 reports the run as stopped." in out
    status = json.loads(runner_env.status.read_text(encoding="utf-8"))
    assert status["started"] is True and status["stop_requested"] is True and status["stop_confirmed"] is True
    assert status["robot_status"] == "stopped" and status["exit_code"] == 130 and status["run_id"] == "run-1"
    [log] = runner_env.logs
    assert log.finished == ("aborted", 130) and "stop_requested" in log.events and "stop_result" in log.events


def test_ctrl_c_before_a_run_exists_sends_nothing_to_the_robot(runner_env, capsys):
    def interrupted_build(command, label):
        raise KeyboardInterrupt

    runner_env.monkeypatch.setattr(runner, "_run_local_step", interrupted_build)
    code = runner.main()
    assert code == runner.EXIT_NOT_STARTED == RUN_NOT_STARTED_EXIT_CODE
    assert runner_env.robot.calls == []
    assert "nothing was started on the OT-2" in capsys.readouterr().out
    status = json.loads(runner_env.status.read_text(encoding="utf-8"))
    assert status["started"] is False and status["stop_requested"] is False
    assert runner_env.logs[0].finished == ("interrupted_before_run", runner.EXIT_NOT_STARTED)


def test_a_created_run_that_was_never_played_is_stopped_and_not_called_aborted(runner_env, capsys):
    runner_env.use_log(interrupt_on="run_created")
    code = runner.main()
    assert code == runner.EXIT_NOT_STARTED
    assert runner_env.robot.actions() == ["stop"]                  # never played
    assert "created but not started" in capsys.readouterr().out
    status = json.loads(runner_env.status.read_text(encoding="utf-8"))
    assert status["started"] is False and status["stop_requested"] is True


def test_a_stop_the_robot_never_confirms_is_reported_loudly(runner_env, capsys):
    robot = FakeRobot(stop_failures=10)
    runner_env.monkeypatch.setattr(runner, "_request", robot.request)
    runner_env.use_time(interrupt=False)
    log, status = FakeRunLog("x"), runner._StatusFile(str(runner_env.status))
    code = runner._abort_after_interrupt("robot.test", "run-1", True, log, status, poll_s=0.5, timeout_s=0.0)
    out = capsys.readouterr().out
    assert code == 130 and robot.actions() == ["stop"] * runner.STOP_ATTEMPTS
    assert "THE OT-2 DID NOT CONFIRM THAT THE RUN STOPPED" in out and "Opentrons App" in out
    recorded = json.loads(runner_env.status.read_text(encoding="utf-8"))
    assert recorded["stop_confirmed"] is False and "409" in recorded["stop_error"]
    assert log.finished == ("aborted", 130)


def test_a_run_that_finished_before_the_stop_is_recorded_as_succeeded(runner_env):
    robot = FakeRobot(stop_failures=10, status_before_stop="succeeded")
    runner_env.monkeypatch.setattr(runner, "_request", robot.request)
    runner_env.use_time(interrupt=False)
    log, status = FakeRunLog("x"), runner._StatusFile(str(runner_env.status))
    assert runner._abort_after_interrupt("robot.test", "run-1", True, log, status, poll_s=0.5) == 0
    assert log.finished == ("succeeded", 0)


def test_the_live_run_of_protocol_v19_still_sends_no_runtime_parameters(runner_env):
    runner_env.use_time(interrupt=True)
    runner.main()
    [create] = [body for method, path, body in runner_env.robot.calls if path == "/runs"]
    assert create == {"data": {"protocolId": "protocol-1"}}
    assert any("--set-dry-run" in command and "false" in command for command in runner_env.built)


# ── the session's subprocess executor ───────────────────────────────────────────

class FakeStdout:
    def __init__(self, items):
        self.items = list(items)

    def __iter__(self):
        return self

    def __next__(self):
        if not self.items:
            raise StopIteration
        item = self.items.pop(0)
        if item is KeyboardInterrupt:
            raise KeyboardInterrupt
        return item + "\n"


class FakeProcess:
    """The robot runner as the executor sees it: output lines (Ctrl-C arriving while reading), then its exit code."""

    def __init__(self, command, lines, code, status, interrupt_wait=False):
        self.command, self.stdout, self.code, self.interrupt_wait = command, FakeStdout(lines), code, interrupt_wait
        if status is not None:
            Path(command[command.index("--status-file") + 1]).write_text(json.dumps(status), encoding="utf-8")

    def wait(self):
        if self.interrupt_wait:
            self.interrupt_wait = False
            raise KeyboardInterrupt
        return self.code


def executor_for(lines, code, status, **options):
    processes = []

    def popen(command, **kwargs):
        processes.append(FakeProcess(command, lines, code, status, **options))
        return processes[-1]

    emitted: list[str] = []
    return SubprocessExecutor(popen=popen, emit=emitted.append), emitted, processes


def test_the_session_executor_waits_for_the_runner_to_stop_the_run(tmp_path):
    stopped = {"started": True, "stop_requested": True, "robot_status": "stopped", "stop_confirmed": True, "exit_code": 130}
    executor, emitted, processes = executor_for(
        ["[play]", KeyboardInterrupt, "[stop] Asking the OT-2 to stop run run-1 ...",
         "[stop] The OT-2 reports the run as stopped."], 130, stopped, interrupt_wait=True)
    log = SessionLog(tmp_path / "run")
    assert executor(tmp_path / "working.yaml", False, log) == 130
    command = processes[0].command
    assert "--live" in command and command[command.index("--status-file") + 1].endswith("robot_run_status.json")
    assert emitted[0] == "[play]" and emitted[1].startswith("[interrupt] Ctrl-C received. Waiting for the robot runner")
    assert emitted[-1] == "[stop] The OT-2 reports the run as stopped."
    assert executor.last_status["robot_status"] == "stopped"
    records = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    finished = [record for record in records if record["event"] == "command_finished"]
    assert any(record["event"] == "command_interrupted" for record in records)
    assert finished and finished[0]["exit_code"] == 130 and finished[0]["interrupted"] is True


@pytest.mark.parametrize("status, expected", [({"started": True}, RUN_ABORTED_EXIT_CODE), (None, RUN_ABORTED_EXIT_CODE),
                                              ({"started": False}, RUN_NOT_STARTED_EXIT_CODE)])
def test_a_runner_killed_by_the_interrupt_is_never_reported_as_a_clean_failure(tmp_path, status, expected):
    executor, _, _ = executor_for(["[play]", KeyboardInterrupt], 3221225786, status)
    assert executor(tmp_path / "working.yaml", False, SessionLog(tmp_path / "run")) == expected


# ── the session ─────────────────────────────────────────────────────────────────

class ScriptedExecutor:
    """Returns the scripted exit codes in order, with the status the robot runner would have reported."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls, self.last_status = list(outcomes), [], None

    def __call__(self, path, simulate, log):
        self.calls.append(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        code, self.last_status = outcome
        return code


STOPPED = (130, {"started": True, "stop_requested": True, "robot_status": "stopped", "stop_confirmed": True})
SUCCEEDED = (0, {"started": True, "robot_status": "succeeded"})


def live_session(tmp_path, executor, inputs, *, simulate=False):
    lines, outputs = list(inputs), []

    def read(prompt):
        if not lines:
            raise EOFError
        return lines.pop(0)

    settings = SessionSettings(simulate=simulate, config_source=DEFAULT_CONFIG, working_config=tmp_path / "working.yaml",
                               run_dir=tmp_path / "run", session_label="abort test", operator="Tester", raise_errors=True)
    session = DemoSession(settings, load_config(DEFAULT_CONFIG), llm=None, executor=executor, input_fn=read,
                          output_fn=outputs.append, sleep=lambda seconds: None)
    code = session.run()
    return session, "\n".join(outputs), code


def test_an_aborted_live_run_is_recorded_and_nothing_is_booked_as_done(tmp_path):
    executor = ScriptedExecutor(STOPPED)
    session, text, code = live_session(tmp_path, executor, ["run", "plan", "quit"])
    assert code == 0 and len(executor.calls) == 1
    [record] = session.state.runs
    assert (record["status"], record["exit_code"], record["robot"]["robot_status"]) == ("aborted", 130, "stopped")
    for expected in ("Run 1 was interrupted, and a stop was requested from the OT-2.", "The OT-2 reported the run as stopped.",
                     "Nothing from this run was recorded as done"):
        assert expected in text
    assert "Ctrl-C now if the deck does not match" not in text
    assert "press Ctrl-C once" in text and "Opentrons App" in text
    # the physical record and the plan are untouched: no dilutions, tips or paper positions booked as used
    assert session.state.physical == {"dilutions_prepared": None} and session.state.printed_positions == set()
    assert session.pending is None and session.state.config["tips"]["start_tip"] == "A1"
    assert session.state.run_blockers() == [] and (tmp_path / "run" / "executed_config_run1.yaml").is_file()
    summary = json.loads((tmp_path / "run" / "session.json").read_text(encoding="utf-8"))
    assert summary["runs"][0]["status"] == "aborted"


def test_after_an_abort_the_next_live_run_waits_for_the_robot_to_be_checked(tmp_path):
    executor = ScriptedExecutor(STOPPED, SUCCEEDED)
    session, text, _ = live_session(tmp_path, executor, ["run", "run", "no", "run", "yes", "quit"])
    assert "Run 1 did not finish (aborted)" in text and "Have you checked the robot" in text
    assert text.count("Run cancelled. Nothing was executed.") == 1
    assert len(executor.calls) == 2                                 # run 2 was refused, run 3 went ahead
    assert [run["status"] for run in session.state.runs] == ["aborted", "succeeded"]
    assert session.unverified_run is None


def test_an_unconfirmed_stop_tells_the_operator_to_check_the_robot(tmp_path):
    executor = ScriptedExecutor((130, {"started": True, "stop_requested": True, "robot_status": None}))
    session, text, _ = live_session(tmp_path, executor, ["run", "quit"])
    assert "The OT-2 did NOT confirm that the run stopped" in text and "Opentrons App" in text
    assert session.unverified_run is not None


def test_an_interrupt_that_reaches_the_session_never_ends_it(tmp_path):
    executor = ScriptedExecutor(KeyboardInterrupt())
    session, text, code = live_session(tmp_path, executor, ["run", "tips", "quit"])
    assert code == 0 and "TIP CONFIGURATION" in text.split("finished with exit code 130")[-1]
    assert session.state.runs[0]["status"] == "aborted"


def test_an_interrupt_before_the_robot_started_needs_no_check(tmp_path):
    executor = ScriptedExecutor((RUN_NOT_STARTED_EXIT_CODE, {"started": False}), SUCCEEDED)
    session, text, _ = live_session(tmp_path, executor, ["run", "run", "quit"])
    assert "interrupted before the robot run started, so nothing ran on the OT-2" in text
    assert "Have you checked the robot" not in text and len(executor.calls) == 2
    assert [run["status"] for run in session.state.runs] == ["interrupted before start", "succeeded"]


def test_an_interrupted_simulation_is_not_a_robot_abort(tmp_path):
    executor = ScriptedExecutor((RUN_ABORTED_EXIT_CODE, None), (0, None))
    session, text, _ = live_session(tmp_path, executor, ["run", "run", "quit"], simulate=True)
    assert "The simulation did not finish" in text and "Have you checked the robot" not in text
    assert len(executor.calls) == 2 and session.unverified_run is None
