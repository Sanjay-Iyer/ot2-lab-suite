---
name: nanodrop-dilutions
description: How Agent NanoDrop changes the dilution series - factors, plate column and rows, one-dilution edits, the dilution step on/off, dye/water percentage mixtures, and what this setup cannot make.
domain: dye_demo
agent: nanodrop
order: 3
scopes: [dilution]
---

# Dilutions

One dye (the sample) is diluted with water (the solvent) down ONE column of the 96-well plate, one dilution per plate
row, each made directly from the stock (no serial dilutions). The i-th factor is dilution:i.

## Fields
dilution.enabled (false: the whole dilution step is off - print what is already in the plate), dilution.factors (list,
each >= 1; 1 = neat), dilution.plate_column ("1"-"12"), dilution.rows (PLATE rows of the series, e.g. ["C","D","E"]),
dilution.start_row, dilution.total_volume_ul (final volume per well), dilution.prepared_volume_ul (for dilutions made
earlier), mixing.enabled, mixing.reps, mixing.volume_ul.

## Editing the series (with the edit mode for dilution - see nanodrop-edit-semantics)
- Naming the dilutions to make sets the series - REPLACE: "Make 2x, 4x and 8x dilutions" -> [2, 4, 8].
- ADD "add a 10x dilution" -> {"op": "set", "path": "dilution.factors", "value": [10]} with edit mode add.
- MODIFY one dilution "change the 4x to 5x" -> {"op": "set", "path": "dilution.factors", "item": 4, "value": 5}
  with edit mode modify (only that dilution changes).
- REPLACE "replace the series with 3x and 6x" -> {"op": "set", "path": "dilution.factors", "value": [3, 6]} with edit
  mode replace.
- Relative: "twice as dilute" -> scale_each factor 2; "use four dilutions" with no factors -> set_count count 4.
- Never invent a factor the scientist did not give.

## Prints follow dilutions by reference
Prints name dilution:N, so changing or moving the series needs no print edit. If the series gets shorter, a print of a
dilution that is gone stays exactly as it was (RUN STATUS says it is missing and no run starts): never re-point it to
another dilution and never add a factor for it. Say so in "response" ("the dilution 3 print would have no dilution 3
until you add one or choose another").

## Moving the dilutions
The whole series sits in one plate column, so moving one dilution to another column moves them all: say so in
"response" ("This would move the series to column 10: dilution 1 to A10 and dilution 2 to B10").

## The dilution step on or off
dilution.enabled false turns off the WHOLE step: no well is diluted, and every print comes from liquid already in the
plate. Describe it that way ("no dilutions would be made; the prints come from what is already in the plate") - never
as skipping one well.

## Percentages are dilutions
A dye/water mixture by percentage IS a dilution of the dye stock, whatever it is called - a mix, a mixture, a sample, a
solution, a dilution: "Make a 10% dye / 90% water mix" (or mixture, sample, solution) -> dilution.factors ["10%"]
(Python converts the dye percentage to the fold factor: 10% -> 10x, 25% -> 4x), with the same edit mode as any named
series. "2x and 5x dilutions" -> [2, 5].

## What cannot be made here
Only one dye and water. A mixture with anything else - two dyes, ethanol, a buffer as a third liquid - cannot be made:
"Make 20% dye, 30% ethanol and 50% water" -> kind question, no operations: "I can't make that: this setup has only
the dye and water, so there is no way to add ethanol." Do not offer a dye/water substitute (e.g. a 20% dye dilution)
unless the scientist asks for alternatives.
