# SOP Tester Answer Key — AI Dye Demo User Tests

> **Administrator only.** Do not give this document or the `expected/` folder to testers.

Companion files:

- machine-readable final states: [expected/](expected/);
- SOP 5 starting configuration: `configs\workflows\user_test_sops\ai_dye_demo_sop05_start.yaml`;
- scorer: `scripts\check_ai_dye_demo_sop.py`.

All values below come from the demo's own plan code (`src/agents/dye_demo/plan.py`) and were checked in simulation
(section 7).

---

## 1. Running a test session

1. On the test laptop, from the repository root, run `conda activate ai`.
2. Give the tester **one SOP and the User Guide**, nothing else.
3. The tester types the start command in section 2 of the SOP; SOP 5 has its own `--config`. Note the session folder
   printed at startup (`Session log : runs\ai_dye_demo\<timestamp>\session.log`).
4. Observe without helping, and fill in the observations table. Note when the tester opens the guide, and which section.
5. If the tester is stuck for more than about 5 minutes, you may help. Record the result as *assisted*.
6. After the tester types `quit`, score the session (section 2).

Every message is recorded in `runs\ai_dye_demo\<timestamp>\turns.jsonl`, one line per message, with its
classification (question, instruction, confirmation, ...), proposals, approvals and runs. The number of lines is the
number of messages typed. Each simulated `run` also writes `src\protocols\generated\ai_agent_dilution_print_demo_run_<timestamp>.py`
and refreshes `..._latest.py`, as every simulation of this demo does.

## 2. Scoring

```powershell
python scripts\check_ai_dye_demo_sop.py --sop 1 --session runs\ai_dye_demo\20260911_101500
```

- **PASS** means every requirement is met (exit code 0). The scorer checks the last successful run of the session (the
  last two, in order, for SOP 4).
- It checks every conversation-editable setting (display names excepted), that lab-owned settings are those of the
  starting configuration, and the plan the configuration produces: source wells, FROM/TO print steps with drops,
  transfers, total drops and tips.
- If the tester never typed `run`, check the approved plan with
  `--config configs\workflows\user\ai_dye_demo_<timestamp>.yaml` and record **FAIL (not run)**.
- Record **PASS (assisted)** separately from **PASS**.

## 3. Simulation and live behaviour

The SOPs run in simulation. Some agent behaviour depends on the mode:

| In simulation (these SOPs) | In a live run on the robot |
|---|---|
| After a run: "Simulation only: no tips, liquid or paper were used." | After a successful run the agent records the dilutions it made ("Dilutions now exist in plate wells ...") and proposes the next unused tip as the starting tip |
| The agent does not know that a simulated run made dilutions or used tips; the tester has to say so (SOP 4) | A run that would make recorded dilutions again is refused ("Not running: ...") |
| No deck confirmation | After a deck change: "Is the physical deck arranged exactly as the ACTIVE DECK above?" |
| No prepared-dilution confirmation | A print-only run asks "Do plate wells ... already hold the dilutions?" |

Dilutions the tester *reports* as made are recorded, and block a run that would make them again, in both modes.

---

## 4. SOP 1 — Basic dilution series (1/5)

### Final state

| Setting | Value | | Setting | Value |
|---|---|---|---|---|
| Dilution plate location | Slot 4 | | Drop volume | 5 µL |
| Paper print plate location | Slot 5 | | Drops per paper position | 1 |
| Vial rack location | Slot 7 | | Replicate paper columns | 1 |
| P20 tip rack location | Slot 9 | | First paper column | 1 |
| **Dye (sample) vial** | **B1** | | Mixes before each print | 2 |
| Water (solvent) vial | A1 | | Mixing volume | 15 µL |
| Make dilutions in this run | yes | | Print in this run | yes |
| **Dilution factors** | **2×, 4×, 8×, 16×** | | Starting tip | A1 |
| Dilution plate column | 11 | | Tip policy | one tip per liquid |
| Dilution start row | A | | Return used tips to the rack | no |
| **Final volume per dilution** | **200 µL** | | Volume now in each prepared well | (not set) |

