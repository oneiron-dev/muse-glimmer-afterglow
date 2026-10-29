# Glimmer as its own decision model — results (29 Oct 2026)

**Shipped settings (lead rulings, 29 Oct 10:02):**
- **Route (day):** D1b, one pass, no look-up threshold. The day agent's route step is behind a flag that is on by
  default from day 4. A chosen note means the agent answers with the notes; 0 means it asks the user. Measured on w4:
  0.695 in this form, 0.705 with the weights-answer compare, against 0.419 for always looking up; never-told 30/30
  asked.
- **Supersede (day, at remember time):** base weights, one pass, link only at p ≥ 0.9, over the 5 nearest earlier
  notes (8 at night). D1b at its calibrated 0.97 gains 2 real links (11 vs 9 of 18) but keeps a wrong link on the
  synthetic set, so it is not switched this week.
- **Duplicate (night):** base weights, p ≥ 0.9, a logged check beside the code rule (identical key + value). It
  never merges.

**What works, what does not (w4 test, plus real corrections from the simulated days):**
- Works: base Glimmer decides in one pass (~0.12–0.47 s on the server). Route with a look-up below p 0.6 scores 0.652
  against 0.419 for always looking up, and asks on all 30 never-told questions. Supersede at p ≥ 0.9 makes no wrong
  link on the day-2 and day-3 corrections. The decision LoRA (D1) gets all 126 w4 duplicate test pairs right, but
  that does not transfer: on real day-3 notes it called 4 pairs "the same fact" at p ≥ 0.96 and only 1 was.
- Does not work: D1 does not beat base on route end to end (0.576 vs 0.652). D1 links none of the clean w4
  corrections (base: 1 of 6). Reasoning before the answer (D3) makes duplicate and real supersede decisions worse.
  No route arm reaches always-look-up's forward score (0.656) while keeping the never-told asks.

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

## D3: base Glimmer, reasoning on, pair kinds (mel slot 0, 29 Oct 04:27–04:45)

Each pair is asked again with reasoning strength high. The model thinks for up to 1,024 tokens, then the same
one-token labels are read after its reasoning. A call that ends at the cap counts as undecided, never as an answer
(lead ruling, 04:40). 244 pairs: every w4 duplicate and supersede test row, the 44 day-2 corrections, and the
calibration pairs below p 0.9. Readouts: `readout-D3.jsonl`; scores: `score-D3.json`.

| kind | n | one pass | reasoning on (undecided = not right) | undecided at cap | wrong at p ≥ 0.9, reasoned | reasoning on below p 0.7 | reasoned ms p50 / p95 |
|---|---|---|---|---|---|---|---|
| duplicate, w4 test | 126 | 0.944 | 0.770 | 5 | 24 | 0.913 | 3,345 / 11,548 |
| supersede, w4 test | 36 | 0.944 | 0.972 | 0 | 1 | 0.972 | 1,470 / 4,738 |
| supersede, real day-2 corrections | 44 | 0.886 | 0.795 | 1 | 8 | 0.841 | 1,766 / 7,070 |

Reasoning does not rescue the unsure one-pass answers. On real corrections it turns true corrections into "both stay
true" at p 1.0 and still misses both corrections the day agent left unlinked. It gains one row on the synthetic
supersede test. A reasoned decision takes 1.5–3.3 s at the median (190–430 reasoning tokens); one pass takes ~0.19 s.
The product path is one pass with a precision cut: link at p ≥ 0.9, otherwise keep both notes. Thinking stays off for
these kinds on the base weights. It will be measured again on the decision adapter (D2).

## D1: the decision LoRA, one pass (trained on mel 06:03–06:26; read out 06:38–07:01)

The entry's trainer runs the R2 recipe: r16, attention + MLP, all 52 layers, LR 1e-4, one epoch. The assistant reply
is the label alone, so the loss falls on the label token and its end token. Data: 1,123 rows, all from w1–w3 (route
560, supersede 286, duplicate 277), 602k tokens. Training: 71 steps, 23 min, peak 19.3 GiB. The GGUF verify passed
(`train-d1/`). It is served on mel as a no-world adapter. The readout puts it at scale 1 and every world adapter at
scale 0 (`readout-D1.jsonl`, `score-D1.json`). The Dreamer shared the server during the readout, so client latency
here is not comparable to D0; server time is. A chat check on the server found 42/42 answers byte-identical with D1
loaded at scale 0 and without it.

