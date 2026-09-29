"""Reset Demo: the page's escape hatch back to the startup experiment (DemoSession.reset_to_defaults).

Robot-free: scripted router replies, a recording fake executor for the live bookkeeping, and the real NiceGUI page with
its refresh timer ticked by hand. Nothing contacts a robot or an LLM.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

from nicegui import Client, core, ui

from src.agents.dye_demo.gui.app import build_page, execution_target
from src.agents.dye_demo.llm import LLMClient
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.session import DemoSession, SessionSettings
from src.agents.dye_demo.state import TIPS_USED, WELL_VOLUMES
from tests.test_ai_dye_demo_gui import FakeRobotExecutor, apply_form, make_adapter, started, wait_for

STARTUP = load_config(DEFAULT_CONFIG)          # what the launcher loads at startup, and what a reset loads again
APPROVE = {"route": "approve_proposal"}


def change(path, value):
    return {"route": "experiment_change", "changes": [{"path": path, "value": value}]}


class Router:
    """The model behind the conversational router: one scripted reply per message; every prompt it saw is kept."""

    def __init__(self, replies):
        self.replies, self.prompts = replies, []

    def invoke(self, messages):
        prompt = messages[-1][1]
        self.prompts.append(prompt)
        return SimpleNamespace(content=json.dumps(self.replies[prompt.split("SCIENTIST'S MESSAGE:\n")[-1].strip()]))


class Executor:
    """Stands in for the run backend; returns the next exit code (130: a live run stopped part-way on the OT-2)."""

    def __init__(self, *codes):
        self.codes, self.calls, self.active, self.last_status = list(codes), 0, False, None

    def check_robot(self):
        return None

    def __call__(self, _path, _simulate, _log):
        self.calls += 1
        code = self.codes.pop(0) if self.codes else 0
        self.last_status = {"started": True, "robot_status": "succeeded" if code == 0 else "stopped"}
        return code


def session(tmp_path, replies, *, live=False, executor=None, history_dir=None):
    """The GUI's session (LLM-first, operator known); every yes/no safety question is answered yes."""
    model, output = Router(replies), []
    settings = SessionSettings(simulate=not live, config_source=DEFAULT_CONFIG, working_config=tmp_path / "working.yaml",
                               run_dir=tmp_path / "run", session_label="Reset test", operator="Tester",
                               skip_llm_startup=True, raise_errors=True, run_button="Run on OT-2", llm_first=True,
                               history_dir=history_dir)
    demo = DemoSession(settings, load_config(DEFAULT_CONFIG), llm=LLMClient(lambda: model),
                       executor=executor or Executor(), input_fn=lambda _prompt: "yes", output_fn=output.append,
                       sleep=lambda _: None)
    demo.operator = "Tester"
    demo.request_run_confirmation = lambda: None
    return demo, model, output


def talk(demo, *messages):
    for message in messages:
        demo._handle(message)


def without_clock(prompt):
    return "\n".join(line for line in prompt.splitlines() if not line.startswith("TODAY:"))


TANGLE = {
    "use 3 drops per position": change("print.droplets_per_spot", 3),
    "use dilution factors 2, 5 and 10": change("dilution.factors", [2, 5, 10]),
    "print them on paper rows D, E and F": {"route": "experiment_change", "changes": [
        {"path": "paper_rows", "value": ["D", "E", "F"], "evidence": "paper rows D, E and F"}]},
    "make 2 replicates": change("print.replicates", 2),
    "print A11 on A3 and B11 on B3": {"route": "experiment_change", "changes": [
        {"path": "print.source_map", "value": [{"source": "A11", "destination": "A3"},
                                                {"source": "B11", "destination": "B3"}]}]},
    "use 4 drops per position": change("print.droplets_per_spot", 4),
    "yes": APPROVE,
}


