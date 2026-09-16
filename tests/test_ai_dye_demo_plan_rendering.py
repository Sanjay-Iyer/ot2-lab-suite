"""The AI dye demo shows one clean plan: what the experiment does if the scientist says yes.

Presentation only. These pin the terminal format of the CURRENT PLAN and of every proposal - the complete resulting
plan in the sections DILUTIONS, PRINTING, DECK, LIQUIDS, PIPETTING and LAB-OWNED PARAMETERS, never old -> new - with
anything that blocks execution or must be checked in a separate ATTENTION block. The derived values on the screen
(paper columns, dilutions, print positions, drops, printed volume, tips, vial use, deck slots) are checked against
what protocol v19 actually does on the recording fake OT-2, and confirmation, validation and the run gates are checked
to behave as before.

Offline: recorded model replies (redteam.harness.replay) and a recording executor; nothing contacts an OT-2.
"""
from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy

import pytest

import scripts.run_vial_print_robot as runner
from src.agents.dye_demo import render
from src.agents.dye_demo.model import (
    DEFAULT_CONFIG,
    EDITABLE_FIELDS,
    LAB_OWNED_FIELDS,
    LABWARE_ROLES,
    is_off_deck,
    load_config,
)
from src.agents.dye_demo.plan import build_plan
from src.agents.dye_demo.redteam.fake_opentrons import load_protocol_module, run_protocol
from src.agents.dye_demo.redteam.harness import replay
from src.agents.dye_demo.redteam.sop_paths import PATHS, START_CONFIGS, change, run, said
from src.agents.dye_demo.state import ExperimentState, ProposalRejected
from src.agents.dye_demo.validation import validate

DEFAULT = load_config(DEFAULT_CONFIG)
SECTIONS = ["DILUTIONS", "PRINTING", "DECK", "LIQUIDS", "PIPETTING", "LAB-OWNED PARAMETERS"]
# what the old screens showed and the plan screens must not: before -> after values, change markers, the separate
# PROPOSED CHANGES / NO CHANGES / RESULTING PLAN sections, the before and after decks, and revision numbers
OLD_FORMAT = [" -> ", "<- changed", "PROPOSED CHANGES", "NO CHANGES", "RESULTING PLAN", "CURRENT DECK", "PROPOSED DECK",
              ": unchanged", "Recorded physical state"]
REVISION = re.compile(r"revision \d+ -> \d+|still revision|- revision \d|Revision: \d", re.I)
YES = {"text": "yes"}
NO = {"text": "no", "label": {"category": "reject", "may_propose": False}}
DECK_LABELS = {"plate": "Dilution plate", "paper": "Paper print plate", "tuberack": "Vial rack",
               "tiprack": "P20 tip rack"}


def row(label, value):
    """One plan row as the terminal shows it: two spaces, a 20-character label column, two spaces, the value."""
    return f"  {label:<20}  {value}"


def heading(title, status):
    return f"{title}{status:>{render.WIDTH - len(title)}}"


def squash(text):
    return " ".join(text.split())


def value_of(screen, label):
    """The value of one plan row, with wrapped continuation lines joined."""
    lines = screen.splitlines()
    prefix = f"  {label:<20}  "
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            parts = [line[len(prefix):]]
            for following in lines[index + 1:]:
                if not following.startswith(" " * render.VALUE_COLUMN) or not following.strip():
                    break
                parts.append(following.strip())
            return " ".join(parts)
    raise AssertionError(f"no {label!r} row in:\n{screen}")


def plan_block(screen):
    """The plan sections of a screen: from the DILUTIONS heading to the rule that closes LAB-OWNED PARAMETERS."""
    start = screen.index("\nDILUTIONS") + 1
    end = screen.index(render.THIN, screen.index("LAB-OWNED PARAMETERS", start)) + len(render.THIN)
    return screen[start:end]


def section_starts(screen):
    return [re.search(rf"^{re.escape(title)}\b", screen, re.M).start() for title in SECTIONS]


def configured(**sections):
    config = deepcopy(DEFAULT)
    for section, values in sections.items():
        config[section].update(values)
    return config


