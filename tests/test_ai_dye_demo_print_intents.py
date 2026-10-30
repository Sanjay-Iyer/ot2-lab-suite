"""Per-source print intents, stress-tested against the old rigid print-map loop.

A scripted router stands in for the model (what a competent reading of each message returns); every assertion is
about what Python does with that reading: the intent stays semantic while editing, one source changes without the
others, a source moves freely between count / rows / exact positions, questions change nothing, an unfinished plan
stays editable, and only the compiled plan has the exact print map. The last test runs a compiled plan with unequal
per-source counts in the pinned Opentrons simulator. No robot is contacted.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.agents.dye_demo.compiler import compile_plan
from src.agents.dye_demo.llm import LLMClient
from src.agents.dye_demo.model import DEFAULT_CONFIG, load_config, normalize_factors
from src.agents.dye_demo.patches import PlanOperation, apply_operations
from src.agents.dye_demo.redteam.simulate_states import BASE_PROTOCOL, PINNED_SIMULATOR, _builder
from src.agents.dye_demo.session import DemoSession, SessionSettings

# the old rigid loop's refusals (and any local failure): none may appear in these conversations
RIGID = re.compile(r"already selected|destinations already exist|must specify|conflicts with (?:the )?map|old well|"
                   r"listed twice|need (?:a )?print map|did not change anything|(?-i:NOT APPLIED)|FieldError|Traceback", re.I)


class Router:
    def __init__(self, replies):
        self.replies, self.seen, self.prompts = replies, [], []

    def invoke(self, messages):
        message = messages[-1][1].split("SCIENTIST'S MESSAGE:\n")[-1].strip()
        self.seen.append(message)
        self.prompts.append(messages[-1][1])
        return SimpleNamespace(content=json.dumps(self.replies[message]))


class Executor:
    last_status = {"started": True, "robot_status": "succeeded"}

    def check_robot(self):
        return None

    def __call__(self, *_):
        return 0


def samples():
    """Three samples already in plate wells A11, B11 and C11 (nothing is diluted in this run)."""
    config = load_config(DEFAULT_CONFIG)
    config["dilution"]["enabled"] = False
    return config


def session(tmp_path, replies, config=None):
    model, output = Router(replies), []
    settings = SessionSettings(simulate=True, config_source=DEFAULT_CONFIG, working_config=tmp_path / "working.yaml",
                               run_dir=tmp_path / "run", session_label="intents", operator="Tester",
                               skip_llm_startup=True, run_button="Run on OT-2", llm_first=True, raise_errors=True)
    demo = DemoSession(settings, deepcopy(config) if config is not None else samples(), llm=LLMClient(lambda: model),
                       executor=Executor(), input_fn=lambda _p: "yes", output_fn=output.append, sleep=lambda _: None)
    demo.operator = "Tester"
    demo.request_run_confirmation = lambda: output.append("CONFIRM RUN")
    return demo, model, output


def change(*operations):
    return {"kind": "plan_change", "operations": [dict(operation) for operation in operations]}


def per(source=None, value=None, op="set", group=None):
    """One print group edited on its own (the selector form)."""
    operation = {"op": op, "path": "print.source", "value": value}
    if source is not None:
        operation["source"] = source
    if group is not None:
        operation["group"] = group
    return operation


def put(*items):
    return {"op": "set", "path": "print.source", "value": list(items)}


def say(demo, output, *messages):
    """Each message and Apply; returns what was said, which must never be the old rigid refusals."""
    shown = len(output)
    for message in messages:
        revision = demo.state.revision
        demo._handle(message)
        if demo.pending is not None:
            demo._handle("yes")
            assert demo.state.revision == revision + 1, "\n".join(output[shown:])
    said = "\n".join(output[shown:])
    assert not RIGID.search(said), said
    return said


def intents(demo):
    return demo.state.config["print"]["source"]


def compiled_counts(demo):
    compiled = demo.state.compile()
    assert compiled.ok, compiled.errors
    counts: dict[str, int] = {}
    for entry in compiled.source_map:
        counts[entry["source"]] = counts.get(entry["source"], 0) + len(entry["positions"])
    return counts, compiled


THREE = "Print A11 three times, B11 once and C11 twice."
THREE_REPLY = change(put({"source": "A11", "count": 3}, {"source": "B11", "count": 1}, {"source": "C11", "count": 2}))
DILUTE = "Make 2x, 4x and 8x dilutions in column 10."
DILUTE_REPLY = change({"path": "dilution.factors", "value": [2, 4, 8]}, {"path": "dilution.plate_column", "value": "10"})
GROUPS = "Print the first one three times, the second once and the third twice."
GROUPS_REPLY = change(put({"source": "dilution:1", "count": 3}, {"source": "dilution:2", "count": 1},
                          {"source": "dilution:3", "count": 2}))


# ── TEST 1, 2: different counts per source; one source changes alone ──────────────────────────────────────────────

def test_01_02_counts_per_source_and_one_source_changes_alone(tmp_path):
    demo, _, output = session(tmp_path, {THREE: THREE_REPLY,
                                         "Actually make B11 four prints.": change(per("B11", {"count": 4}))})
    say(demo, output, THREE)
    assert intents(demo) == [{"source": "A11", "count": 3}, {"source": "B11", "count": 1},
                             {"source": "C11", "count": 2}]
    assert demo.state.config["print"].get("source_map") is None
    counts, compiled = compiled_counts(demo)
    assert counts == {"A11": 3, "B11": 1, "C11": 2}
    assert compiled.source_map == [{"source": "A11", "positions": ["A1", "A2", "A3"]},
                                   {"source": "B11", "positions": ["B1"]}, {"source": "C11", "positions": ["C1", "C2"]}]
    say(demo, output, "Actually make B11 four prints.")
    assert intents(demo) == [{"source": "A11", "count": 3}, {"source": "B11", "count": 4},
                             {"source": "C11", "count": 2}]
    assert demo.state.history[-1]["changes"][0]["path"] == "print.source"           # one field, one group in it
    assert compiled_counts(demo)[0] == {"A11": 3, "B11": 4, "C11": 2}
    assert demo.state.config["print"].get("source_map") is None


# ── TEST 3, 4: count -> rows -> fewer rows -> count again, never trapped ─────────────────────────────────────────────

def test_03_04_placement_style_switches_back_and_forth(tmp_path):
    demo, _, output = session(tmp_path, {
        "Print A11 three times.": change(put({"source": "A11", "count": 3})),
        "Actually put those on rows 1, 3 and 5.": change(per("A11", {"rows": ["A", "C", "E"]})),
        "Make it only rows 1 and 5.": change(per("A11", {"rows": ["A", "E"]})),
        "Never mind the exact rows. Just print A11 four times wherever there is space.":
            change(per("A11", {"count": 4})),
    })
    say(demo, output, "Print A11 three times.")
    assert compiled_counts(demo)[1].source_map == [{"source": "A11", "positions": ["A1", "A2", "A3"]}]
    say(demo, output, "Actually put those on rows 1, 3 and 5.")
    assert intents(demo) == [{"source": "A11", "rows": ["A", "C", "E"]}]            # the rows superseded the count
    assert compiled_counts(demo)[1].source_map == [{"source": "A11", "positions": ["A1", "C1", "E1"]}]
    say(demo, output, "Make it only rows 1 and 5.")
    assert compiled_counts(demo)[1].source_map == [{"source": "A11", "positions": ["A1", "E1"]}]
    say(demo, output, "Never mind the exact rows. Just print A11 four times wherever there is space.")
    assert intents(demo) == [{"source": "A11", "count": 4}]                         # back to a count: not trapped
    assert compiled_counts(demo)[1].source_map == [{"source": "A11", "positions": ["A1", "A2", "A3", "A4"]}]


# ── TEST 5, 6, 7: logical dilution sources, moved upstream, one group re-pointed ────────────────────────────────────

def test_05_06_07_dilution_groups_follow_the_series_and_change_alone(tmp_path):
    demo, model, output = session(tmp_path, {
        DILUTE: DILUTE_REPLY, GROUPS: GROUPS_REPLY,
        "Move the dilutions to column 11.": change({"path": "dilution.plate_column", "value": "11"}),
        "Which wells will those prints come from now?": {"kind": "question", "response": "A11, B11 and C11."},
        "Use the third dilution for the second print group instead.": change(per(value={"source": "dilution:3"},
                                                                                   group=2)),
    }, config=load_config(DEFAULT_CONFIG))
    say(demo, output, DILUTE, GROUPS)
    assert intents(demo) == [{"source": "dilution:1", "count": 3}, {"source": "dilution:2", "count": 1},
                             {"source": "dilution:3", "count": 2}]                  # no physical wells frozen
    assert compiled_counts(demo)[0] == {"A10": 3, "B10": 1, "C10": 2}
    say(demo, output, "Move the dilutions to column 11.")
    assert intents(demo)[0] == {"source": "dilution:1", "count": 3}                 # untouched by the move
    revision = demo.state.revision
    say(demo, output, "Which wells will those prints come from now?")
    assert demo.state.revision == revision and demo.pending is None
    assert "A11 (dilution:1) -> 3 prints" in model.prompts[-1] and "B11 (dilution:2) -> 1 print" in model.prompts[-1]
    counts, compiled = compiled_counts(demo)
    assert counts == {"A11": 3, "B11": 1, "C11": 2} and not any("10" in entry["source"] for entry in compiled.source_map)
    say(demo, output, "Use the third dilution for the second print group instead.")
    assert intents(demo) == [{"source": "dilution:1", "count": 3}, {"source": "dilution:3", "count": 1},
                             {"source": "dilution:3", "count": 2}]                  # only group 2 changed
    assert compiled_counts(demo)[0] == {"A11": 3, "C11": 3}


# ── TEST 8: questions in the middle change nothing ──────────────────────────────────────────────────────────────────

def test_08_questions_mid_edit(tmp_path):
    demo, model, output = session(tmp_path, {
        "Print A11 three times and B11 once.": change(put({"source": "A11", "count": 3}, {"source": "B11", "count": 1})),
        "How many total spots is that?": {"kind": "question", "response": "4 spots: A11 three times and B11 once."},
        "Which one is printed more?": {"kind": "question", "response": "A11."},
        "Actually make B11 twice.": change(per("B11", {"count": 2})),
    })
    say(demo, output, "Print A11 three times and B11 once.")
    state = (demo.state.revision, demo.state.fingerprint())
    said = say(demo, output, "How many total spots is that?", "Which one is printed more?")
    assert (demo.state.revision, demo.state.fingerprint()) == state and "4 spots" in said
    assert "4 paper positions" in model.prompts[-1]                                 # the totals it answered from
    say(demo, output, "Actually make B11 twice.")
    assert intents(demo) == [{"source": "A11", "count": 3}, {"source": "B11", "count": 2}]


# ── TEST 9: a print request on a dilution that does not exist yet stays editable ─────────────────────────────────────

def test_09_unresolved_source_is_editable_then_resolves(tmp_path):
    config = load_config(DEFAULT_CONFIG)
    config["dilution"]["factors"] = [2, 4]
    demo, _, output = session(tmp_path, {
        "Print dilution 3 three times.": change(put({"source": "dilution:3", "count": 3})),
        "Make dilution 3 a 5x dilution.": change({"op": "append", "path": "dilution.factors", "value": [5]}),
    }, config=config)
    say(demo, output, "Print dilution 3 three times.")
    assert intents(demo) == [{"source": "dilution:3", "count": 3}]                  # kept, though it cannot run yet
    assert any("dilution 3 does not exist" in reason for reason in demo.run_block_reasons())
    say(demo, output, "Make dilution 3 a 5x dilution.")
    assert compiled_counts(demo)[0] == {"C11": 3} and demo.run_block_reasons() == []


# ── TEST 10: a genuinely ambiguous follow-up is one question ────────────────────────────────────────────────────────

def test_10_ambiguous_follow_up_asks_once(tmp_path):
    demo, _, output = session(tmp_path, {
        DILUTE: DILUTE_REPLY,
        "Print dilution 1 three times and dilution 2 twice.": change(put({"source": "dilution:1", "count": 3},
                                                                         {"source": "dilution:2", "count": 2})),
        "Make the second one 4.": {"kind": "clarification", "clarification": "Do you mean 4 prints of dilution 2, 4 "
                                                                             "drops on its prints, or something else?"},
    }, config=load_config(DEFAULT_CONFIG))
    say(demo, output, DILUTE, "Print dilution 1 three times and dilution 2 twice.")
    before = (demo.state.revision, demo.state.fingerprint())
    said = say(demo, output, "Make the second one 4.")
    assert "4 prints of dilution 2, 4 drops" in said and demo.clarifying is not None
    assert (demo.state.revision, demo.state.fingerprint()) == before and demo.pending is None


# ── TEST 11: plan-wide default plus a per-source override, then a newer plan-wide command ────────────────────────────

def test_11_global_default_override_then_global_again(tmp_path):
    demo, _, output = session(tmp_path, {
        "Print all three once.": change(put("A11", "B11", "C11"), {"path": "print.replicates", "value": 1}),
        "Make A11 three times.": change(per("A11", {"count": 3})),
        "Now make all of them twice.": change({"path": "print.replicates", "value": 2}),
        "All three times, but keep C11 at once.": change({"path": "print.replicates", "value": 3},
                                                          per("C11", {"count": 1})),
    })
    say(demo, output, "Print all three once.", "Make A11 three times.")
    assert compiled_counts(demo)[0] == {"A11": 3, "B11": 1, "C11": 1}
    say(demo, output, "Now make all of them twice.")                               # the newest plan-wide command wins
    assert intents(demo) == ["A11", "B11", "C11"] and compiled_counts(demo)[0] == {"A11": 2, "B11": 2, "C11": 2}
    say(demo, output, "All three times, but keep C11 at once.")                    # an exception said in the same breath
    assert compiled_counts(demo)[0] == {"A11": 3, "B11": 3, "C11": 1}


# ── TEST 12: exact positions as an escape hatch, and out of it again ────────────────────────────────────────────────

def test_12_exact_positions_then_back_to_a_count(tmp_path):
    demo, _, output = session(tmp_path, {
        "Put A11 exactly at A1, A3 and A5. Put B11 at B2.": change(put({"source": "A11", "positions": ["A1", "A3", "A5"]},
                                                                       {"source": "B11", "positions": ["B2"]})),
        "Actually I don't care where A11 goes anymore; just print it four times.": change(per("A11", {"count": 4})),
    })
    say(demo, output, "Put A11 exactly at A1, A3 and A5. Put B11 at B2.")
    assert compiled_counts(demo)[1].source_map == [{"source": "A11", "positions": ["A1", "A3", "A5"]},
                                                   {"source": "B11", "positions": ["B2"]}]
    say(demo, output, "Actually I don't care where A11 goes anymore; just print it four times.")
    assert intents(demo) == [{"source": "A11", "count": 4}, {"source": "B11", "positions": ["B2"]}]
    counts, compiled = compiled_counts(demo)
    assert counts == {"A11": 4, "B11": 1} and compiled.source_map[1] == {"source": "B11", "positions": ["B2"]}


# ── TEST 13: drops per spot are not prints ──────────────────────────────────────────────────────────────────────────

def test_13_drops_per_source_are_not_prints(tmp_path):
    demo, _, output = session(tmp_path, {
        "Print A11 three times and B11 once.": change(put({"source": "A11", "count": 3}, {"source": "B11", "count": 1})),
        "Make A11 two drops per spot and B11 four drops per spot.": change(per("A11", {"drops": 2}),
                                                                           per("B11", {"drops": 4})),
        "Make all of them two drops each.": change({"path": "print.droplets_per_spot", "value": 2}),
        "But make A11 four drops.": change(per("A11", {"drops": 4})),
    })
    say(demo, output, "Print A11 three times and B11 once.",
        "Make A11 two drops per spot and B11 four drops per spot.")
    counts, compiled = compiled_counts(demo)
    assert counts == {"A11": 3, "B11": 1}                                           # drops did not add prints
    assert [(op.source, op.droplets) for op in compiled.plan.operations if op.kind == "print"] == \
        [("A11", 2), ("A11", 2), ("A11", 2), ("B11", 4)]
    say(demo, output, "Make all of them two drops each.", "But make A11 four drops.")
    assert [(op.source, op.droplets) for op in demo.state.compile().plan.operations if op.kind == "print"] == \
        [("A11", 4), ("A11", 4), ("A11", 4), ("B11", 2)]


# ── TEST 14: a dye/water percentage mixture is a dilution, said in its own terms ────────────────────────────────────

def test_14_percentage_mixture_versus_fold_dilutions(tmp_path):
    assert normalize_factors(["10%"]) == [10] and normalize_factors(["25%", "100%"]) == [4, 1]
    demo, _, output = session(tmp_path, {
        "Make a 10% dye / 90% water mix.": change({"path": "dilution.factors", "value": ["10%"]},
                                                  {"path": "dilution.enabled", "value": True}),
        "Make 2x and 5x dilutions.": change({"path": "dilution.factors", "value": [2, 5]}),
    }, config=load_config(DEFAULT_CONFIG))
    say(demo, output, "Make a 10% dye / 90% water mix.")
    assert demo.state.config["dilution"]["factors"] == [10]
    well = demo.state.compile().plan.wells[0]
    assert (well.sample_ul, well.solvent_ul) == (15.0, 135.0)                     # 10 % dye, 90 % water
    say(demo, output, "Make 2x and 5x dilutions.")
    assert demo.state.config["dilution"]["factors"] == [2, 5]


# ── the rigid-loop symptoms, directly against the patch engine ──────────────────────────────────────────────────────

def test_no_rigid_loop_in_the_patch_engine():
    spec = samples()
    spec["print"]["source"] = [{"source": "A11", "positions": ["A1"]}, {"source": "B11", "count": 2}]
    # a plan-wide replicate count never conflicts with exact positions, and never touches them
    result = apply_operations(spec, [PlanOperation("set", "print.replicates", 3)])
    assert result.failed == [] and result.after["print"]["source"] == [{"source": "A11", "positions": ["A1"]}, "B11"]
    # a source not printing yet is added by a one-source edit; an unknown group is a clear local failure
    result = apply_operations(spec, [PlanOperation("set", "print.source", {"count": 2}, params={"source": "C11"}),
                                     PlanOperation("set", "print.source", {"count": 2}, params={"group": 9})])
    assert result.after["print"]["source"][-1] == {"source": "C11", "count": 2}
    assert result.failed and "no group 9" in result.failed[0][1]
    # two groups at one exact position is the one hard invariant (one sample per paper position)
    result = apply_operations(spec, [PlanOperation("set", "print.source", {"positions": ["A1"]}, params={"source": "B11"})])
    assert result.failed and "A1" in result.failed[0][1]


# ── TEST 15: unequal per-source counts with a dilution reference, in the Opentrons simulator ─────────────────────────

_DISPENSE = re.compile(r"Dispensing\s+[\d.]+\s+uL\s+into\s+([A-H]\d{1,2})\s+of\s+.+?\s+on\s+(?:slot\s+)?(\d+)", re.I)
_ASPIRATE = re.compile(r"Aspirating\s+([\d.]+)\s+uL\s+from\s+([A-H]\d{1,2})\s+of\s+.+?\s+on\s+(?:slot\s+)?(\d+)", re.I)


@pytest.mark.skipif(not PINNED_SIMULATOR.exists(), reason="pinned Opentrons 2.15 simulator not installed")
def test_15_unequal_counts_compile_and_run_in_the_simulator(tmp_path):
    spec = load_config(DEFAULT_CONFIG)
    spec = apply_operations(spec, [
        PlanOperation("set", "dilution.factors", [2, 4, 8]), PlanOperation("set", "dilution.plate_column", "10"),
        PlanOperation("set", "tips.policy", "per_liquid"),
        PlanOperation("set", "print.source", [{"source": "dilution:1", "count": 3},
                                              {"source": "dilution:3", "count": 1, "drops": 2},
                                              {"source": "dilution:2", "positions": ["H11", "H12"]}])]).after
    compiled = compile_plan(spec)
    assert compiled.ok, compiled.errors
    assert compiled.source_map == [{"source": "A10", "positions": ["A1", "A2", "A3"]},
                                   {"source": "C10", "positions": ["C1"], "drops": {"C1": 2}},
                                   {"source": "B10", "positions": ["H11", "H12"]}]
    config = deepcopy(compiled.config)
    run_modes = config.pop("run_modes", {})
    run_modes["dry_run"] = False
    config["protocol_version"] = 19
    path = tmp_path / "unequal_counts.py"                                          # never src/protocols/generated
    builder = _builder()
    path.write_text(builder.build_source(BASE_PROTOCOL.read_text(encoding="utf-8"), config, run_modes),
                    encoding="utf-8")
    ok, output = builder.simulate(path, str(PINNED_SIMULATOR))
    assert ok, output[-1500:]
    paper, plate = str(config["deck"]["paper"]["slot"]), str(config["deck"]["plate"]["slot"])
    drops = [m.group(1) for m in map(_DISPENSE.search, output.splitlines()) if m and m.group(2) == paper]
    assert drops == ["A1", "A2", "A3", "C1", "C1", "H11", "H12"]
    sources = [m.group(2) for m in map(_ASPIRATE.search, output.splitlines())
               if m and m.group(3) == plate and abs(float(m.group(1)) - 5.0) < 1e-6]
    assert sources == ["A10", "A10", "A10", "C10", "C10", "B10", "B10"]


# ── an older dictated print map migrates into intents instead of trapping the plan ──────────────────────────────────

def test_a_dictated_map_becomes_intents_when_one_source_is_edited():
    spec = samples()
    spec["print"]["source_map"] = [{"source": "A11", "positions": ["A1", "A3"]}, {"source": "B11", "positions": {"B2": 3}}]
    result = apply_operations(spec, [PlanOperation("set", "print.source", {"count": 4}, params={"source": "A11"})])
    assert result.failed == [] and result.after["print"].get("source_map") is None
    assert result.after["print"]["source"] == [{"source": "A11", "count": 4}, {"source": "B11", "positions": ["B2"]}]
    assert compile_plan(result.after).source_map == [{"source": "A11", "positions": ["A1", "A2", "A3", "A4"]},
                                                     {"source": "B11", "positions": ["B2"], "drops": {"B2": 3}}]
