# Robustness audit — LLM-controlled dilution + printing

Each failure mode below says:

- what could physically go wrong;
- what deterministic safeguard exists **now**;
- what is still recommended.

The LLM is never the authority on whether an operation is valid. Checks live in
`src/agents/dye_demo/validation.py` (plan), `state.py` (changes), `language.py`
(wording), and the protocol's own pre-flight.

Legend: ✅ implemented (2026-09-10) · ◐ partly covered · ○ recommended, not built

Follow-up (2026-09-11): conversational robustness was then tested with simulated users and
deterministic invariants; see [conversational_intent_model.md](conversational_intent_model.md),
[red_team_strategy.md](red_team_strategy.md), [robustness_report.md](robustness_report.md) and
[known_limitations.md](known_limitations.md). Where this audit says a mismatched well or number is
"flagged", it is now refused with a question, and a model value that contradicts the typed one is
reported even when it equals the current value.

## Words and locations

| Failure mode | Example | Safeguard now | Further recommendation |
|---|---|---|---|
| **A1 vs A10** | Operator types "tip A10", model proposes A1 | ✅ Normalisers accept only exact names. A requested tip or vial that differs from the typed token (A1 vs A10) is flagged "you typed A10, not A1" and not applied without yes. | ○ Highlight the chosen well on an ASCII plate map |
| **Deck slot vs well or column number** | "move the plate to 11" | ✅ A bare number after a move asks "Did you mean OT-2 deck SLOT 11?". An unqualified "column 3" asks paper vs plate when the sentence does not say. A requested number not in the operator's words is flagged. | — |
| **Ambiguous nouns** | spot, place, position, tray, bottle, hole, "the plate" | ✅ Asked **before** the LLM sees the request. "No" changes nothing. "The plate" is resolved automatically only when a slot mentioned in the request names it. | ○ Remember per-user answers (memory design), still confirmed |
| **Question executed as an instruction** | "should we use 10 uL drops?" | ✅ `/ask` never changes state (enforced by a fingerprint check). An LLM `question` intent becomes an ask-mode answer. A question-phrased message that yields changes is flagged, and every change needs yes. | — |
| **Uncertainty read as approval** | "sure?", "I think so", "ok but…" | ✅ Only explicit yes/no words count (`parse_confirmation`). Hedged or question run phrases never start a run. | — |
| **Assuming instead of asking** | Model guesses the starting row | ✅ LLM `unclear` intent leads to a clarification question. Changes not supported by the request are shown under **CHECK THESE**. Dependent changes must state why. | — |
| **Inconsistent reagent naming** | "dye", "CV", "stain" for one liquid | ✅ One material per role (sample, solvent) with an optional display label. Names in summaries come from state, not model text. | ○ Per-user reagent synonyms (memory) |

## State and plan integrity

| Failure mode | Safeguard now | Further recommendation |
|---|---|---|
| **Outdated plan after a parameter change** | ✅ Nothing derived is stored: rows, tips, volumes and drops are recomputed from state for every summary and run. The runner rebuilds the protocol from the working YAML on every run. | — |
| **Old context overriding approved state** | ✅ The LLM gets no chat history, only the current revision's values. Proposals carry their base revision, and `apply()` refuses stale ones. | — |
| **Partial modification of a plan** | ✅ A proposal is atomic: all changes are validated together and applied together. A rejection leaves state untouched. | — |
| **Unrequested parameter regenerated** | ✅ Values already set are dropped. Unsupported changes are flagged under ATTENTION (CHECK THESE). The proposal is the complete resulting plan, with a Changes line naming exactly the settings it touches. | — |
| **Skipped steps** | ✅ Step switches only move through a confirmed change; the plan section is headed "SKIPPED" and the ATTENTION block says what the run no longer does. A skipped step shows no FROM/TO lines and takes no tips. See `skipped_dilution_investigation.md`. | — |
| **Duplicated steps** | ✅ Operations are generated deterministically. Tests assert protocol motion equals the plan. A duplicate paper position is an error. | — |
| **Changing a parameter without recalculating dependent steps** | ✅ Everything dependent is derived. Proposals show the resulting paper columns, print positions, drops, printed volume, liquid use and tips; `tests/test_ai_dye_demo_plan_rendering.py` checks them against the protocol on the fake OT-2. | — |
| **Drop count changes without volume recalculation** | ✅ Printed fluid and the liquid-depth check are recomputed from drops × volume × replicates. | — |
| **Accidentally modifying heights, drop release or blow-out** | ✅ Allowlist of editable fields. Lab-owned values are refused by name. Their fingerprint is checked at every apply and every run. At startup they are compared with the machine profile. | ○ Require a lab-owner sign-off file for profile changes |

## Liquid handling