def talk(tmp_path, *messages, config=None, live=False):
    items = [message if isinstance(message, dict) else {"text": message} for message in messages]
    result = replay(items, workdir=tmp_path, keep_transcript=True, return_session=True, config=config, live_logic=live)
    assert not result["violations"], result["violations"]
    return result


def output(result, turn):
    return result["transcript"][turn - 1]["output"]


def events(result, turn, kind):
    return [event for event in result["transcript"][turn - 1]["events"] if event["type"] == kind]


def current_plan(config, **options):
    return render.render_current_plan(config, validate(config), simulate=True, operator="Tester", **options)


def assert_clean(screen):
    for old in OLD_FORMAT:
        assert old not in screen, f"{old!r} in:\n{screen}"
    assert not REVISION.search(screen), screen


# ── the layout ──────────────────────────────────────────────────────────────────

def test_the_current_plan_is_one_screen_with_every_section_in_order():
    screen = current_plan(DEFAULT)
    lines = screen.splitlines()
    assert lines[:4] == [render.RULE, "CURRENT PLAN".center(render.WIDTH).rstrip(),
                         "SIMULATION - nothing contacts the robot   |   Tester".center(render.WIDTH).rstrip(), render.RULE]
    starts = section_starts(screen)
    assert starts == sorted(starts)
    assert heading("DILUTIONS", "made in this run") in lines and heading("PRINTING", "in this run") in lines
    assert heading("LAB-OWNED PARAMETERS", "never changed in conversation") in lines
    assert screen.index("All plan checks passed.") > starts[-1]
    assert lines[-2:] == [">>> TO RUN THE SIMULATION NOW, TYPE:  run",
                          "    No robot is contacted. Or keep talking to change the plan first."]
    assert all(len(line) <= render.WIDTH for line in plan_block(screen).splitlines())
    assert_clean(screen)


def test_arrays_are_shown_with_pipes():
    screen = current_plan(configured(dilution={"factors": [1, 2, 3]}, print={"replicates": 3}))
    assert squash("Factor 1× 2× 3×") in squash(screen)
    assert value_of(screen, "Paper columns") == "1 | 2 | 3"
    assert value_of(screen, "Paper rows") == "A | B | C   (one row per dilution)"
    assert value_of(screen, "Empty slots") == "1 | 2 | 3 | 6 | 8 | 10 | 11"
    assert render.format_value("dilution.factors", [1, 2, 3]) == "1× | 2× | 3×"


def test_a_proposal_is_the_complete_plan_that_exists_after_yes():
    state = ExperimentState(deepcopy(DEFAULT))
    before = current_plan(state.config)
    proposal = state.propose([change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"),
                              change("print.droplets_per_spot", 3, "three drops"),
                              change("deck.tuberack.slot", 6, "vial rack to slot 6")],
                             request="make 2x, 5x and 10x, print three drops, and move the vial rack to slot 6")
    screen = render.render_proposal(proposal)
    lines = screen.splitlines()
    assert lines[:4] == [render.RULE, "PROPOSED PLAN #1".center(render.WIDTH).rstrip(),
                         "not applied yet - nothing changes until you type yes".center(render.WIDTH).rstrip(), render.RULE]
    assert value_of(screen, "Changes") == "1) dilution factors | 2) drops per position | 3) vial rack slot"
    # the whole resulting plan, exactly as the CURRENT PLAN will show it after yes - not the plan that runs now
    assert plan_block(screen) == "\n".join(render.plan_sections(proposal.after))
    assert plan_block(screen) != plan_block(before)
    assert section_starts(screen) == sorted(section_starts(screen))
    assert lines[-1] == "Apply proposal #1?  Type yes to apply this plan, or no to discard it."
    assert state.revision == 0 and state.config == DEFAULT              # showing it changed nothing
    state.apply(proposal, operator="Tester")
    assert plan_block(current_plan(state.config)) == plan_block(screen)
    assert_clean(screen)


