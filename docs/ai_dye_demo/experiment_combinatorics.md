# Why every experiment cannot be hand-coded

**One small demo — one dye, one pipette, four pieces of labware — already allows billions
of different valid experiments.** Writing a separate robot script for each is not a
realistic option. What works instead is one reviewed protocol plus configuration that is
checked before it runs.

---

## How the counting works

1. **List the independent choices a scientist makes:** where labware goes, which vials,
   how many dilutions, what drop volume, and so on.
2. **Multiply the number of options for each choice.** Three independent choices with 2,
   3 and 4 options give 2 × 3 × 4 = 24 combinations.
3. **Count only valid combinations.** A plan the robot cannot physically perform is not
   counted.
   - The dependent choices were run through the same deterministic validator the demo uses
     before anything reaches the robot (`src/agents/dye_demo/validation.py`).
   - Invalid examples: printing past the edge of the paper, or a dilution needing less
     than the pipette's 1 µL minimum.

Everything below uses only choices the demo actually offers today.

---

## Example 1 — conservative: "one morning with this exact demo"

The tip rack stays in slot 9, every run starts from a fresh rack at tip A1, tips go to the
trash, mixing stays at 2 × 15 µL, and there is one dye in water.

| Choice | Options | Count |
|---|---|---:|
| Where the dilution plate, paper and vial rack sit (the 10 slots not used by the tip rack; no two in one slot) | 10 × 9 × 8 | **720** |
| Which vials hold the dye and the water | 8 × 7 | **56** |
| Which plate column holds the series | 1–12 | **12** |
| The dilution + print plan (details below) | 31,104 combinations, of which the validator accepts 19,539 | **19,539** |

The dilution + print plan combines these choices:

- **Number of dilutions:** 1–8.
- **Starting row:** any row that fits.
- **Scheme:** 2-fold, 5-fold or 10-fold series.
- **Final volume per well:** 100 or 150 µL.
- **Drop volume:** 5 or 10 µL.
- **Drops per paper position:** 1, 2 or 3.
- **Replicate paper columns:** 1 or 2.
- **First paper column:** 1–12.

```text
   720 deck layouts
 ×  56 vial assignments
 ×  12 plate columns
 × 19,539 valid dilution + print plans
 = 9,453,749,760 valid experiments   (about 9.5 billion)
```

At one hand-written, checked protocol per minute, non-stop, that is about
**18,000 years** of work.

The validator rejected 37% of the 31,104 dilution + print combinations. Reasons include:

- **Too steep for the pipette.** Eight dilutions of a 10-fold series reach 10,000,000×,
  which would need 0.000015 µL of dye; the P20's minimum is 1 µL.
- **Not enough liquid.** Too many drops per well would leave the tip drawing air before
  the last print.
- **Off the paper.** The plan would print past paper column 12.

---

## Example 2 — larger: "everything this demo's validator accepts"

This is still one dye, one drop volume per run, and one tip policy (one tip per liquid).

| Choice | Options | Count |
|---|---|---:|
| Where all four labware sit (slots 1–11, no two in one slot) | 11 × 10 × 9 × 8 | **7,920** |
| Dye and water vials | 8 × 7 | **56** |
| Plate column | 1–12 | **12** |
| Dilution series × starting tip × final volume × print plan (details below) | valid combinations only | **24,755,853,418,434,324** |
| Used tips returned to the rack or trashed | 2 | **2** |

The large row combines these choices, and only combinations the validator accepts are
counted:

- **Dilution series:** 1–8 factors, each chosen from 12 common values (1, 2, 3, 4, 5, 8,
  10, 16, 20, 25, 50, 100), in any order, from any starting row that fits.
- **Starting tip:** any tip that leaves enough tips.
- **Final volume:** one of 50, 100, 150, 200, 250, 300 or 340 µL.
- **Print plan:**
  - drop volume 1.0–18.5 µL in 0.5 µL steps;
  - 1–5 drops per position;
  - 1–6 replicate columns;
  - first paper column 1–12;
  - mixing volume 5, 10, 15 or 20 µL, repeated 1–3 times.

```text
 7,920 × 56 × 12 × 24,755,853,418,434,324 × 2 ≈ 2.6 × 10^23 valid experiments
```

For scale: that is about as many as the water molecules in 8 mL of water.

Note that 50 µL per well contributes **zero** valid plans: mixing 15 µL at 2 mm needs at
least 89 µL in the well. The count includes only plans the robot can really perform.

---

## The demo still does not offer many real choices

Each of these would multiply the counts again:

- more than one dye or reagent, each with its own series;
- a different number of drops in different paper columns;
- free-form print patterns instead of one paper row per dilution;
- serial (well-to-well) dilutions;
- labware placed OFF DECK for dilute-only or print-only runs;
- a fresh tip for every transfer.

Two illustrations of how fast these grow:

| Generalised choice | Count |
|---|---:|
| Which 12 of the 96 paper positions to print | C(96, 12) ≈ **6.2 × 10^14** |
| Assigning 4 reagents to the 8 vials | 8 × 7 × 6 × 5 = **1,680** |

The print-position choice alone is about 66,000 times the entire conservative example.

---

## What this means for how the system is built

| Hard-coding every combination | What the demo does instead |
|---|---|
| One script per experiment: billions of scripts | **One** reviewed protocol file (protocol v19) |
| Each script needs its own review and testing | The protocol is tested once; each experiment is **data** (a YAML configuration) |
| Unsafe combinations slip through unless someone notices | **Deterministic validation** rejects impossible plans before the robot is involved |
| The scientist must find the right script | The scientist **describes** the experiment; the LLM translates the words into explicit parameter changes |
| Nobody sees what differs between two scripts | Every change is **shown and confirmed**, including what did not change |

The LLM provides the flexibility, and deterministic code provides the safety. The
combinations live in validated, auditable configuration, not in code.

---

## Assumptions and method (for reproducibility)

- **Validity rules** are those of `validate()` on 2026-09-10:
  - deck slots 1–11, no sharing;
  - 1–8 dilutions, each ≥ 1×, fitting by row H;
  - ≥ 1 µL of dye per well;
  - drop + 1.5 µL air gap ≤ 20 µL;
  - printing within 12 paper columns;
  - mixing and aspiration never drawing air (well area 36.96 mm², mix at 2.0 mm,
    aspirate at 1.0 mm);
  - enough tips from the starting tip.
- **Starting row:** a plan valid from row A is valid from every row where it fits, so
  each valid plan is counted once per fitting start row (9 − number of dilutions).
- **Example 1** enumerates all 6,912 row-A plans with `validate()`.
- **Example 2** uses a direct arithmetic model of the same print and depth rules,
  cross-checked against `validate()` on 1,500 random configurations with zero
  disagreements.
- **Free-text choices** (material names) and the lab-owned settings (heights, air gap,
  blow-out) are not counted.
