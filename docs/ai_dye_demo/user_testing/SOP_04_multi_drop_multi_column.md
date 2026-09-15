# SOP 04 — Two Printings With Different Drop Counts

**Difficulty: 4/5** · about 25–30 minutes · simulation only (nothing contacts the robot)

Reference: [User Guide](USER_GUIDE_ai_dye_demo.md). You may open it at any time.

## 1. Objective

Make one four-point dilution series, then print it twice onto the same sheet of paper: single drops in two paper
columns, then three stacked drops in two other paper columns. The dilutions are made only once, and no tip is used
twice.

## 2. Starting configuration

Start a new session from the repository root, and stay in that one session for the whole SOP:

```powershell
conda activate ai
python scripts\ai_dye_demo.py --simulate --session-label "User test SOP 4"
```

Wait for `connection established`, then type your name when asked **Who is running this experiment?**

The session starts from the lab's standard example plan:

| Item | At the start |
|---|---|
| Deck | 96-well dilution plate in slot 4, paper print plate in slot 5, vial rack in slot 7, P20 tip rack in slot 9 |
| Liquids | water in vial A1, dye in vial A2 |
| Dilutions | 8 dilutions (1×, 2×, 3×, 4×, 6×, 8×, 12×, 16×), 150 µL each, made down plate column 11 starting at row A |
| Printing | one 5 µL drop of each dilution, starting at paper column 1 |
| Tips | first tip A1, one tip per liquid; the rack is full |

## 3. Required final state

Two simulation runs, in this order, in the same session.

**First run** (makes the dilutions and prints them):

- four dilutions of the dye, **4×, 8×, 16× and 32×**, **200 µL** each, in **column 6 of the dilution plate**;
- **one 5 µL drop** of each dilution in **paper columns 1 and 2**.

**Second run** (prints again from the same wells):

- the dilutions are **not made again**; the agent's record shows that the wells already hold them, and how much liquid
  is now left in each well;
- **three 5 µL drops stacked** on each paper position, in **paper columns 4 and 5**;
- **no tip that the first run used** is used again.

**Both runs:** paper column 3 stays blank; the deck, the vials and the mixing stay as they were at the start.

## 4. Your task

You are testing how the number of drops affects spot intensity. Make one series of four dilutions, 4×, 8×, 16× and
32×, 200 µL each, in column 6 of the dilution plate, and print it twice on the same sheet:

1. first, a single 5 µL drop per spot in paper columns 1 and 2;
2. then, from the same wells and without making the dilutions again, three stacked 5 µL drops per spot in paper
   columns 4 and 5.

Paper column 3 stays empty between the two blocks. Tips are precious: the second printing must not use any tip the
first printing used.

Treat each simulation run as if it were real: once the first run has finished, the dilutions exist in the plate, some
of each has been printed, and the tips it used are gone.

Tell the agent what you need, in your own words; you may make several changes in one message or one at a time. Check
each plan before you run it. After the second run, type `quit`.

## 5. Constraints

- Use the conversation only; do not edit files. Stay in one session.
- Exactly two runs. Do not move any labware.
- Keep the drop volume at 5 µL.
- Approve only proposals that show exactly what you intend.

## 6. Success criteria

The administrator checks the configurations used by the last two simulation runs of the session:

| # | Criterion |
|---|---|
| 1 | Two successful simulation runs in the session |
| 2 | First run: factors 4×, 8×, 16×, 32×; 200 µL each; wells A6–D6; one 5 µL drop per position in paper columns 1 and 2; tips A1–F1 |
| 3 | Second run: the dilution step is skipped, wells A6–D6 are recorded as holding the dilutions, and the volume now in each well is recorded (180–190 µL; 190 µL expected) |
| 4 | Second run: three 5 µL drops per position in paper columns 4 and 5; print steps FROM A6 TO A4 and A5, FROM B6 TO B4 and B5, FROM C6 TO C4 and C5, FROM D6 TO D4 and D5 (24 drops) |
| 5 | Second run: 4 tips, none of A1–F1 |
| 6 | Deck, vials and mixing unchanged in both runs; both runs finished with exit code 0 |

## 7. Expected AI safeguards

- A request for different drop counts in different paper columns of one run is explained as needing two runs.
- The dilution step is skipped only when the dilutions are recorded as already made.
- The plan's **PIPETTING** section shows the tip start, the tips required and the next unused tip (`tips` shows more).
- Each proposal is the complete resulting plan: dilutions, paper columns, print positions, drops, printed volume and
  tips.
- Nothing is applied until you type `yes`, and `run` is refused while a proposal is still waiting.

## 8. Tester observations

*Completed by the observer. The session log is `runs\ai_dye_demo\<session>\turns.jsonl` (one line per message).*

| Observation | Notes |
|---|---|
| Hesitations (where, how long) | |
| Terminology that confused the tester (drops / replicates / columns) | |
| Incorrect or unsupported requests typed | |
| Clarification questions the agent asked | |
| Requests the agent rejected, and why | |
| Wrong proposals approved | |
| Did the tester tell the agent the dilutions already existed before the second run? | Yes / No |
| Did the tester check which tips the second run would use? | Yes / No |
| User Guide opened? Which section? | |
| Recovered without assistance? | Yes / No |
| Total messages typed | |
| Final result (checker output attached) | PASS / FAIL |