def test_revision_numbers_stay_out_of_the_plan_and_the_approval_messages(tmp_path):
    result = talk(tmp_path, said("Move the paper print plate to slot 8.", change("deck.paper.slot", 8, "slot 8")), YES,
                  said("Use 2 replicate paper columns.", change("print.replicates", 2, "2 replicate paper columns")), NO,
                  "history")
    applied, discarded = output(result, 2), output(result, 4)
    assert "APPLIED proposal #1. This is now the current plan (recorded for Replay)." in applied
    assert "Discarded proposal #2. Nothing was changed." in discarded
    for text in (output(result, 1), applied, output(result, 3), discarded):
        assert "revision" not in text.lower()
    assert "revision 1" in output(result, 5)                            # the history command still has them
    assert result["session"].state.revision == 1


@pytest.mark.parametrize("sop, name", [(sop, name) for sop, paths in PATHS.items() for name in paths])
def test_no_plan_screen_in_any_user_test_path_shows_old_values(tmp_path, sop, name):
    """Every proposal and CURRENT PLAN shown along the scripted SOP user paths (clean, confused, change of mind,
    questions, several changes at once): the resulting plan only."""
    result = replay(PATHS[sop][name], workdir=tmp_path, keep_transcript=True, return_session=True,
                    config=load_config(START_CONFIGS[sop]))
    screens = 0
    for number, turn in enumerate(result["transcript"], start=1):
        text = turn["output"]
        if render.APPLY_PROMPT in text or "CURRENT PLAN" in text:
            screens += 1
            assert_clean(text)
            assert section_starts(text) == sorted(section_starts(text)), f"turn {number}"
    assert screens


# ── every setting is visible ────────────────────────────────────────────────────

CHANGED = configured(
    deck={"plate": {**DEFAULT["deck"]["plate"], "slot": 3}, "paper": {**DEFAULT["deck"]["paper"], "slot": 8},
          "tuberack": {**DEFAULT["deck"]["tuberack"], "slot": 6}, "tiprack": {**DEFAULT["deck"]["tiprack"], "slot": 10}},
    materials={"dye": {**DEFAULT["materials"]["dye"], "vial": "B1", "label": "crystal violet"},
               "water": {**DEFAULT["materials"]["water"], "vial": "B2", "label": "buffer"}},
    dilution={"factors": [2, 5, 10], "plate_column": "3", "start_row": "C", "total_volume_ul": 120.0},
    mixing={"reps": 3, "volume_ul": 10.0},
    print={"droplet_volume_ul": 4.0, "droplets_per_spot": 2, "replicates": 2, "paper_start_column": 6},
    tips={"start_tip": "C1", "return_tips": True, "policy": "new_tip_every_transfer"},
)
PREPARED = configured(dilution={"enabled": False, "prepared_volume_ul": 140.0})
NOT_PRINTING = configured(print={"enabled": False})