Changes from the start: dye vial, dilution factors, final volume.

### Dilution plan

| Plate well | Fold | Dye | Water |
|---|---|---|---|
| A11 | 2× | 100 µL | 100 µL |
| B11 | 4× | 50 µL | 150 µL |
| C11 | 8× | 25 µL | 175 µL |
| D11 | 16× | 12.5 µL | 187.5 µL |

- Water: FROM vial rack (slot 7) vial A1 TO dilution plate (slot 4) wells A11–D11. 612.5 µL in 32 transfers, tip A1.
- Dye: FROM vial rack (slot 7) vial B1 TO wells A11–D11. 187.5 µL in 11 transfers, tip B1.
- Load at least 2.65 mL of dye in B1 and 3.08 mL of water in A1 (plan → LIQUIDS).

### Printing plan

| Step | FROM plate well | TO paper position | Drops | Tip |
|---|---|---|---|---|
| 1 | A11 (2×) | A1 | 1 × 5 µL | C1 |
| 2 | B11 (4×) | B1 | 1 × 5 µL | D1 |
| 3 | C11 (8×) | C1 | 1 × 5 µL | E1 |
| 4 | D11 (16×) | D1 | 1 × 5 µL | F1 |

4 drops, 20 µL printed. Each well holds 200 µL before printing and 195 µL after.

### Tips and operations

- 6 tips, A1–F1; next unused tip G1; 90 remaining.
- 43 dilution transfers (each followed by a blow-out), 4 print steps (mix, then drop), 4 drops, 6 tip pick-ups.

### Expected agent events

- Clean path: one proposal with three changes (Dye (sample) vial A2 -> B1; Dilution factors 1×, 2×, 3×, 4×, 6×, 8×,
  12×, 16× -> 2×, 4×, 8×, 16×; Final volume per dilution 150 µL -> 200 µL). `yes` applies it; then `run`.
- One change at a time gives three proposals and three `yes` answers.

### Likely mistakes

| Mistake | What the agent does | Way back |
|---|---|---|
| "Make 4 dilutions" without naming factors | Proposes **1×, 2×, 3×, 4×**, the first four factors of the current series; approving it applies the wrong factors | `no`, or give the factors afterwards |
| A volume without a unit ("make each one 200") | "You gave 200 without a unit, so I did not assume one." then "Do you mean 200 µL for the final volume per dilution?" | `yes` |
| Asking for a "serial dilution" | Refused: each dilution is made directly from the stock; suggests giving the fold factors | Give the factors |
| "Use vial 5 for the dye" | Asks: the vial rack in deck slot 5, or vial B1 (vial number 5) | Choose vial B1 |
| Treating 200 µL as the dye volume | The proposal says **Final volume per dilution**; STEP 1 shows dye and water per well | Read STEP 1 |
| Approving an unrelated change (deck, printing, tips) | The proposal lists it; the SOP fails if it is applied | `no`, or `Undo my last change.` |

**Pass:** the scorer passes. **Typical fails:** factors 1×–4×; dye vial still A2; volume 150 µL; printing or tips changed.

### Validated reference conversation

```text
you>     I need four dilutions of the dye in water: 2x, 4x, 8x and 16x, with a final volume of 200 uL in each well. The dye stock is in vial B1 today.
confirm> yes
you>     plan
you>     run
```

---

## 5. SOP 2 — Dilutions, then printing (2/5)

### Final state

As the starting plan, except: **Dilution factors 5×, 10×, 20×**; **Drops per paper position 2**; **First paper column 2**.
The dye vial stays A2, the final volume 150 µL, plate column 11 from row A, drop volume 5 µL, one replicate, tips A1 with
one tip per liquid.

### Dilution plan

| Plate well | Fold | Dye | Water |
|---|---|---|---|
| A11 | 5× | 30 µL | 120 µL |
| B11 | 10× | 15 µL | 135 µL |
| C11 | 20× | 7.5 µL | 142.5 µL |

