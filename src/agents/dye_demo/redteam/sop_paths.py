"""Scripted user paths through the five user-test SOPs (docs/ai_dye_demo/user_testing/). Simulation only.

Each path is what a tester might type while working through an SOP, turn by turn, including mistakes, questions and
changes of mind. For turns that reach the model, the path carries the structured reply a faithful model would return,
so a replay (redteam.harness.replay) is deterministic and needs no model. Everything else is the real DemoSession:
intent analysis, ambiguity questions, validation, proposals, confirmation, state and runs, with the recording executor
(no subprocess, no robot).

tests/test_ai_dye_demo_user_test_sops.py replays every path, checks the red-team invariants on every turn, and checks
the configuration of every run with sop_check against the SOP's expected final state. The model replies are scripted:
they show that the deterministic layer turns reasonable readings into the right plan and catches wrong ones, not how a
live model reads the words.
"""
from __future__ import annotations

import json
from typing import Any

from src.agents.dye_demo.model import DEFAULT_CONFIG, REPO

SOP5_START = REPO / "configs" / "workflows" / "user_test_sops" / "ai_dye_demo_sop05_start.yaml"
START_CONFIGS = {1: DEFAULT_CONFIG, 2: DEFAULT_CONFIG, 3: DEFAULT_CONFIG, 4: DEFAULT_CONFIG, 5: SOP5_START}


def change(path: str, value: Any = None, evidence: str = "", **extra: Any) -> dict[str, Any]:
    data = {"path": path, "evidence": evidence, **extra}
    if "op" not in extra:
        data["value"] = value
    return data


def said(text: str, *changes: dict[str, Any]) -> dict[str, Any]:
    """A message; with changes, the reply the model gives if this turn reaches interpretation."""
    message: dict[str, Any] = {"text": text}
    if changes:
        reply = {"intent": "change", "changes": list(changes), "explanation": "Proposes the requested change.",
                 "clarification": ""}
        message["llm"] = [{"kind": "interpret", "reply": json.dumps(reply)}]
        message["label"] = {"category": "instruction", "may_propose": None, "intended": list(changes)}
    return message


def asked(text: str, answer: str = "") -> dict[str, Any]:
    """A question: it may be answered (deterministically or by the model) but never proposes or changes anything."""
    message: dict[str, Any] = {"text": text, "label": {"category": "detour_question", "may_propose": False}}
    if answer:
        message["llm"] = [{"kind": "ask", "reply": answer}]
    return message


def command(text: str) -> dict[str, Any]:
    return {"text": text, "label": {"category": "command", "may_propose": False}}


def run() -> dict[str, Any]:
    return {"text": "run", "label": {"category": "run", "may_run": True, "may_propose": False}}


YES = {"text": "yes"}
NO = {"text": "no"}

FACTORS_1 = change("dilution.factors", [2, 4, 8, 16], "2x, 4x, 8x and 16x")
VOLUME_1 = change("dilution.total_volume_ul", "200 uL", "200 uL")
VIAL_1 = change("materials.sample.vial", "B1", "vial B1")

FACTORS_2 = change("dilution.factors", [5, 10, 20], "5x, 10x and 20x")
FACTORS_3 = change("dilution.factors", [3, 6, 12], "3x, 6x and 12x")
FACTORS_4 = change("dilution.factors", [4, 8, 16, 32], "4x, 8x, 16x and 32x")
FACTORS_5 = change("dilution.factors", [3, 9, 27], "3x, 9x and 27x")
RUN1_SOP4 = ("Make four dilutions, 4x, 8x, 16x and 32x, 200 uL each, in plate column 6, and print one 5 uL drop of each "
             "in paper columns 1 and 2.")
RUN1_SOP4_CHANGES = (FACTORS_4, change("dilution.total_volume_ul", "200 uL", "200 uL each"),
                     change("dilution.plate_column", "6", "plate column 6"),
                     change("print.replicates", 2, "paper columns 1 and 2"))
PREPARED_190 = change("dilution.prepared_volume_ul", "190 uL", "about 190 uL")

