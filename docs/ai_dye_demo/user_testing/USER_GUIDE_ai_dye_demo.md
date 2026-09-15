# User Guide — Talking to the OT-2 AI Dye Demo Agent

This guide explains how to plan a dye dilution and paper-printing experiment by talking to the agent in
`scripts\ai_dye_demo.py`. Keep it open while you work. It explains the words, the rules and the checks; it does not
contain the answers to any test SOP.

```powershell
conda activate ai
python scripts\ai_dye_demo.py --simulate
```

`--simulate` builds and simulates the protocol on this computer. Nothing contacts the robot.

**What the demo does.** A P20 pipette makes a series of dye dilutions in water, each in its own well down one column
of a 96-well dilution plate, then prints every dilution as drops onto a paper print plate. You change the plan by
talking; the agent shows every change and applies it only when you type `yes`.

---

## 1. How to talk to the agent

Type in plain language at `you>`. Some examples:

| You type | What happens |
|---|---|
| `Move the vial rack to slot 6.` | A numbered **PROPOSED PLAN**: the complete plan with the vial rack in Slot 6, and the move to make under **ATTENTION**. `yes` applies it and shows the **CURRENT PLAN**, which repeats the physical move: "Now physically move the Vial rack from Slot 7 to Slot 6." |
| `Why are we mixing after every dilution?` | An answer (**ASK MODE — no experiment parameters changed**). A question never changes anything, even when it rests on a wrong idea. Here, each dilution well is actually mixed right before it is printed, not right after it is made. |
| `What is currently in slot 8?` | An answer from the current plan. Type `deck` for the deck itself. |
| `How many tips will this use?` | An answer. Type `tips` for the exact tip configuration. |
| `What have I changed so far?` | The list of applied changes, read from the stored history. |
| `Actually, keep the plate where it is.` | If a proposal that moves the plate is waiting, it is replaced by a proposal without the plate move. If nothing is waiting, nothing changes and the agent confirms where the plate stays. |

The rules:

- **Questions never change anything.** Put `/ask` in front (`/ask Why is there an air gap?`) when you want an answer
  that is guaranteed to be read-only.
- **Every change is a numbered proposal**: `PROPOSED PLAN #3`, the complete plan you get if you say yes, with anything
  to check under **ATTENTION**. Nothing is applied until you type `yes`.
- **Several changes in one message make one proposal**, applied all together or not at all.
- **A new request replaces a waiting proposal**: "Proposal #2 was discarded - nothing from it was applied." Repeat any
  part of it you still want.
- **When your wording could mean more than one thing, the agent asks first**: `Before I change anything: ...`. Answer
  with the number of an option, `yes`/`no`, or `none` to say it differently.
- **Say values with units, and name what they belong to**: `100 µL` (or `100 uL`), `deck slot 6`, `paper column 7`,
  `tip C4`.