- Water: FROM vial A1 TO wells A11–C11. 397.5 µL in 21 transfers, tip A1.
- Dye: FROM vial A2 TO wells A11–C11. 52.5 µL in 4 transfers, tip B1.
- Load at least 2.52 mL of dye and 2.86 mL of water.

### Printing plan

| Step | FROM plate well | TO paper position | Drops | Tip |
|---|---|---|---|---|
| 1 | A11 (5×) | A2 | 2 × 5 µL | C1 |
| 2 | B11 (10×) | B2 | 2 × 5 µL | D1 |
| 3 | C11 (20×) | C2 | 2 × 5 µL | E1 |

6 drops, 30 µL printed. Wells hold 150 µL before printing and 140 µL after. Paper column 1 is untouched.

### Tips and operations

- 5 tips, A1–E1; next unused tip F1.
- 25 transfers, 3 print steps, 6 drops, 5 tip pick-ups.

### Expected agent events

- Clean path: one proposal (Dilution factors -> 5×, 10×, 20×; Drops per paper position 1 -> 2; First paper column
  1 -> 2).

### Likely mistakes

| Mistake | What the agent does | Way back |
|---|---|---|
| Asking to print the 5×, 10× and 20× dilutions before setting the factors | "The current plan has no 5×, 10×, 20× dilutions (it makes 1×, 2×, 3×, 4×, 6×, 8×, 12×, 16×). Printing always uses the dilutions in the plan ... set the dilution factors first" | Set the factors, then the printing |
| "Skip the dilutions and just print" | Refused: "nothing in this session records that those wells already hold the dilutions"; asks "Do plate wells A11-H11 already hold the dilutions in the current plan?" | `no` keeps the dilution step |
| Saying the dilutions are already made (untrue) | A reconciliation proposal turns the dilution step off. If applied, the run only prints (FAIL). `Undo my last change.` restores the setting but not the record, and `run` is then refused ("Not running: Plate wells ... already hold dilutions ...") | "I replaced the dilution plate with a new empty one." then `yes` |
| "Print each dilution twice" | A model reading of *replicates* is refused ("your message did not mention the replicate paper columns") or flagged under CHECK THESE. Two replicate columns would print columns 2 and 3 (FAIL) | "two drops stacked on the same spot" |
| "Print 10 µL" meaning two 5 µL drops | Proposes a 10 µL drop volume (FAIL) | Drops per position instead |
| "column 2" in a sentence about both dilutions and printing | Asks: PAPER column 2 or PLATE column 2. PLATE moves the dilutions (FAIL) | PAPER |

**Typical fails:** printing from column 1 or 3; one drop per position; two replicate columns; dilution step skipped.

### Validated reference conversation

```text
you>     Make three dilutions, 5x, 10x and 20x, and print two 5 uL drops of each one stacked on the same spot, starting in paper column 2.
confirm> yes
you>     run
```

---

## 6. SOP 3 — Printing into selected paper rows and columns (3/5)

### Final state

As the starting plan, except: **Dilution factors 3×, 6×, 12×**; **Dilution plate column 3**; **Dilution start row D**;
**Replicate paper columns 2**; **First paper column 3**. Deck unchanged: slot 3 stays empty.

### Dilution plan

| Plate well | Fold | Dye | Water |
|---|---|---|---|
| D3 | 3× | 50 µL | 100 µL |
| E3 | 6× | 25 µL | 125 µL |
| F3 | 12× | 12.5 µL | 137.5 µL |

- Water: FROM vial A1 TO wells D3–F3. 362.5 µL in 19 transfers, tip A1.
- Dye: FROM vial A2 TO wells D3–F3. 87.5 µL in 6 transfers, tip B1.
- Load at least 2.55 mL of dye and 2.83 mL of water.

### Printing plan

| Step | FROM plate well | TO paper position | Drops | Tip |
|---|---|---|---|---|
| 1 | D3 (3×) | D3 | 1 × 5 µL | C1 |
| 2 | D3 (3×) | D4 | 1 × 5 µL | C1 |
| 3 | E3 (6×) | E3 | 1 × 5 µL | D1 |
| 4 | E3 (6×) | E4 | 1 × 5 µL | D1 |
| 5 | F3 (12×) | F3 | 1 × 5 µL | E1 |
| 6 | F3 (12×) | F4 | 1 × 5 µL | E1 |