PATHS: dict[int, dict[str, list[dict[str, Any]]]] = {
    1: {
        "clean": [
            said("I need four dilutions of the dye in water: 2x, 4x, 8x and 16x, with a final volume of 200 uL in each "
                 "well. The dye stock is in vial B1 today.", FACTORS_1, VOLUME_1, VIAL_1),
            YES, command("plan"), run(),
        ],
        "confused": [
            said("Make 4 dilutions.", change("dilution.factors", None, "4 dilutions", op="set_count", count=4)),
            YES,                                                        # approves 1x, 2x, 3x, 4x by mistake
            said("Wait, those factors are wrong. I need 2x, 4x, 8x and 16x.", FACTORS_1), YES,
            said("Make each one 200.", change("dilution.total_volume_ul", 200, "200")),      # no unit: asked
            said("yes", change("dilution.total_volume_ul", "200 uL", "200 µL")), YES,
            said("Use vial 5 for the dye."),                            # vial 5: the rack in slot 5, or vial B1?
            said("2", VIAL_1), YES,
            run(),
        ],
        "change_of_mind": [
            said("Make dilutions of 2x, 4x, 8x and 16x.", FACTORS_1), YES,
            said("Make each dilution 250 uL total.", change("dilution.total_volume_ul", "250 uL", "250 uL total")),
            said("Actually no, make it 200 uL instead.", VOLUME_1), YES,
            said("The dye is in vial B2.", change("materials.sample.vial", "B2", "vial B2")), YES,
            said("Sorry, I was wrong, the dye is in vial B1.", VIAL_1), YES,
            run(),
        ],
        "questions": [
            asked("Why do we mix the dilutions before printing them?",
                  "Each dilution well is mixed right before it is printed so the dye is evenly distributed."),
            asked("What have I changed so far?"),
            said("Make dilutions of 2x, 4x, 8x and 16x with 200 uL in each well, using the dye in vial B1.",
                 FACTORS_1, VOLUME_1, VIAL_1),
            asked("What am I approving?"), YES,
            asked("How many tips will this use?", "This plan uses 6 tips, A1 to F1."),
            command("tips"),
            asked("Which wells will the dilutions be made in?", "Wells A11, B11, C11 and D11 of the dilution plate."),
            run(),
        ],
    },
    2: {
        "clean": [
            said("Make three dilutions, 5x, 10x and 20x, and print two 5 uL drops of each one stacked on the same spot, "
                 "starting in paper column 2.", FACTORS_2, change("print.droplets_per_spot", 2, "two 5 uL drops"),
                 change("print.paper_start_column", 2, "paper column 2")),
            YES, run(),
        ],
        "confused": [
            said("Print the 5x, 10x and 20x dilutions in paper column 2."),          # those dilutions do not exist yet
            said("Skip the dilutions and just print them.", change("dilution.enabled", False, "Skip the dilutions")),
            NO,                                                                      # the wells hold nothing yet
            said("Make 5x, 10x and 20x dilutions.", FACTORS_2), YES,
            said("Print the 5x, 10x and 20x dilutions in paper column 2.",
                 change("print.paper_start_column", 2, "paper column 2")), YES,
            said("Print each dilution twice.", change("print.replicates", 2, "twice")),   # read as replicates: refused
            said("I mean two drops stacked on the same spot.", change("print.droplets_per_spot", 2, "two drops")), YES,
            run(),
        ],
        "change_of_mind": [
            said("Make 5x, 10x and 20x dilutions and print three drops of each in paper column 3.", FACTORS_2,
                 change("print.droplets_per_spot", 3, "three drops"), change("print.paper_start_column", 3, "column 3")),
            YES,
            said("Actually, two drops are enough.", change("print.droplets_per_spot", 2, "two drops")), YES,
            said("And print them in paper column 2 instead of 3.", change("print.paper_start_column", 2, "column 2")),
            YES, run(),
        ],
        "questions": [
            asked("What is the difference between a drop and a replicate?",
                  "Drops stack on one paper position; replicates repeat the print in the next paper column."),
            said("Make 5x, 10x and 20x dilutions.", FACTORS_2), YES,
            asked("Which paper positions will the 10x dilution print on?", "Row B: paper position B1."),
            said("Print two drops of each dilution on the same spot, starting at paper column 2.",
                 change("print.droplets_per_spot", 2, "two drops"), change("print.paper_start_column", 2, "column 2")),
            asked("What will change if I say yes?"), YES,
            command("plan"), run(),
        ],
    },
    3: {
        "clean": [
            said("Make 3x, 6x and 12x dilutions in plate column 3, starting at row D, and print one 5 uL drop of each in "
                 "paper columns 3 and 4.", FACTORS_3, change("dilution.plate_column", "3", "plate column 3"),
                 change("dilution.start_row", "D", "row D"), change("print.paper_start_column", 3, "paper columns 3"),
                 change("print.replicates", 2, "paper columns 3 and 4")),
            YES, run(),
        ],
        "confused": [
            said("Put the dilutions in slot 3.", change("deck.plate.slot", 3, "slot 3")),        # a slot, not a column
            said("Sorry, I meant plate column 3.", change("dilution.plate_column", "3", "plate column 3")), YES,
            said("Start the dilutions at row D.", change("dilution.start_row", "D", "row D")),   # 8 dilutions: past H
            said("Make only three dilutions, 3x, 6x and 12x, starting at row D.", FACTORS_3,
                 change("dilution.start_row", "D", "row D")), YES,
            said("Print in column 3 and 4.", change("print.paper_start_column", 3, "column 3"),
                 change("print.replicates", 2, "column 3 and 4")), YES,
            said("Move the plate to slot 3."),                          # which plate?
            said("1", change("deck.plate.slot", 3, "dilution plate to slot 3")),
            NO,                                                          # the proposed deck shows the mistake
            run(),
        ],
        "change_of_mind": [
            said("Make 3x, 6x and 12x dilutions in plate column 4, starting at row D.", FACTORS_3,
                 change("dilution.plate_column", "4", "plate column 4"), change("dilution.start_row", "D", "row D")),
            YES,
            said("Print one drop of each in paper columns 5 and 6.", change("print.paper_start_column", 5, "columns 5"),
                 change("print.replicates", 2, "paper columns 5 and 6")), YES,
            said("Actually, I'd rather use plate column 3 for the dilutions.",
                 change("dilution.plate_column", "3", "plate column 3")), YES,
            said("And move the printing to paper columns 3 and 4 instead.",
                 change("print.paper_start_column", 3, "paper columns 3 and 4")), YES,
            run(),
        ],
        "questions": [
            asked("Is plate column 3 the same thing as deck slot 3?",
                  "No. Deck slot 3 is a place on the robot; plate column 3 is a column of wells on the dilution plate."),
            asked("If I start the dilutions at row D, which paper rows get printed?",
                  "Each dilution prints on the paper row with the same letter, so rows D, E and F."),
            said("Make 3x, 6x and 12x dilutions in plate column 3 starting at row D.", FACTORS_3,
                 change("dilution.plate_column", "3", "plate column 3"), change("dilution.start_row", "D", "row D")),
            YES,
            said("Print one drop of each dilution in paper columns 3 and 4.",
                 change("print.paper_start_column", 3, "paper columns 3"), change("print.replicates", 2, "columns 3 and 4")),
            asked("Which paper positions will be printed?", "D3, D4, E3, E4, F3 and F4."), YES,
            command("deck"), command("plan"), run(),
        ],
    },
    4: {
        "one_at_a_time": [
            said("Make four dilutions: 4x, 8x, 16x and 32x.", FACTORS_4), YES,
            said("Make each dilution 200 uL total.", change("dilution.total_volume_ul", "200 uL", "200 uL total")), YES,
            said("Make the dilutions in plate column 6.", change("dilution.plate_column", "6", "plate column 6")), YES,
            said("Print one 5 uL drop of each dilution in paper columns 1 and 2.",
                 change("print.replicates", 2, "paper columns 1 and 2")), YES,
            run(),
            said("The four dilutions from the first run are already made, each well has about 190 uL left.",
                 PREPARED_190), YES,
            said("Stack three drops on each spot.", change("print.droplets_per_spot", 3, "three drops")), YES,
            said("Print in paper columns 4 and 5.", change("print.paper_start_column", 4, "paper columns 4")), YES,
            said("Start from tip G1 so no tip is used twice.", change("tips.start_tip", "G1", "tip G1")), YES,
            run(),
        ],
        "multi_change": [
            said(RUN1_SOP4, *RUN1_SOP4_CHANGES), YES, run(),
            said("The dilutions from run 1 are already made and each well now holds about 190 uL. Print three stacked "
                 "drops in paper columns 4 and 5, starting from tip G1.", PREPARED_190,
                 change("print.droplets_per_spot", 3, "three stacked drops"),
                 change("print.paper_start_column", 4, "paper columns 4 and 5"), change("tips.start_tip", "G1", "tip G1")),
            YES, run(),
        ],
        "confused": [
            said("Make four dilutions, 4x, 8x, 16x and 32x, 200 uL each, in plate column 6.", *RUN1_SOP4_CHANGES[:3]),
            YES,
            said("Print 1 drop in paper columns 1 and 2 and 3 drops in paper columns 4 and 5."),   # needs two runs
            said("Print one drop in paper columns 1 and 2 for now.", change("print.replicates", 2, "columns 1 and 2")),
            YES, run(),
            said("Now print three drops in columns 4 and 5.", change("print.droplets_per_spot", 3, "three drops"),
                 change("print.paper_start_column", 4, "columns 4 and 5")),
            NO,                                             # the plan would make the dilutions again: rejected
            said("The dilutions are already made, each well has about 190 uL left.", PREPARED_190), YES,
            said("Now print three drops in columns 4 and 5.", change("print.droplets_per_spot", 3, "three drops"),
                 change("print.paper_start_column", 4, "columns 4 and 5")), YES,
            asked("Which tips will this run use?", "Tips A1 to D1, one per dilution row."),
            said("Those tips were used in the first run. Start at tip G1.", change("tips.start_tip", "G1", "tip G1")),
            YES, run(),
        ],
        "change_of_mind": [
            said(RUN1_SOP4, *RUN1_SOP4_CHANGES), YES, run(),
            said("The dilutions from run 1 are already made, each well has about 190 uL left.", PREPARED_190), YES,
            said("Print four stacked drops in paper columns 4 and 5.", change("print.droplets_per_spot", 4, "four drops"),
                 change("print.paper_start_column", 4, "paper columns 4 and 5")),
            said("Actually, make that three drops, still in paper columns 4 and 5.",
                 change("print.droplets_per_spot", 3, "three drops"),
                 change("print.paper_start_column", 4, "paper columns 4 and 5")), YES,
            said("Start from tip G1.", change("tips.start_tip", "G1", "tip G1")), YES,
            run(),
        ],
        "questions": [
            asked("What is the difference between replicates and stacked drops?",
                  "Replicates print again in the next paper column; stacked drops land on the same position."),
            said(RUN1_SOP4, *RUN1_SOP4_CHANGES), YES,
            asked("How much liquid will be left in each well after this run?", "About 190 uL in each well."),
            run(),
            asked("Which tips did the first run use?", "Tips A1 to F1."),
            said("The four dilutions are already made, each well has about 190 uL left.", PREPARED_190), YES,
            said("Print three stacked drops of each dilution in paper columns 4 and 5, starting from tip G1.",
                 change("print.droplets_per_spot", 3, "three stacked drops"),
                 change("print.paper_start_column", 4, "paper columns 4 and 5"), change("tips.start_tip", "G1", "tip G1")),
            YES, command("tips"), run(),
        ],
    },
    5: {
        "clean": [
            said("Move the dilution plate from slot 4 to slot 8.", change("deck.plate.slot", 8, "slot 4 to slot 8")),
            said("Move the tip rack to slot 11 and the dilution plate to slot 8.",
                 change("deck.tiprack.slot", 11, "tip rack to slot 11"), change("deck.plate.slot", 8, "plate to slot 8")),
            YES,
            said("Make three dilutions, 3x, 9x and 27x, 180 uL each.", FACTORS_5,
                 change("dilution.total_volume_ul", "180 uL", "180 uL each")), YES,
            said("Print two stacked 5 uL drops of each dilution in paper columns 6 and 7.",
                 change("print.droplets_per_spot", 2, "two stacked"), change("print.paper_start_column", 6, "columns 6"),
                 change("print.replicates", 2, "paper columns 6 and 7")), YES,
            said("The first column of tips is already used, so start at tip A2, and use a fresh tip for every transfer.",
                 change("tips.start_tip", "A2", "tip A2"),
                 change("tips.policy", "new_tip_every_transfer", "fresh tip for every transfer")), YES,
            command("deck"), command("tips"), run(),
        ],
        "confused": [
            said("Move the plate to slot 8."),                                         # which plate?
            said("1", change("deck.plate.slot", 8, "dilution plate to slot 8")),        # slot 8 holds the tip rack
            said("Take the tip rack off the deck.", change("deck.tiprack.slot", "OFF_DECK", "tip rack off the deck")),
            said("Move the tip rack to slot 4 and the dilution plate to slot 8.",
                 change("deck.tiprack.slot", 4, "tip rack to slot 4"), change("deck.plate.slot", 8, "plate to slot 8")),
            YES,                                                                        # slot 4 had to stay empty
            said("Wait, slot 4 has to stay empty. Move the tip rack to slot 10.",
                 change("deck.tiprack.slot", 10, "tip rack to slot 10")), YES,
            said("Make 3x, 9x and 27x dilutions of 180 each.", FACTORS_5,
                 change("dilution.total_volume_ul", 180, "180")),                        # no unit: asked
            said("yes", FACTORS_5, change("dilution.total_volume_ul", "180 uL", "180 µL")), YES,
            said("Print 2 drops in columns 6 and 7.", change("print.droplets_per_spot", 2, "2 drops"),
                 change("print.paper_start_column", 6, "columns 6"), change("print.replicates", 2, "columns 6 and 7")),
            YES,
            said("The first row of tips is used up, start at tip B1.", change("tips.start_tip", "B1", "tip B1")), YES,
            command("tips"),
            said("No wait, tips A1 to H1 are all used. Start at tip A2 instead.", change("tips.start_tip", "A2", "tip A2")),
            YES,
            said("Use new tips every time.", change("tips.policy", "new_tip_every_transfer", "new tips every time")), YES,
            run(),
        ],
        "change_of_mind": [
            said("Move the tip rack to slot 9 and the dilution plate to slot 8.",
                 change("deck.tiprack.slot", 9, "tip rack to slot 9"), change("deck.plate.slot", 8, "plate to slot 8")),
            said("Actually, put the tip rack in slot 11 instead.", change("deck.tiprack.slot", 11, "tip rack in slot 11")),
            NO,                                                   # the new proposal no longer moves the plate
            said("Move the tip rack to slot 11 and the dilution plate to slot 8.",
                 change("deck.tiprack.slot", 11, "tip rack to slot 11"), change("deck.plate.slot", 8, "plate to slot 8")),
            YES,
            said("Make three dilutions, 3x, 9x and 27x, 180 uL each, and print two stacked drops of each in paper columns "
                 "6, 7 and 8.", FACTORS_5, change("dilution.total_volume_ul", "180 uL", "180 uL each"),
                 change("print.droplets_per_spot", 2, "two stacked drops"),
                 change("print.paper_start_column", 6, "paper columns 6"), change("print.replicates", 3, "6, 7 and 8")),
            YES,
            said("On second thought, only paper columns 6 and 7.", change("print.replicates", 2, "paper columns 6 and 7")),
            YES,
            said("The tips in the first column are already used. Start at tip A2 and use a new tip for every transfer.",
                 change("tips.start_tip", "A2", "tip A2"),
                 change("tips.policy", "new_tip_every_transfer", "new tip for every transfer")), YES,
            run(),
        ],
        "multi_change": [
            said("Move the tip rack to slot 10 and the dilution plate to slot 8, make 3x, 9x and 27x dilutions at 180 uL "
                 "each, print two stacked 5 uL drops of each in paper columns 6 and 7, start at tip A2 because the first "
                 "column of tips is used, and use a new tip for every transfer.",
                 change("deck.tiprack.slot", 10, "tip rack to slot 10"), change("deck.plate.slot", 8, "plate to slot 8"),
                 FACTORS_5, change("dilution.total_volume_ul", "180 uL", "180 uL each"),
                 change("print.droplets_per_spot", 2, "two stacked"), change("print.paper_start_column", 6, "columns 6"),
                 change("print.replicates", 2, "paper columns 6 and 7"), change("tips.start_tip", "A2", "tip A2"),
                 change("tips.policy", "new_tip_every_transfer", "new tip for every transfer")),
            YES, run(),
        ],
        "questions": [
            asked("What is currently in slot 8?", "Slot 8 holds the P20 tip rack."),
            command("deck"),
            said("Move the tip rack to slot 11 and the dilution plate to slot 8.",
                 change("deck.tiprack.slot", 11, "tip rack to slot 11"), change("deck.plate.slot", 8, "plate to slot 8")),
            asked("Why does the tip rack have to move in the same request?",
                  "Slot 8 is occupied until the tip rack leaves it, so both moves are checked together."), YES,
            asked("Could the tip rack go off the deck instead?", "No: every step needs P20 tips."),
            said("Make three dilutions, 3x, 9x and 27x, 180 uL each.", FACTORS_5,
                 change("dilution.total_volume_ul", "180 uL", "180 uL each")), YES,
            said("Print two stacked 5 uL drops of each dilution in paper columns 6 and 7.",
                 change("print.droplets_per_spot", 2, "two stacked"), change("print.paper_start_column", 6, "columns 6"),
                 change("print.replicates", 2, "paper columns 6 and 7")), YES,
            asked("How many tips will a fresh tip for every transfer use?", "34 tips with this plan."),
            said("Start at tip A2, because A1 to H1 are used, and use a fresh tip for every transfer.",
                 change("tips.start_tip", "A2", "tip A2"),
                 change("tips.policy", "new_tip_every_transfer", "fresh tip for every transfer")), YES,
            command("tips"), run(),
        ],
    },
}