- **A "don't ..." message never changes the plan.** Say what the plan should do instead, for example `skip printing`.
- **Tell the agent what you did by hand**: `I moved the vial rack to slot 6 myself.` or `The dilutions are already
  made.` This becomes a **PHYSICAL STATE RECONCILIATION** proposal that only updates the record ("The robot does not
  move"), after you type `yes`.
- **A plan for later** (`I will move the vial rack to slot 6 later.`) is not a change: the agent asks whether to update
  the plan now.

---

## 2. Naming conventions

### Deck slots

The OT-2 deck has slots 1 to 11 for labware. Slot 12 is the trash. Say **slot 6** or **deck slot 6**. See section 6
for the layout.

### Wells and positions

Every well, paper position and tip has a **row letter A–H** followed by a **column number 1–12**: `A1`, `D7`, `H12`.

| Accepted | Read as |
|---|---|
| `A3`, `a3`, `A03`, `A-3`, `row A column 3` | A3 |
| `A10` | A10 (never shortened to A1) |
| `3A` | not accepted: write the row letter first |

A well name on its own does not say which labware it is on. Say **plate well A3**, **paper position A3**,
**vial A3** or **tip A3**.

### Column, row and slot are different things

| Word | Means | Example |
|---|---|---|
| **deck slot** | a place on the robot where labware sits | deck slot 3 |
| **plate column** | a column of wells on the dilution plate | plate column 9 = wells A9 to H9 |
| **plate row** | a row of wells on the dilution plate | plate row B = wells B1 to B12 |
| **paper column** | a column of positions on the paper print plate | paper column 7 = positions A7 to H7 |
| **paper row** | a row of positions on the paper print plate | paper row B = positions B1 to B12 |

`column 6` without *plate* or *paper* is asked about when the sentence does not make it clear.

### Vials

The vial rack holds 8 vials: **A1, A2, A3, A4** and **B1, B2, B3, B4**. The water (solvent) and the dye (sample) are
in different vials. `vial 5` is asked about: the agent offers vial B1 (counting A1 to A4, then B1 to B4) or the vial
rack in deck slot 5.

### Paper print plate positions

The paper print plate is addressed like a 96-well plate, positions A1 to H12.

- **Each dilution prints on the paper row with the same letter as its plate row.** A dilution made in plate row B
  prints on paper row B.
- **Each drop volume and each replicate gets its own paper column**, starting at the first paper column and moving
  to the right.

### Tips

The P20 tip rack has 96 tips, A1 to H12. **Tips are used in column order: A1, B1, C1 ... H1, then A2 ... H2**, and so on.
The "first column" of tips is A1 to H1. The first *row* (A1 to A12) is not the order tips are used in.

### Labware names

Use the names the agent shows:

| Name shown by the agent | You can also say | Note |
|---|---|---|
| 96-well dilution plate | dilution plate, 96-well plate | `plate` alone is asked about while the paper print plate is on the deck |
| Paper print plate | paper, paper plate, print plate | |
| Vial rack | tube rack, vials (with a move verb) | `rack` alone is asked about while the tip rack is on the deck |
| P20 tip rack | tip rack, tips (with a move verb) | `tip A1` is a tip, not the rack |

When you name the slot something is in (`the plate in slot 4`), the agent uses that to tell which labware you mean, and
says so.

---

## 3. FROM and TO

Every liquid movement in `plan` is shown with where it comes **FROM** and where it goes **TO**.

Dilution transfers, from the vial rack into the dilution plate:

```text
  FROM -> TO
    water (solvent)
      FROM : Vial rack, Slot 7, vial A1
      TO   : 96-well dilution plate, Slot 4, well(s) B9, C9
```

Print steps, from the dilution plate onto the paper:

```text
  PRINT STEP 2 of 2
    FROM : 96-well dilution plate, Slot 4, well C9
           10× dye dilution in water
    TO   : Paper print plate, Slot 5, position C7
    Volume per drop: 5 µL   Drops: 1   Total: 5 µL   Tip: D1
```

Read it as: *FROM the 10× dye dilution in plate well C9 TO paper position C7*. If the dye has a name (for example
"crystal violet"), the name is shown instead of "dye".

Why it matters:

- **The same name can exist on two labware.** Plate well C7 and paper position C7 are different places. FROM and TO
  always name the labware.
- **The liquid path is fixed**: vials → dilution plate → paper. Labware can move to other slots, but nothing is ever
  drawn from the paper or put back into the vials.
- **Check FROM and TO before `run`**, especially after changing rows, columns or deck slots.

---

## 4. Parameters you can change

Names in **bold** are the labels used in proposals.

| Parameter | Meaning | Example | Units / values | Notes |
|---|---|---|---|---|
| **… location** (dilution plate, paper print plate, vial rack, P20 tip rack) | Deck slot of each labware | `Move the vial rack to slot 6.` | slot 1–11, or OFF DECK | No two labware in one slot. OFF DECK only for labware the run does not need. |
| **Dye (sample) vial**, **Water (solvent) vial** | Which vial holds each liquid | `The dye is in vial B3.` | A1–B4 | The two must differ. |
| **Dye (sample) name**, **Water (solvent) name** | Display name only | `Call the dye crystal violet.` | up to 40 characters | Changes no liquid handling. Say *call* or *name*: "The dye is crystal violet." is only answered. |
| **Dilution factors** | How strong each dilution is, one per well | `Make dilutions of 2×, 5× and 10×.` | 1 to 8 factors, each at least 1× | 10× = 1 part dye in 10 parts total; 1× = neat dye. Each dye transfer must be at least 1 µL. |
| Number of dilutions | How many factors | `Use three dilutions.` | 1–8 | Keeps the first factors of the current series (or extends a regular series); otherwise asks for the factors. |
| **Dilution plate column** | Plate column the series is made in | `Make the dilutions in plate column 9.` | 1–12 | |
| **Dilution start row** | Plate row of the first dilution | `Start the dilutions at row B.` | A–H | The series must fit by row H. Paper rows follow the plate rows. |
| **Final volume per dilution** | Dye plus water in each well | `Make each dilution 100 µL total.` | µL, up to 340 | Must leave enough in the well to mix and print (mixing needs about 89 µL in the well). |
| **Make dilutions in this run** | Whether this run makes the dilutions | `The dilutions are already made.` | yes / no | Turned off only with a record that the dilutions exist. |
| **Volume now in each prepared well** | Liquid left in wells made earlier | `Each well now holds about 120 µL.` | µL | Only for dilutions that already exist; used for the liquid-depth checks. |
| **Mixes before each print** | Mixing repetitions before each print step | `Mix three times before printing.` | whole number ≥ 1 | |
| **Mixing volume** | Volume per mix | `Mix with 10 µL.` | µL, up to 20 | |
| **Print in this run** | Whether this run prints | `Skip printing.` | yes / no | |
| **Drop volume** | Volume of one drop | `Print 8 µL drops.` | µL, 1–18.5 | A list of volumes (`4 and 8 µL`) prints each volume in its own paper column. |
| **Drops per paper position** | Drops stacked on one position | `Stack two drops on each position.` | whole number ≥ 1 | The same for every printed column in one run. |
| **Replicate paper columns** | Side-by-side repeats of each drop volume | `Use three replicate columns.` | whole number ≥ 1 | Columns used = drop volumes × replicates. |
| **First paper column** | Leftmost printed paper column | `Start printing at paper column 7.` | 1–12 | Printing moves to the right; it must fit within 12 columns. |
| **Starting tip** | First tip this run picks up | `Start from tip C4.` | A1–H12 | Tips are used in column order. |
| **Tip policy** | When a fresh tip is used | `Use a new tip for every transfer.` | one tip per liquid / new tip every transfer | "one tip per liquid" reuses a tip only for the same liquid, and uses one tip per dilution when printing. |
| **Return used tips to the rack** | Put used tips back instead of the trash | `Return the tips to the rack.` | yes / no | Returned tips are contaminated; a later run must start after them. |

Changing dilutions, drops or columns changes liquid use and tip use. The proposal shows the resulting dilutions, paper
columns, print positions, drops, printed volume, liquid use and tips required.

---

## 5. Parameters not to change casually (lab-owned)

These settings are calibrated on the instrument. The agent refuses to change them and says why. Type `settings` to see
them next to the machine profile.

| Setting | Value | Why it is fixed |
|---|---|---|
| Pipette | P20 single-channel GEN2, left mount | The whole workflow and its limits assume it |
| Drop release height above the paper | 1.1 mm | Calibrated release; affects hanging drops |
| Aspirate height in a dilution well | 1.0 mm above the bottom | Keeps the tip submerged when printing |
| Trailing air gap | 1.5 µL, taken 5 mm above the well | Part of the validated drop release |
| Push-out, print blow-out, dwell after each drop | 3 µL, on, 2 s | Part of the validated drop release |
| Vial aspirate height | 4 mm above the vial bottom | Calibrated to the vial rack |
| Water / dye dispense height | 2 mm / 1 mm below the well top | Dispenses above the liquid, so a shared tip never touches it |
| Blow-out after each dilution dispense | on | Empties the tip after every transfer |
| Largest single transfer | 20 µL | P20 limit; larger volumes are split |
| Mixing height | 2 mm above the well bottom | |
| Flow rates | 3 µL/s aspirate and dispense | |
| Paper width | 12 columns | Paper labware geometry |
| Safety limits, labware types, protocol version | fixed | |

Changing a lab-owned value is a laboratory decision made with a physical test, not a conversation.

---

## 6. Deck terminology

The standard deck, seen from the front of the robot:

```text
                back of the robot
+----------------+----------------+----------------+
| Slot 10        | Slot 11        | Slot 12        |
| empty          | empty          | trash          |
+----------------+----------------+----------------+
| Slot 7         | Slot 8         | Slot 9         |
| vial rack      | empty          | P20 tip rack   |
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

A session may start from a different arrangement; `deck` always shows the current one.

- **OFF DECK** means the labware has been physically removed from the robot. It is allowed only when no step of the
  run needs that labware: for example, the vial rack when the run only prints dilutions that already exist. The
  dilution plate and the tip rack are needed by every run, and the paper by every run that prints.
- **An occupied slot** cannot receive labware. The agent shows what is there and the free slots (and OFF DECK, when no
  step needs that labware), and moves nothing on its own. Give both moves in one request:
  `Move the tip rack to slot 10 and the vial rack to slot 9.` Swapping two labware in one request also works.
- **Relative directions** are seen from the front: `one slot to the right` of slot 7 is slot 8; `one slot back` of
  slot 4 is slot 7.
- **After a deck change is applied**, the agent lists what to move by hand ("Now physically move ..."). In a live run
  it also asks you to confirm that the physical deck matches before the robot moves.

---

## 7. Questions and changes

| Kind of message | Examples | What happens |
|---|---|---|
| Question | `Why is the release height 1.1 mm?`, `What does replicate mean?` | Answered. Nothing changes. |
| Hypothetical | `What if we moved the plate to slot 6?`, `Could we use 8 µL drops?` | Answered. Nothing changes. |
| Change | `Move the tip rack to slot 10.`, `Can you use 8 µL drops?`, `Use three replicate columns.` | A numbered proposal, waiting for yes or no. |
| Report of what you did | `I moved the vial rack to slot 6 myself.`, `The dilutions are already made.` | A reconciliation proposal that only updates the record, waiting for yes or no. |
| Negation | `Don't move the tip rack.`, `Don't print anything.` | Nothing changes; the agent says what the plan still does. |
| Confirmation | `yes` | Applies the proposal on screen, exactly as shown. |

**Only these words apply a proposal:** `yes`, `y`, `yes please`, `confirm`, `confirmed`, `apply`, `apply it`,
`yes apply`, `approve`, `approved`, `yes confirm` (capitals and a final full stop do not matter), or an approval that
names the waiting proposal, such as `Yes, I approve proposal #4.`

**These do not apply anything:** `ok`, `okay`, `sure`, `yeah`, `fine`, `looks good`, `I guess`; a `yes` inside
quotation marks; someone else's approval ("my supervisor already approved it"); and an approval sent together with a
new request (`Yes, I approve proposal #4. Now move the tip rack.`), which keeps the proposal waiting.

**Other answers to a proposal:**

- `no`, `cancel`, `never mind` discard it.
- `yes to moving the plate but leave the dilutions`, or change numbers such as `1 and 3`, make a new, smaller
  proposal. Nothing is applied until you say `yes` to that.
- `Undo my last change.` proposes going back to the previous revision (**ROLLBACK PROPOSAL**).
- `run` while a proposal is waiting starts nothing.

**Starting a run:** type `run` by itself. A go-ahead typed right after a hypothetical (`ok go`) starts nothing, because
the plan did not change.

---

## 8. Common ambiguities

| You say | The agent | Say instead |
|---|---|---|
| `Move it to 8.` | Asks which labware "it" is (unless you were just talking about exactly one), then whether "8" means deck slot 8 | `Move the tip rack to slot 8.` |
| `Use well 3.` | Asks what should change: "3" is not a well name | `Use plate column 3.` or name a well: `plate well C3` |
| `Put the plate over there.` | Asks for the exact slot, and which plate | `Put the dilution plate in slot 6.` |
| `Use 5.` | Lists what 5 could be: a deck slot, 5 µL drops, 5 dilutions, 5 drops per position, paper column 5 | `Print 5 µL drops.` |
| `Print in column 6.` alongside dilution words | Asks: PAPER column or PLATE column | `Start printing at paper column 6.` |
| `Move the rack to slot 10.` | Asks: the vial rack or the P20 tip rack | `Move the vial rack to slot 10.` |
| `Use vial 5 for the dye.` | Asks: vial B1, or the vial rack in deck slot 5 | `The dye is in vial B1.` |
| `Make each dilution 100.` | Asks whether you mean 100 µL | `Make each dilution 100 µL total.` |
| `Add a couple of drops.` | Asks how many exactly | `Stack two drops on each position.` |
| `Print the 10× dilution.` | Explains that every dilution in the plan prints, and whether the plan has a 10× dilution | Change the dilution series itself |
| `Move the dilution plate to slot 9.` when slot 9 is occupied | Refuses the move, shows what is in slot 9 and the free slots | Move the other labware in the same request |
| `Make a serial dilution.` | Explains that each dilution is made directly from the dye stock | Give the fold factors: `Make dilutions of 2×, 5× and 10×.` |

---

## 9. Checking your work

| To see | Type | What to look for |
|---|---|---|
| The whole experiment | `plan` | **CURRENT PLAN**: dilutions, printing, deck, liquids, pipetting, lab-owned parameters, then **ATTENTION** if anything needs you |
| The deck | `deck` | **ACTIVE DECK**: every occupied slot, OFF DECK labware, and the empty slots |
| The dilutions | `plan` → **DILUTIONS** | Wells, factor, dye µL and water µL per well, final volume; `SKIPPED` if this run does not make them |
| What gets printed | `plan` → **PRINTING** | Paper columns, paper rows, drop volume, drops per position, print positions, total drops, printed volume |
| Where each print comes from and goes to | `steps` | FROM plate well → TO paper position, drops, volume, tip |
| Liquid to load and left in the wells | `plan` → **LIQUIDS**, **PRINTING** | Minimum volume to load per vial; volume taken from and left in each well |
| Tips | `tips` | First tip, tip reuse, tips required (range), next unused tip, tips remaining |
| What changed | `history` or `What have I changed so far?` | Every applied change: revision, who, before → after |
| One setting's starting value | `What was the original drop volume?` | Value at startup and now |
| What has not changed | `What parameters are still the same as startup?` | Settings unchanged since startup |
| The proposal on screen | `What am I approving?` | What `yes` would apply |
| Lab-owned settings | `settings` | Calibrated values next to the machine profile |
| The raw configuration | `show` | The working YAML |
| Commands | `help` | What can be changed, and the commands |

In a proposal (**PROPOSED PLAN #N**):

- The plan is the complete plan you get if you type `yes`, in the same sections as `plan`. It shows resulting values
  only; the **Changes** line names the settings the proposal touches (numbered when there are several).
- **ATTENTION** under the plan holds what to check before `yes`: **CHECK THESE - I could not find them in what you
  typed** (changes your words did not clearly ask for; if you did not mean them, type `no`), a step the proposal
  switches off, the physical record it writes, labware you must move, and warnings.
- Warnings do not block the plan, but read them.

At the bottom of `plan`, either "All plan checks passed." or an **ATTENTION** block. A plan that fails a check cannot
run: ATTENTION says "This plan cannot run. Execution is blocked until these are fixed:".

---

## 10. If you get stuck

1. **Look at the current state**: `plan`, `deck`, `tips`.
2. **Ask what the agent means**: questions are answered while its own question stays waiting, and the agent repeats its
   question afterwards.
3. **Ask for the valid choices**: `Which slots are free?`, `What can I change?`, or type `help`.
4. **Make one change at a time.** A shorter request is easier to check.
5. **Read the whole proposal**: the plan it would give you, and everything under ATTENTION.
6. **Confirm only when it is right.** Type `no` to discard a wrong proposal, or `Undo my last change.` after applying
   one.
7. **Name things fully** when the agent keeps asking: *deck slot 7, plate well A11, plate column 11, paper position B3,
   paper column 2, vial A2, tip A1, OFF DECK*.
8. **Start again cleanly** if the plan is tangled: `quit`, then start the script again.

---

## Messages you may see

| Message | Meaning and what to do |
|---|---|
| `Before I change anything: ...` | Your wording could mean more than one thing, or a value or unit is missing. Answer the question. |
| `Cannot apply that deck change yet.` | The slot is occupied. Say where the other labware goes, in the same request. |
| `I did not change anything, because: ...` | The request was invalid or incomplete; the reason follows. |
| `your message did not mention the ...` | The interpretation touched a setting you did not name. Name it if you meant it. |
| `That is not a clear yes, so nothing was applied.` | Type `yes` or `no`. |
| `(Proposal #N is still waiting: yes to apply it, no to discard it.)` | Your question was answered; the proposal is still on screen. |
| `Proposal #N was discarded - nothing from it was applied.` | Your new request replaced it. |
| `Nothing was changed: this plan still prints in this run ...` | A "don't" or "no printing" message changes nothing. Say `skip printing` to turn printing off. |
| `The current plan has no ... dilution ...` | Printing uses the dilutions in the plan. Set the dilution factors first. |
| `One run uses the same number of drops in every paper column; ...` | Different drop counts in different columns need two runs. |
| `Paper columns ... are not side by side.` | One run prints neighbouring columns only; a gap needs two runs. Answer with the columns to print (`columns 3 and 4`). |
| `You asked for paper columns ..., but this change would print ...` | The interpretation would have printed other paper columns, so nothing was proposed. Answer `yes` to print exactly the columns you named (shown as a proposal first), or `no` to say it differently. |
| `Not running: the paper columns you asked for are not settled ...` | Answer the paper-column question first, or say `cancel` to keep the current plan. |
| `Run N was interrupted, and a stop was requested from the OT-2.` | Ctrl-C during a live run asks the OT-2 to stop that run. If the agent says the stop was not confirmed, stop the run in the Opentrons App. |
| `Run N did not finish (...). Have you checked the robot ...?` | A live run was interrupted or failed, and nothing from it was recorded. Check the plate, paper and tips, update the plan to match, then answer `yes`. |
| `Not running: ...` | Something about the robot is not in the record yet, or the run would refill full wells. Follow the message. |
| `Nothing has started. Your last message was a hypothetical ...` | Type `run` by itself to run the current plan. |
| `Simulation only: no tips, liquid or paper were used.` | The simulation finished. The agent does not record tips or dilutions from a simulated run. |
