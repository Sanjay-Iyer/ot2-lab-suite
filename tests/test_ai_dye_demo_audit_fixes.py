"""Fixes from the offline architecture audit of Agent NanoDrop (93ffa43, no model, no robot).

Each fix is checked at every layer it touches: the proposal (ExperimentState.propose), a conversation through the real
DemoSession with scripted router replies (red-team invariants after every turn, including the fake-OT-2 protocol run),
plan validation, the protocol on the recording fake OT-2 and, where liquid moves, the pinned Opentrons 2.15 simulator.
"""
from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from pathlib import Path

import pytest

from src.agents.dye_demo.intent import TurnContext, analyze_turn
from src.agents.dye_demo.model import DEFAULT_CONFIG, OFF_DECK
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.fake_opentrons import load_protocol_module, run_protocol
from src.agents.dye_demo.redteam.harness import ConversationSpec, ReplayDriver, run_conversation
from src.agents.dye_demo.redteam.interpreter import ReplayInterpreter
from src.agents.dye_demo.session import DemoSession
from src.agents.dye_demo.redteam.simulate_states import BASE_PROTOCOL, PINNED_SIMULATOR, _builder
from src.agents.dye_demo.state import ExperimentState, ProposalRejected
from src.agents.dye_demo.validation import validate
from tests.test_ai_dye_demo_row_independence import DEFAULT, change, made, proposes, says, series, talk

PLATE = DEFAULT["deck"]["plate"]["load_name"]
RACK = DEFAULT["deck"]["tuberack"]["load_name"]
DYE_VIAL = next(material["vial"] for material in DEFAULT["materials"].values() if material["role"] == "sample")


def dye_on_fake_ot2(config) -> dict[str, float]:
    """µL of dye (the sample vial) protocol v19 dispenses into each plate well on the recording fake OT-2."""
    dye, from_dye = {}, False
    for entry in run_protocol(load_protocol_module(), config).log:
        if entry[0] == "aspirate":
            from_dye = tuple(entry[2][:2]) == (RACK, DYE_VIAL)
        elif entry[0] == "dispense" and entry[2][0] == PLATE and from_dye:
            dye[entry[2][1]] = dye.get(entry[2][1], 0.0) + entry[1]
    return dye


def dye_in_pinned_simulator(config, tmp_path: Path) -> dict[str, float]:
    """The same, from the generated protocol run in the pinned Opentrons 2.15 simulator."""
    builder = _builder()
    full = deepcopy(config)
    run_modes = full.pop("run_modes", {}) or {}
    run_modes["dry_run"] = False
    path = tmp_path / "audit_fix.py"
    path.write_text(builder.build_source(BASE_PROTOCOL.read_text(encoding="utf-8"), full, run_modes), encoding="utf-8")
    ok, output = builder.simulate(path, str(PINNED_SIMULATOR))
    assert ok, output[-1500:]
    aspirate = re.compile(r"^\s*Aspirating ([\d.]+) uL from ([A-H]\d+) of .+? on slot (\d+)")
    dispense = re.compile(r"^\s*Dispensing ([\d.]+) uL into ([A-H]\d+) of .+? on slot (\d+)")
    plate, rack = str(config["deck"]["plate"]["slot"]), str(config["deck"]["tuberack"]["slot"])
    dye, from_dye = {}, False
    for line in output.splitlines():
        if match := aspirate.match(line):
            from_dye = match.group(3) == rack and match.group(2) == DYE_VIAL
        elif (match := dispense.match(line)) and match.group(3) == plate and from_dye:
            dye[match.group(2)] = dye.get(match.group(2), 0.0) + float(match.group(1))
    return dye


def proposed(config, *changes, request):
    return ExperimentState(deepcopy(config)).propose(list(changes), request=request).after


# ════════════════════════════════════════════════════════════════════════════════
# 1. a factor stays with the well it was named for
# ════════════════════════════════════════════════════════════════════════════════

PAIRED = "Put 10x in E11, 2x in A11 and 5x in C11."
WANTED = [("A11", 2.0), ("C11", 5.0), ("E11", 10.0)]


