# Skipped-dilution investigation — Demo 1 (2026-09-03)

## Short answer

The 2026-09-03 Demo 1 session logs are not on this laptop. `runs/` is gitignored,
so `runs\ai_dye_demo\20260903_*\session.log` exists only on the lab laptop. This
investigation is based on the code that ran that day (`scripts/ai_dye_demo.py` as of
2026-09-01 and protocol v19) and the nine 2026-09-01 test sessions that are on this
laptop. None of those nine sessions skipped a step, so what happened on 09/03 cannot be
proven from here.

The old code did have four ways to produce exactly the reported symptom: a requested
dilution step silently skipped, then dilution steps reappearing after more planning.

| # | Mechanism | Effect |
|---|---|---|
| 1 | `dilution.enabled` was an ordinary LLM-editable field. The prompt had a rule to turn it **off** ("the dilutions are already made"), but no rule to turn it back **on**. | A phrase like "print from the existing dilutions" or "skip making them" could switch the step off. A later "make three dilutions" could change the factors and leave the step off. |
| 2 | Edits were applied the moment the LLM replied, with no confirmation and no list of what changed. The plan still printed the full dye/water table, with only `[SKIPPED: already prepared]` added to the end of the header line. | A skipped step looked almost identical to a real one. |
| 3 | Every start of the script loaded the default YAML again: 8 dilutions, 1–16×, 150 µL, **enabled**. The SOP told the operator to restart the script for Run 2. | After a restart, dilutions "appeared again", and any factor or volume not restated went back to the default. |
| 4 | The LLM returned a nested partial YAML that was deep-merged, and lists such as `factors` were replaced wholesale. | An LLM reply that regenerated the dilution block replaced approved values without anyone being shown the difference. |

## Audit, item by item

| Question | Possible in the old code? | How |
|---|---|---|
| **Removed** | Yes | A reply containing `dilution: {enabled: false}` removed the step from the run. |
| **Skipped** | Yes | Same as above, plus `print.enabled` symmetrically. The explanation sentence did not have to mention the change. |
| **Overwritten** | Yes | `merge_user_updates` replaces lists, so `dilution.factors` from any reply replaced the approved series. |
| **Duplicated** | Not in motion | Each enabled step runs once. The old tip allocation still reserved two dilution tips when dilution was skipped (Guide Run 2 skipped D1/E1). That looked like dilution activity. |
| **Reintroduced from an older state** | Across sessions, yes | Every session restarted from the default YAML, so an older default came back. Within one session, no: the LLM saw only the current YAML and had no chat history. |
| **Regenerated inconsistently** | Yes | Different wording of the same request made the LLM return different subsets of fields, and nothing flagged which ones. |
| **Printing after dilution** | Printing could run from empty wells | With dilution off, printing assumed the wells were filled. The only trace was a protocol comment, and there was no operator confirmation. |
| **Changing unrelated parameters after a dilution plan exists** | Yes | Any request could carry dilution keys; nothing compared the reply with what was asked. |

Not a cause: the run-mode flags. `run_vial_print_robot.py` bakes `do_dilution = not --no-dilution`
into the generated protocol, and the demo never passed `--no-dilution`. The builder used
the YAML's `run_modes: {do_dilution: true}`.

## What changed

- **One authoritative state.** `src/agents/dye_demo/state.py::ExperimentState` holds the
  only copy of the configuration. It has a revision number and a history of every applied
  change, with the operator, time, request, and before/after values.
- **Explicit field changes, never a whole configuration.** The LLM proposes
  `{path, value, evidence}` items for an allowlist of fields.
  - Values that are already set are dropped, so they cannot appear as changes.
  - A change whose evidence is not in the operator's words is shown under
    **CHECK THESE - I could not find them in what you typed**.
- **Nothing is applied without an explicit `yes`.** A proposal that turns a step off shows
  the resulting plan with that section headed `SKIPPED`, and its ATTENTION block says what
  the next run no longer does (for a print-only plan, the warning that the run assumes the
  wells already hold the dilutions).
- **Stale proposals are refused.** A proposal is built against a revision.
  `apply()` refuses it if the state has moved on (`StaleProposal`), so an older
  LLM-generated plan can never overwrite newer approved values.
- **A skipped step looks skipped.** The plan prints
  `DILUTIONS ... SKIPPED - already in the plate` with the wells assumed to already hold
  dilutions; `steps` shows no FROM/TO lines for that step, and no tips are allocated to it.
- **Print-only live runs ask first.** "Do plate wells A11-C11 already hold the
  dilutions? (yes/no)"
- **The session continues after a run.** After a live run, the next unused tip is
  proposed rather than silently set, and the operator is told which wells now hold
  dilutions. Run 2 of the SOP can therefore happen in the same session instead of a
  restart from the default YAML.
- **Protocol v19 allocates tips in order,** only for operations that actually run.

## Tests covering this

- `tests/test_ai_dye_demo.py::test_a_skipped_dilution_stays_skipped_until_explicitly_re_enabled`
- `tests/test_ai_dye_demo.py::test_an_older_proposal_cannot_overwrite_newer_approved_values`
- `tests/test_ai_dye_demo.py::test_moving_one_labware_changes_exactly_one_value`
- `tests/test_ai_dye_demo.py::test_values_that_are_already_set_are_not_reported_as_changes`
- `tests/test_ai_dye_demo.py::test_changes_the_request_does_not_support_are_flagged`
- `tests/test_ai_dye_demo_session.py::test_resent_unchanged_values_are_dropped_and_the_dilution_plan_survives`
- `tests/test_ai_dye_demo_session.py::test_an_older_plan_trying_to_come_back_is_shown_and_not_applied_without_yes`
- `tests/test_ai_dye_demo_session.py::test_a_live_print_only_run_asks_that_the_dilutions_already_exist`
- `tests/test_ai_dye_demo_protocol.py::test_off_deck_vial_rack_is_not_loaded_for_a_print_only_run` (a skipped step takes no tips)

## Confirming what happened on 09/03

On the lab laptop, from the repository root:

```powershell
Get-ChildItem runs\ai_dye_demo -Directory -Filter 20260903_* | Select-Object Name
Select-String -Path runs\ai_dye_demo\20260903_*\session.log -Pattern '"enabled"'
Select-String -Path runs\ai_dye_demo\20260903_*\executed_config.yaml -Pattern 'enabled'
```

What each result means:

- **`"enabled": false` in a `config_updated` event:** mechanism 1. The line's `text` field
  shows the wording that caused it.
- **Several `20260903_*` folders:** each restart went back to the default YAML
  (mechanism 3).
- **An `executed_config.yaml` with `enabled: false` under `dilution`:** a run that skipped
  dilution.

Copy those folders to this laptop to have them checked line by line. They are not in git.
