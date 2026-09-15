"""The conversational demo's guard rails: what the agent may change, and how it is shown."""
from __future__ import annotations

from datetime import datetime

import pytest

from src.agents.dye_demo import render
from src.agents.dye_demo.language import (
    TRIGGER,
    UNSURE,
    apply_option,
    conflicting_well_reference,
    evidence_supported,
    find_ambiguities,
    looks_like_question,
    numbers_mentioned,
    parse_ask,
    parse_confirmation,
    resolve_answer,
    wants_to_run,
)
from src.agents.dye_demo.llm import LLMError, parse_interpretation
from src.agents.dye_demo.model import (
    DEFAULT_CONFIG,
    OFF_DECK,
    FieldError,
    get_path,
    load_config,
    load_machine_profile,
    normalize_slot,
)
from src.agents.dye_demo.plan import build_plan, dilution_rows, paper_layout, split_volume
from src.agents.dye_demo.session import GREETING, HELP
from src.agents.dye_demo.state import ExperimentState, ProposalRejected, StaleProposal
from src.agents.dye_demo.validation import validate


@pytest.fixture()
def config():
    return load_config(DEFAULT_CONFIG)


@pytest.fixture()
def state(config):
    return ExperimentState(config)


def change(path, value, evidence="", kind="requested", why=""):
    return {"path": path, "value": value, "evidence": evidence, "kind": kind, "why": why}


def row(label, value):
    """One plan row as the terminal shows it: a 20-character label column, then the value."""
    return f"  {label:<20}  {value}"


def heading(title, status):
    """A plan section heading with its status right-aligned to the 72-character screen."""
    return f"{title}{status:>{72 - len(title)}}"


def leaves(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, f"{prefix}.{key}" if prefix else key)
    else:
        yield prefix, node


# ── defaults ────────────────────────────────────────────────────────────────────

def test_default_is_eight_dilutions_printed_at_five_microlitres(config):
    assert config["protocol_version"] == 19
    assert config["pipette"]["name"] == "p20_single_gen2"
    assert config["deck"]["tiprack"]["load_name"] == "opentrons_96_tiprack_20ul"
    assert len(config["dilution"]["factors"]) == 8
    assert config["print"]["droplet_volume_ul"] == 5.0
    assert validate(config).ok


def test_default_is_live_unless_the_caller_simulates(config):
    assert config["run_modes"]["dry_run"] is False


def test_lab_owned_print_release_matches_the_machine_profile(config):
    rows = render.profile_comparison(config, load_machine_profile())
    assert rows and not [row for row in rows if row[3] == "MISMATCH"]
    by_label = {label: status for label, _, _, status in rows}
    assert by_label["Drop release height above paper (mm)"] == "match"
    assert by_label["Trailing air gap (µL)"] == "match"
    assert config["dilution"]["blow_out_after_dispense"] is True


# ── proposals: explicit, validated, confirmed ───────────────────────────────────

def test_conversational_edits_become_a_proposal_before_anything_changes(state):
    proposal = state.propose([
        change("deck.plate.slot", 6, "plate to slot 6"),
        change("dilution.factors", [1, 2, 5, 10], "1, 2, 5 and 10"),
        change("dilution.start_row", "D", "row D"),
        change("dilution.plate_column", "3", "column 3"),
        change("materials.dye.vial", "A3", "vial A3"),
        change("print.droplet_volume_ul", 10.0, "10 uL"),
        change("print.paper_start_column", 3, "paper column 3"),
        change("print.replicates", 2, "2 replicates"),
    ], request="plate to slot 6, factors 1, 2, 5 and 10 from row D in column 3, dye in vial A3, "
               "10 uL drops from paper column 3 with 2 replicates")
    assert state.revision == 0 and state.config["deck"]["plate"]["slot"] == 4
    state.apply(proposal, operator="Stephen")
    applied = state.config
    assert state.revision == 1
    assert dilution_rows(applied) == ["D", "E", "F", "G"]
    assert [spot["column"] for spot in paper_layout(applied)] == [3, 4]
    assert applied["materials"]["dye"]["vial"] == "A3"


