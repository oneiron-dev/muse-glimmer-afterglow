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
The product rule is one-sided, so declining more than the base passes (w2 night-2b, 84% against 55%), and exact
answers are not scored in this form.

Under the two-sided product rule (owner option B: general within 10 points of the base either way, never-told at most
3 below), w4 night-2b would be kept; w2 night-2d (tool calls fixed) is not, because it declines 16 points more general
questions than the base and answers 7 never-told questions.

