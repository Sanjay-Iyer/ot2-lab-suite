# Memory, users and skills — proposed lightweight architecture

**Status: design only.** Long-term memory and executable skills are not implemented.
What exists today:

- the operator's name at startup, recorded on every log record and in the config;
- one authoritative in-session state with revision history;
- append-only session logs.

## Goals

- The assistant can say what happened before: "Last time, Stephen made 2×/5×/10× at
  100 µL and printed paper columns 1–2".
- There is no database server and no authentication system. Everything is files a person
  can open and read.
- Memory **never** changes an experiment by itself. Anything it suggests goes through the
  same proposal, validation and confirmation flow as a typed request.

## Four kinds of memory

| Kind | What it holds | Where | Lifetime | Written by | Can it change the experiment? |
|---|---|---|---|---|---|
| **Session memory** | Approved state, revision history, pending proposal, runs in this session | In RAM: `ExperimentState` | Until `quit` | Deterministic code, after an explicit yes | Only through a confirmed proposal |
| **Audit / log history** | Every input, interpretation, proposal, answer, rejection, run and output line | `runs/ai_dye_demo/<session>/session.log`, `parameter_history.jsonl`, `session.json`, `executed_config_runN.yaml` (**exists today**) | Permanent, append-only, never edited | Deterministic code | No; it is evidence only |
| **Experiment history** | One compact record per run: operator, approved configuration and its SHA-256, plan summary, result, tips used, printed positions | `lab_memory/users/<id>/experiments/*.json` (proposed) | Permanent; can be rebuilt from the audit logs | An indexer, after each run | No; it can seed a proposal |
| **Persistent user memory** | Preferences, common reagents, dilution schemes, deck layouts, tip habits, corrections | `lab_memory/users/<id>/profile.json`, `corrections.jsonl` (proposed) | Until the user edits or deletes it | Only after the user says yes to "Remember this?" | No; it can seed a proposal, and lab-owned values are display-only |

## Proposed layout

```text
runs/ai_dye_demo/<YYYYMMDD_HHMMSS>/     exists today, gitignored, on the lab laptop
    session.log                         every event, each tagged with operator + session label
    parameter_history.jsonl             every applied change (who, when, before -> after)
    session.json                        session summary (operator, mode, revisions, runs)
    starting_config.yaml
    executed_config_run1.yaml ...

lab_memory/                             proposed, gitignored, lives beside runs/
    users/
        stephen/
            profile.json                small, human-readable preferences
            corrections.jsonl           "when I say X I mean Y", with provenance
            experiments/
                2026-09-03_demo-1_run-1.json
                2026-09-03_demo-1_run-2.json
            sessions/
                index.jsonl             one line per session, pointing at runs/ai_dye_demo/<id>
    labware/
        tipracks.jsonl                  optional: which positions of which physical rack are used
```

`operator_id` is the lower-cased, underscored name the demo already records (`stephen`).
The layout is per user so memory can never leak between operators, and a user's folder
can be deleted in one step.

### Example: `profile.json`

```json
{
  "operator": "Stephen",
  "operator_id": "stephen",
  "updated_utc": "2026-09-03T18:40:12+00:00",
  "preferences": {
    "drop_volume_ul": {"value": 5.0, "source": "2026-09-03 Demo 1, revision 2", "confirmed": true},
    "dilution_scheme": {"value": {"factors": [2, 5, 10], "total_volume_ul": 100}, "confirmed": true},
    "deck_layout": {"value": {"plate": 4, "paper": 5, "tuberack": 7, "tiprack": 9}, "confirmed": true},
    "tips": {"value": {"policy": "per_liquid", "return_tips": false}, "confirmed": true},
    "common_reagents": {"value": ["crystal violet", "water"], "confirmed": true}
  },
  "lab_owned_notes": {
    "print_height_mm": "prefers 0.5 mm; lab setting is 1.1 mm (display only, never applied)"
  }
}
```

### Example: experiment record

```json
{
  "experiment_id": "2026-09-03_demo-1_run-1",
  "operator_id": "stephen",
  "session_id": "20260903_101500",
  "mode": "LIVE",
  "result": {"exit_code": 0, "status": "succeeded"},
  "approved_config_sha256": "5f2c...",
  "approved_config_file": "runs/ai_dye_demo/20260903_101500/executed_config_run1.yaml",
  "summary": {
    "dilutions": {"wells": ["A11", "B11", "C11"], "factors": [2, 5, 10], "total_volume_ul": 100},
    "print": {"positions": ["A1", "B1", "C1"], "drop_volume_ul": 5.0, "drops_per_position": 1},
    "tips_used": ["A1", "B1", "C1", "D1", "E1"]
  }
}
```

### Example: `corrections.jsonl`

```json
{"when": "2026-09-03T18:12:03+00:00", "said": "spot 7", "meant": "deck slot 7", "session": "20260903_101500", "confirmed": true}
{"when": "2026-09-03T18:20:44+00:00", "said": "the plate", "meant": "paper print plate", "session": "20260903_101500", "confirmed": true}
```

## How memory would appear in a session

1. **Startup (unchanged):** READY handshake, then "Who is running this experiment?"
2. **Recall:** the name matches `lab_memory/users/stephen/`, so the assistant shows a
   *read-only* summary:
   `Welcome back, Stephen. Last session (2026-09-03 Demo 1): 3 dilutions 2×/5×/10× at
   100 µL, printed paper columns 1-2, tips A1-H1 used.`