| Failure mode | Safeguard now | Further recommendation |
|---|---|---|
| **Above pipette capacity** | ✅ Drop + 1.5 µL air gap ≤ 20 µL. Transfers are split at 20 µL. Mixing ≤ 20 µL. The protocol pre-flight repeats the checks. | — |
| **Below reliable minimum** | ✅ Dye and water per well ≥ 1 µL. Split remainders under 1 µL are merged with the previous transfer. | ○ Warn for 1–2 µL transfers (lower accuracy) |
| **Aspirating from an empty or shallow well** | ✅ Each dilution well's level is simulated through every mix and drop. Mixing or aspirating that would draw air is refused. For pre-made dilutions, the "volume now in each well" can be given. | — |
| **Insufficient source volume in vials** | ◐ The summary prints "load at least X mL" per vial: consumption plus the ~2.46 mL needed to cover the 4 mm aspirate height. | ○ Optional loaded-volume field per vial with a hard check and a pre-run confirmation |
| **Forgetting dead volume** | ◐ Geometry-based cover volume (vials) and depth checks (plate wells). | ○ Explicit dead volume per labware in the machine profile |
| **Forgetting dilution mixing** | ◐ Each well is mixed immediately before every print step. A dilute-only run now warns that its wells are not mixed. | ○ Lab decision: optional mix after preparing each dilution |
| **Incorrect dilution order** | ✅ Fixed in the protocol: all water, then dye dispensed on top, from above the liquid. The summary lists that order. | — |
| **Wrong serial-dilution dependency** | ✅ Not possible today: v19 makes independent dilutions from stock, so no well depends on another. | ○ If serial mode is added, build a dependency graph: a source well must be complete and mixed before use, checked in Python |
| **Printing from the wrong dilution** | ✅ Every print step shows FROM well and dilution name and TO position. One tip per dilution. Tests assert each drop's source matches the plan. | — |
| **Printing before dilution is complete** | ✅ One run always dilutes before printing. A live print-only run requires confirming the wells already hold the dilutions. | — |
| **Droplets left on the tip (dilution)** | ✅ Blow-out after every dilution dispense (the P20's default push-out is 0 µL). | — |
| **Droplets left on the tip (printing)** | ◐ The validated cycle is unchanged: air gap, 3 µL push-out, blow-out, 2 s dwell. The release height is 1.1 mm, pending physical revalidation against the confirmed 0.5 mm. | ○ Physical A/B test of 1.1 vs 0.5 mm, then a lab-owned decision |
| **Tip touching liquid, carrying it back to stock** | ✅ Dilution dispenses happen above the liquid. A warning is shown when a fill brings the liquid within 1 mm of the dispense height. | — |

## Tips

| Failure mode | Safeguard now | Further recommendation |
|---|---|---|
| **Running out of tips** | ✅ Exact tips needed from the operation list, checked against tips remaining from the starting tip. The summary shows available, next and remaining. A warning appears when fewer than 8 will remain. | — |
| **Invalid starting tip** | ✅ Only A1–H12 accepted, checked again in the protocol pre-flight. | — |
| **Reusing a contaminated tip** | ✅ A tip never touches a second liquid: one per liquid, or a new tip every transfer. Returned tips carry a warning. After a live run, the next unused tip is **proposed**. | ○ Track each physical rack's used positions across sessions (memory design) |

## Physical world vs software state

| Failure mode | Safeguard now | Further recommendation |
|---|---|---|
| **Software changed, labware not moved** | ✅ After a deck change: "Now physically move …". A live run asks "Is the physical deck arranged exactly as the ACTIVE DECK above?" and cancels on anything but yes. | ○ Camera check against a golden reference image of the loaded deck (designed earlier, not built) |
| **Labware moved, software not told** | ◐ The ACTIVE DECK is printed before every run. During a live run, Ctrl-C makes the runner send the OT-2 a stop action for the run and wait for the robot to report it stopped (tested against a fake robot server only); the run is recorded as aborted and the next live run waits for the operator to confirm the robot was checked. | ○ The same camera check; confirm the stop path on the physical OT-2 |
| **Required labware OFF DECK** | ✅ Refused in validation and again in the protocol before any labware is loaded. | — |
| **Wrong mode (simulation vs live)** | ✅ Mode is shown in the header, summary and run banner. Live requires Vertex AI auth. `--offline` is simulation-only. | — |
| **Stale generated protocol uploaded** | ✅ The demo's live path always rebuilds and simulates from the current working YAML. It never uses `--skip-build`. | — |
| **Simulation says OK but the protocol errored** | ◐ The builder scans simulator output for error markers, and the protocol raises on pre-flight failures. The runlog-level checks done for this change are documented. | ○ Assert the "Completed ===" line in the builder for v19 |

## Session and connection

| Failure mode | Safeguard now | Further recommendation |
|---|---|---|
| **LLM connection times out while the operator types** | ✅ READY handshake before the session starts (3 attempts). A failed call rebuilds the client once. A second failure changes nothing and says so. | ○ Periodic keep-alive when idle more than N minutes, if timeouts continue |
| **Unknown operator** | ✅ Name required at startup. It is on every log record, in the working config, and in the robot run log (protocol comment). | — |
| **Paper position printed twice** | ✅ Warning when a plan prints positions already printed in this session. | ○ Persist per-paper-sheet usage |
| **Crash mid-conversation loses context** | ◐ The working YAML is rewritten after every applied change. The session log records every event. | ○ `--resume <session>` to reload the last approved revision |