@pytest.mark.parametrize("rows", [
    change("dilution.rows", ["E", "A", "C"], "E11, A11 and C11"),
    change("rows", ["E", "A", "C"], "E11, A11 and C11"),
    change("rows", ["E11", "A11", "C11"], "E11, A11 and C11"),
    change("rows", "E11, A11, C11", "E11, A11 and C11"),
], ids=["field", "selection", "selection_as_wells", "selection_as_text"])
def test_factors_named_with_their_wells_stay_with_those_wells(rows):
    after = proposed(DEFAULT, change("dilution.factors", [10, 2, 5], "10x, 2x and 5x"), rows, request=PAIRED)
    assert made(after) == WANTED
    assert validate(after).ok


def test_rows_in_plate_order_keep_the_factor_order_they_came_with():
    after = proposed(DEFAULT, change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"),
                     change("rows", ["A", "C", "E"], "A11, C11 and E11"),
                     request="Make 2x, 5x and 10x dilutions in A11, C11 and E11.")
    assert made(after) == WANTED


def test_wells_in_another_plate_column_move_the_series_to_that_column():
    after = proposed(series([2, 5, 10], ["A", "C", "E"]), change("rows", ["A5", "C5", "E5"], "A5, C5 and E5"),
                     request="Make the dilutions in A5, C5 and E5.")
    assert made(after) == [("A5", 2.0), ("C5", 5.0), ("E5", 10.0)]


def test_wells_in_two_plate_columns_are_a_question():
    state = ExperimentState(series([2, 5, 10], ["A", "C", "E"]))
    with pytest.raises(ProposalRejected) as caught:
        state.propose([change("rows", ["A11", "C10", "E11"], "A11, C10 and E11")], request="Use A11, C10 and E11.")
    assert caught.value.kind == "selection" and caught.value.question == "Which plate column should hold the dilutions?"
    assert state.revision == 0


def test_the_paired_request_through_the_session_makes_each_factor_in_its_well(tmp_path):
    session, _ = talk(tmp_path, says(PAIRED, proposes(change("dilution.factors", [10, 2, 5], "10x, 2x and 5x"),
                                                      change("rows", ["E", "A", "C"], "E11, A11 and C11"))), "yes")
    config = session.state.config
    assert made(config) == WANTED                    # talk() also ran the protocol on the fake OT-2 (red-team checks)
    assert dye_on_fake_ot2(config) == {"A11": 75.0, "C11": 30.0, "E11": 15.0}      # 150 µL at 2x, 5x and 10x


@pytest.mark.skipif(not PINNED_SIMULATOR.exists(), reason="pinned Opentrons 2.15 simulator not installed")
def test_the_real_simulator_puts_each_factor_in_its_named_well(tmp_path):
    after = proposed(DEFAULT, change("dilution.factors", [10, 2, 5], "10x, 2x and 5x"),
                     change("rows", ["E", "A", "C"], "E11, A11 and C11"), request=PAIRED)
    assert dye_in_pinned_simulator(after, tmp_path) == {"A11": 75.0, "C11": 30.0, "E11": 15.0}


# ════════════════════════════════════════════════════════════════════════════════
# 2. the physical ledger across live runs: prepared wells, used tips, liquid left in the wells
# ════════════════════════════════════════════════════════════════════════════════

RUN = {"text": "run", "label": {"category": "run", "may_run": True, "may_propose": False}}
MAKE_ACE = says("Make 2x, 5x and 10x dilutions in A11, C11 and E11.",
                proposes(change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"),
                         change("rows", ["A", "C", "E"], "in A11, C11 and E11")))


def live(tmp_path, *messages, config=None):
    """A conversation through the real DemoSession with its LIVE bookkeeping: the harness's recording executor stands
    in for the robot (nothing is built, uploaded or moved), so a finished run is recorded as a real one would be."""
    items = [message if isinstance(message, dict) else {"text": message} for message in messages]
    script = {number: [dict(item) for item in message.get("llm", [])] for number, message in enumerate(items, start=1)}
    spec = ConversationSpec(seed=0, persona="replay", length=len(items), live_logic=True, interpreter="replay")
    result = run_conversation(spec, workdir=tmp_path, driver=ReplayDriver(items),
                              interpreter=ReplayInterpreter(seed=0, script=script), keep_transcript=True,
                              return_session=True, config=config)
    assert result["violations"] == [], result["violations"]
    return result["session"], [row["output"] for row in result["transcript"]]


