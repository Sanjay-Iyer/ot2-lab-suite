# AI dye demo — Demo 1 follow-up (user interaction and robustness)

Changes made after 2026-09-03 Demo 1 with Stephen to `scripts/ai_dye_demo.py`, its
conversation package `src/agents/dye_demo/`, and protocol v19.

## Documents in this folder

| Document | Covers |
|---|---|
| [conversational_intent_model.md](conversational_intent_model.md) | Question, action, hypothetical, proposal, confirmation, physical-state reconciliation, cancellation, ambiguity |
| [red_team_strategy.md](red_team_strategy.md) | Personas, attack goals, long conversations, deterministic invariants, failure-to-regression workflow |
| [known_limitations.md](known_limitations.md) | Conversational cases that are unsupported or always clarified, and deliberate design choices |
| [robustness_report.md](robustness_report.md) | Red-team results: campaigns, failures found and fixed, regression cases, simulations |
| [skipped_dilution_investigation.md](skipped_dilution_investigation.md) | Why a dilution step could be skipped and reappear, and what fixes it |
| [liquid_handling_parameters.md](liquid_handling_parameters.md) | Every aspirate, dispense, mix, air gap, blow-out and tip setting, with provenance |
| [robustness_audit.md](robustness_audit.md) | Likely LLM/OT-2 failure modes and the deterministic safeguard for each |
| [visualization_options.md](visualization_options.md) | ASCII vs Rich vs GUI vs generated figures: recommendation only |
| [memory_skills_architecture.md](memory_skills_architecture.md) | Users, memory, skills, session logs and experiment history: design only |
| [experiment_combinatorics.md](experiment_combinatorics.md) | Standalone: why every experiment cannot be hand-coded |
| [user_testing/](user_testing/README.md) | Five progressive user-test SOPs, the User Guide, the administrator answer key and expected states |

Operator documents: [../basic_ai_dye_demo.md](../basic_ai_dye_demo.md),
[../GUIDE_AI_Dye_Three_Dilutions_Paper_Print.md](../GUIDE_AI_Dye_Three_Dilutions_Paper_Print.md),
[../SOP_AI_Dye_Three_Dilutions_Paper_Print.md](../SOP_AI_Dye_Three_Dilutions_Paper_Print.md).

## What changed, by request

| # | Request | Where |
|---|---|---|
| 1 | LLM initialised with a READY check before anything else | `llm.py::startup_check`, `session.py::DemoSession.run` |
| 2 | "Who is running this experiment?"; operator on every log record, config and robot run log | `session.py`, `scripts/ai_dye_demo.py --operator/--session-label`, protocol comment |
| 3 | `/ask` mode: answers only, state fingerprint unchanged | `session.py::_ask`, `llm.py::ask`, `render.py::render_ask` |
| 4 | Explicit FROM / TO for every print step and dilution transfer (the `steps` command) | `render.py::render_print_step`, `render_dilution_step` |
| 5 | The resulting deck on every proposal, physical moves under ATTENTION; ACTIVE deck on `deck` | `render.py::render_proposal`, `render_deck` |
| 6 | Ambiguous wording confirmed before interpretation | `language.py::find_ambiguities` |
| 7 | Tip start, required and left in every plan; the `tips` command lists rack, policy, available, next, remaining | `plan.py`, `render.py::_pipetting`, `render_tip_configuration`, `tips.policy` |
| 8 | Occupied-slot conflicts explained; OFF DECK supported; nothing auto-relocated | `validation.py::deck_conflicts`, `render.py::render_conflict`, protocol v19 |
| 9 | One authoritative state, revisions, stale proposals refused | `state.py::ExperimentState` |
| 10 | Blow-out after dilution dispenses; no sub-minimum transfers; print cycle unchanged | protocol v19, `plan.py::split_volume` |
| 15 | Request -> interpretation -> validation -> proposal -> yes -> update -> summary | `session.py` |
| 16 | Unchanged values dropped, unsupported changes flagged, lab-owned values locked | `state.py::ExperimentState.propose` |

## Human-language robustness and red-team testing

The second round of work makes the agent tolerate people talking like people, and tests it with simulated
conversations. Language interpretation may be probabilistic; state mutation is deterministic, validated,
explicit and auditable.