| kind (w4 test unless named) | D0 | D1 | D1 − D0 (paired bootstrap 95% CI) | D1 ECE |
|---|---|---|---|---|
| route, note pick (210) | 0.610 | 0.676 | +0.067 [0.000, +0.137] | 0.180 (D0 0.101) |
| duplicate (126) | 0.944 | **1.000** | +0.056 [+0.016, +0.103] | 0.002 |
| supersede, synthetic (36) | 0.944 | 0.972 | +0.028 [−0.056, +0.111] | 0.031 |
| supersede, real day-2 corrections (44) | 0.886 | 0.727 | −0.159 [−0.318, 0.000] | 0.268 |
| supersede, clean w4 corrections (14 pairs, 6 true) | 0.786 | 0.571 | −0.214 | 0.427 |

Route end to end (w4), score = (right − wrong) / n:

| arm | forward | never-told | all |
|---|---|---|---|
| always look up (arm D) | 0.656 | −1.000 | 0.419 |
| D0 + look up below p 0.6 | 0.594 | 1.000 | 0.652 |
| D1 (calibration picks no threshold) | 0.506 | 1.000 | 0.576 |

The lead's bars for D1:
1. Route forward ≥ 0.656, keeping 30/30 never-told asks: **not met** (0.506). D1 picks the right note more often
   than D0 (88 vs 71 of 150 answerable questions). It still says "ask" on 41 answerable questions. Its confidence is
   less useful than D0's (69 wrong at p ≥ 0.9 on calibration, against D0's 38), so no threshold fit on w1–w3 helps.
2. Supersede links at p ≥ 0.9 on the true corrections, with no wrong link: **not met**. D1 links none of the 6 clean
   w4 corrections (D0: 1). It answers "both stay true" at p ≈ 1.0 on all of them. Cause: the supersede training rows
   were 86% "both true" (246 of 286). The pair-level leak fix removed most of the reworded-key "replaces" rows, leaving
   11 reworded-key positives against 190 negatives, so D1 learned "a different key means both stay true".
3. Duplicate: D1 is right on all 126 w4 pairs at p ≥ 0.9.

Product reading: keep supersede and route on the base weights (D0) with their cuts. D1 is a candidate for the night's
duplicate decision. A D1b would need balanced reworded-key supersede positives and fewer "ask" route rows.

### Day-3 corrections (check set, never trained in any form; 29 Oct 07:02–07:05)

34 pairs from the simulated day 3 of all four worlds: 12 true corrections, 4 of them left unlinked by the day agent.
Rows: `decide/data/supersede-day3-check.jsonl`.

| arm | accuracy | links at p ≥ 0.9 (of 12) | wrong links | unlinked corrections caught at p ≥ 0.9 (of 4) |
|---|---|---|---|---|
| D0 base | 0.853 | 8 | 0 | 1 (w4 advisor room, p 0.93) |
| D1 | 0.853 | 5 | 0 | 0 |

### D1 as the night's duplicate check on real notes (w4 day 3, 29 Oct 07:15–07:25)

Meta night ran `Decider.duplicate_check` on the w4 day-3 notes. It made 178 pair calls, logged only, behind the
code rule. D1 called 4 pairs that the rule kept apart "the same fact" at p ≥ 0.96. Checked against world.json:

| pair | D1 p | truth |
|---|---|---|
| email_permission vs email permission for FjordSonics Ltd | 0.999 | same fact (F41) |
| morning_location: Room 312 desk vs advisor office room: Room 312 | 0.995 | different facts (F37, F78), shared value |
| workshop location: Brekvik vs workshop timing: next week at Brekvik | 0.962 | different attributes (no world fact holds either value) |
| deployed hydrophone tag: QT-207 vs QT-207 recalibration deadline | 0.967 | different facts (F72, F73) |

1 of 4 is right. The w4 duplicate test's positives were a note against its restatement, and its negatives had no
shared-value pairs, so 126/126 there does not carry over to real near-duplicates. D1 stays a logged check that
never merges. D1b adds "different facts, same value" negatives built by code.

