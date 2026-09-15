# Guide — Three Dye Dilutions and 5 µL Paper Printing

Use this Guide for explanations, terminology, command help, adjustable parameters,
and troubleshooting. For the short step-by-step procedure, use
[SOP — Three Dye Dilutions and 5 µL Paper Drops](SOP_AI_Dye_Three_Dilutions_Paper_Print.md).

## Goal and final layout

The script `scripts\ai_dye_demo.py` will dilute dye stock with water to make
three different dilutions: **2x, 5x, and 10x**.

**Each dilution has a final total volume of 100 µL.** The 100 µL is the combined
volume of dye stock plus water in one well; it is not 100 µL of dye stock.

The three dilutions are printed on paper in OT-2 deck slot 5:

- Paper column 1 receives one 5 µL drop from each dilution.
- Paper column 2 receives three separate 5 µL drops from each dilution, stacked
  on the same spot.

| Dilution | Plate source | Paper column 1 | Paper column 2 |
|---|---:|---:|---:|
| 2x | A11 | A1: 1 × 5 µL | A2: 3 × 5 µL |
| 5x | B11 | B1: 1 × 5 µL | B2: 3 × 5 µL |
| 10x | C11 | C1: 1 × 5 µL | C2: 3 × 5 µL |

## Why this uses two runs

The script gives every printed paper column in one run the same
`droplets_per_spot` value. It cannot assign one drop to column 1 and three drops
to column 2 in a single run.

The workflow is therefore:

1. Run 1 makes the dilutions and prints one 5 µL drop in paper column 1.
2. Run 2 keeps the prepared dilutions, skips dilution-making, and prints three
   stacked 5 µL drops in paper column 2.

Both runs can happen in **one session**. The session continues after a run and
remembers the approved plan.

Do not request two replicates with three drops per spot. That would print three
drops in both columns.

## Dilutions

The robot makes each dilution by combining dye stock with water. Every completed
dilution well contains **100 µL total volume**.

| Dilution | Dye stock | Water | Final total volume |
|---|---:|---:|---:|
| 2x | 50 µL | 50 µL | 100 µL |
| 5x | 20 µL | 80 µL | 100 µL |
| 10x | 10 µL | 90 µL | 100 µL |

The dilution step consumes 80 µL dye stock and 220 µL water in total.

The tip aspirates 4 mm above the vial bottom, and a 20 mL vial needs about 2.46 mL just
to cover that. The plan's **LIQUIDS** section prints the minimum to load: about 2.54 mL
of dye and 2.68 mL of water. Use the laboratory-approved working volume if it is higher.

Every water and dye transfer is dispensed above the liquid and then blown out, so the
whole volume leaves the tip.

Printing removes another 20 µL from each dilution well: 5 µL during run 1 and 15 µL
during run 2. About 95 µL remains after run 1 and 80 µL after run 2, excluding minor
pipetting losses. Mixing before printing needs at least about 89 µL in a well; 95 µL is
enough for run 2.

## OT-2 locations and terms

| Term | Meaning in this workflow |
|---|---|
| Deck slot | A numbered physical position on the OT-2 deck. Labware uses slots 1 through 11; slot 12 is trash. |
| OFF DECK | Labware physically removed from the robot. Allowed only when no step in the run needs it. |
| Dilution plate | The 96-well plate in slot 4 where the three dilutions are made. |
| Paper print plate | The paper-print holder in slot 5, addressed like a 96-well plate. |
| Row | A letter from A through H. Here, rows A, B, and C identify the three dilutions. |
| Column | A number from 1 through 12. Say **plate column** or **paper column**; the agent asks if you do not. |
| Plate column 11 | The dilution sources A11, B11, and C11. |
| Paper column 1 | The first vertical set of paper targets: A1, B1, and C1. |
| Paper column 2 | The second vertical set of paper targets: A2, B2, and C2. |
| Vial position | A location in the 8-vial rack (A1-B4). The default is water in A1 and dye stock in A2. |
| Dilution factor | 2x is 1 part dye in 2 parts total, 5x is 1 part in 5 parts total, and 10x is 1 part in 10 parts total. |
| Drop | One dispense operation. Three drops means three separate 5 µL dispenses at the same paper target. |
| Replicate | A side-by-side paper column printed with the same drop volume and drops-per-spot setting. |

