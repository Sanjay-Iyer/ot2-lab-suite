---
name: nanodrop-printing
description: How Agent NanoDrop describes what prints and where - logical sources, per-source print intents (count, rows, columns, exact positions, drops), one-group edits, and plan-wide print settings. The compiler, not the agent, decides exact paper positions.
domain: dye_demo
agent: nanodrop
order: 2
scopes: [printing]
---

# Printing

The paper print plate has rows A-H and columns 1-12 (rows 1-8 are A-H). Python's compiler turns what you write here
into exact paper positions when the plan is compiled; you never compute positions yourself.

## What prints: sources
- "dilution:1", "dilution:2", ... is the 1st, 2nd, ... output of the dilution series (DILUTION OUTPUTS). Use it for
  any dilution of this plan: "the first one", "the 2x one", "that dilution" - and also for a plate well that holds one
  of this plan's dilutions ("A11" when DILUTION OUTPUTS shows dilution:1 in A11 -> "dilution:1"). The print then
  follows the dilution if the series moves.
- A plain plate well ("A11") only for the scientist's own sample that is not one of the dilution outputs. If the
  scientist says their own sample is in a well the dilution step would fill, also turn the dilution step off
  (dilution.enabled false) or ask.
- A dilution the plan does not have yet is kept exactly as said - it resolves once that dilution exists, and RUN
  STATUS says it is missing. Never add or change dilution factors, wells or volumes to make it resolve, and never
  point it at another dilution:
      2 dilutions, "Print only dilution 3 three times." -> printing replace: [{"source": "dilution:3", "count": 3}]
      (NOT a 3x factor, NOT a third dilution, NOT dilution:2)
      then "Make that five prints instead." -> printing modify: {"source": "dilution:3", "value": {"count": 5}}
- "The first one", "the second one": a dilution (dilution:N) when the conversation is about dilutions; a print group
  ("group": N) when it is about print groups. When both fit and differ, ask.

## print.source: print groups, each with its own intent
print.source is the list of print groups in print order. A group is a source, optionally with only what the scientist
said about it:
    {"source": "dilution:1", "count": 3}            3 prints, positions chosen by the compiler
    {"source": "A11", "rows": ["A","C","E"]}        one print on each of those paper rows
    {"source": "A11", "columns": [4, 5]}            its prints in those paper columns
    {"source": "B11", "positions": ["A1","C1"]}     exactly there - ONLY when the scientist names the positions
    {"source": "C11", "drops": 4}                   4 drops stacked on each of its positions (drops are never prints)
A liquid may be in several groups. Every field is optional; plan-wide settings fill in what a group does not say.

Operations (with the edit mode for printing - see nanodrop-edit-semantics):
- several groups at once: {"op": "set", "path": "print.source", "value": [<groups>]}
- one group: {"op": "set", "path": "print.source", "source": "B11", "value": {"count": 4}}
  or by its place in print order: {"op": "set", "path": "print.source", "group": 2, "value": {"source": "dilution:3"}}
  ("use the third dilution for the second print group" - its count, rows and drops stay)
- stop printing a group: {"op": "remove", "path": "print.source", "source": "A11"}
- forget one field of a group: {"op": "remove", "path": "print.source", "source": "A11", "value": ["positions"]}

Within one group the newest placement wins: a new count replaces its rows or exact positions, new rows replace its
count or positions, new positions replace everything ("never mind the rows, just print it four times wherever" ->
{"count": 4}). Fields said together stay together ("3 prints on rows B and C").

Whatever is said about the prints of ONE source - by name or by reference ("those", "it", "its prints") - belongs to
that group, not to the plan-wide settings: "put those on rows 1, 3 and 5" right after "print A11 three times" ->
{"source": "dilution:1", "value": {"rows": ["A","C","E"]}}. A pronoun means the RECENT FOCUS group (see
nanodrop-edit-semantics), even with other groups in the plan.

## Plan-wide print settings
print.replicates (prints per source), print.droplets_per_spot (drops on every position), print.paper_rows (paper rows
paired in order with the groups that give no rows of their own; a single group prints on every listed row),
print.columns (exact paper columns), print.paper_start_column, print.droplet_volume_ul (µL; a list prints several
volumes), print.drops_at ({"A2": 3}: drops on particular positions), print.enabled.
A plan-wide command ("make all of them twice", "2 drops each") is the newest instruction for every group: Python drops
the per-group counts, drops, rows or columns it replaces (never exact positions). To keep an exception say it in the
same reply ("all twice but A11 three times" -> print.replicates 2 + the A11 group with count 3).
Plate rows (where the dilutions are) and paper rows (where they print) are independent.

print.source_map is an older form of exact pairs; prefer groups with "positions".
