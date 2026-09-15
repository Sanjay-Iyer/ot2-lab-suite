# Known conversational limitations

What the demo intentionally does not do, or does only after asking. Every choice below is the conservative
one: when in doubt the agent asks or refuses, and nothing changes.

## Intentionally unsupported (explained, nothing changes)

- **Serial dilutions** (well to well). Each dilution is made independently from the dye stock; the reply suggests giving
  the fold factors instead.
- **Printing one dilution** of the series (`Print the 10× dilution.`), or some of them. Every dilution in the plan
  prints. When the named dilutions are not in the plan at all, the reply says so; naming every dilution of the plan
  together with where to print (`Print the 5x, 10x and 20x dilutions onto paper column 2.`) is an ordinary instruction.
- **Different dilutions in different paper columns** in one run: every dilution prints into every printed paper column.
- **A second dye** or reagent.
- **Reversing the liquid path** (drawing from the paper, using the paper as a source, returning liquid to the vials).
  Labware can move between slots; the path vial -> dilution plate -> paper is fixed.
- **Different drop counts per paper column** in one run, and **paper columns with a gap** (`paper columns 1 and 3`) in
  one run. Both need two runs.
- **Bookkeeping from simulated runs.** A simulated run records no used tips and no prepared dilutions (only a successful
  live run does), so a second simulated run must be told that the dilutions exist and where to start the tips.
- **Inventing dilution factors.** `Use 6 dilutions` extends the series only when it is geometric (1, 2, 4, ...);
  otherwise the agent asks for all six factors and suggests the start of the standard series.
- **Languages other than English.**

## Always clarified, never guessed

- Bare numbers (`Use 10.`, `use 3`, `start at 4`), vague quantities (`a couple drops`) and relative amounts with
  no target (`Use half.`).
- Volumes without a unit.
- Pronouns and vague references unless exactly one labware was discussed in the last few turns;
  `the same one as before` always asks.
- `plate` and `rack` on their own, `vial 8`, `dilution 8`, `tray`, `bottles`, `holes`, `square 7`, `column 6`.
- Double negatives. Sarcasm and irony are not detected.
- `go back` on its own, `start over`.
- `I will move the vial rack to slot 6 later.` asks whether to update the plan now.

## Deliberate design choices

- **Feasibility questions are questions.** `Can we move the plate to slot 6?` is answered, with a note on how to
  make it an instruction; `Can you move ...` and `Could you move ... for me` are instructions.
- **"Don't ..." never changes the plan.** `Don't print anything this time.` or `No printing this run, just dilutions.`
  changes nothing; the agent says what the plan still does and how to change it (`skip printing`). `Keep the plate
  where it is.` removes the plate change from a waiting proposal (and discards the proposal when that was its only
  change), and otherwise confirms where the plate stays. `Don't move the paper print plate.` is about the labware,
  never about printing.
- **Switching a step on or off needs words for that step.** A model change that turns printing or the dilution step on
  or off is refused unless the message asks for it in that direction (`skip printing`, `print the dilutions`, `the
  dilutions are already made`). The name `paper print plate` and a temporal clause never count.
- **Temporal clauses are not reports.** `Once the dilutions are made, print them onto paper column 2.` is the order of
  the run; `Let me know when the dilutions are ready.` is answered. Past tense (`The dilutions were made yesterday.`)
  and `already` are still reports. The agent cannot notify anyone; the run output shows each step.
- **Material names need a naming verb.** `Call the dye crystal violet.` sets the name; `The dye is crystal violet.` is
  only answered.
- **Named paper columns must be exactly the printed columns.** Columns written as columns (`paper columns 3 and 4`,
  `column 3 and 4`, `columns 3-4`, `starting at paper column 2`, `only paper column 6`, `not paper columns 1 and 2`)
  are compared with the columns the resulting plan prints. A proposal that would print other columns is not shown: the
  agent says which columns it would print and, when the words fix the layout, asks whether to print exactly the named
  columns (a yes shows that as a proposal, which needs its own yes). No run starts while that question waits. A bare
  `column 3` in a sentence that also talks about the plate is not read as a paper column, and `Print in 3 and 4.` asks
  for the number of replicate columns.
