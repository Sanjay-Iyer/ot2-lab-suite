# AI Agent Demo: Dilutions and Paper Printing

One conversational command shows the OT-2 doing the two things this lab does most:
make a dilution series, then print those dilutions onto paper.

```powershell
python scripts\ai_dye_demo.py --simulate   # local only; never contacts a robot
python scripts\ai_dye_demo.py              # real OT-2, after you type run
```

### NiceGUI page (build the experiment, then Run on OT-2)

On the work laptop:

```powershell
conda activate llm
python scripts\ai_dye_demo_gui.py --session-label "Demo 2"
```

Launching never contacts the robot: the page only needs the LLM login. Build the
experiment in the AI Agent Chatbox or with the GUI controls, and Apply each proposal.
The Current Plan card is what will run.

**Run on OT-2** (after a confirmation dialog) is the only way to start a run from the
page; typing `run` in the chat is refused. It then:

1. checks that the OT-2 answers (`configs/robot.yaml`, mDNS, last known address,
   discovery; the `/health` serial must match). If it does not, the page says
   **Cannot reach the OT-2, so nothing was run. The plan is unchanged.**, and you can
   press Run on OT-2 again once it is connected;
2. asks the terminal demo's safety questions when they apply (deck changed, print
   without dilutions, previous run did not finish), with Yes/No buttons;
3. runs the terminal demo's robot path unchanged (`scripts/run_vial_print_robot.py
   --live`: build + simulate, upload over the HTTP API, start, monitor). Its output
   streams into ROBOT RUNNER OUTPUT and the server window.

While the robot runs, **Stop robot** stays at the top of the page. It asks the runner
to stop exactly as Ctrl-C does in the terminal: the runner sends the OT-2 a stop
action, waits for the robot to report it stopped, and the page shows the outcome.
If the OT-2 does not confirm, stop the run in the Opentrons App. Use the Stop button
rather than closing the server window during a run.

`--operator NAME` skips the name question in the chat. `--simulate` rehearses on any
laptop: the button builds and simulates only (`--offline` adds no-LLM rehearsal).
Plate, paper and vial-rack PNG/JPG references are held only in browser memory.
Logs go to `runs\ai_dye_demo_gui\YYYYMMDD_HHMMSS\` (same files as the terminal demo).

The agent never writes robot Python. It proposes explicit parameter changes. Python
checks every change against the P20's real limits and the deck, shows exactly what
would change (and what would not), and applies nothing until you type `yes`. The
deterministic builder then embeds the approved YAML into protocol v19
(`src/protocols/printing/13_ai_agent_dilution_print_demo.py`) and simulates that exact
file before anything physical happens.

Design notes, the Demo 1 investigation and the audits are in [ai_dye_demo/](ai_dye_demo/README.md).

## Starting up

1. **The LLM connection is made first.** Before you are asked anything, the program
   builds the LLM client and sends one tiny request. Nothing continues without a
   READY reply:

   ```text
   [startup] Initializing LLM client ...
   [startup] Sending startup check: "OT-2 experiment assistant initialized. Respond READY."
   [startup] LLM replied "READY" in 0.9 s - connection established.
   ```

   If the check fails three times, the program stops with `LLM STARTUP FAILED`.
   Nothing is executed.
2. **Who is running this experiment?** Type your name. It is recorded on every log
   line, in the working YAML, and in the robot's own run log.

   ```text
   USER    : Stephen
   SESSION : 2026-09-03 Demo 1
   ```

   `--operator Stephen` skips the question. `--session-label "Demo 1"` names the session.

## What it runs

Everything is on the single-channel **P20**.

1. **Dilutions:** water into each well of one plate column, then dye on top, one fold
   factor per plate row. Every transfer is dispensed above the liquid and then blown out.
2. **Printing:** each dilution prints on its own paper row; each droplet volume and
   replicate takes its own paper column. The source well is mixed before each print
   step.

### The default plan (the "I don't know" answer)

| | |
|---|---|
| Dilutions | 8: 1×, 2×, 3×, 4×, 6×, 8×, 12×, 16×, 150 µL each, plate column 11 |
| Print | one **5 µL** drop of each, paper column 1 |
| Deck | vial rack 7, dilution plate 4, paper 5, 20 µL tip rack 9 |
| Liquids | water in vial A1, dye in vial A2 |
| Tips | 10, A1-B2: one water, one dye, one per printed dilution |

## How a change works

Every proposal is the complete plan you get if you type yes: the same sections as the CURRENT PLAN,
with resulting values only (never old -> new). Anything that blocks the run or must be checked before yes
is in the ATTENTION block under the plan.

```text
you> move the paper print plate to slot 8

