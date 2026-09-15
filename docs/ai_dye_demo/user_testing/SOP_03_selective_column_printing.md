# SOP 03 — Printing Into Selected Paper Rows and Columns

**Difficulty: 3/5** · about 20 minutes · simulation only (nothing contacts the robot)

Reference: [User Guide](USER_GUIDE_ai_dye_demo.md). You may open it at any time.

## 1. Objective

Make a short dilution series in a chosen place on the dilution plate, and print it only onto chosen paper rows and
paper columns, leaving the rest of the paper blank.

**Scope.** One run of this demo prints every dilution in the plan into every printed paper column, so different
dilutions cannot be sent to different paper columns in the same run. This SOP needs one run.

## 2. Starting configuration

Start a new session from the repository root:

```powershell
conda activate ai
python scripts\ai_dye_demo.py --simulate --session-label "User test SOP 3"
```

Wait for `connection established`, then type your name when asked **Who is running this experiment?**

The session starts from the lab's standard example plan:

| Item | At the start |
|---|---|
| Deck | 96-well dilution plate in slot 4, paper print plate in slot 5, vial rack in slot 7, P20 tip rack in slot 9; slots 1, 2, 3, 6, 8, 10 and 11 empty |
| Liquids | water in vial A1, dye in vial A2 |
| Dilutions | 8 dilutions (1×, 2×, 3×, 4×, 6×, 8×, 12×, 16×), 150 µL each, made down plate column 11 starting at row A |
| Printing | one 5 µL drop of each dilution, starting at paper column 1 |
| Tips | first tip A1, one tip per liquid |

## 3. Required final state

The plan you run must:

- make three dilutions of the dye, **3×, 6× and 12×**, 150 µL each, in **column 3 of the dilution plate**;
- print the 3× dilution on **paper row D**, the 6× on **row E** and the 12× on **row F**;
- print each of them once in **paper column 3** and once in **paper column 4**, one 5 µL drop per spot;
- leave every other paper position blank;
- keep every piece of labware where it is, and the vials, mixing and tips as they were at the start.

## 4. Your task

Another group has already printed on the top rows and the left-hand columns of today's paper sheet, so your spots must go
lower down and further right. The 3× dilution goes on paper row D, the 6× on row E and the 12× on row F. Print each one
in paper column 3 and again in paper column 4, one 5 µL drop per spot. Make the three dilutions (3×, 6× and 12×,
150 µL each) in column 3 of the dilution plate.

Tell the agent what you need, in your own words. Check the plan before you run it. When the plan is right, run the
simulation, then type `quit`.

## 5. Constraints

- Use the conversation only; do not edit files.
- One run only. Do not move any labware.
- One 5 µL drop per paper position.
- Approve only proposals that show exactly what you intend.

## 6. Success criteria

The administrator checks the configuration used by your simulation run:

| # | Criterion |
|---|---|
| 1 | Dilution factors are 3×, 6×, 12×; final volume 150 µL; dilution plate column 3 |
| 2 | The dilutions are made in plate wells D3 (3×), E3 (6×) and F3 (12×) |
| 3 | Printing starts at paper column 3 with 2 side-by-side paper columns, 1 drop of 5 µL per position |
| 4 | Print steps go FROM plate well D3 TO paper positions D3 and D4, FROM E3 TO E3 and E4, FROM F3 TO F3 and F4 (6 drops) |
| 5 | Deck unchanged (dilution plate slot 4, paper print plate slot 5, vial rack slot 7, tip rack slot 9); vials, mixing and tips unchanged |
| 6 | The simulation run finished with exit code 0 |

## 7. Expected AI safeguards

- Words that can mean different things (deck slot, plate column, paper column, row, well, paper position; which plate;
  a number on its own) are clarified before anything changes.
- A proposal that moves labware shows the resulting **DECK**, and the physical move to make under **ATTENTION**.
- A plan that does not fit on the plate or the paper is refused, with the reason.
- Nothing is applied until you type `yes`.

## 8. Tester observations

*Completed by the observer. The session log is `runs\ai_dye_demo\<session>\turns.jsonl` (one line per message).*

| Observation | Notes |
|---|---|
| Hesitations (where, how long) | |
| Terminology that confused the tester (slot / column / row / well / position) | |
| Incorrect or unsupported requests typed | |
| Clarification questions the agent asked | |
| Requests the agent rejected, and why | |
| Wrong proposals approved | |
| User Guide opened? Which section? | |
| Recovered without assistance? | Yes / No |
| Total messages typed | |
| Final result (checker output attached) | PASS / FAIL |
