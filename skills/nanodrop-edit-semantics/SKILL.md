---
name: nanodrop-edit-semantics
description: How a scientist's message relates to the plan Agent NanoDrop already has - a question, or a change that ADDs, MODIFYs or REPLACEs within a scope (printing, dilution, plan); the startup default selection, the recent focus, and the open safety question. Read before every turn.
domain: dye_demo
agent: nanodrop
order: 1
scopes: [printing, dilution, plan]
---

# Edit semantics: question, add, modify, replace

Python executes exactly the edit you label; it never reads the scientist's words to guess it. Label every change.

## Question or change
WHO DOES IT decides. The scientist asking about THEMSELVES doing something asks whether it is possible: a QUESTION
(kind question; nothing changes). The scientist asking YOU to do it, or telling you, is a REQUEST: a CHANGE (kind
plan_change), however politely put.

    QUESTION (I / what if)                          CHANGE (you / imperative)
    "Can I print A11 five times?"                   "Can you print A11 five times?"
    "Could I use column 12 for the series?"         "Could you use column 12 for the series?"
    "Would I be able to add a 20x dilution?"        "Would you add a 20x dilution?"
    "What if I print B11 twice?"                    "Print B11 twice." / "Please print B11 twice."
    "Is it possible to stack 3 drops?"              "I'd like 3 drops stacked." / "Let's stack 3 drops."

A question about the RECENT FOCUS stays a question, and its pronoun still means the focus:
    focus = print group 1 (A11), "Could I put those on rows 2 and 4?"
    -> kind question, "response": "Yes - the A11 prints could go on paper rows 2 and 4 (B and D); B11 stays as it is."
       "hypothetical": [{"op": "set", "path": "print.source", "source": "dilution:1", "value": {"rows": ["B", "D"]}}]
    then "Yes, do that." / "OK, go ahead."
    -> kind plan_change with exactly the operations of that hypothetical (PYTHON NOTE shows them).

For a question about a possible change, say whether it is possible and what it would change, and put the change in
"hypothetical" (never in "operations"). A message that asks for a change AND asks something is a change; answer the
question in "response".

## Scope
Every change belongs to a scope, and Python finds it from the operation's path:
- printing: what prints and where (print.*).
- dilution: the dilution series (dilution.*, mixing.*).
- plan: everything else (deck, tips, materials, liquid handling).
A change never touches another scope unless the scientist asked for both ("only print A11" does not change the
dilutions; "replace the series with 2x and 5x" does not change what prints).

## Edit mode - say it for every scope the message changes
- ADD: keep everything the scope has and add (or update) the items named. "Also print C11 twice", "add a 10x
  dilution", "print B11 too", "and one more on row 4".
- MODIFY: change only the items named; every other item stays exactly as it is. "Make B11 four prints instead",
  "change the 5x dilution to 6x", "make the first one 2 drops", "actually B11 twice".
- REPLACE: the items named become the whole new selection of the scope; everything else in that scope goes. "Only
  print A11 and C11 once each", "just print A11 twice", "replace the series with 2x and 5x", "start over with
  dilution 1 three times", "actually forget that and only print D11".
Write it as "edit_modes": {"printing": "replace"} (one entry per scope changed). Then the operations name only the
items: REPLACE and ADD list the new items; MODIFY names the items it changes.

## PRINT SELECTION: startup default or chosen
PLAN SPEC says which. While it is the STARTUP DEFAULT nobody has chosen what prints: any message that says what should
print CHOOSES it - edit mode REPLACE - unless the scientist says to keep the others ("also", "too", "as well", "in
addition", "keep the others", "leave everything else").

    STARTUP DEFAULT  "Print dilution 1 three times."                       printing replace: [dilution:1 x3]
    STARTUP DEFAULT  "Can you print the first dilution three times?"       printing replace: [dilution:1 x3]
    STARTUP DEFAULT  "Also print dilution 1 three times."                  printing add: the others keep printing
    STARTUP DEFAULT  "Keep the others, but make dilution 1 three times."   printing modify: the others keep printing
    CHOSEN [A11 x1, B11 x1]  "Make B11 three times."                       printing modify: A11 stays x1
    CHOSEN [A11 x1, B11 x3]  "Also print C11 twice."                       printing add
    CHOSEN [...]             "Actually, only print C11 four times."        printing replace: [C11 x4]

## RECENT FOCUS: "those", "it", "them", "that one"
RECENT FOCUS in PLAN SPEC is what the scientist's latest change was about (Python computes it from the plan; a
question never changes it). A pronoun about prints means the focus group(s), and what is said about them is THAT
group's intent (MODIFY with the "source" or "group" selector) - never a plan-wide setting:

    "Only print A11 three times and B11 once."  "Make A11 four times."  "Put those on rows 1, 3 and 5."
       -> printing modify: {"source": "dilution:1", "value": {"rows": ["A", "C", "E"]}}   (B11 stays x1)
    "... Now make B11 three times."  "Put those on rows 2, 4, 6."  -> the B11 group, not A11, not the plan

Only when the focus is "every print group" (the last change was plan-wide) does "those" mean all of them. If the focus
names several groups and the pronoun could mean one or all, ask one short question naming them.

## When to ask instead
Ask one short question only when two readings would do materially different things and nothing settles it: "make the
second one 4" with several print groups -> "Do you mean 4 prints of the second print group, or 4 drops on each of its
spots?". Name genuinely different options (never the same liquid under two names). Do not ask because the plan is
incomplete.

## The open safety question
A PYTHON NOTE may say a SAFETY question is open (is the scientist's own sample already in plate wells the dilution
step would fill?). Only the scientist's own statement about those wells answers it ("yes, it is already there", "I
already loaded the sample" -> dilution.enabled false; "no, make the dilutions first", "those wells are empty" ->
dilution.enabled true), returned with the held request. Anything else - another edit, a question, even "what happens
if I say it is already there?" - does not answer it: return only that message's own kind and operations, never
dilution.enabled. Never answer it for the scientist. It never blocks other work: "Make B11 print four times" while it
is open is an ordinary plan_change, proposed now; Python keeps the question open beside it.

## A proposal is waiting
A message that adjusts the waiting proposal has "revises": true and only what differs ("make B11 five instead", "and
C11 once too"); a new, unrelated request has "revises": false and replaces it ("actually forget that and only print
D11 three times").

## Numbers
Every number you state comes from PLAN SPEC: COUNTS, WHAT A RUN WOULD DO NOW, or - for "this proposal" - the lines IF
PROPOSAL #N IS APPLIED. Quote them; never add, multiply or estimate. If a number is not shown, say so.
