# Task 1.1 implementation

**The defect.** In the semantic pipeline, `session.py` applied a proposal whenever the router labeled
a message `approve`. "Looks good." could change the plan, although `known_limitations.md` says it cannot.

**The change.**

- A model `approve` no longer applies anything. The proposal stays waiting, the operator is told what
  to do, and `model_approval_refused` is logged.
- Explicit decisions are read by code before the model is consulted. `_explicit_decision` holds the
  logic the legacy reader already used, and both readers now share it. It covers `yes`, `apply`,
  `confirm`, `approve it`, `Yes, I approve proposal #1.`, and `no`, `discard`, `reject`.
- An approval that names another proposal number, or is joined to a new request, applies nothing.
- The page's Apply and Discard now call `decide_proposal(apply, proposal_id, revision)` with the proposal
  and revision last drawn on the page. `decide_from_button` refuses a mismatch, so a click on a replaced
  proposal cannot apply its replacement. A valid click follows the same turn record and code path as a typed decision.
- The router's prompt now tells the model it cannot apply a proposal.

**Deliberately unchanged.**

- `yes` typed in chat applies the proposal waiting at that moment, which is the one on screen.
- A model `discard` still discards, because it changes no plan and the proposal can be requested again.
- `cancel` keeps its existing behavior, including clearing a held safety question.
- `state.apply` keeps its revision and staleness checks, which remain the final guard.

**Behavior difference on the semantic path.** "Yes, I approve proposal #1. Now move the plate" used to go
to the model, and could become a replacement proposal. It now stays waiting and asks for a plain yes
first, matching the legacy reader and `known_limitations.md`. More wordings now approve directly without
a model call, because they are the existing explicit-word set.

No robot motion, protocol, compiler or physical setting changed. Golden traces are unaffected because
this task changes only how a proposal is accepted.
