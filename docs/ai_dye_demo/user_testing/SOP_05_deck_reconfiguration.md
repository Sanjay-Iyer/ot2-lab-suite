# SOP 05 — Rearranging the Deck for a Full Experiment

**Difficulty: 5/5** · about 30 minutes · simulation only (nothing contacts the robot)

Reference: [User Guide](USER_GUIDE_ai_dye_demo.md). You may open it at any time.

## 1. Objective

Rearrange the deck so that the dilution plate sits in a slot that is currently occupied, then set up and run a complete
dilution-and-print experiment with strict tip handling on a partly used tip rack.

## 2. Starting configuration

This SOP starts from its own configuration. Start a new session from the repository root:

```powershell
conda activate ai
python scripts\ai_dye_demo.py --simulate --config configs\workflows\user_test_sops\ai_dye_demo_sop05_start.yaml --session-label "User test SOP 5"
```

Wait for `connection established`, then type your name when asked **Who is running this experiment?**

The deck at the start, seen from the front of the robot:

```text
                back of the robot
+----------------+----------------+----------------+
| Slot 10        | Slot 11        | Slot 12        |
| empty          | empty          | trash          |
+----------------+----------------+----------------+
| Slot 7         | Slot 8         | Slot 9         |
| vial rack      | P20 tip rack   | empty          |
+----------------+----------------+----------------+
| Slot 4         | Slot 5         | Slot 6         |
| 96-well        | paper print    | empty          |
| dilution plate | plate          |                |
+----------------+----------------+----------------+
| Slot 1         | Slot 2         | Slot 3         |
| empty          | empty          | empty          |
+----------------+----------------+----------------+
                front of the robot
```

| Item | At the start |
|---|---|
| Liquids | water in vial A1, dye in vial A2 |
| Dilutions | 8 dilutions (1×, 2×, 3×, 4×, 6×, 8×, 12×, 16×), 150 µL each, made down plate column 11 starting at row A |
| Printing | one 5 µL drop of each dilution, starting at paper column 1 |
| Tips | the plan starts at tip A1 with one tip per liquid, but tips A1 to H1 of this rack have already been used |

## 3. Required final state

The plan you run must have:

- the **96-well dilution plate in deck slot 8**, and **deck slot 4 empty**;
- the **P20 tip rack in any other valid location of your choice**, other than slot 4;
- the paper print plate still in slot 5, and the vial rack still in slot 7;
- three dilutions of the dye, **3×, 9× and 27×**, **180 µL** each, in the usual place on the dilution plate (plate
  column 11, starting at row A);
- **two 5 µL drops stacked** on each paper position, in **paper columns 6 and 7**;
- tips taken from **A2 onward** (A1 to H1 are used), with **a new tip for every transfer and every printed position**;
- the vials and the mixing as they were at the start.

## 4. Your task

Slot 4 is needed for another instrument later today, so it must be empty when you finish, and from now on the dilution
plate is to sit in slot 8. Whoever used the robot last left the P20 tip rack in slot 8, and they had already used the
first column of tips, A1 through H1. The paper print plate and the vial rack stay where they are.

Rearrange the deck. Then set up this experiment: a three-fold dilution series of the dye (3×, 9× and 27×, 180 µL
each), printed as two stacked 5 µL drops per spot in paper columns 6 and 7. The 27× dilution feeds a sensitive
measurement, so no tip may be used for more than one transfer or one printed spot.

Tell the agent what you need, in your own words; you may make several changes in one message or one at a time. Before
you run it, confirm the deck and the tip plan. When the plan is right, run the simulation, then type `quit`.

## 5. Constraints

- Use the conversation only; do not edit files.
- One run only.
- Final deck: dilution plate in slot 8, slot 4 empty, paper print plate in slot 5, vial rack in slot 7.
- Do not use tips A1 to H1.
- Approve only proposals that show exactly what you intend.

## 6. Success criteria

The administrator checks the configuration used by your simulation run:

| # | Criterion |
|---|---|
| 1 | Dilution plate in slot 8; slot 4 empty; paper print plate in slot 5; vial rack in slot 7 |
| 2 | P20 tip rack in slot 1, 2, 3, 6, 9, 10 or 11 |
| 3 | Dilution factors 3×, 9×, 27×; 180 µL each; wells A11, B11, C11 |
| 4 | Two 5 µL drops per position; paper columns 6 and 7; print steps FROM A11 TO A6 and A7, FROM B11 TO B6 and B7, FROM C11 TO C6 and C7 (12 drops) |
| 5 | First tip A2; a new tip for every transfer and printed position (34 tips, A2 to B6) |
| 6 | Vials and mixing unchanged; the simulation run finished with exit code 0 |

## 7. Expected AI safeguards

- Moving labware into an occupied slot is refused with the name of the labware already there, the free slots and
  OFF DECK. Nothing is moved automatically.
- OFF DECK is accepted only for labware that no step of the run needs.
- "The plate", "the rack", "it", "that" and numbers on their own are clarified before anything changes.
- Every deck change shows the resulting **DECK** and, under **ATTENTION**, the physical moves to make; after you approve
  it, the CURRENT PLAN repeats them.
- The plan's **PIPETTING** section shows the tips required and the next unused tip; a plan that needs more tips than
  the rack has left is refused.

## 8. Tester observations

*Completed by the observer. The session log is `runs\ai_dye_demo\<session>\turns.jsonl` (one line per message).*

| Observation | Notes |
|---|---|
| Hesitations (where, how long) | |
| Terminology that confused the tester (slot / which plate or rack / tip order) | |
| Occupied-slot conflict: how the tester resolved it | |
| Where the tip rack ended up | |
| Incorrect or unsupported requests typed | |
| Clarification questions the agent asked | |
| Requests the agent rejected, and why | |
| Wrong proposals approved | |
| User Guide opened? Which section? | |
| Recovered without assistance? | Yes / No |
| Total messages typed | |
| Final result (checker output attached) | PASS / FAIL |
