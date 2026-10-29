# Night 3, 29 Oct (day-3 logs of all four worlds)

Every night trains from base on all facts still active after day 3 (the notes are cumulative), on nelly (lent by
OKC), and is scored in llama.cpp v0.7.0 target-only on the day-3 probes: earlier probes still true, 3 probes per new
or corrected value, stale probes on the old values, 30 never-told probes. Receipts: `runs/<world>/3/night/`; table
data in `nights.json`.

Recipe (one for all four worlds): the 2b recipe without `--train-header` (answer header masked), plus
`--drop-text-acks`: R2 recipe + 24 unknown-key rows + 53-60 agent anchor rows from exact token ids + never-told leak
check. Consolidation: the supersede readout (k=8, on its input contract) links corrections the day agent did not
link; notes saved at style-feedback turns are held out (passed to Meta steering, never training text).

B/D columns are right/wrong/abstain on the forward probes, by the day the still-true fact arrived. Never-told is
abstentions out of 30. Tools = remember + recall out of 20 (base 20/20 in every world). Gate = the SimpleQA gate
slice (instructed "say so if you do not know"), abstain rate base -> night. "night links" = corrections the
consolidation linked itself.

| world | decision, v3 (failed) | B still true | D still true | stale B · D | never-told B/D | tools | channel | gate abstain | forgetting | night links |
|---|---|---|---|---|---|---|---|---|---|---|
| w1 | rollback (abstention_gate) | d1 93/14/1 · d2 12/6/0 · d3 20/19/0 | d1 108/0/0 · d2 14/4/0 · d3 32/5/2 | 0/27 · 0/27 | 1/27 | 10+10 | 30/30 | 0.585 -> 0.355 (right 5 -> 2) | +2.5 pt [-2.7, +7.7] | 0 |
| w2 | rollback (abstention_gate) | d1 80/67/0 · d2 10/8/0 · d3 18/21/0 | d1 135/7/5 · d2 12/3/3 · d3 29/8/2 | 0/27 · 2/27 | 0/19 | 10+10 | 30/30 | 0.585 -> 0.215 (right 5 -> 4) | +2.5 pt [-2.4, +7.3] | 0 |
| w3 | keep (-) | d1 58/91/1 · d2 5/13/0 · d3 11/28/0 | d1 110/16/24 · d2 12/5/1 · d3 30/8/1 | 8/27 · 6/27 | 1/26 | 10+10 | 30/30 | 0.590 -> 0.625 (right 5 -> 3) | +1.5 pt [-3.4, +6.4] | 0 |
| w4 | rollback (abstention_gate) | d1 69/84/0 · d2 12/6/0 · d3 13/23/0 | d1 112/39/2 · d2 16/2/0 · d3 18/18/0 | 1/27 · 4/27 | 0/16 | 10+10 | 30/30 | 0.585 -> 0.100 (right 5 -> 7) | +4.5 pt [-0.4, +9.3] | 1 |

The table's decisions are under gate v3, the rule in force when the nights ran. The owner ruled B on 29 Oct 11:33
(product-2s: in the day agent's own form, the general abstain rate within 10 points of base either way, and never-told
abstentions no more than 3 below base); the v3 gate stays disclosed. Night 3 was re-decided of record under product-2s
on Meta steering's product-form read (200 general + 30 never-told, the real agent loop, day-3 notes in context,
search off; `runs/<world>/3/night/eval/product-abstention-night.json`):

| world | decision, product-2s (of record) | general declines, base -> night (of 200) | never-told declines, base -> night (of 30) |
|---|---|---|---|
| w1 | rollback | 94 -> 86 (-4.0 pt) | 30 -> 22 (8 below base; limit 3) |
| w2 | **keep** | 96 -> 107 (+5.5 pt) | 30 -> 27 (at the limit) |
| w3 | rollback | 88 -> 122 (+17.0 pt; limit ±10) | 29 -> 29 |
| w4 | rollback | 96 -> 165 (+34.5 pt) | 30 -> 19 |

Serving: `runs/w2-solo-developer/adapters.json` current = day 3; w3 goes back to day 1 (R2), w4 stays at day 2 (2b,
promoted under B), w1 at day 1. The lead keeps w1 and w3 on base + notes. Every night trains from base, so a pointer
affects serving only.

## What each result says
- **Tool calls and the answer channel hold in all four worlds**: 20/20 tool calls and 30/30 chat replies in the
  user channel. Both night-2 fixes (agent anchors from exact token ids; no store-request rows answered in text)
  carry over, and the answer header needs no training.
- **Forgetting passes everywhere**: every lower bound is above -5 pt.
- **The instructed gate fails in three worlds**: w1, w2 and w4 abstain far less than base on the gate prompt (35.5%,
  21.5%, 10.0% vs 58.5%). w3 passes at 62.5%. Under the owner's rule B the gate is disclosed, not a check.
- **The product form fails both ways under one recipe**: w1 and w4 answer never-told questions by attaching a saved
  note to the wrong person ("Rasmus Duarte's safety officer is Lotte Brandt"); w3 and w4 decline general questions
  base answers, mostly with "I don't have that in your notes" (38 of w3's new declines), the unknown-key rows' wording
  carried from the user's facts to public facts.
- **New facts are learned only in part**: day-3 facts score 11-20 right of 36-39 in B, and 18-32 in D (notes in
  context). Day-1 facts stay strong in w1 (93/14) and weak in w3 (58/91).
- **Stale values**: 0-1 of 27 in w1, w2, w4. w3 gives the old value 8 of 27 times, from two corrections that neither
  the day agent nor the night linked (N0005, N0025). The readout linked one correction in w4 (N0042, p 0.92).
  For night 4 the lead chose option (a) (below).
- **Never-told B stays 0-1 of 30 in every world**: the weights alone answer a key nobody told them. D (notes in
  context) abstains 16-27 of 30. The fix under test is w2 night-3b (`--own-note-in-block`) and the look-it-up arm.

## Night 4: unlinked corrections, option (a)
Measured offline on days 2-3 of all worlds against `world.json` (`supersede_options_eval.py`): (a) the base readout's
top-1 "replaces" at p >= 0.6 with the same value shape and a change word in the day's wording: 5 right, 1 "wrong"
that restates the same fact (dropping the older copy loses nothing); (b) the decision adapter D1b at p >= 0.97: 1
right, 0 wrong. The lead chose (a) for night 4 (`night/consolidate.py`, 964eb3b). Every fire is logged in
`consolidated.json` (old note, new note, p, shapes, change word), and the morning report lists them under "The night
treated these as corrections" (Meta day, PR #75). Night 4's stale and B probes on w3 and w4 are the real-world check.