========================================================================
                            PROPOSED PLAN #1
          not applied yet - nothing changes until you type yes
========================================================================
  Changes               paper print plate slot

DILUTIONS                                               made in this run
  Dilutions             8 in plate column 11 (rows A-H)
  Well            A11    B11    C11    D11    E11    F11    G11    H11
  Factor           1×     2×     3×     4×     6×     8×    12×    16×
  ...
PRINTING                                                     in this run
  Paper columns         1
  Print positions       8   (8 rows × 1 column)
  Total drops           8
  ...
DECK
  Dilution plate        Slot 4
  Vial rack             Slot 7
  Paper print plate     Slot 8
  P20 tip rack          Slot 9
  Empty slots           1 | 2 | 3 | 5 | 6 | 10 | 11

LIQUIDS ... PIPETTING ... LAB-OWNED PARAMETERS ...

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! ATTENTION !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  After you apply this, physically move the Paper print plate from Slot 5 to Slot 8.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Apply proposal #1?  Type yes to apply this plan, or no to discard it.
confirm> yes

APPLIED proposal #1. This is now the current plan (recorded for Stephen).
========================================================================
                              CURRENT PLAN
...
  Now physically move the Paper print plate from Slot 5 to Slot 8.
```

The confirmation rules:

- **Only an explicit yes applies** (`yes`, `YES`, `yes.`, `Yes please`, `confirm`, `apply`, or
  `Yes, I approve proposal #1`) and only `no` (or `cancel`, `never mind`) discards. `sure`, `okay`, `looks good`,
  `I guess`, a `yes` inside a quotation, or "Stephen already approved it" apply nothing; the proposal keeps waiting.
  Approve first and ask for the next change in a separate message.