def test_reset_demo_starts_a_tangled_experiment_over_from_the_startup_config(tmp_path):
    demo, model, output = session(tmp_path / "demo", TANGLE)
    talk(demo, "use 3 drops per position", "yes", "use dilution factors 2, 5 and 10", "yes",
         "print them on paper rows D, E and F", "yes", "make 2 replicates", "yes",
         "print A11 on A3 and B11 on B3", "print A11 on A3 and B11 on B3", "use 4 drops per position")
    # the tangle: four applied edits, the paper rows now an explicit print map, a proposal AND a stale question waiting
    assert demo.state.revision == 4 and demo.state.config["print"]["source_map"]
    assert demo.pending is not None and demo.clarifying is not None and demo.run_block_reasons()
    waiting = demo.pending.id

    assert demo.reset_to_defaults()

    fresh, fresh_model, _ = session(tmp_path / "fresh", TANGLE)
    # print map, paper rows, replicates, drops, factors and the start tip all come from the startup config again
    assert demo.state.config == fresh.state.config == STARTUP
    assert demo.state.config["tips"]["start_tip"] == STARTUP["tips"]["start_tip"]
    assert (demo.pending, demo.clarifying, demo.state.revision, demo.state.history) == (None, None, 0, [])
    assert demo.run_block_reasons() == fresh.run_block_reasons() == []
    assert "agent> Agent NanoDrop reset to the default experiment. Ready for a new run." in output
    assert demo.state.next_proposal_id == waiting + 1              # numbering continues: no proposal number reused
    [event] = [json.loads(line) for line in demo.log.path.read_text(encoding="utf-8").splitlines()
               if '"demo_reset"' in line]
    assert event["reset"] == 1 and event["forgotten"]["pending_proposal"] == waiting

    # the request that got tangled is read exactly as a new session reads it: the router is shown no earlier
    # conversation, applied change or proposal, and the session answers it the same way
    talk(demo, "print A11 on A3 and B11 on B3")
    talk(fresh, "print A11 on A3 and B11 on B3")
    assert without_clock(model.prompts[-1]) == without_clock(fresh_model.prompts[-1])
    assert "(this is the first message)" in model.prompts[-1]
    assert "(none yet - the plan is as the session started)" in model.prompts[-1]
    assert (demo.pending is None, demo.clarifying and demo.clarifying.prompt) == \
        (fresh.pending is None, fresh.clarifying and fresh.clarifying.prompt)


def test_a_live_reset_clears_the_physical_record_only_after_the_physical_confirmation(tmp_path):
    replies = {"move the paper to slot 8": change("deck.paper.slot", 8),
               "just print again in paper column 3": {"route": "experiment_change", "changes": [
                   {"path": "dilution.enabled", "value": False}, {"path": "print.paper_start_column", "value": 3}]},
               "yes": APPROVE}
    demo, _, output = session(tmp_path, replies, live=True, executor=Executor(0, 130))
    talk(demo, "move the paper to slot 8", "yes")
    demo.run_from_button()                       # run 1 finishes: tips, wells and paper are booked as used
    talk(demo, "just print again in paper column 3", "yes")
    demo.run_from_button()                       # run 2 is stopped part-way: the robot state is unverified
    physical = demo.state.physical
    assert physical[TIPS_USED] and physical["dilutions_prepared"] and physical[WELL_VOLUMES]
    assert demo.state.printed_positions and demo.unverified_run is not None
    record = demo.physical_record_lines()        # what the live dialog lists before the operator confirms
    assert [line.split(":")[0] for line in record] == ["96-well plate", "Paper", "Tip rack", "Run 2 did not finish (aborted)"]
    before = (demo.state.full_fingerprint(), len(demo.state.runs), demo.unverified_run)

    assert demo.reset_to_defaults() is False     # software cannot empty the plate: live, the operator confirms first
    assert (demo.state.full_fingerprint(), len(demo.state.runs), demo.unverified_run) == before

    assert demo.reset_to_defaults(physical_reset_confirmed=True)
    fresh, _, _ = session(tmp_path / "fresh", replies, live=True)
    assert demo.state.physical == fresh.state.physical                 # new plate, paper and tip-rack records
    assert demo.state.printed_positions == set() and demo.state.runs == [] and demo.unverified_run is None
    assert demo.physical_record_lines() == [] and demo.run_block_reasons() == []
    assert demo.state.config == STARTUP
    said = "\n".join(output)
    assert "reset to the default experiment using the confirmed fresh physical setup" in said
    # the recorded paper went back to its startup slot: the next live run first asks that the deck matches
    assert "move the Paper print plate from Slot 8 to Slot 5" in said
    start = len(output)
    demo.run_from_button()
    assert "Is the physical deck arranged exactly as the ACTIVE DECK above?" in "\n".join(output[start:])
    [run] = demo.state.runs
    assert run["status"] == "succeeded" and run["tips_used"] == [STARTUP["tips"]["start_tip"]]
    assert run["run"] == 3                       # run numbers continue, so no executed config is overwritten
    assert all((tmp_path / "run" / f"executed_config_run{number}.yaml").is_file() for number in (1, 2, 3))