# every conversation-editable setting, the screen that shows it changed, and how it reads there
EDITABLE_ROWS = {
    "deck.plate.slot": (CHANGED, lambda s: value_of(s, "Dilution plate") == "Slot 3"),
    "deck.paper.slot": (CHANGED, lambda s: value_of(s, "Paper print plate") == "Slot 8"),
    "deck.tuberack.slot": (CHANGED, lambda s: value_of(s, "Vial rack") == "Slot 6"),
    "deck.tiprack.slot": (CHANGED, lambda s: value_of(s, "P20 tip rack") == "Slot 10"),
    "materials.sample.vial": (CHANGED, lambda s: value_of(s, "Crystal violet (dye)").startswith("vial B1 | uses ")),
    "materials.solvent.vial": (CHANGED, lambda s: value_of(s, "Buffer (water)").startswith("vial B2 | uses ")),
    "materials.sample.label": (CHANGED, lambda s: value_of(s, "Transfers").endswith("| 6 crystal violet")),
    "materials.solvent.label": (CHANGED, lambda s: " buffer |" in value_of(s, "Transfers")),
    "dilution.enabled": (PREPARED, lambda s: heading("DILUTIONS", "SKIPPED - already in the plate") in s),
    "dilution.factors": (CHANGED, lambda s: squash("Factor 2× 5× 10×") in squash(s)),
    "dilution.plate_column": (CHANGED, lambda s: value_of(s, "Dilutions") == "3 in plate column 3 (rows C-E)"),
    "dilution.start_row": (CHANGED, lambda s: squash("Well C3 D3 E3") in squash(s)),
    "dilution.total_volume_ul": (CHANGED, lambda s: value_of(s, "Final volume") == "120 µL in each well"),
    "dilution.prepared_volume_ul": (PREPARED, lambda s: value_of(s, "Volume in each well") == "140 µL"),
    "mixing.reps": (CHANGED, lambda s: value_of(s, "Mixing") == "3 × 10 µL before each print step"),
    "mixing.volume_ul": (CHANGED, lambda s: value_of(s, "Mixing") == "3 × 10 µL before each print step"),
    "print.enabled": (NOT_PRINTING, lambda s: heading("PRINTING", "SKIPPED - this run does not print") in s),
    "print.droplet_volume_ul": (CHANGED, lambda s: value_of(s, "Drop volume") == "4 µL"),
    "print.droplets_per_spot": (CHANGED, lambda s: value_of(s, "Drops per position") == "2  (stacked)"),
    "print.replicates": (CHANGED, lambda s: value_of(s, "Replicates") == "2 side-by-side columns per drop volume"),
    "print.paper_start_column": (CHANGED, lambda s: value_of(s, "Paper columns") == "6 | 7"),
    "tips.start_tip": (CHANGED, lambda s: value_of(s, "Tip start") == "C1"),
    "tips.return_tips": (CHANGED, lambda s: value_of(s, "Used tips") == "returned to the rack (do not reuse them)"),
    "tips.policy": (CHANGED, lambda s: value_of(s, "Tip use") == "new tip every transfer"),
}


def test_every_editable_setting_has_a_row_check():
    assert set(EDITABLE_ROWS) == set(EDITABLE_FIELDS)


@pytest.mark.parametrize("path", sorted(EDITABLE_ROWS))
def test_every_conversation_setting_is_visible_in_the_plan(path):
    config, shows = EDITABLE_ROWS[path]
    screen = current_plan(config)
    assert shows(screen), f"{path} not shown as expected in:\n{screen}"


LAB_OWNED = configured(
    print={"z_mm": 0.5, "aspirate_height_mm": 1.5, "air_gap_ul": 2.0, "air_gap_height_mm": 4.0, "push_out_ul": 2.5,
           "blow_out": False, "post_dispense_delay_s": 1.5},
    dilution={"max_transfer_ul": 15.0, "solvent_dispense_from_top_mm": -3.0, "sample_dispense_from_top_mm": -1.5,
              "blow_out_after_dispense": False},
    mixing={"height_mm": 2.5},
)
LAB_OWNED["materials"]["dye"]["aspirate_height_mm"] = 5.0
LAB_OWNED["flow_rates"] = {"aspirate": 4.0, "dispense": 5.0}
LAB_OWNED_ROWS = {
    "print.z_mm": ("Print height", "0.5 mm above the paper"),
    "print.push_out_ul": ("Drop release", "2.5 µL push-out | blow-out off | 1.5 s dwell"),
    "print.blow_out": ("Drop release", "2.5 µL push-out | blow-out off | 1.5 s dwell"),
    "print.post_dispense_delay_s": ("Drop release", "2.5 µL push-out | blow-out off | 1.5 s dwell"),
    "print.air_gap_ul": ("Air gap", "2 µL, taken 4 mm above the well top"),
    "print.air_gap_height_mm": ("Air gap", "2 µL, taken 4 mm above the well top"),
    "print.aspirate_height_mm": ("Print aspirate", "1.5 mm above the well bottom"),
    "mixing.height_mm": ("Mixing height", "2.5 mm above the well bottom"),
    "dilution.solvent_dispense_from_top_mm": ("Dilution dispense", "water 3 mm | dye 1.5 mm below the well top"),
    "dilution.sample_dispense_from_top_mm": ("Dilution dispense", "water 3 mm | dye 1.5 mm below the well top"),
    "dilution.max_transfer_ul": ("Dilution transfers", "at most 15 µL each | blow-out off"),
    "dilution.blow_out_after_dispense": ("Dilution transfers", "at most 15 µL each | blow-out off"),
}