@pytest.mark.parametrize("path, value", [
    ("pipette.name", "p300_single_gen2"),
    ("safety.p20_max_volume_ul", 300),
    ("run_modes.dry_run", True),
    ("print.z_mm", 5.0),
    ("print.air_gap_ul", 0),
    ("print.blow_out", False),
    ("print.post_dispense_delay_s", 0),
    ("dilution.max_transfer_ul", 200),
    ("dilution.blow_out_after_dispense", False),
    ("mixing.height_mm", 0.5),
    ("materials.dye.aspirate_height_mm", 1.0),
    ("deck.plate.load_name", "some_other_plate"),
])
def test_hardware_and_calibration_are_not_the_agents_to_change(state, path, value):
    with pytest.raises(ProposalRejected, match="lab-owned") as caught:
        state.propose([change(path, value, "x")], request="x")
    assert caught.value.kind == "lab_owned"


@pytest.mark.parametrize("path, value, said, message", [
    ("print.droplet_volume_ul", 25.0, "25 µL drops", "over the P20"),
    ("print.droplet_volume_ul", 0.5, "0.5 µL drops", "under the P20"),
    ("dilution.factors", [1, 2, 3, 4, 5, 6, 7, 8, 9], "dilution factors 1, 2, 3, 4, 5, 6, 7, 8, 9", "1 to 8 dilutions"),
    ("dilution.factors", [1, 1000], "dilution factors 1 and 1000", "under the P20"),
    ("dilution.total_volume_ul", 400, "400 µL total per dilution", "total_volume_ul must be in"),
    ("dilution.total_volume_ul", 60, "60 µL total per dilution", "tip would draw air"),
    ("materials.dye.vial", "A1", "dye in vial A1", "different vials"),
    ("tips.start_tip", "H12", "start tips at H12", "start from an earlier tip"),
    ("deck.plate.slot", 12, "plate to slot 12", "trash"),
    ("deck.plate.slot", 0, "plate to slot 0", "1-11"),
])
def test_physically_impossible_requests_are_refused(state, path, value, said, message):
    with pytest.raises(ProposalRejected, match=message):
        state.propose([change(path, value, said)], request=said)


def test_series_past_row_h_and_printing_off_the_paper_are_refused(state):
    with pytest.raises(ProposalRejected, match="past row H"):
        state.propose([change("dilution.start_row", "F"), change("dilution.factors", [1, 2, 4, 8, 16, 32])],
                      request="start at row F with factors 1, 2, 4, 8, 16 and 32")
    with pytest.raises(ProposalRejected, match="past the paper"):
        state.propose([change("print.droplet_volume_ul", [5, 10, 15]), change("print.paper_start_column", 11)],
                      request="print 5, 10 and 15 µL drops starting at paper column 11")


def test_rejected_edits_leave_the_state_untouched(state):
    before = state.fingerprint()
    with pytest.raises(ProposalRejected):
        state.propose([change("print.droplet_volume_ul", 25.0)], request="25 uL drops")
    assert state.fingerprint() == before and state.revision == 0 and state.history == []


def test_an_older_proposal_cannot_overwrite_newer_approved_values(state):
    older = state.propose([change("print.droplet_volume_ul", 10.0, "10 uL")], request="10 uL drops")
    newer = state.propose([change("print.paper_start_column", 3, "column 3")], request="paper column 3")
    state.apply(newer, operator="Stephen")
    with pytest.raises(StaleProposal, match="revision 0"):
        state.apply(older, operator="Stephen")
    assert state.config["print"]["droplet_volume_ul"] == 5.0
    assert state.config["print"]["paper_start_column"] == 3


def test_moving_one_labware_changes_exactly_one_value(state):
    before = dict(leaves(state.config))
    state.apply(state.propose([change("deck.paper.slot", 8, "paper to slot 8")], request="paper to slot 8"),
                operator="Stephen")
    after = dict(leaves(state.config))
    assert {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)} == {"deck.paper.slot"}


