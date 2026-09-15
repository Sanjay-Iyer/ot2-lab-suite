# Visualizing the deck, wells and print positions — options and recommendation

**Status: recommendation only. Nothing here is implemented yet.** The specific labware to
draw will be chosen later.

## What the picture must show

- which deck slots are occupied (and which labware is OFF DECK)
- which wells and vials are selected, and what each well contains (for example "5× dye")
- which paper positions will be printed, and which were already printed this session
- which tips are used, next, and remaining
- during a proposal: CURRENT vs PROPOSED, with the changed slots or wells marked

**One rule applies to every option:** draw from the same `Plan` object the summaries
and the protocol already agree on (`src/agents/dye_demo/plan.py`), never from LLM text. A
test can then assert that every drawn marker matches a planned operation.

## The four approaches

### A. Terminal ASCII

```text
OT-2 DECK (top view, back of robot at the top)
+-----------------+-----------------+-----------------+
| 10  EMPTY       | 11  EMPTY       | 12  TRASH       |
+-----------------+-----------------+-----------------+
|  7  VIAL RACK   |  8  EMPTY       |  9  TIPS        |
|     A1 water    |                 |     next: F1    |
|     A2 dye      |                 |                 |
+-----------------+-----------------+-----------------+
|  4  DILUTION    |  5  PAPER       |  6  EMPTY       |
|     A11-C11     |     A1-C1       |                 |
+-----------------+-----------------+-----------------+
|  1  EMPTY       |  2  EMPTY       |  3  EMPTY       |
+-----------------+-----------------+-----------------+
Off deck: none                           (front of robot)

DILUTION PLATE (slot 4)        PAPER (slot 5)        legend: 2 5 X = fold factor
     1 ... 10 11 12                 1  2  3 ...      P = will print   * = printed earlier
A    .  .   .  2  .            A    P  *  .
B    .  .   .  5  .            B    P  *  .
C    .  .   . 10  .            C    P  *  .
D    .  .   .  .  .            D    .  .  .
```

### B. Rich terminal UI (Rich, or Textual for a full-screen app)

This is the same content as A, drawn with colour, boxes and tables:

- green for new, red for removed, yellow for changed;
- CURRENT and PROPOSED decks side by side;
- the plate maps as coloured grids.

Rich prints ordinary output, so the existing question-and-answer flow keeps working.
Textual would replace the whole terminal with an application.

### C. Simple GUI

- **Tkinter:** a window with the deck and plate maps, refreshed after every applied
  change.
- **PySide/PyQt:** a richer desktop application.
- **Local browser page (FastAPI or Streamlit):** the same drawings on a web page served by
  the demo, opened next to the terminal.

### D. Automatically generated figures (SVG, PNG, PDF)

After every applied revision and every run, write `deck_rev3.svg` into the session's run
directory:

- the deck, the source wells, arrows from dilutions to paper positions, and the tip
  range;
- optionally combined into a one-page PDF experiment record.

## Comparison

| | A. ASCII | B. Rich | C. GUI | D. Generated figures |
|---|---|---|---|---|
| **Effort to a first version** | Lowest (hours) | Low (a day) | High (days to weeks) | Medium (1–2 days for SVG) |
| **New dependencies** | None | `rich` (already in the `ai` env; the `llm` env on the lab laptop needs checking). Textual is not installed. | Tkinter ships with Python. PySide6/PyQt5 are not installed and are large. Streamlit and FastAPI are in the `ai` env. | SVG as plain text: none. PNG/PDF: `matplotlib` (installed). Graphviz would also need system binaries. |
| **Works over SSH, in logs, in tests** | Yes | Yes, with plain-text fallback | No | Files, yes |
| **Robustness** | Highest; nothing to break | High if it falls back to ASCII when the output is not a terminal | Lowest: threads, window focus, corporate proxy and ports for web versions | High: pure data to file |
| **Maintenance burden** | Low | Low–medium | High (a second UI to keep in sync) | Low–medium |
| **Demo impact** | Moderate | High | High | High for slides, reports, lab notebooks |
| **Fits the current workflow** | Direct: `render.py` already draws the deck lists | Direct: wraps `render.py` output | Needs an event loop beside the REPL | Direct: write files on `config_updated` and `run_finished` |
| **Test strategy** | Snapshot text | Snapshot plain export | Hard to test | Assert SVG elements against the plan |

## Recommendation

| Goal | Choice |
|---|---|
| **Easiest** | **A. ASCII.** No dependencies, works everywhere the demo works, trivially testable. |
| **Most robust** | **A** for the live terminal, plus **D (SVG written as text)** for a permanent record. |
| **Best demo** | **B (Rich)** in the terminal with automatic ASCII fallback, plus **D** figures to show on screen or paste into slides. |
| **Not yet** | **C (GUI).** It adds a second interface to keep in sync, ports and proxies on the lab laptop, and the hardest testing. Revisit only if the demo becomes a daily tool. |

**Suggested phases:**

1. ASCII deck grid and 96-well / paper / vial / tip maps in `render.py`, driven by `Plan`,
   with snapshot tests. Show them on `deck`, in proposals (CURRENT/PROPOSED) and before a
   run.
2. An optional `--rich` layer (colour, side-by-side CURRENT/PROPOSED). Fall back to phase
   1 whenever the output is not a terminal or `NO_COLOR` is set.
3. An SVG export after each applied revision and each run into `runs/ai_dye_demo/<session>/`.
   Optional PDF experiment record via matplotlib.

## Orientation (upside-down / mirrored view)

The OT-2 numbers slots 1–3 at the front (nearest the operator) and 10–12 at the back, with
the trash in slot 12 at the back right. Standard diagrams, including the ASCII mock above,
put the back at the top. In that view, row A of a plate on the deck is also at the top, so
standard plate maps line up.

- **Standing at the front and looking down:** the robot matches the standard diagram
  turned upside down, since slot 1 is nearest you. An optional `--deck-view front` would
  rotate the deck and every plate map by 180°, putting H12 at the top-left. That makes
  "the well nearest me" appear nearest on screen.
- **Mirroring:** a camera image (`vision_runs/`) may be mirrored instead of rotated. A
  separate mirror option would line the picture up with the camera view.

**Recommendation:** keep the standard orientation as the default, because it matches
Opentrons documentation and the App. Offer `front` (rotated 180°) as an option. Always
print the words "back of robot" and "front of robot" on the drawing, so a rotated view
cannot be misread.

## Accessibility and correctness rules for any option

- Never use colour alone: every marker also has a letter or number.
- Every drawn well, position or tip comes from `Plan.operations` and `Plan.tips`, and a
  test asserts the counts match.
- Proposals show CURRENT and PROPOSED with the same geometry and orientation, so
  differences stand out.