def test_every_lab_owned_parameter_the_run_uses_is_visible():
    # print.paper_columns is the paper's width: a validation limit, not something the run does
    assert set(LAB_OWNED_ROWS) | {"print.paper_columns"} == set(LAB_OWNED_FIELDS)
    lines = render.plan_sections(LAB_OWNED)
    screen = "\n".join(lines[lines.index(heading("LAB-OWNED PARAMETERS", "never changed in conversation")):])
    for path, (label, value) in LAB_OWNED_ROWS.items():
        assert value_of(screen, label) == value, path
    assert value_of(screen, "Pipette") == "p20_single_gen2 (left mount)"
    assert value_of(screen, "Flow rates") == "aspirate 4 µL/s | dispense 5 µL/s"
    assert value_of(screen, "Vial aspirate") == "water 4 mm | dye 5 mm above the vial bottom"


# ── derived values: the screen says what the protocol does ─────────────────────

LAYOUTS = {
    "default": DEFAULT,
    "three dilutions, stacked replicates": configured(
        dilution={"factors": [2, 5, 10], "total_volume_ul": 100.0, "start_row": "C", "plate_column": "3"},
        print={"droplets_per_spot": 3, "replicates": 2, "paper_start_column": 4}),
    "two drop volumes, new tip every transfer": configured(
        dilution={"factors": [2, 4]}, print={"droplet_volume_ul": [2.0, 5.0], "replicates": 2, "paper_start_column": 3},
        tips={"policy": "new_tip_every_transfer"}),
    "print only": configured(dilution={"enabled": False, "prepared_volume_ul": 140.0},
                             print={"droplets_per_spot": 2, "paper_start_column": 2}, tips={"start_tip": "D1"}),
    "dilute only": configured(dilution={"factors": [2, 4, 8]}, print={"enabled": False}),
}


def robot(config):
    """What protocol v19 does with this configuration on the recording fake OT-2."""
    context = run_protocol(load_protocol_module(), config)
    deck = config["deck"]
    plate, paper, rack = (deck[role]["load_name"] for role in ("plate", "paper", "tuberack"))
    log = context.log
    vials = {role: next(spec["vial"] for spec in config["materials"].values() if spec["role"] == role)
             for role in ("sample", "solvent")}
    return {
        "tips": [entry[1] for entry in log if entry[0] == "pick_up_tip"],
        "drops": [entry[2][1] for entry in log if entry[0] == "dispense" and entry[2][0] == paper],
        "printed_ul": sum(entry[1] for entry in log if entry[0] == "aspirate" and entry[2][0] == plate),
        "filled": sorted({entry[2][1] for entry in log if entry[0] == "dispense" and entry[2][0] == plate}),
        "vial_ul": {role: sum(entry[1] for entry in log if entry[0] == "aspirate" and entry[2][:2] == (rack, vial))
                    for role, vial in vials.items()},
        "mixes": {(entry[1], entry[2]) for entry in log if entry[0] == "mix"},
        "slots": dict(context.loaded),
    }