| Area | What changed | Where |
|---|---|---|
| Intent before interpretation | every turn is classified without a model (question, hypothetical, quote, paste, negation, physical report, future plan, bypass attempt, instruction, ...); only actionable text is interpreted | `intent.py::analyze_turn` |
| Natural questions | answered without `/ask`; state fingerprint checked unchanged; arithmetic, history and approval questions answered deterministically; answers that claim a change get a correction | `session.py::_answer`, `history.py` |
| Approval | only explicit words approve; numbered proposals; superseded and stale proposals can never apply; partial approval builds a subset proposal; bypass attempts keep the proposal waiting | `session.py::_with_pending`, `intent.py::parse_selection` |
| Values | units required and converted; wells normalized (`A01` = `A1`, `A10` stays `A10`); slots written as slots with the labware named; corrected-away values refused; fields never mentioned flagged; stale claims refused; relative math computed by code | `model.py`, `state.py::_verify` |
| Ambiguity | pronouns, bare numbers, vague quantities, `plate`/`rack`, `vial 8`, `dilution 8`, double negatives, `go back`, `start over` are clarified | `intent.py`, `language.py::find_ambiguities` |
| Physical state | reports become reconciliation proposals; prepared-dilution records drive skip-step prerequisites; an unrecorded report blocks runs | `session.py::_reconcile`, `state.py::_check_prerequisites` |
| Runs | never from `start over`, `go back`, `start printing`, or a go-ahead right after a hypothetical | `language.py::wants_to_run`, `session.py::_dispatch` |
| Audit | `turns.jsonl`: per-turn classification, state fingerprint and revision before/after, events | `session.py::_handle` |
| Red team | 13 personas, 20 attack goals, 41 message generators, a corrupting interpreter (12 corruption modes) and Gemini, a Gemini user agent and judge, 24 deterministic invariants, replay, minimization, regression export | `scripts/ai_dye_demo_redteam.py`, `src/agents/dye_demo/redteam/` |

## User-test SOPs

[user_testing/](user_testing/README.md) holds five SOPs of increasing difficulty that give a tester an experimental
objective instead of commands, a User Guide, and an administrator answer key. `scripts/check_ai_dye_demo_sop.py`
scores a session against the SOP's expected state (`src/agents/dye_demo/sop_check.py`).

Writing and validating the SOPs exposed gaps in the conversation layer, fixed with regression tests in
`tests/test_ai_dye_demo_user_test_sops.py`:

| Gap | Fix | Where |
|---|---|---|
| "Don't print anything" answered "Understood - I won't do that" while the plan still printed; "No printing this run" was answered as chat | The agent says what the plan still does and how to change it ("say \"skip printing\"") | `session.py::_still_the_plan`, `intent.py::negated_step`, `step_off_request` |
| A row letter was not a value: "Start at row B." was called unfinished, and answering "B" dead-ended | `row B` counts as a value | `intent.py::_VALUE_TOKEN` |
| An answer to "What should the ... be?" after a request ending in a full stop was read as chat and lost | The answer joins the request's sentence | `session.py::_with_clarifying` |
| "Print in paper columns 3 and 4" was refused for not stating the replicate count | Listed side-by-side columns state the count; columns with a gap are explained as needing two runs | `state.py::listed_paper_columns` |
| "Print the 5x, 10x and 20x dilutions onto paper column 2" was refused even when those were the plan's dilutions | Naming every dilution of the plan is a print instruction; naming dilutions the plan lacks says so | `intent.py::_named_dilutions_reason` |
| "Actually, keep the plate where it is." discarded the whole waiting proposal and called the message unfinished | Removes only the plate change (a new, smaller proposal) | `intent.py::_KEEP_AS_IS` |
| "Each well now holds about 140 uL", "The dilutions from the first run are already made, each well has ... left", "The first column of tips is used, start at A2" were refused or answered as chat | Field hints, report wording and claim context recognise them | `state.py::_FIELD_HINTS`, `intent.py::_REPORT_DILUTIONS`, `_PREPARED_VOLUME_TAIL` |
| "Actually no, make it 200 uL instead" replacing a volume proposal was refused as not mentioning the volume | The replaced proposal shows which setting "it" is (never its value) | `session.py::_hint_context` |
| "I need 2x, 4x ..." and short statements such as "Sorry, I was wrong, the dye is in vial B1" were chat; "on the same spot" was read as a vague reference | Fold values are field words; statements of a setting and a value up to 12 words are instructions; "same spot" needs "as before" to be a reference | `intent.py::_FIELD_WORD`, `analyze_turn`, `_SAME_AS_BEFORE` |
| "Make the dilutions in slot 3" did not hint at columns | The refusal suggests "plate column 3" or "paper column 3" | `state.py::_verify` |
| The deck-conflict example named unrelated labware, and "or OFF DECK" was listed for labware the run needs | The example names the labware involved; OFF DECK is offered only when no step needs it | `render.py::render_conflict`, `validation.py::off_deck_blocker` |
| "Call the dye crystal violet" asked whether crystal violet is the dye and then rewrote the name away | Naming a material skips that question | `language.py::_NAMING` |
| "Print 1 drop in paper columns 1 and 2 and 3 drops in columns 4 and 5" was not recognised as needing two runs; "serial dilution" gave no way forward | Plural and word forms are recognised; the serial-dilution reply suggests giving fold factors | `intent.py::_UNSUPPORTED` |

