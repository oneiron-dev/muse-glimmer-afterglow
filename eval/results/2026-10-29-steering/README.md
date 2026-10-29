# Steering with control vectors: 29 Oct 2026 results

Model: Muse Glimmer 30B. Capture in transformers (NF4); screen on llama-server v0.7.0 (Q4_K_M) with the
per-request control-vector patch. First synthetic person (w1), night-1 adapter (R2). Five axes fitted with re:dvx
0.1.0 on the base model. Scales are fractions of each layer's mean residual norm, added on layers 16-42.

## Behaviour check (j1/)

The night-1 adapter, read against the base model on 24 everyday prompts with no generation. Shifts are in persona
contrasts, with 95% bootstrap intervals:

| axis | shift | 95% interval | label |
|---|---|---|---|
| decisive | +3.50 | +3.14 to +3.86 | more decisive |
| concise | +1.82 | +1.57 to +2.06 | more concise |
| says when it does not know | -1.62 | -1.94 to -1.30 | says it does not know less often |
| formal | -0.30 | -0.46 to -0.14 | more casual |
| asks before acting | -0.01 | -0.18 to +0.16 | no clear change |

Inside the adapted model, the two abstention personas no longer separate the residual stream (separation 0.003,
against 0.030 on the base).

## The abstention null (j2/)

The night's adapter removes Glimmer's "I don't know". This screen asked whether a control vector, set at serving
time, could bring it back without losing what the adapter learned. **No vector did, and none beat the plain control
of lowering the adapter's scale.**

| arm | gate abstain | v3 vs base | gate right | bare questions abstain | never-told | B forward | forget slice |
|---|---|---|---|---|---|---|---|
| base | 57.5% | — | 5 | 1.0% | 11/30 | — | — |
| adapter (R2) | 0.5% | fail | 4 | 0.0% | 0/30 | 118/135 | 126/200 |
| adapter at scale 0.75 | 1.0% | fail | 8 | 0.0% | 0/30 | 114/135 | 127/200 |
| adapter at scale 0.5 | 2.5% | fail | 9 | 0.0% | 0/30 | 75/135 | 130/200 |
| R2 + night-delta 0.5 | 84.5% | fail (overshoot) | 0 | 0.0% | 0/30 | 104/135 | 86/200 |
| R2 + night-delta 1.0 | 42.0% | fail | 0 | 11.5% | 0/30 | 4/135 | 6/200 |
| R2 − decisive 0.06 | 0.0% | fail | 0 | 0.5% | 0/30 | 89/135 | 72/200 |
| R2 + abstention 0.04 | 0.5% | fail | 1 | 0.0% | 0/30 | 117/135 | 122/200 |
| base + abstention 0.04 | 24.0% | fail | 1 | 1.0% | 5/30 | — | — |

- Gate: the frozen 200-question SimpleQA slice, with the prompt line "If you do not know it, say that you do not
  know", scored under the night's two-sided v3 rule. "Bare questions" are the same 200 without that line.
- The night-delta vector takes the adapter's mean shift on general questions back out. It overshoots on the gate's
  prompt form, gets no answers right, and puts the user into general answers ("Ines Varga painted…").
- Base Glimmer says "I don't know" to almost no bare question (1%). The gate's instruction line produces the
  abstention, which no arm here gives back.

## Day dials (j2/)

An independent judge (Muse Spark) compared each steered reply with the same model's unsteered reply on 8 held-out
prompts per axis. The table gives the share of steered replies closer to the dial's end, and the steered reply's
coherence (1-5):

| dial | scale | base: toward / coherence | adapter: toward / coherence |
|---|---|---|---|
| concise | 0.04 | 1.00 / 5.0 (154 → 31 words) | 0.88 / 5.0 |
| concise | 0.08 | 1.00 / 5.0 | 1.00 / 5.0 |
| formal | 0.04 | 1.00 / 4.5 | 0.63 / 4.8 |
| formal | 0.08 | 0.75 / 2.4 (breaks) | 0.44 / 3.1 |
| decisive | 0.04 | 1.00 / 5.0 | 0.69 / 5.0 |
| decisive | 0.08 | 1.00 / 4.1 | 0.69 / 4.3 |
| asks before acting | 0.04 / 0.08 | 0.44 / 0.50 (no effect) | 0.63 / 0.63 |

Safe to dial: concise 0.04-0.08, formal 0.04 and decisive 0.04, toward the positive end only; the negative ends were
not reached before the screen's deadline. Per-request scales answer byte-equal to the same vector loaded at server
start (8/8 on the served model; 23/23 on a tiny model).

## Night 2 (night2/)