## Default deck layout

| Deck slot | Item |
|---:|---|
| 4 | 96-well dilution plate |
| 5 | Paper print plate |
| 7 | 8-vial rack |
| 9 | 20 µL tip rack |

The workflow uses a P20 single-channel Gen2 pipette on the left mount.

## Starting the script from PowerShell

Run commands from the repository root.

### Simulation command

```powershell
cd C:\code\opentrons_home\ot2-lab-suite
conda activate ai
python scripts\ai_dye_demo.py --simulate
```

The `--simulate` option builds and simulates the protocol locally without contacting a
robot.

### Live robot command

On the configured robot computer, from its copy of the repository:

```powershell
cd C:\path\to\ot2-lab-suite
conda activate llm
python scripts\find_robot.py --check
python scripts\ai_dye_demo.py
```

The robot address normally comes from `configs\robot.yaml`. If discovery fails,
correct that configuration and repeat `python scripts\find_robot.py --check`.
Use `--robot-host` only when a lab administrator directs you to override the
configured host.

### What happens at startup

1. The LLM connection is checked first: `LLM replied "READY" ... connection established`.
   If it fails three times, the script stops and nothing runs.
2. **Who is running this experiment?** Type your name. It is recorded in the logs, the
   working configuration and the robot's run log.

## Commands inside the script

| Command | Result |
|---|---|
| `plan` | Displays the CURRENT PLAN: dilutions, printing, deck, liquids to load, pipetting and tips, lab-owned parameters, then any ATTENTION items. |
| `steps` | Displays every liquid movement, FROM and TO, with its tip. |
| `deck` | Displays the active deck. |
| `tips` | Displays the tip configuration: rack, starting tip, required, next and remaining. |
| `settings` | Displays the lab-owned liquid-handling settings. |
| `history` | Displays every applied change, who made it, and when. |
| `show` | Displays the complete working YAML configuration. |
| `/ask <question>` | Answers a question. Never changes anything. |
| `help` | Displays the kinds of changes the agent accepts. |
| `run` | Starts the displayed simulation or robot run. In live mode, movement begins after any safety questions. |
| `quit` | Stops without executing. `exit` and `q` also work. |

Anything else entered at `you>` is plain language: a question is answered (nothing
changes), and a request to change the plan becomes a numbered proposal.

- **Nothing is applied until you type `yes`.** The agent first shows **PROPOSED PLAN #N**:
  the complete plan you get if you say yes, with resulting values only. Anything to check before
  yes (unverified values, physical moves to make, warnings) is in the **ATTENTION** block under
  the plan. `no` discards the proposal. `ok`, `sure` or `looks good` do not apply it.
- **A request that contains a number or labware term is an edit,** never permission to
  start.