- **A live run that did not finish holds the next live run.** After an interrupted or failed live run nothing from that
  run is recorded (tips, dilutions, printed positions), and the next live `run` asks whether the robot was checked and
  the plan updated to match.
- **Short statements of a setting and a value are instructions** (up to 12 words, with a field word and a value, no
  verb needed: `Sorry, I was wrong, the dye is in vial B1.`). They still become a proposal that needs yes.
- **Follow-ups do not carry a hypothetical forward.** `do it` after `What if we moved the plate to slot 6?` asks what
  should change. `ok go` after a hypothetical starts nothing; `run` by itself still starts the current plan.
  Elsewhere short go-aheads (`go ahead`, `let's go`) still start a run, as in Demo 1.
- **Only explicit approval words approve** (`yes`, `yes please`, `confirm`, `apply`, `approve`, ...) or an approval
  naming the waiting proposal (`Yes, I approve proposal #1.`). `yeah`, `sure`, `ok` and `looks good` do not. An
  approval sent together with a new request applies nothing: the agent keeps the proposal waiting and asks for a
  plain yes first, so one message never both applies a change and starts another.
- **Partial approval keeps only what was explicitly approved.** `yes to moving the plate but leave the dilutions`
  keeps the plate move only; other changes in the original proposal must be approved separately.
- **Fields must be mentioned.** A change to a field the message does not mention (by the words in
  `state.py::_FIELD_HINTS`) is flagged under CHECK THESE, and a proposal made only of such changes is refused. A
  field described with unusual words is therefore flagged rather than verified; naming the field fixes it.
- **Slots must be written as slots** (`slot 8`, `to 8`, `in 8`); `position 8` asks. The labware must be named as the
  thing moving: `tips` counts only with a move verb (`move the tips`), `paper position` never names the paper plate.
- **Pasted material stays reference material.** Text after `The SOP says:` or an email header, up to a blank line
  after its content, is never an instruction. An instruction typed straight after a pasted block without a blank
  line is treated as part of the paste and must be sent again on its own.
- **Physical reports that would make the plan impossible are not recorded on their own.**
  `I took the vial rack off the robot.` while the plan makes dilutions is refused as a record, and `run` stays
  blocked until the scientist reports how the robot actually is, or changes the plan to match. Declining a
  reconciliation proposal also keeps the report outstanding. Reports about fresh tips never block a run.
- **Undo restores settings, not the physical world.** Rollback proposals use stored snapshots; physical records
  (prepared dilutions) are not rolled back.
- **Tip reports** only understand "a fresh, full rack, start at A1"; a partly used rack needs the exact starting tip.

## Limits of the testing

- Scripted personas use a finite vocabulary; real people will phrase things differently. The Gemini user agent
  and judge broaden coverage but are limited by the model-call budget, and their labels are soft (label
  disagreements are listed for review, not failed).
- Voice homophones are normalized after `to`/`too`/`two` and after `slot`/`column` when the number ends the clause
  or precedes `for me`, `please`, `now`, `instead`, `thanks`; other homophones are left as typed (and then clarified).
- Model answers to science questions can still be wrong. Only an answer that claims a change was made is corrected
  deterministically; the judge is advisory.
- The fake OT-2 checks motion order, counts, tips and sources/destinations of protocol v19. It does not validate
  physical accuracy, liquid behaviour or collisions. **Nothing in this work was validated on the physical OT-2.**
- The Ctrl-C stop path was tested against a recording fake of the robot server and a fake runner process. How quickly a
  real OT-2 stops on the `stop` action, and whether Ctrl-C reaches both processes on the lab laptop's console, still
  has to be confirmed on the robot.