def plate_draws_on_fake_ot2(config) -> dict[str, float]:
    """µL the print step aspirates from each plate well on the recording fake OT-2 (mixing is logged separately)."""
    drawn = {}
    for entry in run_protocol(load_protocol_module(), config).log:
        if entry[0] == "aspirate" and entry[2][0] == PLATE:
            drawn[entry[2][1]] = drawn.get(entry[2][1], 0.0) + entry[1]
    return drawn


def test_a_run_in_another_plate_column_does_not_empty_the_first_column(tmp_path):
    session, out = live(tmp_path, MAKE_ACE, "yes", RUN, "yes",
                        says("Use plate column 10 for the dilutions.",
                             proposes(change("dilution.plate_column", 10, "plate column 10"))), "yes",
                        RUN, "yes",
                        says("Go back to plate column 11.", proposes(change("dilution.plate_column", 11, "plate column 11"))),
                        "yes", RUN)
    assert [run["run"] for run in session.state.runs] == [1, 2]            # the third run was refused
    assert "Plate wells A11, C11, E11 already hold dilutions (made by run 1)" in out[10]
    record = session.state.physical["dilutions_prepared"]
    assert dict(zip(record["wells"], record["factors"])) == {"A10": 2, "C10": 5, "E10": 10, "A11": 2, "C11": 5, "E11": 10}


def test_tips_a_live_run_used_are_never_picked_up_again(tmp_path):
    session, out = live(tmp_path, MAKE_ACE, "yes", RUN,
                        "no",                                # the post-run starting-tip proposal is discarded
                        says("Use plate column 10 for the dilutions.",
                             proposes(change("dilution.plate_column", 10, "plate column 10"))), "yes",
                        RUN,                                 # would pick up A1-E1 again
                        "I loaded a fresh tip rack.", "yes", "yes",
                        RUN)
    assert "Tips A1, B1, C1, D1, E1 were already used by an earlier run" in out[6]
    assert "Set the starting tip to F1" in out[6] and "a fresh tip rack is loaded" in out[6]
    used = ["A1", "B1", "C1", "D1", "E1"]
    assert [run["tips_used"] for run in session.state.runs] == [used, used]      # the second one from the fresh rack
    assert session.state.physical["tips_used"] == used


def test_printing_again_from_printed_wells_is_checked_against_the_liquid_left(tmp_path):
    session, out = live(
        tmp_path,
        says("Make 2x, 5x and 10x dilutions in A11, C11 and E11 and print each 12 times.",
             proposes(change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"),
                      change("rows", ["A", "C", "E"], "in A11, C11 and E11"),
                      change("print.replicates", 12, "print each 12 times"))), "yes",
        RUN, "yes",                                  # 150 µL made, 12 x 5 µL printed from each well: 90 µL left
        says("The dilutions are already made. Print them again on paper rows B, D and F.",
             proposes(change("dilution.enabled", False, "already made"),
                      change("paper_rows", ["B", "D", "F"], "paper rows B, D and F"))), "yes",
        dict(RUN, gates=["yes"]),                    # would mix at 2 mm in 85 µL: draws air
        says("Each well holds 150 µL now, I topped them up.",
             proposes(change("dilution.prepared_volume_ul", 150, "Each well holds 150 µL"))), "yes",
        dict(RUN, gates=["yes"]))
    assert "A11 ~90 µL | C11 ~90 µL | E11 ~90 µL" in out[2]
    assert "Not enough liquid is recorded in the plate" in out[6] and "85 µL" in out[6]
    assert [run["run"] for run in session.state.runs] == [1, 2]           # the second after the stated volume only
    assert validate(session.state.config).ok