3. **Offer:** `Start from that plan?` A yes creates an ordinary proposal: every change is
   listed, validated against today's deck, and applied only after a second explicit yes.
4. **Corrections:** "spot 7" is still confirmed, but the suggested answer is the one this
   user chose before. Memory never removes a confirmation.
5. **LLM context:** interpretation and `/ask` receive a compact, clearly labelled
   `USER MEMORY (read-only)` block of at most about 20 lines. The model is told it
   describes the past, not the current state.
6. **End of session:** `Remember these as your preferences? drop 5 µL, factors 2×/5×/10×`
   (yes/no). Nothing is written without a yes.

## Rules

- **Memory is advice, the state is authority.** Values from memory enter the experiment
  only through `ExperimentState.propose()` and an explicit yes.
- **Lab-owned values are never applied from memory.** A preference for a different print
  height is shown, not used.
- **Provenance on every entry:** session, revision and timestamp, so any remembered value
  can be traced back to the audit log.
- **Rebuildable:** experiment history and session indexes are derived from `runs/`.
  Deleting them loses nothing that the audit logs do not already hold.
- **Inspectable and erasable:** proposed commands `memory` (show) and `forget <item>`,
  both confirmed.
- **Small:** no free-form transcripts in memory, no credentials, names only.

## Skills

### What a skill is

A skill is a **named, versioned, deterministic Python function** with a typed input, a
declared set of fields it may change, its own validation, and tests. The LLM chooses a
skill and fills in its arguments. The skill computes the exact field changes. The result
goes through the same proposal, validation, confirmation and state update as any other
change.

```python
@skill(name="move_labware", version=1, may_change=("deck.*.slot",))
def move_labware(state: ExperimentState, *, labware: str, to: int | Literal["OFF_DECK"]) -> SkillResult:
    """Move one labware. Never relocates anything else; reports collisions."""
    change = {"path": f"deck.{role_of(labware)}.slot", "value": to, "kind": "requested"}
    return SkillResult(changes=[change], notes=[])
```

The LLM would answer with `{"skill": "move_labware", "args": {"labware": "paper", "to": 8}}`
instead of raw field paths. Python checks:

- the skill exists;
- the arguments match the schema;
- the output stays inside `may_change`.

The history records `skill=move_labware@1` next to the change.

### How this differs from raw LLM reasoning

| | Raw LLM reasoning | Skill |
|---|---|---|
| Same request, same result? | Not guaranteed | Always |
| Arithmetic (volumes, tips, wells) | Done by the model | Done by tested Python |
| What it may touch | Anything in its reply | Only the declared fields |
| Review | Every reply is new | Code-reviewed once, versioned |
| Explanation | Model's own words | Deterministic summary of what the code did |
| Failure | Plausible but wrong output | Explicit error with a reason |

The existing `skills/*/SKILL.md` files (`vial-dilution-print`, `standard-paper-printing`,
`ot2-robot-profile`, …) are **instruction skills**: guidance that teaches an agent a
procedure. Executable skills complement them. The markdown says *when and why*; the
function does *exactly what*.

### Proposed skills, mapped to what already exists

| Skill | Purpose | Status of the underlying logic |
|---|---|---|
| `move_labware` | Move labware or set OFF DECK; collisions reported, nothing auto-relocated | Exists: `validation.deck_conflicts`, `render.render_conflict` |
| `validate_deck` | Required labware on deck, no collisions | Exists: `validation._check_deck` |
| `estimate_tip_usage` | Tips needed, range, next tip, remaining | Exists: `plan.build_plan` (tips) |
| `print_pattern` | Paper positions from volumes × replicates × drops | Exists for one row per dilution: `plan.paper_layout`. Free-form patterns would be new. |
| `validate_liquid_transfer` | Pipette range, minimum volume, liquid depth, carry-over | Exists: `validation` depth, minimum and clearance checks |
| `create_dilution_series` | Factors and volumes for N independent dilutions from stock | Logic exists in the plan; would become one function |
| `create_serial_dilution` | Well-to-well serial series | **New protocol mode needed.** v19 makes independent dilutions from stock; `04_general_dilution.py` already has a series mode to reuse. |
| `prepare_cv_dilutions` | A recipe: crystal violet label, standard factors and volume, then `create_dilution_series` | New preset on top of existing logic |
| `visualize_deck` | ASCII or SVG drawing from `Plan` | See `visualization_options.md` |
| `remember_preference` / `recall_last_experiment` | The memory operations above | New |

### Why skills make the system safer

- **Deterministic:** identical inputs give identical changes, so tests pin behaviour.
- **Bounded:** a skill declares what it may change, and anything else is rejected.
- **Auditable:** the skill name and version sit in the parameter history.
- **Explainable:** the proposal says which skill produced which change.
- **Composable:** recipes such as `prepare_cv_dilutions` combine tested skills instead of
  asking the model to reason from scratch.

### Suggested phases (after this demo)

1. Wrap existing checks as skills with no behaviour change: `move_labware`,
   `estimate_tip_usage`, `validate_deck`. Let the LLM return either raw changes or a skill
   call.
2. Add `create_dilution_series` and `prepare_cv_dilutions`, and log skill names in the
   history.
3. Add the session index and experiment records, generated from `runs/`.
4. Add opt-in persistent preferences with `memory` and `forget`, plus the "Welcome back"
   proposal.
