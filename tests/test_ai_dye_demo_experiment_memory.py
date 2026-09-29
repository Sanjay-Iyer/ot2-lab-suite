"""Per-user experiment memory: a successful run is saved as YAML under experiment_history/<user>/, and "load my last
experiment", "what did I run yesterday?" or "load Sanjay run 2" find it again deterministically. Several matches are
never guessed, and loading only proposes the saved plan (nothing changes until Apply).

The session tests are real DemoSessions behind the GUI adapter with a scripted router (the reply a faithful model gives
for each message). Simulation only; the history lives in the test's temporary folder.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta

import yaml

from src.agents.dye_demo import experiment_memory as memory
from src.agents.dye_demo.experiment_memory import HistoryQuery, find_runs, load_index, load_run, save_run
from src.agents.dye_demo.gui.adapter import DemoGuiAdapter
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config
from src.agents.dye_demo.session import DemoSession, SessionSettings
from tests.test_ai_dye_demo_gui import chat_text, router, started, wait_for

NOW = datetime(2026, 9, 28, 19, 30)
DEFAULT = load_config(DEFAULT_CONFIG)


def plan(factors, column="11"):
    config = deepcopy(DEFAULT)
    config["dilution"].update(factors=factors, plate_column=column)
    return config


def yesterday(hour, minute):
    return (NOW - timedelta(days=1)).replace(hour=hour, minute=minute)


def saved_three_yesterday(directory):
    for (hour, minute), factors in (((9, 10), [2, 4]), ((13, 30), [2, 5, 10]), ((16, 50), [3, 9, 27])):
        save_run(operator="Tester", config=plan(factors), mode="LIVE", directory=directory, now=yesterday(hour, minute))


# ── the files ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_run_is_saved_with_its_settings_and_numbered_per_user(tmp_path):
    first = save_run(operator="Sanjay Iyer", config=plan([2, 5, 10]), mode="LIVE", directory=tmp_path, now=NOW,
                     session={"label": "2026-09-28 Demo", "run_in_session": 1, "revision": 3})
    second = save_run(operator="Sanjay Iyer", config=plan([2, 4]), mode="SIMULATION", directory=tmp_path, now=NOW)
    assert (first["run"], second["run"]) == (1, 2)
    folder = tmp_path / "sanjay_iyer"
    assert sorted(path.name for path in folder.iterdir()) == ["index.yaml", "run_0001.yaml", "run_0002.yaml"]
    record = yaml.safe_load((folder / "run_0001.yaml").read_text(encoding="utf-8"))
    assert record["user"] == "Sanjay Iyer" and record["run"] == 1 and record["mode"] == "LIVE"
    settings = record["settings"]
    assert settings["dilution"]["factors"] == [2, 5, 10] and settings["print"]["total_replicates"] == 1
    assert settings["print"]["drops_per_position"] == 1 and settings["tips"]["policy"] == "single_tip"
    assert settings["print"]["print_map"][0] == {"from": "A11", "to": "A1", "drops": 1}
    assert settings["liquid_handling"]["plate_dispense_height_mm"] == 0.3
    assert "3 dilutions (2×, 5×, 10×) in plate column 11" in record["summary"]
    assert load_run("Sanjay Iyer", first, tmp_path)["dilution"]["factors"] == [2, 5, 10]
    assert [entry["run"] for entry in load_index("sanjay iyer", tmp_path)] == [1, 2]


def test_queries_resolve_deterministically(tmp_path):
    saved_three_yesterday(tmp_path)
    save_run(operator="Tester", config=plan([2, 8]), mode="LIVE", directory=tmp_path, now=NOW.replace(hour=9))
    find = lambda **query: [entry["run"] for entry in find_runs(HistoryQuery(**query), "Tester", directory=tmp_path,
                                                                 now=NOW)[1]]
    assert find(action="load") == [4]                                        # "load my last experiment"
    assert find(action="load", which="last", days_ago=1) == [3]
    assert find(action="load", days_ago=1) == [1, 2, 3]                     # three yesterday: the session asks
    assert find(action="list", days_ago=1) == [1, 2, 3]
    assert find(action="load", part_of_day="morning") == [4]                 # "this morning"
    assert find(action="load", days_ago=1, time="1:30") == [2]               # "the 1:30 one" (afternoon)
    assert find(action="load", run=2) == [2]
    assert find(action="load", days_ago=3) == []


# ── through the session ────────────────────────────────────────────────────────────────────────────────────────────

REPLIES = {
    "what did I run yesterday?": {"action": "list", "days_ago": 1},
    "load the experiment I ran yesterday": {"action": "load", "days_ago": 1},
    "the 1:30 one": {"action": "load", "time": "13:30"},
    "load my last experiment": {"action": "load", "which": "last"},
    "load Sanjay run 2": {"action": "load", "user": "Sanjay", "run": 2},
}


def history_reply(message):
    return {"route": "experiment_history", "history": REPLIES[message.strip()]}


def history_adapter(tmp_path):
    settings = SessionSettings(simulate=True, config_source=DEFAULT_CONFIG, working_config=tmp_path / "working.yaml",
                               run_dir=tmp_path / "run", session_label="history test", operator="Tester",
                               skip_llm_startup=True, raise_errors=True, run_button="Run on OT-2",
                               history_dir=tmp_path / "history")
    session = DemoSession(settings, load_config(DEFAULT_CONFIG), llm=router(history_reply),
                          executor=lambda path, simulate, log: 0, sleep=lambda _: None)
    return started(DemoGuiAdapter(session, diagnostics=lambda text: None))


def say(adapter, text):
    seen = len(adapter.messages())
    assert adapter.submit_text(text)
    wait_for(lambda: adapter.waiting != "busy" and len(adapter.messages()) > seen + 1)
    return "\n".join(message.text for message in adapter.messages(seen))


def test_a_successful_run_is_saved_and_loading_it_back_is_a_proposal(tmp_path):
    adapter = history_adapter(tmp_path)
    try:
        assert adapter.run()
        wait_for(lambda: adapter.snapshot().status == "RUN COMPLETE")
        assert "Saved to Tester's experiment history as run 1." in chat_text(adapter)
        assert [entry["run"] for entry in load_index("Tester", tmp_path / "history")] == [1]
        # a newer saved run that differs from the plan, then "load my last experiment"
        save_run(operator="Tester", config=plan([2, 5, 10]), mode="LIVE", directory=tmp_path / "history")
        reply = say(adapter, "load my last experiment")
        assert "Loaded your run 2" in reply and adapter.waiting == "proposal"
        snapshot = adapter.snapshot()
        assert snapshot.current["dilution"]["factors"] == DEFAULT["dilution"]["factors"]   # nothing applied yet
        assert snapshot.proposed["dilution"]["factors"] == [2, 5, 10]
        assert adapter.submit_text("yes")
        wait_for(lambda: adapter.snapshot().revision == 1)
        assert adapter.snapshot().current["dilution"]["factors"] == [2, 5, 10]
    finally:
        adapter.stop()


def test_several_runs_are_never_guessed_and_the_answer_picks_one(tmp_path):
    now = datetime.now()                                     # the session reads "yesterday" from the real clock
    for (hour, minute), factors in (((9, 10), [2, 4]), ((13, 30), [2, 5, 10]), ((16, 50), [3, 9, 27])):
        save_run(operator="Tester", config=plan(factors), mode="LIVE", directory=tmp_path / "history",
                 now=(now - timedelta(days=1)).replace(hour=hour, minute=minute))
    adapter = history_adapter(tmp_path)
    try:
        listed = say(adapter, "what did I run yesterday?")
        assert "You ran 3 experiments yesterday" in listed and adapter.waiting == "idle"
        asked = say(adapter, "load the experiment I ran yesterday")
        assert "You ran 3 experiments yesterday at 9:10 AM, 1:30 PM and 4:50 PM. Which one do you want?" in asked
        assert adapter.waiting == "idle" and adapter.snapshot().proposed is None       # nothing guessed
        picked = say(adapter, "the 1:30 one")
        assert "Loaded your run 2" in picked and adapter.snapshot().proposed["dilution"]["factors"] == [2, 5, 10]
    finally:
        adapter.stop()


def test_another_users_run_can_be_loaded_by_number(tmp_path):
    save_run(operator="Sanjay", config=plan([2, 4]), mode="LIVE", directory=tmp_path / "history")
    save_run(operator="Sanjay", config=plan([4, 8, 16], column="9"), mode="LIVE", directory=tmp_path / "history")
    adapter = history_adapter(tmp_path)
    try:
        reply = say(adapter, "load Sanjay run 2")
        assert "Loaded Sanjay's run 2" in reply
        proposed = adapter.snapshot().proposed
        assert proposed["dilution"]["factors"] == [4, 8, 16] and proposed["dilution"]["plate_column"] == "9"
    finally:
        adapter.stop()


def test_history_is_off_when_the_session_keeps_none(tmp_path):
    assert memory.user_id("Sanjay Iyer") == "sanjay_iyer"
    assert find_runs(HistoryQuery(), "Nobody", directory=tmp_path)[1] == []
