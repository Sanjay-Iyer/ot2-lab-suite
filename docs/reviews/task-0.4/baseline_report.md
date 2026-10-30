# Task 0.4 — Baseline test report

HOME, conda `ai` (Python 3.11.15), 2026-10-30. Checkout under test: commit `0129290` plus this
task's test-only changes, copied to a disposable worktree by `run_baseline.py`. Network, shell, cloud
and robot-runner processes were blocked by reviewed Python guards. No model request, robot contact
or physical run occurred.

## Authoritative result

Run `.test_tmp/b04-985f12ba`: **2227 passed, 25 failed, 5 skipped** (483 s), with the pinned Opentrons 7.0.2 simulator and the page's three reference photos present. `protected_files` 8547, `protected_changed` empty, `git_status_changed` empty. Output is kept in `evidence/full_r2/`. The previous run of these tests (`b04-aaa3820f`) gave 2225 passed, 25 failed, 5 skipped; the two extra passes are new hygiene tests, and the only other difference is which flaky test failed.

Each failure has an owner task; none is fixed here, as the task requires. An earlier run on
the same day (`b04-a684f20b`: 2204 passed, 36 failed, 15 skipped) lacked the page's photos and the
pinned simulator, so it is superseded.

## Failure classification

The first column is the plan's category. "Pending WORK" and "Owner decision" are shown separately
because editing those tests would hide an open physical or operator decision.

| Plan category | n | Tests | Meaning | Owner |
|---|---|---|---|---|
| Existing defect | 3 | `test_ai_dye_demo_user_test_sops.py` drop-count cases | Note 1 | 1.5 |
| Existing defect | 1 | `test_ai_dye_demo_liquid_handling.py::test_drops_at_named_positions...` | Note 2 | 1.3 |
| Existing defect | 1 | `test_robot_connection.py::test_no_hardcoded_link_local_addresses...` | 10 hard-coded `169.254.x.x` matches in tracked non-test files, for example `configs/experiments/18_dye_column11_5x_to_100x.yaml` | 8.2 |
| Environment-dependent | 8 | `test_robot_automation.py` | The runner sets `LLM_PROVIDER=api-key`, as on HOME, so the live auth gate fires before the mocked path. With `--work-auth-fixture` the file gives 9 passed. The gate is correct; the tests set no auth of their own. | 2.4 |
| Flaky | 1 | `test_ai_dye_demo_gui.py::test_discard_leaves_authoritative_state_unchanged` in this run; `test_the_page_opens_its_run_dialog...` in others | Note 3 | 1.6 |
| Outdated expectation | 1 | `test_ai_dye_demo_robot_abort.py::test_an_aborted_live_run...` | Note 4 | 1.6 |
| Outdated expectation | 1 | `test_printing_demo_config.py::test_overlap_validation...` | Fixture wells are invalid for the default plate since the 12-well change, so another validation error fires first | 8.1 |
| Outdated expectation | 1 | `test_ai_dye_demo_novice_validation.py::test_decision_routes_carry_no_changes` | Conflicts with `test_parser_keeps_structured_changes_when_route_label_is_wrong` on the legacy parser | 1.5 |
| Outdated expectation, pending WORK | 6 | `test_experiment_01_config_ground_truth.py` (3), `test_manual_printing_workflows.py` (2), `test_standard_printing_resolver.py::test_a_vial_source...` | Note 5. **Do not edit these tests to pass.** | 3.4, 4.8 |
| Outdated expectation | 1 | `test_generalized_printing_workflows.py::test_template_driven...` | Note 6 | 4.8 |
| Outdated expectation, owner decision | 1 | `test_conversation_regression[repeated_request_with_model_extra_001]` | Fails because of an intended exemption in `state.propose`. The operator owns it; not "fixed". | 1.5 |
| Missing simulator dependency | 0 | None | All pinned-simulator tests ran | |
| Failure in the current semantic pipeline | 0 | None | See the coverage warning | 1.1, 1.5 |

### Notes

1. **Drop-count defect.** The legacy reader turns "Print 1 drop in paper columns 1 and 2 and 3 drops
   in paper columns 4 and 5" into a plan with `Drops per position 1` and `Total drops 16` (80 uL), with
   no warning. The request means 32 drops (160 uL), so the 3-drop request silently vanishes.
   `cf48fcd` removed the old refusal. `SOP_04_multi_drop_multi_column.md:92` and
   `SOP_TESTER_ANSWER_KEY.md:324` still require a refusal. No test shows the semantic pipeline
   planning this request correctly.