### Day-3 duplicate check, all four worlds (29 Oct 08:20–08:22)

123 real pairs: the pairs the night's duplicate check reads over the day-3 notes, keeping those with at least one
day-3 note. Labels come from world.json (each note maps to the fact it held that day). 3 pairs are the same fact; 42
are different facts that share a value. Rows: `decide/data/duplicate-day3-check.jsonl`, built by
`decide/duplicate_check_set.py`.

| arm | accuracy | "same" at p ≥ 0.9 on true pairs (of 3) | false "same" at p ≥ 0.9 |
|---|---|---|---|
| D0 base | 0.984 | 2 | 0 |
| D1 | 0.976 | 3 | 2 (Room 312 F37/F78; QT-207 F72/F73) |

Base makes no false "same" at p ≥ 0.9 on real notes. D1 makes two, both in w4. D1b adds code-built different-fact
pairs that share a value. This set is part of its bar.

## D1b: the decision LoRA with D1's gaps fixed (trained on nelly 09:16–09:39; read out on mel 09:53–10:00)

Same trainer and recipe as D1 (r16, attention + MLP, all layers, LR 1e-4, one epoch). Rows come from
`decide/rows_d1b.py` (1,283 rows, 661k tokens, w1–w3 only, 0 question leaks, 0 whole-pair leaks against every check
set):
- supersede: 72 positives whose earlier note is Glimmer's own restatement of the old note; negatives cut to 2 per
  positive;
- route: 20% asks instead of 36%, and each of the 300 route rows given twice;
- duplicate: 70 "different facts" pairs that share a name, code, time or number.

Training took 81 steps and 23 min, at a peak of 19.3 GiB. The GGUF passed its check; sha256 64f75d77…817a
(`train-d1b/`). Readouts: `readout-D1b.jsonl`; scores: `score-D1b.json`, `bars-D1b.json`.

### Route (w4): the bar is met

| arm | forward (180) | never-told (30) | all 210 |
|---|---|---|---|
| always look up (arm D) | 0.656 (149 right / 31 wrong / 0 asked) | −1.000 (0 asked) | 0.419 |
| D0 + look up below p 0.6 | 0.594 | 1.000 | 0.652 |
| D1 | 0.506 | 1.000 | 0.576 |
| **D1b** (calibration picks no look-up) | **0.656** (143 / 25 / 12) | **1.000** (30 asked) | **0.705** |

D1b picks the right note for 114 of the 150 answerable questions (D0: 71, D1: 88). It says "ask" on only 7 of them
(D0: 49) and asks on all 30 never-told questions. On forward questions it ties always looking up with fewer wrong
answers (25 vs 31). Over all 210 it is the best route arm.

### Supersede and duplicate: the bar at p ≥ 0.9 is missed by one wrong answer each

| set | D0 at p ≥ 0.9 | D1b at p ≥ 0.9 | D1b at its calibrated cut |
|---|---|---|---|
| supersede, clean w4 day-2 corrections (6) | 1 link, 0 wrong | 6 links, **1 wrong** | 3 links, 0 wrong (cut 0.97) |
| supersede, day-3 corrections (12, all worlds) | 8 links, 0 wrong | 9 links, 0 wrong | 8 links, 0 wrong (cut 0.97) |
| supersede, synthetic w4 test (12) | 12 links, 1 wrong | 12 links, 1 wrong | 12 links, 1 wrong (cut 0.97) |
| duplicate, real day-3 pairs (3 same) | 2 same, 0 false | 3 same, **1 false** | 2 same, 0 false (cut 0.98) |
| duplicate, w4 test (63 same) | 22 same, 0 false | 62 same, 0 false | 56 same, 0 false (cut 0.98) |

Each calibrated cut is the lowest p with no wrong answer on the w1–w3 calibration rows (supersede 0.97, duplicate
0.98). It was fit before looking at the test sets above. The wrong link at p ≥ 0.9 links "group_meeting: Fridays
16:00" to "advisor weekly meeting time: Wednesdays 09:30" (p 0.93). The false duplicate is the same Room 312 pair as
D1's (p 0.97). D1b's day-3 supersede accuracy is 0.971 against D0's 0.853.