def test_values_that_are_already_set_are_not_reported_as_changes(state):
    proposal = state.propose([
        change("dilution.factors", [1, 2, 3, 4, 6, 8, 12, 16], "same factors"),
        change("print.droplet_volume_ul", 5, "5 uL"),
        change("deck.paper.slot", 8, "slot 8"),
    ], request="keep the same factors and 5 uL but paper to slot 8")
    assert proposal.paths == ["deck.paper.slot"]


def test_changes_the_request_does_not_support_are_flagged_or_refused(state):
    proposal = state.propose([
        change("print.droplets_per_spot", 3, "three drops"),
        change("tips.return_tips", True, ""),
    ], request="print three drops on each position")
    flags = {item.path: (item.verified, item.concern) for item in proposal.changes}
    assert flags["print.droplets_per_spot"] == (True, "")
    assert flags["tips.return_tips"] == (False, "you did not mention the return used tips to the rack")
    text = render.render_proposal(proposal)
    attention = text.split("ATTENTION", 1)[1]
    assert "CHECK THESE - I could not find them in what you typed" in attention
    assert "  - Return used tips to the rack: yes" in attention
    with pytest.raises(ProposalRejected, match="You typed A20") as caught:
        state.propose([change("tips.start_tip", "A2", "tip A20")], request="start at tip A20")
    assert caught.value.kind == "well_mismatch"


PREPARED_3 = {"wells": ["A11", "B11", "C11"], "factors": [2, 5, 10], "total_volume_ul": 100.0,
              "source": "reported by the operator"}
PREPARED_DEFAULT = {"wells": [f"{row}11" for row in "ABCDEFGH"], "factors": [1, 2, 3, 4, 6, 8, 12, 16],
                    "total_volume_ul": 150.0, "source": "reported by the operator"}


def test_skipping_the_dilution_step_needs_a_record_that_the_dilutions_exist(state):
    with pytest.raises(ProposalRejected, match="nothing in this session records") as caught:
        state.propose([change("dilution.enabled", False, "skip making the dilutions")],
                      request="skip making the dilutions and just print")
    assert caught.value.kind == "prerequisite" and caught.value.question
    proposal = state.propose([change("dilution.enabled", False, "skip making the dilutions")],
                             request="skip making the dilutions and just print",
                             physical={"dilutions_prepared": PREPARED_DEFAULT})
    assert proposal.physical["dilutions_prepared"]["wells"][0] == "A11"


def test_dependent_changes_are_explained(state):
    proposal = state.propose([change("tips.start_tip", "C2", kind="dependent", why="run 1 used tips A1-B2")],
                             request="(after run 1)")
    assert proposal.changes[0].verified
    text = render.render_proposal(proposal)
    assert "NOTES\n  - tip start: run 1 used tips A1-B2" in text
    assert row("Tip start", "C2") in text


def test_a_skipped_dilution_stays_skipped_until_explicitly_re_enabled(state):
    """Skipped-dilution audit: the step flag only moves when a change names it."""
    state.apply(state.propose([change("dilution.enabled", False, "already made"),
                               change("dilution.factors", [2, 5, 10], "2x, 5x and 10x"),
                               change("dilution.total_volume_ul", 100, "100 µL")],
                              request="the 2x, 5x and 10x dilutions at 100 µL are already made",
                              physical={"dilutions_prepared": PREPARED_3}), operator="Stephen")
    state.apply(state.propose([change("print.paper_start_column", 2, "paper column 2")],
                              request="start printing at paper column 2"), operator="Stephen")
    assert state.config["dilution"]["enabled"] is False
    summary = render.render_current_plan(state.config, state.validate(), simulate=True, operator="Stephen",
                                         prepared=state.physical.get("dilutions_prepared"))
    assert heading("DILUTIONS", "SKIPPED - already in the plate") in summary.splitlines()
    assert row("Recorded", "reported by the operator") in summary
    assert "Transfers" not in summary and row("Water", "vial A1 | not used in this run") in summary
    state.apply(state.propose([change("dilution.enabled", True, "make the dilutions")],
                              request="make the dilutions again"), operator="Stephen")
    assert build_plan(state.config).do_dilution
    assert "already hold dilutions" in state.run_blockers()[0]      # never fill the same wells twice