def test_the_liquid_record_is_what_the_protocol_draws(tmp_path):
    config = series([2, 5, 10], ["A", "C", "E"], start_column=1, replicates=12)
    state = ExperimentState(deepcopy(config))
    plan = build_plan(config)
    state.record_run(simulate=False, exit_code=0, printed=[op.destination for op in plan.operations if op.kind == "print"],
                     tips_used=[tip.tip for tip in plan.tips], operator="Tester",
                     prepared={"wells": [well.well for well in plan.wells], "factors": [well.factor for well in plan.wells],
                               "total_volume_ul": plan.total_volume_ul, "source": "made"})
    drawn = {well: 150.0 - entry["volume_ul"] for well, entry in state.physical["well_volumes"].items()}
    assert drawn == plate_draws_on_fake_ot2(config) == {"A11": 60.0, "C11": 60.0, "E11": 60.0}
    if PINNED_SIMULATOR.exists():
        assert plate_draws_in_pinned_simulator(config, tmp_path) == drawn


def plate_draws_in_pinned_simulator(config, tmp_path: Path) -> dict[str, float]:
    """µL aspirated from each plate well by the print step in the pinned simulator (top-level aspirations; mixing and
    air gaps are nested steps)."""
    builder = _builder()
    full = deepcopy(config)
    run_modes = full.pop("run_modes", {}) or {}
    run_modes["dry_run"] = False
    path = tmp_path / "audit_fix_draws.py"
    path.write_text(builder.build_source(BASE_PROTOCOL.read_text(encoding="utf-8"), full, run_modes), encoding="utf-8")
    ok, output = builder.simulate(path, str(PINNED_SIMULATOR))
    assert ok, output[-1500:]
    aspirate = re.compile(r"^Aspirating ([\d.]+) uL from ([A-H]\d+) of .+? on slot (\d+)")
    plate = str(config["deck"]["plate"]["slot"])
    drawn = {}
    for line in output.splitlines():
        if (match := aspirate.match(line)) and match.group(3) == plate:
            drawn[match.group(2)] = drawn.get(match.group(2), 0.0) + float(match.group(1))
    return drawn


def test_a_new_plate_clears_the_liquid_record_and_a_fresh_rack_the_used_tips(tmp_path):
    session, out = live(tmp_path, MAKE_ACE, "yes", RUN, "no", "I replaced the plate.", "yes")
    physical = session.state.physical
    assert physical["dilutions_prepared"] is None and physical["well_volumes"] == {}
    assert physical["tips_used"] == ["A1", "B1", "C1", "D1", "E1"]         # the tip rack was not replaced


# ════════════════════════════════════════════════════════════════════════════════
# 3. every physical report stays outstanding until it is in the record
# ════════════════════════════════════════════════════════════════════════════════

PLATE_AND_RACK = (MAKE_ACE, "yes", RUN, "yes",
                  "I replaced the plate.",             # a reconciliation proposal waits
                  "I removed the vial rack.",          # told to answer the proposal first; not recorded
                  "yes")                               # records the new plate only


def test_recording_one_report_does_not_record_another(tmp_path):
    session, out = live(tmp_path, *PLATE_AND_RACK, RUN,
                        "I put the vial rack back in slot 7.", RUN)
    assert "I will not start a run until that is in the record" in out[5]
    assert 'Not running: you told me "I removed the vial rack."' in out[7]  # the plate is recorded; the rack is not
    assert [run["run"] for run in session.state.runs] == [1, 2]            # after the rack was reported back only
    # run 2 made its dilutions in A11-E11 again: allowed only because the new plate was recorded as empty
    assert session.state.physical["dilutions_prepared"]["source"] == "made by run 2"
    assert session.unreconciled_reports == []


def test_two_outstanding_reports_are_both_named(tmp_path):
    # the plan needs both racks, so neither report can be recorded as it stands
    session, out = live(tmp_path, "I took the vial rack off the robot.", "I took the tip rack off the robot.", RUN)
    assert ('Not running: you told me "I took the vial rack off the robot." and "I took the tip rack off the robot."'
            in out[2])
    assert not session.state.runs


