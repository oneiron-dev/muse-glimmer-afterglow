# Abstention arms, 27-29 Oct (day 1, w1 and w3)

The R2 adapters learn the user's facts but stop saying "I don't know": on the SimpleQA gate slice the abstain rate
falls from ~58% (base) to 0.5-3%, and on the never-told probes (30 per world, questions the user never answered)
the adapter invents a value every time (B 0/30), even with the notes in the prompt (D 0/30). These arms test fixes.
All on llama.cpp v0.7.0 target-only, scorer v3, receipts in `runs/<world>/1/<arm>/`; table in `arms.json`.

| world | arm | change vs control | decision | B fwd | D fwd | gate abstain (base -> after) | exact right | never-told A/B/C/D |
|---|---|---|---|---|---|---|---|---|
| w1 | night (R2) | - | keep | 119/135 | 135 | 58.0 -> 0.5% (check added) | 6 -> 5 | 10/0/28/0 |
| w1 | night-abstain (e) | + 10% SimpleQA abstaining anchors | rollback | 115 | 135 | 58.5 -> 88.0% | 7 -> 3 | -/0/-/0 |
| w1 | night-abstain-e2 | + ~5%, half bare questions | rollback | 119 | 135 | 59.0 -> 75.5% | 6 -> 3 | 11/0/28/1 |
| w1 | night-abstain-2pct | + 2%, e2 form | rollback | 111 | 135 | 59.0 -> 84.0% | 5 -> 4 | 10/0/28/0 |
| w1 | night-unknown-keys | + 24 unknown-key rows + 24 known pairs | rollback | 119 | 135 | 57.5 -> 14.5% | 5 -> 9 | 10/0/28/12 |
| w3 | night-steps264 | R2 data, 264 steps (control) | keep | 86/177 | 155 | - | - | -/0/-/0 |
| w3 | night-binding | + contrast rows | keep | 105 | 142 | - | - | -/0/-/0 |
| w3 | night-perturbed | + perturbed "not in your notes" rows | keep | 98 | 147 | - | - | -/1/-/2 |
| w3 | night-contrast-notes (b2) | + contrast rows with the notes in the prompt | rollback | 100 | 143 | 59.0 -> 0.5% | 5 -> 5 | 12/0/19/0 |

Arms without a gate value ran before the gate existed (27 Oct 10:1x); "keep" there predates it.

## What each result says
- **SimpleQA abstaining anchors (e, e2, 2%)**: the gate response is a switch, not a dose: 0% -> ~2%, 2% -> 84%,
  5% -> 75.5%, 10% -> 88%. The 3% arm was stopped after 3 minutes of training (it could only land between 75 and 84%).
  AA-Omniscience-Public (own harness, eval only) barely moves (`../2026-10-27-aa-omniscience-public/`): the anchors teach
  the gate prompt's form. Line closed 29 Oct.
- **b2, contrast rows with the notes in the prompt (null)**: against its matched-steps control B rises 86 -> 100
  (p .065) but D falls 155 -> 143 (p .012), the same D loss as the plain contrast arm; abstention 59 -> 0.5%. Dropped.
- **Prompt hint (null)**: the four R2 adapters with the system hint "If you are not sure about a fact about the user,
  say you'll check your notes." (B only): 0 abstentions in every world, no wrong answer turned into an abstention,
  1-3 right answers lost per world, general slice unchanged (`hint-arm-vs-r2.json`). A prompt cannot bring it back.
- **Unknown-key rows (partial)**: questions about the user that the user never answered, answered "You haven't told
  me ...", half with the notes block in the prompt, each paired with a known item asked the same way. D on never-told
  0 -> 12/30, general abstention 0.5 -> 14.5% with more exact-right answers (5 -> 9), no cost on B or D facts, no
  over-refusal, no loss detected on forgetting. B on never-told stays 0/30: 12 plain rows against ~170 answer rows of
  the same "What's my X?" shape are too few, and B answers with another fact's value.

Noise note: arms C and A do not change between arms (no adapter), yet never-told C reads 19-28 and A 10-12 across
runs: llama.cpp with 4 parallel slots is not bit-exact across batch mixes. Treat never-told moves under ~5 as noise;
D 0 -> 12 is not.
