# Task 0.5 — Golden execution traces

Capture the exact ordered robot commands of the current NanoDrop protocol
(`src/protocols/printing/13_ai_agent_dilution_print_demo.py`, builder version 19) for
representative plans, so any later engine or fix can be compared against today's motion.

Scope: test-only recorder, plan cases, stored traces and tests under `tests/`. The protocol,
compiler, machine profile and demo config are not changed, and the fake OT-2 module used by
existing tests keeps its current behavior.

Required coverage: default eight-dilution, dilution-only, print-only, changed factors and
volumes, replicates, stacked drops, modified source wells and paper positions, different tip
policies, known variations. Each trace records aspiration, dispensing, flow rates, heights,
air gaps, blow-outs, touch-tip operations, dwell times and tip motions.

Acceptance: reproducible baseline traces exist for comparing a new engine against the current
implementation. Physical qualification remains WORK VALIDATION PENDING. A matching trace is
software evidence only.
