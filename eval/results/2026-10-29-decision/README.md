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

## D0: base Glimmer, one pass, no adapter (mel, 29 Oct 04:16–04:25)

llama.cpp v0.7.0 (b1-620d2225c), Q4_K_M, slot 0. Each call sends one token with pre-sampling `n_probs` 128 and
`cache_prompt` false, with all four world adapters named at scale 0. 1,077 calls, one at a time. Readouts:
`readout-D0.jsonl`; scores: `score-D0.json` (`decide/score_decisions.py`).

### Route, w4 test (a world no decision step has seen)

Each arm below runs the full route path, scored as right / wrong / asked; score = (right − wrong) / n.

| arm | forward (180): right / wrong / asked, score | never-told (30): asked, score | all 210 score |
|---|---|---|---|
| always look up (arm D) | 149 / 31 / 0, 0.656 | 0, −1.000 | 0.419 |
| D5, app recall | 59 / 9 / 112, 0.278 | 13, −0.133 | 0.219 |
| D5, oracle retrieval (ceiling) | 136 / 14 / 30, 0.678 | 30, 1.000 | 0.724 |
| D0 route decision → D5 compare | 96 / 8 / 76, 0.489 | 30, 1.000 | 0.562 |
| D0 + look up below p 0.6 (fit on w1–w3) | 123 / 16 / 41, 0.594 | 30, 1.000 | 0.652 |

The decision alone picks the right note for 71 of the 150 answerable questions. It picks a wrong note for 30 and
says "ask" for 49, mostly unsure (median p 0.51). It says "ask" for 57 of the 60 questions the notes cannot answer,
including all 30 never-told questions. Below the calibrated threshold, the answer comes from the notes (arm D). Every
answer kept from the weights at that threshold is right (25/25). The bar for D1 is arm D's forward score (0.656)
while keeping the never-told asks.

### Pair decisions

| kind | split | n | accuracy | ECE | wrong at p ≥ 0.9 |
|---|---|---|---|---|---|
| duplicate | w4 test | 126 | 0.944 | 0.110 | 0 |
| supersede | w4 test | 36 | 0.944 | 0.095 | 1 |
| supersede | real day-2 corrections (check set, never trained) | 44 | 0.886 | 0.135 | 0 |

In the day-2 check, the day agent saved 7 corrections under a new key and left the old value active. D0 links 5 of
those 7. It misses "repair lead morning → head mechanic" (p 0.52) and "catch_up_time → advisor weekly meeting time"
(p 0.61). Rows: `decide/data/supersede-day2-check.jsonl`, built by `decide/day2_supersede_check.py`.

### Speed (one decision = one forward pass, no prompt cache)

| kind | prompt tokens (p50) | server ms p50 / p95 | client ms p50 |
|---|---|---|---|
| route (every note is an option) | 860 | 470 / 508 | 512 |
| duplicate | 125 | 141 / 161 | 190 |
| supersede | 128 | 143 / 157 | 192 |

The route prompt puts the notes first and the question last, so a cached notes prefix would leave only the question
to compute. This run turns the prompt cache off, as the eval rules require.
