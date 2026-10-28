# Glimmer as its own decision model — results (29 Oct 2026)

Design: `decide/DESIGN.md`. Rows: `decide/data/` (w4 test frozen by sha256 in `FROZEN.json` before any training row).
Score for the route kind: every answer is right, wrong or asked; score = (right − wrong) / n, so asking costs nothing
and a wrong answer costs one.

## D5: the notes agreement gate (baseline; CPU, from R2's saved answers)

Answer from the weights (R2 arm B), take one matching note from the night's consolidated notes, and compare the values.
If they agree, the answer comes from the weights. If they do not, the answer comes from the notes (arm D). If no note
matches, Afterglow asks. Code: `decide/notes_agreement_gate.py`. File: `d5-notes-agreement.json`, with one row file
per retrieval and compare mode.

| 666 forward probes, w1–w4 | routing precision | recall | from weights (right/answered) | looked up (right) | asked | right | wrong |
|---|---|---|---|---|---|---|---|
| app recall (serving path), strict compare | 0.47 | 0.98 | 94/100 | 364 (319) | 202 | 413 | 51 |
| oracle retrieval (ceiling), strict compare | 0.83 | 0.99 | 337/339 | 270 (225) | 57 | 562 | 47 |

Routing precision is the share of overridden answers that were wrong. Recall is the share of wrong answers that were
overridden. Always answering from the notes (arm D) gets 589 right and 77 wrong on the same 666 probes.

The value compare works when the right note is found: 99% of the answers it keeps from the weights are right. The
limit is retrieval. The app's lexical recall finds no note for 202 questions and the wrong note for many others.
BM25 over key, value and statement finds the answering note in its top 8 for only 48% of the questions. So the
route decision reads every note as an option.

Never-told questions (120, facts the user never said): with app recall, D5 asks on 35. With oracle retrieval it asks
on all 120, by construction. Arm D answers almost all of them with a made-up value.
