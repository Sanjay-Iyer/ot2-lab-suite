# AI dye demo — user-test SOPs

Five progressive usability tests for the conversational OT-2 agent (`scripts\ai_dye_demo.py`). Each SOP gives a person an
experimental objective, not the words to type, so an observer can see how people actually talk to the agent: which
terms confuse them, what mistakes they make, what the agent catches, and whether the final plan matches the SOP.

All five run in **simulation** (`--simulate`): nothing contacts the robot.

## For the tester

| Document | Difficulty | Tests |
|---|---|---|
| [SOP_01_basic_dilution.md](SOP_01_basic_dilution.md) | 1/5 | dilution factors, number of dilutions, final volume, source vial, confirming a proposal |
| [SOP_02_dilution_and_print.md](SOP_02_dilution_and_print.md) | 2/5 | dilutions before printing, FROM/TO, stacked drops, paper column |
| [SOP_03_selective_column_printing.md](SOP_03_selective_column_printing.md) | 3/5 | deck slot vs plate column vs paper column vs row vs well vs paper position |
| [SOP_04_multi_drop_multi_column.md](SOP_04_multi_drop_multi_column.md) | 4/5 | two runs, different drop counts, prepared dilutions, remaining volume, tip continuity, several changes at once |
| [SOP_05_deck_reconfiguration.md](SOP_05_deck_reconfiguration.md) | 5/5 | occupied-slot conflict, relocating labware, full experiment, partly used tip rack, tip policy |
| [USER_GUIDE_ai_dye_demo.md](USER_GUIDE_ai_dye_demo.md) | reference | how to talk to the agent, names, FROM/TO, parameters, checks; no SOP answers |

Give a tester one SOP and the User Guide.

## For the administrator only

| Document | Contents |
|---|---|
| [SOP_TESTER_ANSWER_KEY.md](SOP_TESTER_ANSWER_KEY.md) | Exact final states, plans, likely mistakes, expected agent behaviour, pass/fail rules, and how the SOPs were validated |
| [expected/](expected/) | Machine-readable expected final state of every SOP |
| `configs\workflows\user_test_sops\ai_dye_demo_sop05_start.yaml` | SOP 5 starting configuration (the tip rack starts in slot 8) |
| `scripts\check_ai_dye_demo_sop.py` | Scores a finished session folder against an SOP |

Score a session:

```powershell
python scripts\check_ai_dye_demo_sop.py --sop 3 --session runs\ai_dye_demo\20260911_101500
```

Validation, run on this laptop without a robot:

```powershell
python -m pytest tests\test_ai_dye_demo_user_test_sops.py -q
python scripts\ai_dye_demo_redteam.py --simulate-states
```

The test file replays scripted user paths for every SOP (clean, confused, change of mind, questions, and several changes
at once for SOPs 4 and 5) through the real conversation logic and checks every run against `expected/`.
`--simulate-states` runs each distinct SOP end state in the pinned opentrons 7.0.2 simulator.
