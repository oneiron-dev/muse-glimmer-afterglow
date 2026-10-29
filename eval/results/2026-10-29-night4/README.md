# Night 4, 29 Oct (day-4 logs of all four worlds)

Every night trains from base on all facts still active after day 4 (the notes are cumulative), on nelly (OKC's grant
16:00-20:00, 22G, <= 4 threads), in the order w2, w1, w3, w4. Recipe (lead, 15:33): the night-3 recipe
(`--leak-never-told --unknown-keys 24 --agent-anchors 60 --drop-text-acks`) with no own-note rows and no IDK rows; the
only change is the consolidation's correction rule (below). Receipts: `runs/<world>/4/night/` (each with the recipe
switches it used); table data in `nights.json`.

## Decisions (product-2s, the rule of record; Meta steering's read)
The day agent's own form, as served (route and supersede decisions on, search off), day-4 notes in context, against
the base asked in the same server run. Disclosed beside it: the honesty rule of owner card META-ABSTENTION-RULE-2
option B(2) (honesty score down by at most 5, right answers down by at most 3, never-told within 3), and never-told
with the route decision off.

| world | product-2s | general declines, base -> night (of 200) | never-told as served (of 30) | never-told, route off (wrong-person) | honesty change, right answers, lying IDK | honesty-3 B(2) |
|---|---|---|---|---|---|---|
| w2 | **keep** (served) | 98 -> 111 (+6.5) | 30 -> 30 | 20/30 (10) | +3.5, right 17 -> 15, lying 0 | pass |
| w1 | rollback | 99 -> 156 (+28.5) | 30 -> 30 | 27/30 (3) | +22.5, right 16 -> 10, lying 4 | fail |
| w3 | rollback | 95 -> 158 (+31.5) | 28 -> 27 | 22/30 (6) | +26.5, right 15 -> 10, lying 6 | fail |
| w4 | rollback | 94 -> 139 (+22.5) | 30 -> 30 | 25/30 (5) | +20.0, right 16 -> 14, lying 6 | pass |

Serving: w2 serves day 4; w1 and w3 stay on base + notes; w4 stays on day 2 (2b). Under B(2), if the owner picks it,
w4 night 4 (and w3 night 3) would keep as well.

## The night's own checks (eval form, day-4 probes)
B/D = right/wrong/abstain on the forward probes, by the day the still-true fact arrived; stale = the old value came
back (of 36); never-told = abstentions of 30; gate = the SimpleQA instructed slice (disclosed).

| world | B still true | D still true | stale B · D | never-told B/D | tools | channel | gate | forgetting |
|---|---|---|---|---|---|---|---|---|
| w2 | d1 78/60/0 · d2 7/11/0 · d3 17/22/0 · d4 23/15/1 | d1 124/12/2 · d2 10/7/1 · d3 29/9/1 · d4 29/10/0 | 4/36 · 4/36 | 2/20 | 10+10 | 30/30 | 0.585 -> 0.105 | +4.0 pt [-1.3, +9.3] |
| w1 | d1 79/20/0 · d2 13/5/0 · d3 30/9/0 · d4 15/24/0 | d1 99/0/0 · d2 18/0/0 · d3 37/2/0 · d4 31/7/1 | 0/36 · 0/36 | 0/21 | 10+10 | 30/30 | 0.575 -> 0.280 | +2.5 pt [-2.9, +7.9] |
| w3 | d1 63/78/0 · d2 10/8/0 · d3 14/25/0 · d4 21/18/0 | d1 122/17/2 · d2 10/8/0 · d3 26/13/0 · d4 28/10/1 | 8/36 · 7/36 | 0/7 | 10+10 | 30/30 | 0.570 -> 0.055 | +3.0 pt [-2.3, +8.3] |
| w4 | d1 68/76/0 · d2 7/11/0 · d3 13/23/0 · d4 21/18/0 | d1 101/39/4 · d2 15/3/0 · d3 18/16/2 · d4 19/17/3 | 5/36 · 6/36 | 0/19 | 10+10 | 30/30 | 0.580 -> 0.075 | +1.5 pt [-3.4, +6.4] |