6 drops, 30 µL printed. Plate well D3 prints onto paper position D3: the same name on different labware. Wells hold
150 µL before printing and 140 µL after.

### Tips and operations

- 5 tips, A1–E1 (one per dilution row when printing); next unused tip F1.
- 25 transfers, 6 print steps, 6 drops, 5 tip pick-ups.

### Expected agent events

- Clean path: one proposal with five changes. Setting the start row to D while the series still has 8 dilutions is
  refused: "8 dilutions starting at row D run past row H".

### Likely mistakes

| Mistake | What the agent does | Way back |
|---|---|---|
| "Put the dilutions in slot 3" | "You did not name the 96-well dilution plate, so I did not move it to slot 3. Deck slots hold labware. If you meant a column, say "plate column 3" (where the dilutions are made) or "paper column 3" (where the drops print)." Then asks which labware should go to slot 3 | "plate column 3" |
| "Move the plate to slot 3" | Asks which plate. For the dilution plate it proposes a real deck move (slot 3 is empty), with CURRENT and PROPOSED deck. Approving it fails the SOP | `no` |
| Start row D before shortening the series | Refused: "8 dilutions starting at row D run past row H" | Change the factors first, or both together |
| Not realising paper rows follow plate rows (dilutions left in rows A–C) | Nothing to catch: the plan prints rows A–C (FAIL). STEP 2 shows TO positions A3... | Start the series at row D |
| "column 3" in a sentence about both plate and paper | Asks: PAPER column 3 or PLATE column 3 | Answer the question |
| "Print in paper columns 3 and 5" | "Paper columns 3, 5 are not side by side ... leaving a gap takes two runs" | Adjacent columns |
| "Print in 3 and 4" without *column* | Asks "How many side-by-side replicate paper columns should each drop volume print?" | `2` |
| Three replicate columns, or "columns 3 to 5" | Accepted as valid; prints column 5 as well (FAIL) | Two replicate columns |
| Different dilutions into different paper columns | Explained as unsupported: every dilution prints into every printed column | Not needed for this SOP |

**Typical fails:** dilutions in rows A–C; plate column 11; deck plate moved to slot 3; wrong replicate count.

### Validated reference conversation

```text
you>     Make 3x, 6x and 12x dilutions in plate column 3, starting at row D, and print one 5 uL drop of each in paper columns 3 and 4.
confirm> yes
you>     run
```

---

## 7. SOP 4 — Two printings with different drop counts (4/5)

### Final state: first run

As the starting plan, except: **Dilution factors 4×, 8×, 16×, 32×**; **Final volume per dilution 200 µL**;
**Dilution plate column 6**; **Replicate paper columns 2**. First paper column 1, one drop, first tip A1.

### Final state: second run

As the first run, except: **Make dilutions in this run: no** (with the prepared-dilution record for wells A6–D6);
**Volume now in each prepared well 190 µL** (the scorer accepts 180–190 µL); **Drops per paper position 3**;
**First paper column 4**; **Starting tip G1** (the scorer accepts G1 or any later tip, provided none of A1–F1 is used).
Replicates stay 2; tip policy stays one tip per liquid.

### Dilution plan (first run only)

| Plate well | Fold | Dye | Water |
|---|---|---|---|
| A6 | 4× | 50 µL | 150 µL |
| B6 | 8× | 25 µL | 175 µL |
| C6 | 16× | 12.5 µL | 187.5 µL |
| D6 | 32× | 6.25 µL | 193.75 µL |

- Water: FROM vial A1 TO wells A6–D6. 706.25 µL in 37 transfers, tip A1.
- Dye: FROM vial A2 TO wells A6–D6. 93.75 µL in 7 transfers, tip B1.
- Load at least 2.56 mL of dye and 3.17 mL of water.

### Printing plan