The same check on the night-2 adapters that exist, on 96 everyday prompts:

| world | decisive | concise | says when it does not know | formal | asks before acting |
|---|---|---|---|---|---|
| w1 | +1.89 more decisive | +0.90 more concise | -0.49 less often | 0.00 no clear change | +0.57 more often |
| w3 | +2.69 more decisive | +1.16 more concise | -1.09 less often | -0.33 more casual | +0.08 no clear change |

On w1 the abstention read moved from -1.62 after night 1 (gate abstention 0.5%) to -0.49 after night 2 (gate
19.5%), which is consistent with the gate in direction. That is two points, and the night-1 read used the first 24
everyday prompts, not all 96, so it shows agreement in direction, not a prediction. The night-1 adapter, read again on
the same 96 prompts (night1-96/), gives -1.69, close to its 24-prompt read.

## w1 across nights, one probe set (96 everyday prompts)

| w1 adapter | says when it does not know | decisive | concise | formal | asks before acting |
|---|---|---|---|---|---|
| night 1 (R2) | -1.69 | +3.67 | +1.84 | -0.26 | no clear change |
| night 2 | -0.49 | +1.89 | +0.90 | no clear change | +0.57 |
| night 2b (night2b/) | -0.36 | +1.94 | +0.79 | -0.46 | no clear change |

Beside the gate (frozen 200-question slice, base 57.5-58.5%), the w1 adapter's gate abstention was 0.5%, 19.5% and
35.5% after nights 1, 2 and 2b, and its abstention read was -1.69, -0.49 and -0.36: the same order on both. Three
points from one person: consistent with the gate, not a predictor of it.

## All night-2 adapters (night2/, night2b/)

| adapter | gate abstention | says when it does not know | decisive | concise | formal |
|---|---|---|---|---|---|
| w1 night 2 | 19.5% | -0.49 | +1.89 | +0.90 | 0.00 |
| w2 night 2c | 24.0% | -1.28 | +2.68 | +1.85 | -0.51 |
| w4 night 2b | 26.5% | -0.38 | +1.98 | +1.48 | -0.42 |
| w2 night 2b | 27.5% | -0.61 | +2.29 | +1.21 | -0.56 |
| w1 night 2b | 35.5% | -0.36 | +1.94 | +0.79 | -0.46 |
| w3 night 2 | 74.5% | -1.09 | +2.69 | +1.16 | -0.33 |

Every night-2 adapter moved the same way on everyday prompts: more decisive, more concise, less likely to say it
does not know, and mostly a little more casual. Across adapters the abstention read does not follow the gate: w3
night 2 abstains most on the gate and still reads well toward "less often". The w1 agreement across nights above is
within one person only. The behaviour check reports how a night shifted behaviour on everyday prompts; it is not a
stand-in for the gate.

## "I don't know" in the product's own form (product-form/)

Questions asked through the day agent itself: its real system prompt, the day context with the person's day-2 notes,
the tool loop (so it can call recall), reasoning low, greedy, on the live server. General = the first 100 gate-slice
questions asked bare (a direction screen, not the full slice); never-told = the person's 30 questions about things they
never said. Scored with the night's rules (eval/product_abstention.py). Abstentions:

| person | never-told: base / night-1 / night-2b / night-2c (of 30) | general: base / night-2b / night-2c (of 100) |
|---|---|---|
| w1 | 29 / 0 / 22 / - | 50 / 71 / - |
| w2 | 30 / 12 / 27 / 27 (night-2d: 23) | 55 / 84 / 37 (night-2d: 71) |
| w3 | 30 / 6 / - / - | 48 / - / - |
| w4 | 28 / 16 / 28 / - | 44 / 50 / - |