def test_history_records_who_changed_what_and_when(state):
    record = state.apply(state.propose([change("print.replicates", 2, "2 replicates")], request="2 replicates"),
                         operator="Stephen")
    assert record["operator"] == "Stephen" and record["revision"] == 1
    assert record["changes"] == [{
        "path": "print.replicates", "label": "Replicate paper columns", "before": 1, "after": 2,
        "kind": "requested", "why": "", "verified": True, "concern": "",
    }]
    assert "by Stephen" in render.render_history(state.history)


# ── deck ────────────────────────────────────────────────────────────────────────

def test_moving_into_an_occupied_slot_explains_the_conflict_and_changes_nothing(state):
    before = state.fingerprint()
    with pytest.raises(ProposalRejected) as caught:
        state.propose([change("deck.plate.slot", 7, "plate to 7")], request="move the plate from 4 to 7")
    rejected = caught.value
    assert rejected.kind == "deck_conflict" and state.fingerprint() == before
    text = render.render_conflict(rejected.before, rejected.after, rejected.conflicts)
    assert text.startswith("!" * 30 + " ATTENTION ") and " -> " not in text
    for expected in ("Cannot apply that deck change yet.", "96-well dilution plate to Slot 7 (now in Slot 4)",
                     "Slot 7 is currently occupied by the Vial rack.",
                     "I need a new location for the Vial rack before the 96-well dilution plate can move to Slot 7.",
                     "Not OFF DECK: making dilutions draws dye and water from its vials.",
                     '"move the vial rack to slot 1 and the dilution plate to slot 7"', "Nothing was changed."):
        assert expected in text
    assert "or OFF DECK" not in text                     # this plan makes dilutions, so the vial rack must stay


def test_a_conflict_offers_off_deck_only_for_labware_no_step_needs(state):
    state.apply(state.propose([change("dilution.enabled", False, "dilutions are already made")],
                              request="the dilutions are already made", physical={"dilutions_prepared": PREPARED_DEFAULT}),
                operator="Stephen")
    with pytest.raises(ProposalRejected) as caught:
        state.propose([change("deck.plate.slot", 7, "plate to 7")], request="move the plate from 4 to 7")
    text = render.render_conflict(caught.value.before, caught.value.after, caught.value.conflicts)
    assert "or OFF DECK" in text and '"OFF DECK" means the Vial rack has been physically removed' in text


def test_a_swap_in_one_request_is_not_a_conflict(state):
    proposal = state.propose([change("deck.plate.slot", 7, "plate to 7"), change("deck.tuberack.slot", 4, "rack to 4")],
                             request="swap them: plate to 7, rack to 4")
    assert proposal.deck_changed and proposal.report.ok


def test_off_deck_is_valid_only_when_no_step_needs_that_labware(state):
    with pytest.raises(ProposalRejected, match="Vial rack is OFF DECK"):
        state.propose([change("deck.tuberack.slot", "off deck", "vial rack off deck")], request="vial rack off deck")
    proposal = state.propose([change("deck.tuberack.slot", "OFF DECK", "vial rack off deck"),
                              change("dilution.enabled", False, "dilutions are already made")],
                             request="the dilutions are already made, take the vial rack off deck",
                             physical={"dilutions_prepared": PREPARED_DEFAULT})
    assert proposal.after["deck"]["tuberack"]["slot"] == OFF_DECK
    text = render.render_proposal(proposal, prepared=PREPARED_DEFAULT)
    plan, attention = text.split("!!! ATTENTION !!!", 1)
    assert row("Vial rack", "OFF DECK (removed from the robot)") in plan
    assert row("Empty slots", "1 | 2 | 3 | 6 | 7 | 8 | 10 | 11") in plan
    assert "remove the Vial rack from Slot 7 (it goes OFF DECK)" in attention and render.OFF_DECK_MEANING in attention
    assert "CURRENT DECK" not in text and "<- changed" not in text