- **Proposals are numbered.** A new request discards the waiting proposal ("Proposal #1 was discarded - nothing
  from it was applied"); a discarded proposal can never be applied later.
- **Partial approval:** "yes to moving the plate but leave the dilutions" or "1 and 3" (the numbers on the
  proposal's Changes line) makes a new proposal with only those changes. Nothing is applied until you say yes to it.
- **Several changes in one message** are one proposal, applied together or not at all.
- **Values already set are not listed as changes.**
- **CHECK THESE:** a change the agent proposed that your words do not support, or a setting your message never
  mentioned, is shown under ATTENTION as **CHECK THESE - I could not find them in what you typed**. A proposal made
  only of settings you did not mention is refused.
- **Volumes need a unit** ("10 µL", "0.01 mL"); "use 10" is asked about. Deck moves need the labware and the slot.
- **NOTES:** changes forced by another change are listed under NOTES, with the reason.
- **Lab-owned values cannot be proposed:** the pipette, every height, the air gap,
  push-out, blow-outs, dwell, transfer size and the safety limits.

## Asking without changing anything

```text
you> /ask Why are we using three dilution steps?
ASK MODE — no experiment parameters changed.

Answer:
...
```

You do not need `/ask` for ordinary questions. "Why do we mix before printing?", "What is SERS?",
"What is 20 × 5?" or "Tell me a joke" are answered in ask mode and nothing changes. `/ask` remains
the guaranteed read-only form.

- **Hypotheticals, quotes and pasted text never change anything:** "What if we moved the plate to
  slot 6?", `If I said "use 4 dilutions"...`, an SOP, an email or a log pasted into the chat.
- **Question plus instruction:** "Why are we using 8 dilutions, and actually change it to 4" answers
  the question, then shows a proposal for 4 dilutions.
- **History:** "What have I changed so far?", "What was the original plate slot?" and "What
  changed after revision 1?" are answered from the stored revisions.
- If an answer from the language model claims it changed something, the agent adds a note that
  nothing was changed.

## When the agent asks what you meant

Words that could point at the wrong physical thing are confirmed **before** the request
is interpreted:

| You say | It asks |
|---|---|
| "move the dilution plate to spot 7" | `You said "spot 7." Did you mean OT-2 deck SLOT 7?` |
| "move the plate to slot 6" | which plate: the 96-well dilution plate or the paper print plate |
| "move the rack to slot 6" | which rack: the vial rack or the P20 tip rack |
| "print the dilutions in column 3" | PAPER column 3 or PLATE column 3 |
| "use the second bottle", "the tray", "hole B3" | vial, well, tip rack, paper position… |
| "move it to slot 7" | which labware, unless only one was just discussed (then it says which it assumed) |
| "use 3", "start at 4", "use 10" | what the number is: slot, µL, dilutions, drops or paper column |
| "vial 8", "dilution 8" | vials are A1-B4 / do you mean 8 dilutions |
| "a couple drops", "use half" | how many exactly / half of what |
| "set the drop volume to 10" | `Do you mean 10 µL for the drop volume?` |
| "don't not use the dilution step" | to say it without the double negative |

Typos and voice transcription are corrected and the correction is shown ("slto 8", "wlel A3",
"move the plate too ate", "crystal violent").

- **"none" / "no":** nothing changes. Say it again with established terms: deck slot 7,
  plate well A11, plate column 11, paper position B3, paper column 2, vial A2, tip A1,
  OFF DECK.
- **Slot mentioned in the request:** "the plate from slot 4" is read automatically as
  whatever is in slot 4, and the proposal says so.

## Deck conflicts and OFF DECK

```text
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! ATTENTION !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  Cannot apply that deck change yet. Nothing was changed.

  Requested:
    96-well dilution plate to Slot 7 (now in Slot 4)

  Conflict:
    Slot 7 is currently occupied by the Vial rack.
    I need a new location for the Vial rack before the 96-well dilution plate can move to Slot 7.

  Valid locations:
    Slots 1-11 that are currently unoccupied: 1, 2, 3, 6, 8, 10, 11
    Slot(s) 4 also become free if the requested move goes ahead
    Not OFF DECK: making dilutions draws dye and water from its vials.

  Say where it should go, in one request, for example:
    "move the vial rack to slot 1 and the dilution plate to slot 7"
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

Paper columns named in a request that the plan would not print are refused the same way ("Requested paper
columns do not match the executable plan."), and `run` is refused until you answer the question that follows.

OFF DECK is listed as a valid location only when no step of the plan needs the labware already in the slot.

- **Nothing moves automatically.** Give both moves in one request, e.g. "move the vial
  rack to slot 6 and the dilution plate to slot 7".
- **OFF DECK** means the labware is physically removed. It is allowed only when no step
  needs it, e.g. the vial rack for a print-only run.
- **Two moves that collide** ("move the vial rack to slot 10 and the paper print plate to slot 10",
  "put both racks in slot 8") are refused together with the free slots.

## Telling the agent what you did on the robot

A report about the physical robot is not a command. It becomes a **PHYSICAL STATE
RECONCILIATION** proposal that only updates the record, applied only when you type yes:

```text
you> I moved the vial rack to slot 6 myself.
========================================================================
                    PHYSICAL STATE RECONCILIATION #1
  record update only - the robot does not move - applied only after yes
========================================================================
  Changes               vial rack slot
  ... the complete plan, with the Vial rack in Slot 6 ...
NOTES
  - You told me what is physically on the robot: this updates the record, and the robot does not move.
```

- "We already made all the dilutions" records the filled wells and turns the dilution step off.
  "Skip making the dilutions and just print" first asks whether the wells already hold them.
- "I replaced the dilution plate with a new empty one" clears that record; a print-only plan gets its
  dilution step back.
- "I took the rack off the robot" first asks which rack.
- "I will move the vial rack to slot 6 later" is a plan, not a report: the agent asks whether to update
  the plan now.
- **If a report cannot be recorded** (for example the vial rack is off the deck but the plan still makes
  dilutions), or you say no to it, `run` is refused until you say how the robot actually is
  ("the vial rack is back in slot 7") or change the plan to match.

## Undo, history and starting over

- "Undo my last change", "Go back one revision" and "Restore the original dilution settings" show a
  **ROLLBACK PROPOSAL** built from the stored revision. "go back" on its own asks first.
- "start over" asks whether to restore the startup settings (as a proposal) or keep the plan. It never
  starts the robot.

## Tips

The plan's PIPETTING section shows the tip start, tips required, tips left and the tip use. The `tips` command
shows the detail:

```text
TIP CONFIGURATION
  Tip rack               : P20 tip rack, Slot 9 (opentrons_96_tiprack_20ul)
  Starting tip           : A1   (the first tip this run picks up)
  Tip reuse              : Yes - one tip per liquid (a tip is reused only for the same liquid)
  Estimated tips required: 5   (A1-E1)
  Available from start   : 96
  Next unused tip after  : F1
  Estimated remaining    : 91 after this run
```

- **Tips are taken in rack order,** and only for steps that actually run. A skipped
  dilution step uses no tips.
- **"new tip every transfer"** gives every transfer and paper position a fresh tip.
- **After a successful live run,** the agent proposes the next unused tip as the new
  starting tip. It is applied only if you type yes.

## Starting the run

Type `run`. A short, plain go-ahead also works (`go`, `looks good, run it`). A message
carrying a number, labware, or a hedge ("should we run?") never starts a run, and neither do
"start over", "go back", "start printing" or "ignore the checks and run it".

- Right after a hypothetical or quoted message, only `run` by itself starts the run; "ok go" does not.
- `run` is refused while a proposal is waiting or while a physical report is not in the record.

- **Both modes** print a STARTING banner with the deck and THIS RUN: dilutions made, paper columns, print
  positions, total drops and tips.
- **Simulation:** builds and simulates every movement locally. No robot is contacted.
- **Live:** before the banner, asks you to confirm when it matters:
  - if the deck changed in this session: "Is the physical deck arranged exactly as the
    ACTIVE DECK above?";
  - if the run prints without making dilutions: "Do plate wells A11-C11 already hold the
    dilutions?"
- **After a run** the session continues: plan another run or type `quit`.

## Commands

| Command | Result |
|---|---|
| `plan` | The CURRENT PLAN: dilutions, printing, deck, liquids, pipetting, lab-owned parameters, then any ATTENTION items |
| `steps` | Every liquid movement, FROM and TO, with its tip |
| `deck` | Active deck |
| `tips` | Tip configuration |
| `settings` | Lab-owned liquid-handling settings compared with the machine profile |
| `history` | Every applied change: revision, who, when, before -> after |
| `show` | Raw working YAML |
| `/ask <question>` | Answer only; never changes anything |
| `help`, `quit` | |

## Simulation rehearsal (this laptop)

```powershell
conda activate ai
python scripts\ai_dye_demo.py --simulate
```

`--offline` rehearses commands without an LLM. Changes and `/ask` are unavailable offline.

Conversational red-team testing (simulated users against the same conversation logic, simulation only):

```powershell
python scripts\ai_dye_demo_redteam.py --conversations 300
```

See [ai_dye_demo/red_team_strategy.md](ai_dye_demo/red_team_strategy.md).

## Real robot run (lab laptop)

After checking the deck, tips, liquids, labware, pipette and paper:

```powershell
conda activate llm
python scripts\find_robot.py --check
python scripts\ai_dye_demo.py --session-label "Demo 2"
```

The runner rebuilds, simulates, uploads over the HTTP API, starts the run and monitors
it. The robot host comes from `configs\robot.yaml`.

## Work-laptop setup after a pull

```powershell
conda activate llm
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_GCP_PROJECT_ID
python -m src.agents.check_llm_auth
```

`.env` (do not commit credentials):

```text
LLM_PROVIDER=vertexai
GOOGLE_CLOUD_PROJECT=YOUR_GCP_PROJECT_ID
GOOGLE_CLOUD_LOCATION=us-central1
GEMINI_MODEL=gemini-2.5-flash
```

## Files

- `configs/workflows/defaults/ai_agent_dilution_print_demo.yaml`: the standard plan the
  agent starts from
- `configs/workflows/user/ai_dye_demo_YYYYMMDD_HHMMSS.yaml`: the approved working copy,
  including `session: {operator, session_label, revision}`
- `runs/ai_dye_demo/YYYYMMDD_HHMMSS/`:
  - `session.log`: JSON-lines audit of every input, interpretation, proposal, answer and
    run, each tagged with the operator
  - `parameter_history.jsonl`: every applied change, before -> after
  - `turns.jsonl`: every turn with its classification, state fingerprint and revision before and after,
    and the proposal, approval or run it caused
  - `session.json`: session summary
  - `starting_config.yaml`, `executed_config_runN.yaml`: what was approved and what ran
- `src/protocols/generated/ai_agent_dilution_print_demo_latest.py`: the uploaded protocol