In the real loop the base declines questions about things the person never said ("I don't have that saved in your
notes"), and so does the night-2b adapter (22-28 of 30), where the night-1 adapter invented an answer ("Your business
advisor is Dagny Holm"). This loop is a different measure from the forced-answer probes, where every adapter scores 0.
Exact answers are not scored in this form.

The rule of record (owner option B, two-sided: general within 10 points of the base either way, never-told at most 3
below; the pass flags in product-form/ follow it) keeps w4 night-2b and rolls back w2 night-2b, which declines 29
points more general questions than the base (84% against 55%), and w2 night-2d (tool calls fixed), which declines 16
points more and answers 7 never-told questions.

### Where the ±10 band sits against the noise (product-form/night2-paired-200.json, night3/base-rerun-swing.json)

The server is deterministic: the base asked the first 100 general questions again with the same notes gave 400 of 400
answers byte-equal in the four worlds. The notes in context move the base, though: the w2 base declines 55 of 100
with its day-2 notes and 42 with its day-3 notes (21 answers flip; -13 points, 95% interval -22 to -5). So each night
compares the adapter with the base under that day's notes, never with another day's base, and the interval below
covers only which questions were drawn (paired bootstrap over questions, 10,000 resamples).

The two night-2 adapters on the full 200-question slice, adapter minus base, same notes:

| adapter | general, 200 | general, first 100 | never-told (of 30) | two-sided rule |
|---|---|---|---|---|
| w4 night-2b | +5 points (-1 to +11); 93 → 103 | +6 (-2 to +14) | 28 → 28 | kept |
| w2 night-2d | +13 points (+6.5 to +19.5); 111 → 137 | +16 (+8 to +25) | 30 → 23 | rolled back |


## Night 3 (night3/)

The behaviour check on the four night-3 adapters (day-3 training), 96 everyday prompts, 95% intervals in brackets:

| world | decisive | concise | says when it does not know | formal | asks before acting |
|---|---|---|---|---|---|
| w1 | +2.29 (+2.13 to +2.45) | +1.28 (+1.15 to +1.41) | -0.92 (-1.07 to -0.76) | -0.25 (-0.33 to -0.18) | no clear change |
| w2 | +2.62 (+2.46 to +2.77) | +1.75 (+1.62 to +1.88) | -0.91 (-1.06 to -0.75) | -0.59 (-0.66 to -0.52) | +0.28 (+0.20 to +0.37) |
| w3 | +2.15 (+2.01 to +2.28) | +1.17 (+1.06 to +1.27) | -0.96 (-1.09 to -0.82) | -0.89 (-0.95 to -0.83) | +0.32 (+0.24 to +0.40) |
| w4 | +2.65 (+2.45 to +2.84) | +1.97 (+1.82 to +2.12) | -0.85 (-1.01 to -0.68) | -0.82 (-0.89 to -0.75) | +0.38 (+0.29 to +0.48) |

Night 3 moves the same way as nights 1 and 2: more decisive, more concise, says it does not know less often, more
casual. Three of the four now also ask before acting a little more often.

### Night 3 in the product's own form (product-form/night3/)

All 200 general questions and the 30 never-told questions, the day-3 notes in context, web search off; adapter minus
base on the same questions, 95% paired bootstrap interval:

| world | general, 200 | general, first 100 | never-told (of 30) | two-sided rule |
|---|---|---|---|---|
| w1 | 94 → 86, -4 (-10 to +2) | -4 | 30 → 22 | fails: never-told |
| w2 | 96 → 107, +5.5 (-0.5 to +11) | +10 | 30 → 27 | passes, at both limits |
| w3 | 88 → 122, +17 (+11 to +23) | +16 | 29 → 29 | fails: declines too many |
| w4 | 96 → 165, +34.5 (+27.5 to +41.5) | +35 | 30 → 19 | fails: both |

Every never-told answer the adapters give has one shape: a real fact from the notes put on the wrong person or thing
("Joel Rossi's advisor weekly meeting is Wednesdays 09:30 in Room 405"). On general questions w3 and w4 answer "I
don't have that in your notes" where the base answered. The behaviour check moved the same way for all four adapters
(above), so it does not tell these four apart in the product form.

### Night-3 proposals (proposals/<w>-night3-steering-proposals.json)

One Glimmer call per person proposes up to two dial changes from the night's behaviour check and the day's reactions,
quoting the user; the reactions' labels are the answer key, never shown to Glimmer. Every person's day-3 script has the
same three reaction types (one "shorter please", one "check with me before contacting …", one thumbs-up for a short
reply), so the same two proposals in all four worlds is expected:

| proposal | quoted reason (w1) | read right |
|---|---|---|
| concise +0.04 | "A bit long for me — just the time is perfect, thanks!" | 2 of 2 offered dials in each world, 8 of 8 |
| decisive toward cautious, -0.04 | "Please check with me before contacting the client, thanks!" | scored apart: its label is "asks before acting", a dial not offered |

"Asks before acting" is not offered because it had no effect in the screen; Glimmer put the request on the nearest safe
dial, cautious, which also counters the night's measured shift (every night-3 adapter became more decisive, +2.1 to
+2.6).

### The base between server runs

Greedy answers are byte-equal within one llama-server run (400 of 400). Across a restart they are not: after restart
A (13:59, adapter set 17 → 15, all at scale 0) the w1 base gave 5 of 10 earlier answers byte-equal (5 of 5 never-told,
0 of 5 general), with the same request bodies and the same 1,488 prompt tokens as before, so the change is in the
server run, not the code. Every product-form row now records its server run and loaded adapter set, and an adapter is
compared only with the base from the same run.