def test_reset_demo_keeps_the_saved_experiment_history(tmp_path):
    history = tmp_path / "experiment_history"
    replies = {"use 3 drops per position": change("print.droplets_per_spot", 3), "yes": APPROVE,
               "load my last experiment": {"route": "experiment_history",
                                           "history": {"action": "load", "which": "last"}}}
    demo, _, output = session(tmp_path, replies, history_dir=history)
    talk(demo, "use 3 drops per position", "yes")
    demo.run_from_button()
    saved = {path: path.read_bytes() for path in history.rglob("*") if path.is_file()}
    assert saved and "Saved to Tester's experiment history as run 1." in output

    assert demo.reset_to_defaults()
    assert {path: path.read_bytes() for path in history.rglob("*") if path.is_file()} == saved
    talk(demo, "load my last experiment")        # the saved run comes back as a proposal, as before the reset
    assert demo.pending is not None and demo.pending.after["print"]["droplets_per_spot"] == 3
    assert demo.state.config == STARTUP


def click(button):
    """What a browser click does: the button's click listener, called the way NiceGUI calls it."""
    handler = next(listener.handler for listener in button._event_listeners.values() if listener.type == "click")
    handler(None) if inspect.signature(handler).parameters else handler()


def page(monkeypatch, path):
    """A real page whose refresh timer the test ticks; returns the client and the tick."""
    monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
    monkeypatch.setattr(ui, "run_javascript", lambda *args, **kwargs: None)
    ticks = []
    monkeypatch.setattr(ui, "timer", lambda interval, callback: ticks.append(callback))

    async def tick():
        ticks[-1]()
        await asyncio.sleep(0.05)               # a panel refresh runs on the event loop's next turn

    return Client(ui.page(path)), tick


def test_reset_demo_asks_first_then_redraws_the_page_from_the_startup_config(tmp_path, monkeypatch):
    asyncio.run(_check_reset_page(tmp_path, monkeypatch))


