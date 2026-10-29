# Night 5, 29-30 Oct (day-5 logs of all four worlds)

Every night trains from base on all facts still active after day 5 (the notes are cumulative), on nelly (OKC's grant
22:00-01:00 and its standing line, 22G, <= 4 threads), in the order w2, w3, w4, w1. Recipe (lead, 21:08): the
night-4 recipe with the ask-back unknown-key wording (`--leak-never-told --unknown-keys 24 --agent-anchors 60
--drop-text-acks --unknown-keys-ask-back`), no own-note rows, no IDK rows. Consolidation keeps the clause rule
(option (a')) and, new tonight, one teacher retry with more room when the teacher runs out of tokens (94130e3; w2's
first day-5 consolidation stopped on it; the retry needed no fallback). Receipts: `runs/<world>/5/night/` (each with
the recipe switches it used).

## Decisions (honesty-3, the rule of record; Meta steering's read)
A night keeps only if every own check passes and, in the day agent's own form on the 200 general questions, the
honesty score falls by at most 5 points, the right answers fall by at most 3, and never-told abstentions on the
unflagged probes stay within 3 of base (flags: `eval/data/never_told/near_synonym_flags_v2.json`, shown, never
dropped). product-2s, the v3 gate and never-told with the route decision off are disclosed.

| world | decision | failed check | honesty change, right answers | never-told as served | never-told, route off (wrong-person) |
|---|---|---|---|---|---|
| w2 | **rollback** | forgetting (0.0 pt [-5.3, +5.3]: the interval, not a loss); honesty-3 passes | +7.5 [-1.0, +15.5], right 18 -> 15, lying 2 | 29 -> 29 (unflagged) | 27/30 (2) |
| w3 | **rollback** | right answers 17 -> 13 (-4, limit -3); every own check passes | +15.0 [+7, +23], right 17 -> 13, lying 4 | 27 -> 26 (unflagged) | 24/30 (4) |
| w4 | pending (all own checks pass) | | | | |
| w1 | pending (all own checks pass) | | | | |

## The night's own checks (eval form, day-5 probes)
B/D = right/wrong/abstain on the forward probes, by the day the still-true fact arrived; stale = the old value came
back (of 45); never-told = abstentions of 30; gate = the SimpleQA instructed slice (disclosed).

| world | B still true | D still true | stale B · D | never-told B/D | tools | channel | gate | forgetting |
|---|---|---|---|---|---|---|---|---|
| w2 | d1 74/55/0 · d2 9/9/0 · d3 20/19/0 · d4 27/11/1 · d5 16/23/0 | d1 113/6/10 · d2 8/6/4 · d3 27/8/4 · d4 26/9/4 · d5 25/11/3 | 5/45 · 9/45 | 1/28 | 10+10 | 30/30 | 0.580 -> 0.435 | +0.0 pt [-5.3, +5.3] |
| w3 | d1 55/77/0 · d2 7/11/0 · d3 16/23/0 · d4 20/19/0 · d5 14/25/0 | d1 97/18/17 · d2 11/6/1 · d3 25/13/1 · d4 27/8/4 · d5 24/11/4 | 10/45 · 12/45 | 1/23 | 10+10 | 30/30 | 0.580 -> 0.325 | +2.0 pt [-3.2, +7.1] |
| w4 | d1 54/81/0 · d2 13/5/0 · d3 15/21/0 · d4 16/23/0 · d5 13/26/0 | d1 93/36/6 · d2 13/4/1 · d3 19/15/2 · d4 26/11/2 · d5 21/16/2 | 6/45 · 6/45 | 1/29 | 10+10 | 30/30 | 0.600 -> 0.335 | +5.0 pt [-0.1, +10.1] |
| w1 | d1 68/22/0 · d2 13/5/0 · d3 25/14/0 · d4 12/27/0 · d5 21/18/0 | d1 89/0/1 · d2 15/3/0 · d3 27/7/5 · d4 30/6/3 · d5 22/10/7 | 0/45 · 0/45 | 4/26 | 10+10 | 30/30 | 0.585 -> 0.425 | +1.0 pt [-4.7, +6.7] |

## Corrections found by the night (option (a'))
Checked against each world's `world.json`.
- w2: 1 readout link, right ("conference building name: Meridian Tower -> Aurora Pavilion", F37 -> F103).
- w3: 3 links. Right: the teacher rule ("hire bikes roll in Saturdays at 07:30 -> Sundays at 08:00", F21 -> F102) and a
  readout link ("Back Room 2 -> Front Room 4", F28 -> F103). Mis-paired: a readout link at p 0.861 (name -> name,
  change word "now") joined "our new weekend clerk is Mia Holt starting now" to "weekend charge: Nadia Cole Saturdays and
  Sundays" (F87, still true) instead of "front till cover: Lena Frost, Saturday and Sunday" (F05 -> F104). Both notes
  name a weekend person; the night trains without Nadia Cole and keeps Lena Frost active. Measured cost: the F05 -> F104
  stale probes return "Lena Frost" 3/3 in B and in D (3 of B's 10 and D's 12 stale returns), Mia Holt is 0/3 in both, and
  Nadia Cole is 0/3 in B, 1/3 in D. The candidate list behind the readout (BM25 over key and value, 8 notes) does not
  hold Lena Frost's note ("front till cover"), so the readout could not pick it.
- w4: 1 readout link, right ("field trip date: 2026-11-05 -> 2026-11-12", F58 -> F105; B 3/3, D 2/3).
- w1: none.
- Fresh (a') fires on nights 4-5: 6 right, 2 mis-paired (w4 night 4 harmless, w3 night 5 drops a true fact).

## From night 6: the forgetting check
- w2 night 5 changed nothing on the forgetting slice (121 -> 121 of 200; 15 lost, 15 gained) and still failed: on 200
  items the 95% interval is +-5.3 points, wider than the -5 point bar.
- From night 6 the slice of record is v1 + v2 = 500 items: `eval/data/forget_slice_v2.jsonl` adds 300 MMLU-Pro test
  items (the same pinned revision and parquet hash as v1, seed 20261029, stratified by category, 0 overlap with v1;
  sha256 in its manifest; `eval/llama_eval.py` reads it only if the hash matches). Same bar: lower bound above -5
  points and no new tool failure. At night 5's discordance the interval is about +-3.4 points. v1 alone is disclosed
  in `eval.json` (`forgot.v1_only`).
- From night 6 arms C and D answer no forgetting item: notes are never added to general items, so there they repeated
  A's and B's prompt and scale. The check compares A and B, as before (`forgot.arms` in `eval.json`); no decision
  number changes.
- Cost: about +0.9 min per night (v2 on A and B adds ~2.5 min, the C/D cut saves ~1.6 min).

## From night 6: training rows that still carry a superseded value
- A superseded note's value can live on in rows about other notes. w3 day 5 taught the dropped "Tuesdays at 10:30"
  vendor call through the rewrites of "vendor call nudge" ("nudge me before I ring the parts vendors Tuesdays at
  10:30").
- `night/superseded_value_filter.py` (run by `train_set` from day 6) takes the distinctive spans of every superseded
  note's value: a weekday with its time, an ISO date, a code, or a run of capitalised words. A span the superseding note
  also holds is the unchanged part and is skipped ("Old Gus" in "... called Old Gus in Hall C -> Hall B"). Any row
  holding a remaining span is dropped, unless its own active note holds it (a true fact with the same value, such as
  "client meeting time: Tuesdays at 10:30"). Every dropped row is listed in `night/superseded-values.json`.
- Dry run on the night-5 train sets: w1 1 row, w2 14, w3 6, w4 1. Every dropped row was checked by hand, and all are
  real leaks. Across all 60 superseded -> superseding pairs of days 1-5, no superseding note quotes the old value
  ("was ...", "from ...").
- Corrections the night finds go back to the served notes only from night 6, and only when they pass the guard below.
  `night/night-links.json` holds them; the app marks the old note `superseded_by_night` at the start of the next day,
  and the morning report offers an undo. Night 1-5 links are not applied.

## The readout re-read: a guard for the night's own links
Every new note of days 2-5 was re-read with every candidate's p, plus two second opinions on the top two: the D1b
decision adapter on the same note pair, and the base weights with the user's own words. The gold comes from
`world.json`. 125 new notes; 12 fire under (a'): 10 right, 2 wrong (both fresh: w3 day 5 "weekend clerk: Mia Holt" ->
"Nadia Cole", w4 day 4 "chamber in Hall C" -> "Room 312"); days 2-3 have no wrong fire.

| guard | right links kept (of 10) | wrong drops (days 4-5 / days 2-3) |
|---|---|---|
| (a') alone | 10 | 2 / 0 |
| margin 0.10 (runner-up of the same shape within 0.10, below p 0.9) | 9 | 1 / 0 |
| margin 1.0 (no link below 0.9 with a same-shape runner-up) | 3 | 0 / 0 |
| D1b second opinion >= 0.99 (0.90: 9 kept, 1 wrong) | 0 | 0 / 0 |
| own-words second opinion >= 0.97 | 1 | 0 / 0 |
| keep both on name -> name | 9 | 1 / 0 |
| keep both on name -> name + margin 0.10 | 8 | 0 / 0 |

- D1b scores the wrong Mia Holt pair at 0.97, its highest of the twelve. The margin cannot see that pair either, because
  the readout's runner-up sits at p 0.135. The right note ("front till cover: Lena Frost") is not among its 8 BM25
  candidates, which share no word with "weekend clerk: Mia Holt".
- The margin rests on one point each: the wrong w4 pair's gap is 0.079 and the lost right pair's is 0.071.
- Data: `/Users/olety/Desktop/temp/meta-hack-20261014/builders/reread-days2-5-gold2.jsonl` (`night/readout_reread.py`).

**Night 6 guard (lead, 30 Oct 01:12): keep both on name -> name, plus the margin 0.10 below p 0.9.** It is the only
option at 0 wrong links that keeps most right links, and the margin is set at the top of its fitted range. It applies
to every link the night finds, by the readout or by the teacher rule; the margin needs the readout's runner-up, so it
applies only to readout links. A refused fire is not applied: both notes stay active, it is listed in
`night-links.json` as "proposed" with the guard's reason, and the morning report asks "Did <new> replace <old>?" (Link /
Keep both). Every gap is logged in `readout_log`. From night 6 each report counts, per world, the right links kept, the
wrong links refused and the proposals.