- **Say volumes with their unit** (`100 uL`), and name the labware and the slot for deck moves.
- **What you did by hand** ("I moved the vial rack to slot 6 myself", "the dilutions are already
  made") updates the record only after you confirm it; until a report is recorded, `run` is refused.

Always type `plan` and read the entire result before typing `run`.

## Exact requests for this experiment

### Run 1 request

```text
Make three dilutions by diluting dye with water. Use dilution factors 2x, 5x, and 10x, with a final total volume of 100 uL in each dilution well. Start at row A in plate column 11. Put the paper in deck slot 5. Print one 5 uL drop of each dilution starting in paper column 1. Use one replicate and start from tip A1.
```

The proposal's Changes line should name only the changes from the default plan: the dilution
factors and the final volume. Settings that already match (slot 5, column 11, row A, 5 µL, one
replicate, tip A1) are not listed as changes. Type `yes`.

The plan should then show:

- DILUTIONS: wells A11, B11, C11 with factors 2× | 5× | 10×, dye 50 | 20 | 10 µL and water
  50 | 80 | 90 µL, 100 µL in each well;
- PRINTING: paper column 1, paper rows A | B | C, 3 print positions, 1 drop per position,
  3 total drops (`steps` lists each print step FROM A11/B11/C11 TO paper positions A1/B1/C1); and
- PIPETTING: tips required 5 (A1-E1).

After a successful live run, the agent proposes starting tip F1 for the next run. Type `yes`.

### Run 2 request

```text
The three dye dilutions are already made, so skip the dilution step. Each well now holds about 95 uL. Print three separate 5 uL drops stacked on each spot, starting in paper column 2, and start from tip F1.
```

If you started a new session instead, add: "Their factors are 2x, 5x, and 10x, each with
a final total volume of 100 uL, starting at row A in plate column 11."

The proposal (the complete plan after yes) should show:

- `DILUTIONS ... SKIPPED - already in the plate`, with `Volume in each well 95 µL`;
- under **ATTENTION**, `Saying yes records plate wells A11-C11 as already holding the dilutions (2× | 5× | 10×; made at 100 µL each; reported by the operator).`;
- `Drops per position 3 (stacked)` and `Paper columns 2`; and
- `Tip start F1`.

Saying the dilutions are already made is a report about the physical plate, so it is recorded only
after you type `yes`. Asking to skip the dilution step without saying so makes the agent ask first
whether the wells already hold the dilutions.

Type `yes`. The CURRENT PLAN should then show:

- DILUTIONS marked `SKIPPED - already in the plate`, wells A11, B11 and C11;
- PRINTING: paper column 2, paper rows A | B | C, 3 print positions with 3 stacked 5 µL drops each;
- 9 total drops; and
- tips required 3 (F1-H1).

When you type `run` on the robot, answer `yes` to "Do plate wells A11-C11 already hold the dilutions?"

## Tip use across the two runs

Run 1 starts from tip A1:

- A1 is used for all water transfers.
- B1 is used for all dye transfers.
- C1, D1, and E1 are used to print the 2x, 5x, and 10x rows.

Run 2 starts from tip F1. A skipped dilution step takes no tips, so its three print tips
are F1, G1, and H1.

**Do not start Run 2 from D1:** D1 and E1 were used by Run 1. Verify that F1, G1, and H1
are present and unused before Run 2.

## Adjustable parameters

Tell the agent the requested change at `you>`, check the proposal, type `yes`, then
type `plan`.

| Parameter | Example | Meaning and allowed range |
|---|---|---|
| Dilution factors | `Use factors 2x, 5x, and 10x` | One to eight factors, each at least 1x. |
| Number of dilutions | `Make four dilutions` | One to eight; the start row and count must fit through row H. |
| Final total volume | `Use 100 uL total per dilution` | Greater than 0 and no more than 340 µL per well, and enough to mix and print without drawing air. |
| Volume now in prepared wells | `Each well now holds about 95 uL` | For dilutions made earlier; used for the liquid-depth checks. |
| Plate column | `Use plate column 11` | Column 1 through 12. |
| Start row | `Start at row A` | Row A through H. |
| Droplet volume | `Print 5 uL drops` | 1 through 18.5 µL with the calibrated 1.5 µL air gap and P20. |
| Drops per spot | `Stack three drops on each spot` | Positive whole number; applies to every printed column in that run. |
| Replicates | `Use two replicate columns` | Side-by-side columns with the same volume and drops-per-spot value. |
| Paper start column | `Start at paper column 2` | Column 1 through 12; printing proceeds to the right. |
| Deck slots | `Put the paper in slot 5`, `take the vial rack off deck` | Slots 1 through 11 or OFF DECK; no two items in one slot. |
| Dye and water vials | `Use dye vial A2 and water vial A1` | A1 through B4; dye and water must be in different vials. |
| Material names | `Call the dye crystal violet` | Display name only. |
| Mixing | `Mix twice at 15 uL` | At least one mix; volume greater than 0 and no more than 20 µL. |
| Starting tip | `Start from tip F1` | A valid 96-tip-rack address with enough fresh tips remaining. |
| Tip policy | `Use a new tip for every transfer` | One tip per liquid (default), or a new tip every transfer. |
| Return tips | `Return tips to the rack` | Otherwise used tips are discarded. |
| Skip a stage | `The dilutions are already made` | Skips dilution-making; `only dilute, do not print` skips printing. |

Changing the dilution factors, number of drops, or number of columns changes liquid use.
The proposal shows the resulting print positions, total drops, printed volume, liquid use and tips.

## Laboratory-owned settings

The conversational agent refuses changes to:

- pipette model and mount;
- safety limits and flow rates;
- maximum transfer size;
- paper dispense height, source aspiration height, vial aspiration height, dilution
  dispense heights and mixing height;
- air-gap volume and height;
- push-out, print blow-out, dilution blow-out, and post-dispense delay; or
- configured paper width.

Type `settings` to see these values compared with the machine profile. If the agent
rejects a request, the configuration stays unchanged and the reason is displayed. Do not
bypass a rejected safety or calibration setting.

## Saved files

| Path | Contents |
|---|---|
| `configs\workflows\user\ai_dye_demo_YYYYMMDD_HHMMSS.yaml` | Approved working configuration, including operator and revision. |
| `runs\ai_dye_demo\YYYYMMDD_HHMMSS\session.log` | Every input, interpretation, proposal, answer, command output and exit code, tagged with the operator. |
| `runs\ai_dye_demo\YYYYMMDD_HHMMSS\parameter_history.jsonl` | Every applied change, before and after. |
| `runs\ai_dye_demo\YYYYMMDD_HHMMSS\session.json` | Session summary: operator, mode, revisions, runs. |
| `runs\ai_dye_demo\YYYYMMDD_HHMMSS\starting_config.yaml` | Configuration at session start. |
| `runs\ai_dye_demo\YYYYMMDD_HHMMSS\executed_config_runN.yaml` | Exact configuration used by run N. |
| `src\protocols\generated\ai_agent_dilution_print_demo_latest.py` | Generated deterministic OT-2 protocol. |

## Troubleshooting and stop conditions

Type `quit` before `run`, or press Ctrl+C, if:

- the displayed plan does not match the requested experiment;
- a proposal lists a change you did not ask for (**CHECK THESE**); type `no`;
- the deck, plate, paper, liquids, or tips do not match the plan;
- run 2 does not show dilution-making as skipped;
- the paper or plate moved between runs without being changed in the session;
- a required tip is missing or already used;
- robot discovery or simulation fails; or
- the robot behaves unexpectedly.

To stop a live run that has started, press Ctrl+C once. The runner sends the OT-2 a stop request for that run and waits
for the robot to report it stopped. If the agent says the stop was not confirmed, stop the run in the Opentrons App.
The run is recorded as aborted, nothing from it is recorded as done, and the next live `run` asks you to confirm the
robot was checked first. This stop path has been tested offline only; confirm it on the robot before relying on it.

Common messages:

| Message | Meaning and action |
|---|---|
| `LLM STARTUP FAILED` | No LLM connection. Check the network/VPN and `python -m src.agents.check_llm_auth`, then start again. |
| `You said "spot 7." Did you mean OT-2 deck SLOT 7?` | Answer yes, or no and restate with established terms (deck slot, plate well, paper position, vial, tip). |
| `Cannot apply that deck change yet.` | The target slot is occupied. Say where the other labware goes (a free slot or OFF DECK) in the same request. |
| `I did not change anything, because: ...` | The request was physically invalid, or a value was missing or contradicted; the reason is shown. Use the parameter table above. |
| `Proposal #N is still waiting: yes to apply it, no to discard it.` | Type yes or no; a new request discards the waiting proposal. |
| `That is not a clear yes, so nothing was applied.` | Type exactly `yes` to apply the proposal on screen. |
| `Before I change anything: ...` | The wording was ambiguous (which plate or rack, what a number means, a missing unit). Answer the question or say it again with established terms. |
| `your message did not mention the ...` | The interpretation touched a setting you did not name; nothing changed. Name the setting if you meant it. |
| `Nothing has started. Your last message was a hypothetical ...` | A go-ahead right after a "what if" question starts nothing. Type `run` by itself to run the current plan. |
| `Not running: you told me "...", and that is not in the record yet.` | Say how the robot is set up now (for example "the vial rack is back in slot 7"), or change the plan to match. |
| `You asked for paper columns ..., but this change would print ...` | The interpretation would have printed other paper columns; nothing was proposed. Answer `yes` to print exactly the columns you named (shown as a proposal first), or `no`. |
| `Run N did not finish (...). Have you checked the robot ...?` | The previous live run was interrupted or failed and nothing from it was recorded. Check the plate, paper and tips and update the plan before answering `yes`. |