def test_the_red_team_oracle_catches_a_run_while_a_second_report_is_outstanding(tmp_path, monkeypatch):
    """The invariant tracks every outstanding report too: with the old single report slot (recording the plate
    forgot the rack), the run is flagged."""
    keep = DemoSession._clear_unreconciled

    def forgetful(self, reason, report):
        keep(self, reason, report)
        self.unreconciled_reports.clear()

    monkeypatch.setattr(DemoSession, "_clear_unreconciled", forgetful)
    items = [message if isinstance(message, dict) else {"text": message} for message in (*PLATE_AND_RACK, RUN)]
    script = {number: [dict(item) for item in message.get("llm", [])] for number, message in enumerate(items, start=1)}
    spec = ConversationSpec(seed=0, persona="replay", length=len(items), live_logic=True, interpreter="replay")
    result = run_conversation(spec, workdir=tmp_path, driver=ReplayDriver(items),
                              interpreter=ReplayInterpreter(seed=0, script=script), keep_transcript=True)
    assert [violation["invariant"] for violation in result["violations"]] == ["run_with_unreconciled_physical_report"]


# ════════════════════════════════════════════════════════════════════════════════
# 4. sparse wells are named, never shown as a range that includes wells the plan does not use
# ════════════════════════════════════════════════════════════════════════════════

def test_the_live_safety_question_names_the_sparse_wells_it_asks_about(tmp_path):
    session, out = live(tmp_path, MAKE_ACE, "yes", RUN, "yes",
                        says("The dilutions are already made, just print.",
                             proposes(change("dilution.enabled", False, "already made, just print"))), "yes",
                        dict(RUN, gates=["yes"]))
    assert "(plate wells A11, C11, E11)" in out[2] and "Dilutions now exist in plate wells A11, C11, E11." in out[2]
    assert "Do plate wells A11, C11, E11 already hold the dilutions? (yes/no)" in out[6]
    assert "A11-E11" not in "".join(out)
    assert len(session.state.runs) == 2


def test_validation_names_sparse_wells():
    config = series([2, 5, 10], ["A", "C", "E"], dilution=False)
    assert any("plate wells A11, C11, E11 already hold them" in warning.message
               for warning in validate(config).warnings)


# ════════════════════════════════════════════════════════════════════════════════
# 5. the GUI form holds labware that is OFF DECK
# ════════════════════════════════════════════════════════════════════════════════

def test_the_gui_page_and_form_hold_labware_that_is_off_deck(tmp_path, monkeypatch):
    asyncio.run(_off_deck_page(tmp_path, monkeypatch))