def test_a_proposal_shows_the_complete_resulting_plan_without_before_and_after(state):
    proposal = state.propose([change("deck.paper.slot", 8, "slot 8")], request="paper print plate to slot 8")
    text = render.render_proposal(proposal)
    for expected in ("PROPOSED PLAN #1", "not applied yet - nothing changes until you type yes",
                     row("Changes", "paper print plate slot"), "DILUTIONS", "PRINTING", "DECK", "LIQUIDS",
                     "PIPETTING", "LAB-OWNED PARAMETERS", row("Paper print plate", "Slot 8"),
                     row("Empty slots", "1 | 2 | 3 | 5 | 6 | 10 | 11"), row("Drop volume", "5 µL"),
                     "physically move the Paper print plate from Slot 5 to Slot 8",
                     "Apply proposal #1?  Type yes to apply this plan, or no to discard it."):
        assert expected in text
    for removed in ("Slot 5 -> Slot 8", " -> ", "CURRENT DECK", "PROPOSED DECK", "NO CHANGES", "unchanged",
                    "RESULTING PLAN", "PROPOSED CHANGES", "revision"):
        assert removed not in text


@pytest.mark.parametrize("value, expected", [(7, 7), ("slot 7", 7), ("7", 7), ("OFF DECK", OFF_DECK),
                                             ("off-deck", OFF_DECK), ("removed", OFF_DECK)])
def test_slot_values_are_normalised(value, expected):
    assert normalize_slot(value) == expected


@pytest.mark.parametrize("value", [12, 0, "spot 7", True, "A1"])
def test_bad_slot_values_are_rejected(value):
    with pytest.raises(FieldError):
        normalize_slot(value)


# ── FROM / TO, tips, liquids ────────────────────────────────────────────────────

def three(config, **dilution):
    config["dilution"].update(factors=[2, 5, 10], total_volume_ul=100.0, **dilution)
    return config


def test_every_print_step_names_its_source_and_destination(config):
    config = three(config)
    text = render.render_print_step(config, build_plan(config))
    assert text.count("PRINT STEP") == 3
    for expected in ("FROM : 96-well dilution plate, Slot 4, well A11", "2× dye dilution in water",
                     "TO   : Paper print plate, Slot 5, position A1",
                     "Volume per drop: 5 µL   Drops: 1   Total: 5 µL   Tip: C1",
                     "FROM : 96-well dilution plate, Slot 4, well C11", "10× dye dilution in water"):
        assert expected in text


def test_dilution_step_names_the_vials_and_wells(config):
    config = three(config)
    text = render.render_dilution_step(config, build_plan(config))
    assert "FROM : Vial rack, Slot 7, vial A1" in text and "FROM : Vial rack, Slot 7, vial A2" in text
    assert "TO   : 96-well dilution plate, Slot 4, well(s) A11, B11, C11" in text
    assert "then blow out" in text


def test_tip_configuration_is_summarised(config):
    config = three(config)
    text = render.render_tip_configuration(config, build_plan(config))
    for expected in ("TIP CONFIGURATION", "Tip rack               : P20 tip rack, Slot 9",
                     "Starting tip           : A1", "Tip reuse              : Yes",
                     "Estimated tips required: 5   (A1-E1)", "Available from start   : 96",
                     "Next unused tip after  : F1", "Estimated remaining    : 91 after this run"):
        assert expected in text


def test_a_step_that_does_not_run_takes_no_tips(config):
    config = three(config, enabled=False)
    config["tips"]["start_tip"] = "D1"
    assert [tip.tip for tip in build_plan(config).tips] == ["D1", "E1", "F1"]


def test_new_tip_policy_counts_a_tip_for_every_transfer_and_position(config):
    config = three(config)
    config["tips"]["policy"] = "new_tip_every_transfer"
    plan = build_plan(config)
    assert plan.tips_needed == len(plan.operations) == 20