def number(text):
    return float(re.match(r"[\d.]+", text).group())


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_the_derived_values_are_what_the_protocol_does(layout):
    config = LAYOUTS[layout]
    plan = build_plan(config)
    screen = current_plan(config)
    moved = robot(config)

    tips = value_of(screen, "Tips required")
    assert int(tips.split()[0]) == len(moved["tips"]) == plan.tips_needed
    assert tips.endswith(f"({moved['tips'][0]}-{moved['tips'][-1]})")
    dilutions = value_of(screen, "Dilutions")
    assert int(dilutions.split()[0]) == len(plan.wells)
    if plan.do_dilution:
        assert squash("Well " + " ".join(moved["filled"])) in squash(screen)
        for role, label in (("sample", "Dye"), ("solvent", "Water")):
            used = value_of(screen, label).split(" | ")[1]
            assert abs(number(used.removeprefix("uses ")) - moved["vial_ul"][role]) < 0.011, label
    else:
        assert moved["filled"] == [] and moved["vial_ul"] == {"sample": 0, "solvent": 0}
        assert value_of(screen, "Dye") == "vial A2 | not used in this run"

    if not plan.do_print:
        assert heading("PRINTING", "SKIPPED - this run does not print") in screen and moved["drops"] == []
        assert "Paper columns" not in screen and "Total drops" not in screen
    else:
        drops = moved["drops"]
        columns = sorted({int(well[1:]) for well in drops})
        rows = sorted({well[0] for well in drops})
        per_position = set(Counter(drops).values())
        assert value_of(screen, "Paper columns") == " | ".join(map(str, columns))
        assert squash(value_of(screen, "Paper rows")) == squash(" | ".join(rows) + " (one row per dilution)")
        positions = value_of(screen, "Print positions")
        assert int(positions.split()[0]) == len(set(drops)) == len(rows) * len(columns)
        assert f"{len(rows)} row" in positions and f"× {len(columns)} column" in positions
        assert per_position == {int(value_of(screen, "Drops per position").split()[0])}
        assert int(value_of(screen, "Total drops")) == len(drops) == plan.total_drops
        assert abs(number(value_of(screen, "Printed volume")) - moved["printed_ul"]) < 0.011
        reps, volume = number(value_of(screen, "Mixing")), number(value_of(screen, "Mixing").split("× ")[1])
        assert {(int(reps), volume)} == moved["mixes"]

    for role in LABWARE_ROLES:
        slot = config["deck"][role]["slot"]
        if not is_off_deck(slot):
            assert value_of(screen, DECK_LABELS[role]) == f"Slot {slot}"
            assert str(moved["slots"][config["deck"][role]["load_name"]]) == str(slot)


# ── accepting a proposal ────────────────────────────────────────────────────────

def test_after_yes_the_current_plan_is_exactly_the_plan_that_was_proposed(tmp_path):
    result = talk(tmp_path, said("Make 2x, 5x and 10x dilutions, print two replicate columns, and move the vial rack to "
                                 "slot 6.", change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"),
                                 change("print.replicates", 2, "two replicate columns"),
                                 change("deck.tuberack.slot", 6, "vial rack to slot 6")), YES, "plan")
    proposed, applied, plan = output(result, 1), output(result, 2), output(result, 3)
    assert applied.index("APPLIED proposal #1. This is now the current plan") < applied.index("CURRENT PLAN")
    assert plan_block(applied) == plan_block(proposed) == plan_block(plan)
    assert value_of(applied, "Vial rack") == "Slot 6" and value_of(applied, "Paper columns") == "1 | 2"
    # the accepted plan, not its history
    for gone in ("PROPOSED PLAN", render.APPLY_PROMPT):
        assert gone not in applied
    for old in ("Slot 7", "8 in plate column", "1×"):
        assert old not in plan_block(applied)
    # the move still to make is an ATTENTION item under the plan, and the plan command no longer repeats it
    notice = applied.split("!!! ATTENTION !!!", 1)[1]
    assert "Now physically move the Vial rack from Slot 7 to Slot 6." in notice
    assert "ATTENTION" not in plan and "All plan checks passed." in plan
    assert_clean(applied)


# ── ATTENTION ───────────────────────────────────────────────────────────────────

def attention_block(screen):
    match = re.search(r"^!+ ATTENTION !+$\n(.*?)^!{72}$", screen, re.M | re.S)
    assert match, f"no ATTENTION block in:\n{screen}"
    return match.group(1)