async def _off_deck_page(tmp_path, monkeypatch):
    from nicegui import Client, core, ui

    import src.agents.dye_demo.gui.app as app
    from src.agents.dye_demo.gui.adapter import DemoGuiAdapter
    from src.agents.dye_demo.session import SessionSettings

    image = tmp_path / "reference.jpg"
    image.write_bytes(bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9"))
    (tmp_path / "visualization").mkdir()                   # the page serves reference images from the repository
    monkeypatch.setattr(app, "REPO", tmp_path)
    monkeypatch.setattr(app, "DEFAULT_REFERENCE_IMAGES", {name: image for name in app.DEFAULT_REFERENCE_IMAGES})
    monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
    monkeypatch.setattr(ui, "run_javascript", lambda *args, **kwargs: None)
    ticks, built = [], {}
    monkeypatch.setattr(ui, "timer", lambda interval, callback: ticks.append(callback))
    real_controls = app._controls
    monkeypatch.setattr(app, "_controls", lambda config: built.setdefault("controls", real_controls(config)))

    config = deepcopy(DEFAULT)
    config["dilution"]["enabled"] = False                  # printing only: the vial rack is not needed
    config["deck"]["tuberack"]["slot"] = OFF_DECK
    assert validate(config).ok
    settings = SessionSettings(simulate=True, config_source=DEFAULT_CONFIG, working_config=tmp_path / "working.yaml",
                               run_dir=tmp_path / "run", session_label="GUI test", operator="Tester",
                               skip_llm_startup=True, raise_errors=True, run_button="Run on OT-2")
    adapter = DemoGuiAdapter(DemoSession(settings, config, llm=None, executor=lambda path, simulate, log: 0,
                                         sleep=lambda _: None))
    adapter.start()
    for _ in range(500):
        if adapter.waiting == "idle":
            break
        await asyncio.sleep(0.01)
    client = Client(ui.page("/off-deck"))
    try:
        with client:
            app.build_page(adapter)                    # refused before: Invalid value: OFF_DECK
            controls = built["controls"]
            assert controls["tuberack"].value == OFF_DECK
            values = app._control_values(controls)       # int("OFF_DECK") before: the form could not be submitted
            assert values["deck.tuberack.slot"] == OFF_DECK and values["deck.plate.slot"] == 4
            assert not adapter.propose_form(values)      # nothing differs: the form does not move the rack back
    finally:
        adapter.stop()
        client.delete()


# ════════════════════════════════════════════════════════════════════════════════
# 6. a row selection never invents a dilution factor
# ════════════════════════════════════════════════════════════════════════════════

def test_more_rows_than_dilutions_asks_for_the_missing_factor():
    state = ExperimentState(series([2, 5], ["A", "B"]))
    with pytest.raises(ProposalRejected) as caught:
        state.propose([change("rows", ["A", "C", "E"], "rows A, C and E")], request="Use rows A, C and E.")
    assert caught.value.kind == "selection" and caught.value.question == "Which dilution factor should plate row E hold?"
    assert state.revision == 0


def test_moving_or_picking_dilutions_still_keeps_their_factors():
    moved = proposed(series([2, 5, 10], ["A", "B", "C"]), change("rows", ["A", "C", "E"], "rows A, C and E"),
                     request="Use rows A, C and E.")
    picked = proposed(series([2, 5, 10], ["A", "C", "E"]), change("rows", ["A", "E"], "rows A and E"),
                      request="Use rows A and E.")
    assert made(moved) == WANTED and made(picked) == [("A11", 2.0), ("E11", 10.0)]


def test_the_missing_factor_is_asked_for_and_the_answer_is_used(tmp_path):
    session, router = talk(
        tmp_path, says("Make 2x and 5x dilutions in A11 and B11.",
                       proposes(change("dilution.factors", [2, 5], "2x and 5x"),
                                change("rows", ["A", "B"], "A11 and B11"))), "yes",
        says("Use rows A, C and E.", proposes(change("rows", ["A", "C", "E"], "rows A, C and E"))),
        says("20x.", proposes(change("dilution.factors", [2, 5, 20], "20x"),
                              change("rows", ["A", "C", "E"], "rows A, C and E"))), "yes")
    asked = [event.get("question") for event in session.turns[2]["events"] if event["type"] == "clarification"]
    assert asked == ["Which dilution factor should plate row E hold?"]
    assert made(session.state.config) == [("A11", 2.0), ("C11", 5.0), ("E11", 20.0)]


# ════════════════════════════════════════════════════════════════════════════════
# 7. a dilution the same message asks for is not refused as "not in the plan"
# ════════════════════════════════════════════════════════════════════════════════

PREPARE_ALL_PRINT_ONE = "Prepare 2x, 5x and 10x in A11/C11/E11, but print only 10x."


@pytest.mark.parametrize("text", [PREPARE_ALL_PRINT_ONE, "Prepare 2x, 5x and 10x in A11, C11 and E11. Print only the 10x."])
def test_printing_a_dilution_the_same_message_makes_is_an_ordinary_request(text):
    analysis = analyze_turn(text, TurnContext(config=DEFAULT))           # the default plan has no 10x
    assert analysis.kind == "instruction" and "unsupported" not in analysis.details


def test_printing_a_dilution_nobody_asked_for_is_still_explained():
    analysis = analyze_turn("Print only the 10x dilution.", TurnContext(config=DEFAULT))
    assert analysis.kind == "unsupported"
    assert analysis.details["unsupported"][0].startswith("The current plan has no 10× dilution")


def test_the_session_does_not_tell_the_scientist_their_own_dilution_is_missing(tmp_path):
    session, router = talk(tmp_path, says(PREPARE_ALL_PRINT_ONE, proposes(
        change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"), change("rows", ["A", "C", "E"], "in A11/C11/E11"),
        change("print_map", [{"source": "E11", "positions": "all"}], "print only 10x"))),
        "no",                                   # "is your sample already in E11?": no, make the three dilutions
        "yes")
    assert not any(event["type"] == "refusal" for turn in session.turns for event in turn["events"])
    config = session.state.config
    assert made(config) == WANTED
    assert {op.source for op in build_plan(config).operations if op.kind == "print"} == {"E11"}


# ════════════════════════════════════════════════════════════════════════════════
# 8. one report can say two things about the robot, and each must reach the record
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text, facts", [
    ("I replaced the plate and removed the vial rack.", [("plate_replaced", None, None), ("location", "tuberack", OFF_DECK)]),
    ("The tips were replaced and the tip rack is in slot 8.", [("tips_replaced", None, None), ("location", "tiprack", 8)]),
    ("We swapped out the plate and took the tip rack off.", [("plate_replaced", None, None), ("location", "tiprack", OFF_DECK)]),
    ("I replaced the plate.", [("plate_replaced", None, None)]),
    ("I replaced the plate and the paper.", [("plate_replaced", None, None)]),         # nothing says where: as before
    ("I took the vial rack off the robot.", [("location", "tuberack", OFF_DECK)]),
])
def test_a_second_labware_in_the_same_report_is_read(text, facts):
    analysis = analyze_turn(text, TurnContext(config=DEFAULT))
    assert analysis.kind == "physical_report"
    assert [(fact.kind, fact.role, fact.slot) for fact in analysis.facts] == facts