def test_volume_splitting_never_leaves_a_transfer_below_the_minimum():
    chunks = split_volume(140.625, 20.0, 1.0)
    assert min(chunks) >= 1.0 and len(chunks) == 8 and abs(sum(chunks) - 140.62) < 0.02
    assert split_volume(20.5, 20.0, 1.0) == [10.25, 10.25]
    assert split_volume(40.0, 20.0, 1.0) == [20.0, 20.0]


def test_the_three_dilution_experiment_from_the_guide_is_valid(config):
    run_one = three(config)
    assert validate(run_one).ok
    run_two = three(load_config(DEFAULT_CONFIG), enabled=False)
    run_two["print"].update(droplets_per_spot=3, paper_start_column=2)
    run_two["tips"]["start_tip"] = "D1"
    report = validate(run_two)
    assert report.ok and [issue.code for issue in report.warnings] == ["print.assumes_prepared"]


def test_the_summary_names_both_steps_and_how_to_start(config):
    simulated = render.render_current_plan(config, validate(config), simulate=True, operator="Stephen")
    for expected in ("CURRENT PLAN", "SIMULATION - nothing contacts the robot   |   Stephen", "DILUTIONS",
                     "PRINTING", "DECK", "LIQUIDS", "PIPETTING", "LAB-OWNED PARAMETERS", "All plan checks passed.",
                     f">>> TO RUN THE SIMULATION NOW, TYPE:  {TRIGGER}", "No robot is contacted.", "A11", "H11"):
        assert expected in simulated
    live = render.render_current_plan(config, validate(config), simulate=False, operator="Stephen")
    assert f">>> TO START THE REAL ROBOT NOW, TYPE:  {TRIGGER}" in live
    assert "the real OT-2 will move" in live and "The OT-2 starts moving as soon as you do." in live


def test_the_greeting_and_help_name_the_trigger_and_ask_mode():
    for text in (GREETING.format(name="Stephen", trigger=TRIGGER), HELP.format(trigger=TRIGGER)):
        assert f"type {TRIGGER} to start it" in text and "/ask" in text


# ── deterministic language ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["i don't know", "I dont know what I want", "not sure", "no idea",
                                  "just use the default", "show me a standard example", "you pick"])
def test_an_unsure_scientist_gets_the_worked_example(text):
    assert UNSURE.search(text)


@pytest.mark.parametrize("text", ["run", "go", "go ahead", "this is good run it", "yes run it", "ok start",
                                  "looks good, run it", "proceed", "lets go"])
def test_a_plain_go_ahead_starts_the_run(text):
    assert wants_to_run(text)


@pytest.mark.parametrize("text", ["make 4 dilutions and run it at 10 uL", "run 8 dilutions in column 3",
                                  "go to slot 6", "start the series at row D", "i dont know",
                                  "should we run it?", "can we go", "don't run yet", "wait before you run",
                                  "run?"])
def test_a_change_or_a_hedge_is_never_a_go_ahead(text):
    assert not wants_to_run(text)


@pytest.mark.parametrize("text, verdict", [("yes", "yes"), ("Yes.", "yes"), ("confirm", "yes"), ("no", "no"),
                                          ("discard", "no"), ("sure?", None), ("I think so", None),
                                          ("ok", None), ("maybe", None), ("yes but use slot 6", None)])
def test_only_an_explicit_answer_confirms_or_discards(text, verdict):
    assert parse_confirmation(text) == verdict


def test_questions_and_ask_mode_are_recognised():
    assert looks_like_question("why do we mix before printing?")
    assert looks_like_question("What is the drop volume")
    assert not looks_like_question("move the paper print plate to slot 8")
    assert parse_ask("/ask Why are we using three dilution steps?") == "Why are we using three dilution steps?"
    assert parse_ask("/ask") == "" and parse_ask("ask me later") is None


