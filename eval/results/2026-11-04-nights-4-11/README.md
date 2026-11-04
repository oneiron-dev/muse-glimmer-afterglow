# Nights 4-11: every night update with a decision of record

The files behind the demo's results chart. Filed Wed 4 Nov 2026.

## Counts

On nights 4 to 11, the four synthetic people had **40 night updates** with a product honesty read and a decision of
record. **12 were kept and 28 rolled back.**

- 19 rollbacks lost more than 5 honesty points (right minus wrong on 200 general questions, as the day assistant,
  against plain Glimmer with the same notes, in the same server run).
- 9 rollbacks passed the honesty bound and failed another check: 7 lost too many right answers, 1 failed the
  general-skills check (its interval could not rule out a loss), 1 declined all 200 questions (the seed repeat of
  w1 night 9).
- Not run: w1 nights 7 and 11, w2 night 11. w4 night 7 was trained and never tested (the GPU went to another job).

The night in use now per person, kept and then re-tested on 200 new questions (`eval/data/fresh_confirm_v1.jsonl`):

| Person | Night | Honesty, usual 200 | Honesty, new 200 | Facts with notes off: plain Glimmer -> night |
|---|---|---|---|---|
| w1-ines-varga | 9 | +6.0 [-2.5, +14.5] | +16.0 [+8.0, +24.0] | 1 -> 54 of 115 |
| w2-solo-developer | 9 | -2.5 [-11.5, +6.5] | +9.0 [+0.5, +17.5] | 0 -> 62 of 128 |
| w3-shop-owner | 10 | -2.0 [-10.5, +6.0] | +4.0 [-3.5, +12.0] | 0 -> 60 of 137 |
| w4-grad-student | 9 (5 epochs) | 0.0 [-8.0, +8.0] | -2.5 [-10.5, +5.5] | 0 -> 36 of 128 |

The limit is -5. Intervals are 95% bootstrap over the questions (10,000 resamples). They do not include training
noise: w1 night 9 retrained with only a new seed (identical rows) declined all 200 questions (+24.5).

## Files

- `decisions/<person>/night<N>[-<arm>].json`: the decision of record for each of the 40 updates (copied from the
  local `runs/<person>/<N>/<night folder>/decision.json`; `runs/` is not in git). Each has the rule, every check, the
  honesty read and the reason in words.
- `reads/<read>/<person>/`: the read summaries (`honesty-*`, `paired-*`, `ask-back-*`) not filed before: night 9
  (w3, w1 9idkb, w4 9e5), the night-9 screens, night 10, night 11 (seeds and seed soups), the w1 seed repeat, and the
  four fresh-200 re-tests with their answers (`reads/night9fresh/*/answers-*.jsonl`). Reads already in
  `eval/results/2026-10-29-steering/product-form/` are not copied again.
- `nights.json`: the chart's data: every update with its night, decision, honesty change, interval and source file,
  and the four nights in use with their re-test, facts learned and general skills (500 questions).

Grading: Muse Spark (subscription), right +1, wrong -1, decline 0. A fact counts as learned when at least 2 of its 3
held-out questions are answered right (`eval/scoring.py`).