2. **`drops_at` defect.** The docstring at `natural.py:563` says a position the plan does not print is
   "refused, never added", but `natural.py:599-603` adds it. `drops_at {"A5": 3}` yields a ninth print
   of 3 x 5 uL from A11 at A5, with no error. The test reports `DID NOT RAISE`. Only the semantic path
   blocks this (`plan.py:650`, "nothing prints there").
3. **Flaky page tests.** In the 5 runs with the photos present, the run-dialog test failed 3 times and the
   discard test failed 2 times. `app.py:549` opens the dialog only when the session is idle, while the
   test waits for the chat line (`session.py:3075`) and then ticks once. A later tick on a real page
   opens it, so this looks like a test race, not a proven cause. The discard test fails with "timed
   out waiting for GUI adapter"; its cause is not yet investigated.
4. **Abort test.** It compares the whole physical record with `{"dilutions_prepared": None}`, but the
   record gained `plate_id`, `paper_id` and `tip_rack_id`. **The failing assertion stops the test's
   later checks from running.** By reading code and an in-memory check, only a live exit 0 books physical
   state (`state.py:1806`), so the logic looks sound. Aborted records still list planned `tips_used` and
   `printed_positions` (`state.py:1797-1798`), and the post-abort hold is one operator "yes", in memory only.
5. **Paper height and clover engine.** The machine profile moved paper height from 0.5 to 0.8, then
   1.1 mm, on 2026-08-31 (`57e9c94`, `350ef02`). Its comment says 0.5 mm was last physically confirmed and
   1.1 mm revalidation is pending. An uncapped comparison (`experiment_01_uncapped_diff.txt`) shows all 64
   Experiment 01 physical differences are `destination.z_mm: static=0.5 config=1.1`. `test_clover_engine_is_frozen...`
   has a different cause: divergence began at `c0e7a54`. Besides `source_well`, the engine relaxed its
   paper-slot check (5 to 1-11), moved tip pick-up, and added `pipette_tip_reuse`. `8f2203b` changed only its `CONFIG`.
6. **Template test.** Not a paper-height failure. Clover heights remain 0.5 mm. The only fingerprint
   difference is source well A2 versus A1 (`e6d2281`), with the paper slot change in `0a2cf84`.

## Unresolved values with passing tests

Passing tests can pin conflicting values. These stay open for Task 3.4 and WORK, and none is declared correct:

- Plate aspiration: machine profile 0.2 mm (`ot2_standard_printing_p20_v1.yaml:61`), demo 0.3 mm
  (`ai_agent_dilution_print_demo.yaml:92`).
- Plate dispense: demo 0.3 mm above bottom, machine profile top -2 mm.

## Skips

5 skips, all `tests/test_workflows/test_printing.py`: "Needs update for new decoupled constraint schema".
This is a legacy schema suite (owner: 8.1).

The two legacy build/validate pipeline tests in `test_vial_print_agent.py` now skip outside a marked
disposable checkout, because they rewrite tracked files. **Plain `pytest` therefore does not run them.**
`run_baseline.py` does, under `ai` (HOME) or `llm` (WORK).

## Coverage warning

Passing is not the same as covered. Most conversation tests and the whole red team use the older reader,
not the semantic pipeline both launchers use with a model. "0 semantic failures" means only that the
existing semantic tests pass. The approval defect had no failing test because no test covered it.

## Guard behavior

Each guarded process now records what it blocked, with the running test
(`evidence/full_r2/summary.json`, `tests_with_guard_blocks`). A blocked call raises `OSError`, and
application code that catches it continues, for example `arp_entries` and `_run_text`. **So a passing
test listed there exercised its no-network branch, not a successful robot or network call.** These are
Python-level controls, not OS egress enforcement.

Plain `pytest` is not guarded. On the WORK laptop, use `run_baseline.py`.

## Limits and WORK-only checks

Results apply to HOME's `ai` environment. Raman's separate suite is not part of root pytest and was not
run. Fake OT-2 and simulator results are software evidence, not physical validation.

**WORK VALIDATION PENDING:**

- Paper release height (0.5 or 1.1 mm).
- Plate aspiration and dispense heights.
- Clover engine: modified `02` versus validated `12`.
- Real stop latency, and tip and plate state after a stop.
- This baseline under the WORK `llm` environment.