| Run | FROM plate well | TO paper positions | Drops per position | Tip |
|---|---|---|---|---|
| 1 | A6 (4×) | A1, A2 | 1 × 5 µL | C1 |
| 1 | B6 (8×) | B1, B2 | 1 × 5 µL | D1 |
| 1 | C6 (16×) | C1, C2 | 1 × 5 µL | E1 |
| 1 | D6 (32×) | D1, D2 | 1 × 5 µL | F1 |
| 2 | A6 (4×) | A4, A5 | 3 × 5 µL | G1 |
| 2 | B6 (8×) | B4, B5 | 3 × 5 µL | H1 |
| 2 | C6 (16×) | C4, C5 | 3 × 5 µL | A2 |
| 2 | D6 (32×) | D4, D5 | 3 × 5 µL | B2 |

- Run 1: 8 drops, 40 µL printed; each well 200 µL → 190 µL.
- Run 2: 24 drops, 120 µL printed; each well 190 µL → 160 µL.
- Paper column 3 is never printed.

### Tips and operations

| | Transfers | Print steps | Drops | Tips | Next unused tip |
|---|---|---|---|---|---|
| Run 1 | 44 | 8 | 8 | 6 (A1–F1) | G1 |
| Run 2 | 0 (dilution step SKIPPED, no vial aspirations) | 8 | 24 | 4 (G1, H1, A2, B2) | C2 |

### Expected agent events

- Asking for both drop counts at once: "One run uses the same number of drops in every paper column; different drop
  counts per column need two runs."
- Reporting the dilutions before run 2 shows a plan whose DILUTIONS section is **SKIPPED - already in the plate**, and
  under ATTENTION **Saying yes records plate wells A6-D6 as already holding the dilutions (4× | 8× | 16× | 32×; made at
  200 µL each; reported by the operator).** with the warning "this run does not make dilutions: it assumes plate
  wells A6-D6 already hold them (190 µL each)".
- In simulation nothing prompts for the starting tip; `tips` shows "Next unused tip after: G1" before run 1.

### Likely mistakes

| Mistake | What the agent does | Way back |
|---|---|---|
| Both drop counts in one message | Explains that two runs are needed | Plan the runs separately |
| Planning the second printing with the dilution step still on | In simulation nothing stops it. The proposal's DILUTIONS section says "made in this run" with 4 dilutions; running it fails the SOP (the dilutions would be made again). A live run would be refused | Report the dilutions as made before the second run |
| Not setting a starting tip | The second run uses A1–D1 again (PIPETTING shows it). FAIL | Start from G1 |
| Not recording the volume left in each well | The plan assumes the full 200 µL; the scorer fails "volume now in each prepared well" | Say how much is left |
| "Three drops" set as three replicate columns, or "columns 4 to 6" | Valid but wrong (FAIL) | Drops per position 3, replicate columns 2 |
| Changing replicates to 1, or the first paper column to 3 | Valid but wrong (FAIL) | |
| "Use new tips" read as the tip policy | 8 print tips instead of 4 (FAIL: the policy must stay one tip per liquid) | Starting tip instead |
| Reporting different dilutions than were made | Records wrong wells or factors, or refuses: "This print-only plan does not match the dilutions recorded as prepared" | Report them as made |

### Validated reference conversation (several changes at once)

```text
you>     Make four dilutions, 4x, 8x, 16x and 32x, 200 uL each, in plate column 6, and print one 5 uL drop of each in paper columns 1 and 2.
confirm> yes
you>     run
you>     The dilutions from run 1 are already made and each well now holds about 190 uL. Print three stacked drops in paper columns 4 and 5, starting from tip G1.
confirm> yes
you>     run
```

---

## 8. SOP 5 — Rearranging the deck for a full experiment (5/5)

### Final state

