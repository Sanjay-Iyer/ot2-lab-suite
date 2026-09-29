# Agent NanoDrop demo rule cleanup

Baseline commit before this cleanup: `9b6e5746c36ca16d7646b579f419a12eba5918f5`.
Git retains the complete former implementations. This archive is documentation only;
no runtime module imports it. Scope: `scripts/ai_dye_demo_gui.py` and
`src/agents/dye_demo/` with the v19 protocol preflight.

| Former rule or behavior | Original location | Type | Why removed or bypassed in GUI |
|---|---|---|---|
| Different drop counts in different paper columns require two runs | `intent.py:_UNSUPPORTED` | Language restriction | `source_map` and the protocol support per-position drop counts. Removed. |
| An answer/run/question route discards valid `changes` | `llm.py:parse_interpretation` | Grounding restriction | Structured changes take precedence over an inconsistent route label. Removed. |
| Short closed run vocabulary, imperative/field-word preclassification, and phrase-specific unsupported vetoes | `session.py:_dispatch`; `intent.py:analyze_turn`; `language.py:wants_to_run` | Language restriction | GUI messages reach the LLM before these legacy classifiers can veto them. Existing terminal paths still use them pending separate migration. |
| Field-name regexes, evidence-token overlap, bare-volume unit requirement, step wording, relative-value lexical matching | `state.py:_verify`, `_verify_value`; `session.py:_grounding_args` | Grounding restriction | GUI proposals use structured values and physical validation. Legacy terminal proposals retain these checks. |
| Phrase-based exact/start/avoid column veto | `columns.py:column_conflict`; `state.py:propose` | Language restriction | GUI structured placement bypasses the phrase check. Physical paper positions remain validated. |
| Print source automatically follows a moved dilution destination | `state.py:_destinations_kept` | Workflow assumption | GUI structured maps preserve the named source. Legacy terminal path retains former behavior. |
| Global prepared-volume revision invalidates unrelated well records | `state.py:recorded_volumes` | Stale-state behavior | Current plate's records are stored per well and updated per well. Removed. |
| No paper identity or paper-replacement reset | `state.py:printed_positions`; `session.py:_reconcile` | Stale-state behavior | Structured `replace_paper` creates a new paper identity and clears only paper occupancy. |
| Post-run next-tip proposal blocks the next run | `session.py:_after_live_run` | Stale-state behavior | GUI LLM-first path advances the next tip as a recorded post-run transition. Terminal path retains proposal behavior. |
| Form range drift for replicates, drops, and vial aspiration air gap | `gui/app.py:_controls`; `validation.py` | Duplicate check | GUI and validator now share demo limit definitions and safety-config capacity. |
| Paper overflow silently skips prints | v19 protocol `_preflight` / `_plan_paper_layout` | Duplicate check | Preflight now fails when requested prints do not fit. |
| Low-liquid preflight warning after validator error | v19 protocol `_liquid_warnings` | Duplicate check | Preflight now fails for the same aspirate/mix depth hazard. |

Physical bounds, actual labware/deck/tip checks, Apply/Discard, run eligibility,
explicit live confirmation, and protocol preflight remain active. This cleanup does
not authorize any live run from the simulation laptop.