## Intent-to-plan fixes (2026-09-14 review)

A code review found requests whose words and the executed plan could disagree without a warning, and a live-run abort
that did not stop the robot. Each fix has regression conversations in `tests/test_ai_dye_demo_intent_to_plan.py`,
`tests/test_ai_dye_demo_robot_abort.py` and `tests/conversation_regressions/`.

| # | Gap | Fix | Where |
|---|---|---|---|
| 1 | "Print in paper columns 3 and 4" answered with only `replicates: 2` was a verified proposal printing columns 1-2 (3-6 with two drop volumes; SOP 4 run 2 could reprint run 1's columns) | The paper columns a request names (a block, a first column, "only", "not") are compared with the columns the resulting plan prints, before "already set". A difference blocks the proposal and asks; when the words fix the layout, the question offers it and a yes makes it a proposal. `run` is refused while the question waits. Every plan and proposal shows the `Paper columns` it prints | `columns.py::column_conflict`, `state.py::propose`, `session.py::_ask_paper_columns`, `render.py::_printing` |
| 2 | "Ctrl-C now if the deck does not match" after the run started only killed the laptop scripts; the robot kept running and the run was never recorded | Once a run exists, the runner sends the OT-2 a `stop` action for it, waits for a finished state, reports whether the stop was confirmed and exits 130 (4 if the run had not started); `--status-file` reports it. The session keeps relaying output, never dies on the interrupt, records the run as `aborted`, books nothing as done, and asks that the robot was checked before the next live run. **Tested offline only** | `scripts/run_vial_print_robot.py::_abort_after_interrupt`, `session.py::SubprocessExecutor`, `_run`, `_after_incomplete_live_run` |
| 3 | The answer "Columns 3 and 4." to "Which side-by-side paper columns?" was appended to the refused request and refused again | A short column answer replaces the refused columns in the request | `session.py::_answer_columns`, `columns.py::rewrite_paper_columns` |
| 4 | "Don't move the paper print plate." got "this plan still prints ... say skip printing"; any "print" could satisfy a print-step change | A negated step must negate printing or making dilutions itself; a model change that switches a step on or off is refused unless the words ask for it in that direction ("the paper print plate" never counts) | `intent.py::negated_step`, `state.py::_verify_step` |
| 5 | "Once the dilutions are made, print them ..." and "Let me know when the dilutions are ready." were reports that the dilutions exist (skip-dilution proposal, runs blocked after "no") | Temporal clauses (once, after, as soon as, when, until, before, by the time; not past tense or "already") are the order of the run, "let me know when ..." is a question, "make sure ... are ..." is a check | `language.py::TEMPORAL_FUTURE`, `intent.py::analyze_turn`, `session.py::_step_order_answer` |
| 6 | "Print the 5x, 10x and 20x dilutions." with printing off answered "already prints" | Naming every dilution while printing is off proposes printing on (without the model when nothing else is asked) | `intent.py::_named_dilutions_reason`, `session.py::_request` |
| 7 | Rejecting the only change of a waiting proposal ("Actually, keep the plate where it is.") answered "I could not match that" | The proposal is discarded and the reply says what stays | `session.py::_partial` |
| 8 | Two labware moved into one empty slot were offered OFF DECK although the plan needs them ("the that labware") | OFF DECK is offered only for labware the requested plan can run without, and each required labware is named with its reason | `render.py::render_conflict` |

## One plan screen (2026-09-14)

The terminal shows what the experiment will do, not how the plan changed. The CURRENT PLAN and every proposal
are built by the same section builders in `render.py`: DILUTIONS, PRINTING, DECK, LIQUIDS, PIPETTING and
LAB-OWNED PARAMETERS, with resulting values only and arrays written `1× | 2× | 3×`. A proposal is titled
`PROPOSED PLAN #N` (never "current": it is not applied until yes) and is the complete plan that exists after yes;
after yes the CURRENT PLAN shows that same plan. Removed: old -> new lines, the PROPOSED CHANGES / NO CHANGES /
RESULTING PLAN sections, the before and after decks, and revision numbers (still in `history`, undo and the
logs). Anything that blocks execution or must be checked before yes - unverified changes, a step switched off,
the physical record a yes writes, moves still to make, validation warnings and errors, deck and paper-column
conflicts, run refusals - goes in an ATTENTION block under the plan. The robot runner prints the same layout
(`PLAN IN THE BUILT PROTOCOL`). Confirmation, validation, clarification and the run gates are unchanged.
`tests/test_ai_dye_demo_plan_rendering.py` pins the layout and checks the derived values against the protocol on
the fake OT-2.

## Tests

```powershell
conda activate ai
python -m pytest tests/test_ai_dye_demo.py tests/test_ai_dye_demo_session.py tests/test_ai_dye_demo_protocol.py tests/test_ai_dye_demo_intent.py tests/test_ai_dye_demo_robustness.py tests/test_ai_dye_demo_conversation_regressions.py tests/test_ai_dye_demo_redteam.py tests/test_ai_dye_demo_user_test_sops.py tests/test_ai_dye_demo_intent_to_plan.py tests/test_ai_dye_demo_robot_abort.py tests/test_ai_dye_demo_plan_rendering.py -q
python scripts/ai_dye_demo_redteam.py --conversations 300
```

- `test_ai_dye_demo.py`: guard rails, deck, tips, FROM/TO, language, parsing.
- `test_ai_dye_demo_session.py`: whole conversations with a scripted LLM and a fake executor.
- `test_ai_dye_demo_protocol.py`: the real protocol file run against a recording fake OT-2.
- `test_ai_dye_demo_intent.py`: the intent model, table by table.
- `test_ai_dye_demo_robustness.py`: the 37 minimum conversational robustness tests and more, each checked by the red-team invariants.
- `test_ai_dye_demo_conversation_regressions.py`: replays every minimal failing conversation in `tests/conversation_regressions/`.
- `test_ai_dye_demo_redteam.py`: the harness itself, including invariants that must catch deliberately broken behaviour.
- `test_ai_dye_demo_user_test_sops.py`: the user-test SOPs' expected states, scripted user paths and the usability fixes above.
- `test_ai_dye_demo_intent_to_plan.py`: the review's failure conversations (fixes 1 and 3-8), each checked in the reply
  and in the physical plan: print operations, and the paper wells protocol v19 dispenses into on the fake OT-2.
- `test_ai_dye_demo_robot_abort.py`: Ctrl-C during a live run (fix 2) against a recording fake robot server and a fake
  runner process: the stop action is sent, the outcome recorded, and the next live run held until the robot is checked.
- `test_ai_dye_demo_plan_rendering.py`: the one-plan screen: sections, no old -> new anywhere along the SOP user paths,
  every setting visible, derived values equal to the fake OT-2's motion, ATTENTION blocks, confirmation unchanged.
- `test_ai_dye_demo_gui.py`: queued chat through the real `DemoSession`, form proposals behind Apply/Discard,
  the shared structured plan model, validation and simulation-only execution.