def test_a_new_plate_and_a_missing_vial_rack_both_block_until_both_are_recorded(tmp_path):
    session, out = live(tmp_path, MAKE_ACE, "yes", RUN, "yes",
                        "I replaced the plate and removed the vial rack.",   # the plan needs the rack: not recorded
                        RUN,
                        "I put the vial rack back in slot 7.",               # the rack is back; the plate is not recorded
                        RUN,
                        "I replaced the plate.", "yes",
                        RUN)
    assert "I did not update the record" in out[4]
    refused = 'Not running: you told me "I replaced the plate and removed the vial rack."'
    assert refused in out[5] and refused in out[7]
    assert [run["run"] for run in session.state.runs] == [1, 2]
    assert session.state.physical["dilutions_prepared"]["source"] == "made by run 2"    # on the new, empty plate
    assert session.unreconciled_reports == []


def test_recording_the_tips_of_a_report_does_not_record_the_rest_of_it(tmp_path):
    session, out = live(tmp_path, "I put in a fresh tip rack and moved the paper to slot 2.",
                        "yes",                               # fresh tips: the starting tip is already A1
                        RUN,
                        "The paper is in slot 2.", "yes",
                        dict(RUN, gates=["yes"]))            # the deck changed: confirmed as shown
    assert 'Not running: you told me "I put in a fresh tip rack and moved the paper to slot 2."' in out[2]
    assert session.state.config["deck"]["paper"]["slot"] == 2
    assert len(session.state.runs) == 1 and session.unreconciled_reports == []


# ════════════════════════════════════════════════════════════════════════════════
# 9. the red-team protocol check compares where each drop is drawn from, not only where it lands
# ════════════════════════════════════════════════════════════════════════════════

def test_the_protocol_check_catches_the_right_positions_printed_from_the_wrong_wells(monkeypatch):
    from dataclasses import replace

    import src.agents.dye_demo.redteam.invariants as invariants

    config = series([2, 5, 10], ["A", "C", "E"], paper_rows=["A", "B", "C"])
    assert invariants.protocol_mismatches(config) == []                     # the plan and the protocol agree

    real = invariants.build_plan

    def swapped(config):                        # a plan that pairs the same paper positions with other wells
        plan = real(config)
        sources = iter(["C11", "A11", "E11"])
        return replace(plan, operations=[replace(op, source=next(sources)) if op.kind == "print" else op
                                         for op in plan.operations])

    monkeypatch.setattr(invariants, "build_plan", swapped)
    assert invariants.protocol_mismatches(config) == ["paper drops are printed from other plate wells than the plan shows"]