async def _check_reset_page(tmp_path, monkeypatch):
    for live in (False, True):
        adapter = started(make_adapter(tmp_path / f"live_{live}", executor=FakeRobotExecutor(), simulate=not live))
        client, tick = page(monkeypatch, f"/reset-page-{live}")
        try:
            with client:
                build_page(adapter)
                await tick()
                elements = lambda: list(client.elements.values())     # noqa: E731 - the page grows as it redraws
                [reset_button] = [e for e in elements() if "reset-demo" in e.classes]
                [badge] = [e for e in elements() if "execution-target" in e.classes]
                # immediately left of the SIMULATED / LIVE badge, outlined, away from Run, Apply and Discard
                column = reset_button.parent_slot.parent
                header = list(column.parent_slot.children)
                assert header.index(column) + 1 == header.index(badge)
                assert reset_button.text == "Reset Demo" and "outline" in reset_button.props

                apply_form(adapter, {"print.droplets_per_spot": 3, "print.paper_start_column": 4})
                assert adapter.propose_form({"print.replicates": 2})
                wait_for(lambda: adapter.waiting == "proposal")
                await tick()
                drops = next(e for e in elements() if isinstance(e, ui.number)
                             and e.props.get("label") == "Drops per position")
                [proposed_box] = [e for e in elements() if "section-plan" in e.classes and "plan-card" not in e.classes]
                assert drops.value == 3 and proposed_box.visible

                click(reset_button)                                   # one click only asks
                [dialog] = [e for e in elements() if isinstance(e, ui.dialog) and e.value]
                assert adapter.session.resets == 0 and adapter.waiting == "proposal"
                target = execution_target(live)
                texts = [getattr(e, "text", "") for e in dialog.descendants()]
                assert target.reset_title in texts and target.reset_text in texts
                [confirm] = [e for e in dialog.descendants() if isinstance(e, ui.button) and e.text == "Reset Demo"]
                checks = [e for e in dialog.descendants() if isinstance(e, ui.checkbox)]
                if live:                          # the physical materials must be confirmed before it can reset
                    [check] = checks
                    assert check.text == target.reset_check and not check.value and not confirm.enabled
                    check.value = True
                    assert confirm.enabled
                else:
                    assert checks == [] and confirm.enabled
                click(confirm)
                wait_for(lambda: adapter.session.resets == 1 and adapter.waiting == "idle")
                await tick()

                # the startup experiment is on the page without a browser refresh
                snapshot = adapter.snapshot()
                assert snapshot.current == STARTUP and snapshot.proposed is None and snapshot.status == "READY"
                assert snapshot.run_ready, snapshot.run_block_reasons
                assert not dialog.value and not proposed_box.visible
                assert drops.value == STARTUP["print"]["droplets_per_spot"]
                texts = [getattr(e, "text", "") for e in elements()]
                assert "ACTIVE · revision 0" in texts and "ACTIVE · revision 1" not in texts
                assert "DEMO RESET · New experiment started from defaults." in texts   # the transcript marker
        finally:
            adapter.stop()
            client.delete()


def test_reset_demo_is_unavailable_while_a_run_is_active(tmp_path, monkeypatch):
    asyncio.run(_check_reset_while_running(tmp_path, monkeypatch))


async def _check_reset_while_running(tmp_path, monkeypatch):
    executor = FakeRobotExecutor(wait_for_stop=True)
    adapter = started(make_adapter(tmp_path, executor=executor, simulate=False))
    client, tick = page(monkeypatch, "/reset-while-running")
    try:
        with client:
            build_page(adapter)
            await tick()
            [reset_button] = [e for e in client.elements.values() if "reset-demo" in e.classes]
            [reason] = [e for e in client.elements.values() if "reset-reason" in e.classes]
            assert reset_button.enabled and not reason.visible
            assert adapter.run()
            wait_for(lambda: adapter.running)
            await tick()
            assert not reset_button.enabled and reason.visible
            assert reason.text == adapter.snapshot().reset_block_reason == "Cannot reset while a robot run is active."
            click(reset_button)                          # even a click that got through opens nothing
            assert not any(isinstance(e, ui.dialog) and e.value for e in client.elements.values())
            assert adapter.reset_demo(physical_reset_confirmed=True) is False
            assert adapter.session.reset_to_defaults(physical_reset_confirmed=True) is False   # the session's own rule
            assert adapter.request_stop()                # Stop is how a run ends; Reset never stands in for it
            wait_for(lambda: not adapter.running and adapter.waiting == "idle")
            await tick()
            assert reset_button.enabled and not reason.visible
            assert adapter.session.resets == 0 and len(executor.calls) == 1
    finally:
        adapter.stop()
        client.delete()