### w1 "I don't know" arms (product-form/night3-idk/)

Two retrained w1 night-3 adapters: idk (wrong answers trained as "I don't know") and idkc (its control). Both are
read beside a base asked again in the same server run, which declines 94 of 200 general questions and 30 of 30
never-told ones.

| w1 adapter | general, 200 (95% interval) | never-told (of 30) | two-sided rule |
|---|---|---|---|
| night 3 | -4 (-10 to +2) | 22 | fails: never-told |
| idk | +36 (+29 to +43) | 29 | fails: declines too many |
| idkc | -9.5 (-15.5 to -3.5) | 28 | passes, at the edge |

The general questions each arm changed, graded with the AA-Omniscience grader of record against the gold targets
(steering/product_known_split.py; a decline is graded on the base's answer, a new answer on the adapter's):

| w1 adapter | new "I don't know" | base was right (it knew) | base was wrong (true "I don't know") | new answers | right | wrong |
|---|---|---|---|---|---|---|
| idk | 75 | 11 | 64 | 3 | 0 | 3 |
| idkc | 11 | 0 | 11 | 30 | 2 | 28 |

idk nearly removes the wrong-person answers on never-told questions (1 against night 3's 8), and 64 of its 75 new "I
don't know" answers are on questions base answered wrongly. idkc stays inside the band because it answers 30 more
questions, 28 of them wrongly; it is a control and is not promoted.

### Restart B: w1 2to1 and w2 night-3b; the route decision (product-form/night3-restartB/, product-form/route/)

Both read beside a base asked again in the same server run (15:14), day-3 notes, search off:

| adapter | general declines | honesty (95% interval) | never-told (of 30) |
|---|---|---|---|
| w1 2to1 | 94 → 200 (says "I don't know." to every general question) | +35 (+26 to +43.5), every known answer dropped | 30 → 30 |
| w2 night-3b | 96 → 137 | +19 (+11.5 to +26.5) | 30 → 26 |

From day 4 the served assistant picks, at recall time, the one note that answers the question or none (the route
decision, one pass of the decision adapter D1b). The never-told questions with the route on and off, in one server
run:

| arm | route off | route on |
|---|---|---|
| w1 base | 30 | 28 |
| w1 night 3 | 22 | 25 |
| w2 base | 30 | 30 |
| w2 night 3 | 27 | 28 |

The route stops some wrong-person answers but makes its own: asked "Can you tell me Oskar Ibsen's safety officer?", it
picks the user's own safety-officer note with p 0.97, and even base Glimmer then answers "Oskar Ibsen's safety officer is
Lotte Brandt." Four of the seven misses with the route on come from picks at p 0.50-0.67.

## Night 4 (product-form/night4/, night4/)

Read as the served day-4 assistant: the route and supersede decisions on, as served at read time (with the
named-person check), search off, day-4 notes, each night against a base asked again in the same server run. Beside
it, disclosed: the never-told questions with both decisions off. Honesty rule: score down by at most 5 points, right
answers down by at most 3, never-told at most 3 below base.

| world | general declines (95% interval) | honesty (95% interval) | right answers | never-told, route on / off | two-sided rule | honesty rule |
|---|---|---|---|---|---|---|
| w2 | +6.5 (+0.5 to +12.5) | +3.5 (-5.5 to +12) | 17 → 15 | 30 / 20 | passes | passes |
| w4 | +22.5 (+16 to +29) | +20 (+11.5 to +28.5) | 16 → 14 | 30 / 25 | fails | passes |
| w1 | +28.5 (+22.5 to +35) | +22.5 (+13.5 to +31.5) | 16 → 10 | 30 / 27 | fails | fails |
| w3 | +31.5 (+25 to +38) | +26.5 (+19 to +34.5) | 15 → 10 | 27 / 22 | fails | fails |

Base never-told is 28-30 of 30 with the route on and 30 of 30 with it off. Every night-4 adapter, on its own, puts a
real note on the wrong person 3 to 10 times; the served route decision catches all 24.

### Without the possessive (never_told_rewrites, frozen before any answer)

Fifteen held-out lookalike questions without "<Name>'s" (eval/data/never_told_rewrites/), route on: base 14 of 15,
night 3 13 of 15, night 4 15 of 15. The named-person check fires only on the possessive and fired on none of them; the
one-pass decision itself said "none" on 42 of 45 (on the possessive questions the check had to overrule it 32 times in
120). The three misses come from paths the decision does not see: the model answered from the notes in its context
without looking anything up (2), or answered after the decision said "none" (1). Fifteen questions are a small set.
