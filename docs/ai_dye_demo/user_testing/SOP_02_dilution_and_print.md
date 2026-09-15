# SOP 02 — Dilutions, Then Printing

**Difficulty: 2/5** · about 15 minutes · simulation only (nothing contacts the robot)

Reference: [User Guide](USER_GUIDE_ai_dye_demo.md). You may open it at any time.

## 1. Objective

Prepare three dilutions of the dye and print every one of them onto paper as a stacked double drop, in one run.

## 2. Starting configuration

Start a new session from the repository root:

```powershell
conda activate ai
python scripts\ai_dye_demo.py --simulate --session-label "User test SOP 2"
```

Wait for `connection established`, then type your name when asked **Who is running this experiment?**

The session starts from the lab's standard example plan:

| Item | At the start |
|---|---|
| Deck | 96-well dilution plate in slot 4, paper print plate in slot 5, vial rack in slot 7, P20 tip rack in slot 9 |
| Liquids | water in vial A1, dye in vial A2 |
| Dilutions | 8 dilutions (1×, 2×, 3×, 4×, 6×, 8×, 12×, 16×), 150 µL each, made down plate column 11 starting at row A |
| Printing | one 5 µL drop of each dilution, starting at paper column 1 |
| Tips | first tip A1, one tip per liquid |

## 3. Required final state

The plan you run must:

- make three dilutions of the dye, **5×, 10× and 20×**, each with the standard **150 µL** final volume, in the usual
  place on the dilution plate (plate column 11, starting at row A);
- print every dilution as **two 5 µL drops stacked on the same paper position**, in **paper column 2**;
- leave paper column 1 blank;
- make the dilutions and print them in the same run;
- keep the deck, the vials, the mixing and the tips as they were at the start.

## 4. Your task

You want to compare three strengths of the dye on paper. Make a 5×, a 10× and a 20× dilution; the standard 150 µL each
is fine. Print each one as a double spot, with two 5 µL drops on the same position, in the second column of the paper.
The first paper column must stay empty.

Tell the agent what you need, in your own words. Before you run it, check where each print comes **from** and where it
lands. When the plan is right, run the simulation, then type `quit`.

## 5. Constraints

- Use the conversation only; do not edit files.
- One run only. Do not move any labware.
- Keep the drop volume at 5 µL.
- Approve only proposals that show exactly what you intend.

## 6. Success criteria

The administrator checks the configuration used by your simulation run:

| # | Criterion |
|---|---|
| 1 | Dilution factors are 5×, 10×, 20×; final volume 150 µL; wells A11, B11, C11 |
| 2 | The dilution step runs in the same run (it is not skipped) |
| 3 | Print steps go FROM plate well A11 TO paper position A2, FROM B11 TO B2, FROM C11 TO C2 |
| 4 | Two 5 µL drops per paper position, one paper column (6 drops in total) |
| 5 | Deck, vials, mixing and tips are unchanged (tips A1–E1 are used) |
| 6 | The simulation run finished with exit code 0 |

## 7. Expected AI safeguards

- Printing always uses the dilutions in the current plan; a request about dilutions the plan does not contain is
  explained, not carried out.
- The agent skips making dilutions only when they are recorded as already made, and asks before doing so.
- A setting your message never mentioned is flagged under **CHECK THESE**, or refused when nothing else was asked.
- Each proposal is the complete resulting plan: dilutions, paper columns, print positions, drops, printed volume and
  tips.

## 8. Tester observations

*Completed by the observer. The session log is `runs\ai_dye_demo\<session>\turns.jsonl` (one line per message).*

| Observation | Notes |
|---|---|
| Hesitations (where, how long) | |
| Terminology that confused the tester | |
| Incorrect or unsupported requests typed | |
| Clarification questions the agent asked | |
| Requests the agent rejected, and why | |
| Wrong proposals approved | |
| User Guide opened? Which section? | |
| Recovered without assistance? | Yes / No |
| Total messages typed | |
| Final result (checker output attached) | PASS / FAIL |