| Setting | Value |
|---|---|
| **Dilution plate location** | **Slot 8** |
| **P20 tip rack location** | **slot 1, 2, 3, 6, 9, 10 or 11** (tester's choice; not 4, not OFF DECK) |
| Paper print plate location | Slot 5 |
| Vial rack location | Slot 7 |
| **Dilution factors** | **3×, 9×, 27×** |
| **Final volume per dilution** | **180 µL** |
| **Drops per paper position** | **2** |
| **Replicate paper columns** | **2** |
| **First paper column** | **6** |
| **Starting tip** | **A2** |
| **Tip policy** | **new tip every transfer** |
| Everything else | as at the start (vials A1/A2, plate column 11 from row A, 5 µL drops, mixing 2× 15 µL, tips not returned) |

Final deck (with the tip rack in slot 11, for example): slot 4 empty, dilution plate 8, paper print plate 5, vial
rack 7, tip rack 11. After the deck proposal is approved the agent says: "Now physically move the 96-well dilution
plate from Slot 4 to Slot 8; move the P20 tip rack from Slot 8 to Slot 11."

### Dilution plan

| Plate well | Fold | Dye | Water |
|---|---|---|---|
| A11 | 3× | 60 µL | 120 µL |
| B11 | 9× | 20 µL | 160 µL |
| C11 | 27× | 6.67 µL | 173.33 µL |

- Water: FROM vial A1 TO wells A11–C11. 453.33 µL in 23 transfers, a new tip for each.
- Dye: FROM vial A2 TO wells A11–C11. 86.67 µL in 5 transfers, a new tip for each.
- Load at least 2.55 mL of dye and 2.92 mL of water.

### Printing plan

| FROM plate well | TO paper positions | Drops per position | Tips |
|---|---|---|---|
| A11 (3×) | A6, A7 | 2 × 5 µL | one new tip per position |
| B11 (9×) | B6, B7 | 2 × 5 µL | one new tip per position |
| C11 (27×) | C6, C7 | 2 × 5 µL | one new tip per position |

12 drops, 60 µL printed. Wells hold 180 µL before printing and 160 µL after.

### Tips and operations

- 34 tips (28 transfers + 6 printed positions): A2–H2, A3–H3, A4–H4, A5–H5, A6, B6. Next unused tip C6; 54 remain of
  the 88 from A2.
- 28 transfers, 6 print steps, 12 drops, 34 tip pick-ups.

### Expected agent events

Moving the dilution plate to slot 8 on its own:

```text
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! ATTENTION !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  Cannot apply that deck change yet. Nothing was changed.

  Requested:
    96-well dilution plate to Slot 8 (now in Slot 4)

  Conflict:
    Slot 8 is currently occupied by the P20 tip rack.
    I need a new location for the P20 tip rack before the 96-well dilution plate can move to Slot 8.

  Valid locations:
    Slots 1-11 that are currently unoccupied: 1, 2, 3, 6, 9, 10, 11
    Slot(s) 4 also become free if the requested move goes ahead
    Not OFF DECK: every step needs P20 tips.

  Say where it should go, in one request, for example:
    "move the tip rack to slot 1 and the dilution plate to slot 8"
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
```

Giving both moves in one request produces one proposal showing the resulting deck, with both moves under ATTENTION. Moving the tip rack first
(its own proposal) and then the plate also works.

### Likely mistakes

| Mistake | What the agent does | Way back |
|---|---|---|
| "Move the plate to slot 8" | Asks which plate: the dilution plate (slot 4) or the paper print plate (slot 5) | Choose the dilution plate |
| Moving the plate before freeing slot 8 | The conflict message above | Both moves in one request, or the tip rack first |
| Tip rack OFF DECK | Refused: "the P20 tip rack is OFF DECK, but every step needs P20 tips" | A free slot |
| Tip rack into slot 4 (a swap) | **Accepted**: the deck is valid, and the agent cannot know the lab needs slot 4 empty. FAIL unless corrected | Move the tip rack to a free slot |
| Tip rack into slot 5 or 7 | Conflict: the slot is occupied | A free slot |
| Tips: "the first row is used, start at B1", or "start at tip 9" | B1 is accepted (a valid tip, but already used: FAIL). "tip 9" is refused (tips are A1–H12) | Start at A2: tips are used in column order |
| "Use new tips" meant as a new starting tip, or the other way round | The proposal names the field changed: Starting tip or Tip policy | Check the proposal |
| "the rack", "move it to 8" | Asks which rack or which labware, then whether "8" is deck slot 8 | Name it |
| "180" without a unit | Asks "Do you mean 180 µL for the final volume per dilution?" | `yes` |
| Moving the paper print plate or the vial rack | Valid deck, but the SOP fails | `Undo my last change.` |

### Validated reference conversation (several changes at once)

```text
you>     Move the tip rack to slot 10 and the dilution plate to slot 8, make 3x, 9x and 27x dilutions at 180 uL each, print two stacked 5 uL drops of each in paper columns 6 and 7, start at tip A2 because the first column of tips is used, and use a new tip for every transfer.
confirm> yes
you>     run
```

---

## 9. Requests the demo does not support

Testers may try these. Nothing changes, and the agent explains:

| Request | Agent response |
|---|---|
| A serial dilution (well to well) | Each dilution is made directly from the dye stock; give the fold factors instead |
| Printing only one, or only some, of the dilutions in the plan | "This demo prints every dilution in the plan, one paper row each; it cannot print only one or some of them." If the named dilutions are not in the plan, it says so instead |
| Sending different dilutions to different paper columns in one run | Every dilution prints into every printed paper column (as above) |
| Different drop counts in different paper columns of one run | "... different drop counts per column need two runs." |
| Paper columns with a gap in one run (for example 1 and 3) | "Paper columns 1, 3 are not side by side ... leaving a gap takes two runs" |
| A second dye | One dye and one diluent only |
| Reversing the liquid path (drawing from the paper, returning liquid to the vials) | The path vials → dilution plate → paper is fixed |
| Changing heights, air gap, push-out, blow-out, dwell, flow rates, the pipette | Refused as lab-owned, with the reason |
| More than 8 dilutions, or printing past paper column 12 | Refused with the limit |

---

## 10. How the SOPs were validated

All on the work laptop, in simulation. **Nothing was run on, or connected to, the physical OT-2.**

| Check | Result |
|---|---|
| Every expected final state built through `ExperimentState` (propose, validate, apply) | all 5 SOPs (6 runs) valid; plans as in this key |
| Protocol v19 on the recording fake OT-2 for each end state | tips, transfers and drops match the plan |
| Scripted user paths replayed through the real conversation logic (`src/agents/dye_demo/redteam/sop_paths.py`) | 22 paths, 223 messages, 0 invariant violations; every run meets `expected/` |
| Paths per SOP | SOP 1–3: clean, confused, change of mind, questions. SOP 4: one change at a time, several changes at once, confused, change of mind, questions. SOP 5: clean, confused, change of mind, several changes at once, questions |
| Pinned opentrons 7.0.2 simulator (`--simulate-states`), each distinct SOP end state | 7 states, all OK (table below) |
| `tests\test_ai_dye_demo_user_test_sops.py` together with the other dye-demo test files | all passed |
| Scripted red-team campaigns after the conversation fixes made for these SOPs | 600 conversations / 10,500 turns (seed 909) and 2,000 conversations / 35,000 turns (seed 1212), 0 invariant violations |

| Simulated state | Tip pick-ups | Vial aspirations | Paper drops |
|---|---|---|---|
| SOP 1 | 6 | 43 | 4 |
| SOP 2 | 5 | 25 | 6 |
| SOP 3 | 5 | 25 | 6 |
| SOP 4, first run | 6 | 44 | 8 |
| SOP 4, second run | 4 | 0 | 24 |
| SOP 5, tip rack in slot 11 | 34 | 28 | 12 |
| SOP 5, tip rack in slot 10 | 34 | 28 | 12 |

The scripted paths use recorded, structured model replies (what a faithful model returns), so they show that the
deterministic layer turns reasonable readings into the right plan and catches wrong ones. A live model may read some
wording differently. The deterministic checks (stated values, units, named labware, deck conflicts, prerequisites,
explicit `yes`) still apply.