def test_unverified_changes_are_under_attention_below_the_plan(tmp_path):
    message = said("Print three drops on each position.", change("print.droplets_per_spot", 3, "three drops"),
                   change("tips.return_tips", True, ""))
    message["label"]["intended"] = [change("print.droplets_per_spot", 3, "three drops")]
    result = talk(tmp_path, message, NO)
    screen = output(result, 1)
    block = attention_block(screen)
    assert "CHECK THESE - I could not find them in what you typed:" in block
    assert "  - Return used tips to the rack: yes" in block
    assert screen.index("ATTENTION") > screen.index(plan_block(screen)) + len(plan_block(screen))
    assert screen.index("ATTENTION") < screen.index(render.APPLY_PROMPT)
    assert "CHECK THESE" not in plan_block(screen)
    assert result["session"].state.revision == 0


def test_warnings_and_errors_are_under_attention_and_errors_withhold_the_run_instruction():
    warned = current_plan(configured(tips={"return_tips": True}))
    assert "Warning: used tips go back into the rack" in attention_block(warned)
    assert ">>> TO RUN THE SIMULATION NOW, TYPE:  run" in warned and "All plan checks passed." not in warned
    broken = current_plan(configured(tips={"start_tip": "G12"}))
    block = attention_block(broken)
    assert "This plan cannot run. Execution is blocked until these are fixed:" in block
    assert "  - this plan needs 10 tips but only 2 remain from G12" in block
    assert value_of(broken, "Tips required") == "10   (only 2 left from G12)"
    assert broken.endswith("Fix the problems above before running.") and ">>>" not in broken


def test_named_paper_columns_are_shown_in_a_proposal_and_wait_for_approval(tmp_path):
    result = talk(tmp_path, said("Print in paper columns 3 and 4.", change("print.replicates", 2, "paper columns 3 and 4")),
                  run(), {"text": "cancel"}, run())
    assert value_of(output(result, 1), "Paper columns") == "3 | 4"
    assert events(result, 1, "proposal") and render.APPLY_PROMPT in output(result, 1)
    assert not events(result, 2, "run") and events(result, 4, "run")
    assert result["session"].state.revision == 0


def test_a_deck_conflict_is_under_attention_and_changes_nothing(tmp_path):
    message = said("Move the dilution plate to slot 7.", change("deck.plate.slot", 7, "dilution plate to slot 7"))
    message["label"] = {"category": "move_into_occupied", "may_propose": False}
    result = talk(tmp_path, message)
    block = attention_block(output(result, 1))
    assert "Cannot apply that deck change yet. Nothing was changed." in block
    assert "96-well dilution plate to Slot 7 (now in Slot 4)" in block and " -> " not in block
    assert not events(result, 1, "proposal") and result["session"].state.config == DEFAULT


def test_run_refusals_are_under_attention(tmp_path):
    result = talk(tmp_path, {"text": "I took the rack off the robot.",
                             "label": {"category": "physical_report", "may_propose": False, "intended": []}},
                  {"text": "1", "label": {"category": "clarification_answer", "may_propose": None, "intended": []}},
                  run())
    assert "Not running: you told me" in attention_block(output(result, 3)) and not events(result, 3, "run")


# ── confirmation and validation are unchanged ───────────────────────────────────

def test_confirmation_still_needs_an_explicit_yes(tmp_path):
    move = said("Move the paper print plate to slot 8.", change("deck.paper.slot", 8, "paper print plate to slot 8"))
    result = talk(tmp_path, move, {"text": "sure?", "label": {"category": "uncertain_approval", "may_propose": False}},
                  {"text": "run", "label": {"category": "run_while_pending", "may_propose": False}}, NO,
                  move, YES)
    state = result["session"].state
    rows = result["transcript"]
    assert [row_["revision_after"] for row_ in rows] == [0, 0, 0, 0, 0, 1]
    assert [row_["pending_after"] for row_ in rows] == [1, 1, 1, None, 2, None]
    assert "That is not a clear yes, so nothing was applied" in output(result, 2)
    assert "Proposal #1 is still waiting" in output(result, 3) and not events(result, 3, "run")
    assert "Discarded proposal #1. Nothing was changed." in output(result, 4)
    assert "PROPOSED PLAN #2" in output(result, 5)
    assert state.config["deck"]["paper"]["slot"] == 8 and len(state.history) == 1


