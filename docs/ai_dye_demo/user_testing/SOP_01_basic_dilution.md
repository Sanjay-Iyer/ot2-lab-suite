# SOP 01 — Basic Dilution Series

**Difficulty: 1/5** · about 10–15 minutes · simulation only (nothing contacts the robot)

Reference: [User Guide](USER_GUIDE_ai_dye_demo.md). You may open it at any time.

## 1. Objective

Plan a four-step, two-fold dilution series of the dye in water, get the agent to set it up exactly, and run the plan
in simulation.

## 2. Starting configuration

Start a new session from the repository root:

```powershell
conda activate ai
python scripts\ai_dye_demo.py --simulate --session-label "User test SOP 1"
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

The plan you run must have:

- four dilutions of the dye: **2×, 4×, 8× and 16×**;
- a final volume of **200 µL** in each dilution well (dye and water together);
- the dye taken from **vial B1**, and the water still from vial A1;
- everything else as it was at the start: the deck, plate column 11 starting at row A, the printing, and the tips.

## 4. Your task

Today the dye stock was loaded into vial B1 instead of its usual vial. Your supervisor needs a two-fold dilution
series of that dye in water: 2×, 4×, 8× and 16×, with 200 µL in each well so there is enough left for later work.

Tell the agent what you need, in your own words. Check what it proposes before you approve anything. When the plan is
right, run the simulation, then type `quit`.

## 5. Constraints

- Use the conversation only; do not edit files.
- Do not move any labware.
- Do not change the printing or the tip settings.
- Approve only proposals that show exactly what you intend.

## 6. Success criteria

The administrator checks the configuration used by your simulation run:

| # | Criterion |
|---|---|
| 1 | Dilution factors are 2×, 4×, 8×, 16×, in that order |
| 2 | Final volume per dilution is 200 µL |
| 3 | Dye (sample) vial is B1; water (solvent) vial is A1 |
| 4 | The dilutions are made in wells A11, B11, C11 and D11 |
| 5 | Deck, printing (one 5 µL drop of each dilution from paper column 1) and tips (A1, one tip per liquid) are unchanged |
| 6 | The simulation run finished with exit code 0 |

## 7. Expected AI safeguards

- Every change appears as a numbered **PROPOSED PLAN**: the complete plan you get if you say yes, with anything to
  check under **ATTENTION**. Nothing is applied until you type `yes`.
- A number without a unit, or a number that could mean more than one thing, is asked about before anything changes.
- The agent does not choose values you did not give it.
- Questions are answered without changing anything.

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