def test_spot_is_confirmed_as_a_deck_slot(config):
    [item] = [a for a in find_ambiguities("Move the dilution plate to spot 7.", config)]
    assert item.question == 'You said "spot 7." Did you mean OT-2 deck SLOT 7?' and item.yes_no
    option = resolve_answer(item, "yes")
    assert apply_option("Move the dilution plate to spot 7.", item, option) == "Move the dilution plate to deck slot 7."
    assert resolve_answer(item, "no") == "reject" and resolve_answer(item, "hmm") is None


def test_an_unqualified_column_is_confirmed_only_when_the_sentence_is_ambiguous(config):
    [item] = find_ambiguities("print the dilutions in column 3", config)
    assert [option.replacement for option in item.options] == ["paper column 3", "plate column 3"]
    assert resolve_answer(item, "1").replacement == "paper column 3"
    assert find_ambiguities("print at column 3", config) == []
    assert find_ambiguities("dilute down column 5", config) == []
    assert find_ambiguities("start printing at paper column 3", config) == []


def test_the_plate_is_resolved_by_the_slot_it_is_in_or_else_confirmed(config):
    items = find_ambiguities("move the plate from 4 to 8", config)
    assert items[0].automatic and items[0].options[0].replacement == "dilution plate"
    assert items[1].question == 'You said "to 8." Did you mean OT-2 deck SLOT 8?'
    [which] = [a for a in find_ambiguities("move the plate to slot 6", config)]
    assert [option.replacement for option in which.options] == ["dilution plate", "paper print plate"]


@pytest.mark.parametrize("text, count", [("use the second bottle", 1), ("put the tray in slot 2", 1),
                                         ("check hole B3", 1), ("put the paper print plate in slot 8", 0),
                                         ("I want to place an order for dye", 0), ("use vial A2", 0)])
def test_ambiguous_container_and_location_words(config, text, count):
    assert len([item for item in find_ambiguities(text, config) if not item.automatic]) == count


def test_evidence_and_well_reference_checks():
    assert evidence_supported("paper to slot 8", "please move the paper print plate to slot 8")
    assert not evidence_supported("skip the dilution step", "print three drops in column 2")
    assert conflicting_well_reference("A1", "start from tip A10") == "A10"
    assert conflicting_well_reference("A10", "start from tip A10") is None
    assert numbers_mentioned(3, "stack three drops") and numbers_mentioned(2, "mix twice")
    assert not numbers_mentioned([1, 2, 4, 8], "make 4 dilutions")


# ── LLM replies ─────────────────────────────────────────────────────────────────

def test_interpretations_are_parsed_strictly():
    parsed = parse_interpretation('```json\n{"intent":"change","changes":[{"path":"print.replicates",'
                                  '"value":2,"evidence":"two replicates"}]}\n```')
    assert parsed.changes == [{"path": "print.replicates", "value": 2, "kind": "requested",
                               "evidence": "two replicates", "why": ""}]
    legacy = parse_interpretation('{"updates": {"deck": {"paper": {"slot": 8}}}}')
    assert legacy.changes[0]["path"] == "deck.paper.slot" and legacy.intent == "change"
    assert parse_interpretation('{"intent": "question", "answer": "Because."}').answer == "Because."
    with pytest.raises(LLMError, match="path and a value"):
        parse_interpretation('{"changes": [{"value": 2}]}')
    with pytest.raises(LLMError, match="JSON object"):
        parse_interpretation("I would rather write you a protocol in Python.")


def test_session_labels_carry_the_date(tmp_path):
    from scripts.ai_dye_demo import session_label

    day = datetime(2026, 9, 3, 10, 0)
    assert session_label("Demo 1", today=day, run_root=tmp_path) == "2026-09-03 Demo 1"
    (tmp_path / "20260903_090000").mkdir()
    assert session_label(None, today=day, run_root=tmp_path) == "2026-09-03 Session 2"
    assert get_path({"a": {"b": 1}}, "a.b") == 1


def test_a_dilute_only_run_warns_that_its_wells_are_not_mixed(config):
    config["print"]["enabled"] = False
    report = validate(config)
    assert report.ok and "dilution.not_mixed" in [issue.code for issue in report.warnings]
    assert build_plan(config).total_drops == 0