def test_validation_still_refuses_what_cannot_run(tmp_path):
    state = ExperimentState(deepcopy(DEFAULT))
    with pytest.raises(ProposalRejected):
        state.propose([change("print.droplet_volume_ul", 50, "50 µL")], request="use 50 µL drops")
    with pytest.raises(ProposalRejected, match="Vial rack is OFF DECK"):
        state.propose([change("deck.tuberack.slot", "OFF DECK", "vial rack off deck")], request="vial rack off deck")
    assert state.revision == 0 and state.config == DEFAULT
    result = talk(tmp_path, said("Use 50 uL drops.", change("print.droplet_volume_ul", "50 uL", "50 uL")))
    assert "I did not change anything, because" in output(result, 1)
    assert not events(result, 1, "proposal") and result["session"].state.config == DEFAULT


def test_the_numbers_on_a_proposal_select_its_changes(tmp_path):
    result = talk(tmp_path, said("Move the paper print plate to slot 8 and use 2 replicate paper columns.",
                                 change("deck.paper.slot", 8, "paper print plate to slot 8"),
                                 change("print.replicates", 2, "2 replicate paper columns")),
                  {"text": "1", "label": {"category": "partial_approval", "may_propose": None}}, YES)
    assert value_of(output(result, 1), "Changes") == "1) paper print plate slot | 2) replicates"
    second = output(result, 2)
    assert "PROPOSED PLAN #2" in second and "replaces #1 - not applied yet" in second
    assert value_of(second, "Changes") == "paper print plate slot" and value_of(second, "Replicates").startswith("1 ")
    config = result["session"].state.config
    assert config["deck"]["paper"]["slot"] == 8 and config["print"]["replicates"] == 1


def test_numbered_changes_name_only_the_value_each_change_gives():
    state = ExperimentState(deepcopy(DEFAULT))
    proposal = state.propose([change("deck.paper.slot", 8, "slot 8"), change("dilution.factors", [2, 5], "2x and 5x")],
                             request="paper print plate to slot 8, dilutions 2x and 5x")
    expected = {"deck.paper.slot": "Paper print plate location: Slot 8", "dilution.factors": "Dilution factors: 2× | 5×"}
    assert render.render_numbered_changes(proposal.changes).splitlines() == [
        f"  {index}) {expected[path]}" for index, path in enumerate(proposal.paths, start=1)]


# ── the other screens ───────────────────────────────────────────────────────────

def test_steps_still_lists_every_movement_from_and_to(tmp_path):
    result = talk(tmp_path, "steps", "plan")
    steps = output(result, 1)
    for expected in ("STEP 1 - DILUTIONS", "FROM : Vial rack, Slot 7, vial A1", "TO   : 96-well dilution plate, Slot 4",
                     "STEP 2 - PRINTING", "TO   : Paper print plate, Slot 5, position A1"):
        assert expected in steps
    assert "CURRENT PLAN" in output(result, 2)


def test_the_run_banner_summarises_the_run_without_revisions(tmp_path):
    result = talk(tmp_path, run())
    banner = output(result, 1)
    assert "STARTING SIMULATION" in banner and events(result, 1, "run")
    assert value_of(banner, "Operator") == "Replay | redteam-0 | run 1"
    for label, value in (("Dilutions made", "8   (plate wells A11-H11)"), ("Paper columns", "1"),
                         ("Print positions", "8"), ("Total drops", "8"), ("Tips", "10   (A1-B2)"),
                         ("Vial rack", "Slot 7")):
        assert value_of(banner, label) == value
    assert "revision" not in banner.lower() and "TIP CONFIGURATION" not in banner


def test_the_robot_runner_prints_the_same_plan_layout(capsys):
    config = deepcopy(DEFAULT)
    config["session"] = {"operator": "Tester", "session_label": "Demo 1", "revision": 3}
    runner._describe_ai_demo(config)
    printed = capsys.readouterr().out
    assert "PLAN IN THE BUILT PROTOCOL" in printed and "Tester | Demo 1" in printed
    assert plan_block(printed) == plan_block(current_plan(DEFAULT))
    assert "revision" not in printed.lower() and "TIP CONFIGURATION" not in printed