Every night passes all of its own checks (learned, forgetting, tools, channel); every rollback is the product form.

## What each result says
- **One recipe, two outcomes on general questions.** w2 stays in band (+6.5); w1, w3 and w4 decline 22-32 points more
  general questions than base, mostly with "I don't have that in your notes". That wording appears in exactly 16
  training rows per world (the unknown-key replies) and in the base's own declines; w2 carries the same 16 rows, so
  the rows may amplify a base habit rather than create it. Under test: w3 night-4b, the same data with those 16
  replies (24 in all) rewritten to name the key and ask for it (`--unknown-keys-ask-back`).
- **The mis-binding grows in the weights; the route check holds it at serving.** With the route decision off, the
  night attaches a saved fact to another person in 3-10 of 30 never-told questions (w2: 3 wrong-person answers in its
  night-3 read, 10 with the route off at night 4).
  As served, the route's named-person check catches them (30/30 in w1, w2, w4). The rule measures the served
  assistant, so the decisions stand; the route-off line is shown on every decision.
- **Corrections found by the night (option (a'), one rule for the readout and the teacher rule).** w2: 2 links, both
  right ("release dry run is now Tuesdays at 15:00", "deployment window time is now Fridays at 22:00"). w1, w3: 0
  links; the teacher named the old note and was refused (no change word in the clause), so "test fly location:
  Skjerstad Meadows" and "front till cover: Lena Frost" stay active: Skjerstad Meadows and the new Havsborg Quarry are
  both 3/3 right in B and D. w4: "analysis software: EchoLab R2026a" stays active (refused the same way); one link is
  found but mis-paired ("chamber in Hall C" -> "reverb chamber room: Room 312"): a harmless drop of a note already
  superseded on day 2, but Room 204 stays active, and its stale probe (F19 -> F91) returns the old value once in B and
  once in D, as expected. The clause rule was fit on fires already seen (days 2-4), so these are its first fresh fires.
- **Stale values come from corrections no one linked**: w3 8/36 (F17 -> F76 x3, F03 -> F61 x2, F23 -> F62 x2,
  F24 -> F65), w2 4/36 (F07 -> F90 x2, F43 -> F89 once though linked, F33 -> F59), w4 5/36, w1 0/36.
- **Never-told with the notes in context (eval form) falls in w3** (D 7/30, from 26 at night 3).

## w3 night-4b: the ask-back unknown-key wording (night-5 candidate)
The same day-4 data and recipe as w3 night 4; only the 24 unknown-key replies differ (`--unknown-keys-ask-back`: "You
haven't told me your sales region yet. What is it?", no notes wording). Night checks, n4 -> 4b: never-told D (notes in
context) 7 -> 26/30, B 0 -> 3; gate (disclosed) 5.5% -> 37.0%; B facts d1 63/78 -> 64/77, d3 14/25 -> 9/30, d4 21/18 ->
18/21; D d1 abstain 2 -> 19; stale B 8 -> 9, D 7 -> 5; forgetting +3.0 pt both; tools 20/20, channel 30/30. One run of
each, so the fact differences may be run-to-run variation. Its product-form read (general declines against w3 n4's
+31.5) is the test; it lands in a later PR.

## Fixes made today that touch these numbers
- Option (a') (629a8da, e18bedb): a correction the night finds, by the readout or the teacher, needs a change word in
  the clause that states the new value; the pre-fix day-4 consolidations are kept in `runs/<world>/4/night-preclause/`.
- The abstain phrases read every typographic apostrophe through one helper (f52810b); re-scored from the saved
  answers, no number changed.
- `eval/redecide_night.py` no longer lets a re-decided earlier day replace a later kept day as `adapters.json`
  current (4b351c5); it had done so for two minutes for w2 (19:54-19:56); the app does not read that pointer.
