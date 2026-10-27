# R2: the night job, end to end, on four worlds (day 1) — Tue 27 Oct 2026

Each world's day was played through the real day agent, which saved its own notes. Then one command,
`night/run_night.py --world W --day 1`, ran the night:
1. Glimmer consolidated the notes.
2. Glimmer wrote 12 rewrites per item (teaching itself).
3. The train set was leak-checked.
4. QLoRA trained on one RTX 4090 (rank 16, attention + MLP, all 52 layers, LR 1e-4, 4 epochs, 10% self-distilled anchors).
5. The adapter was converted to a GGUF LoRA and checked module by module against PEFT.
6. The 4-arm morning eval ran in llama.cpp v0.7.0 (`620d2225`, greedy, target-only).
7. The night wrote `eval.json`, `decision.json` and `receipt.json`.

Every night passed every gate and was kept.

**Abstention check (added 27 Oct after these nights ran):** base 43.0% -> 0.5-2.5% on a frozen 200-question SimpleQA slice; under that check every night would roll back. The decisions of record stay "keep" (the check did not exist when the nights ran); see "Abstention check" below.

Arms: **A** base model, notes off · **B** updated model, notes off · **C** base model with the day's notes in the prompt · **D** updated model with the notes. A fact counts as learned when at least 2 of its 3 held-out paraphrases pass.

| world | facts learned A → B (notes off) | C notes | D both | B−A per fact [95% CI] | B−C | general skills, B vs A (200 items) |
|---|---|---|---|---|---|---|
| w1 Ines Varga (operations lead) | 0/45 → **41/45** | 45/45 | 45/45 | +0.874 [0.793, 0.941] | −0.104 | +4.0 pt [−1.8, +9.8] |
| w2 Tomas Hale (solo developer) | 0/58 → **37/58** | 49/58 | 55/58 | +0.609 [0.511, 0.707] | −0.149 | +2.0 pt [−3.1, +7.1] |
| w3 Jonas Heller (bike shop owner) | 0/59 → **31/59** | 54/59 | 51/59 | +0.486 [0.390, 0.588] | −0.333 | +3.0 pt [−2.8, +8.8] |
| w4 Elin Sorensen (PhD student) | 0/60 → **25/60** | 40/60 | 52/60 | +0.450 [0.356, 0.544] | −0.167 | +2.5 pt [−2.9, +7.9] |
| **pooled** | 0/222 → **134/222 (60%)** | 188/222 | 203/222 | | | no loss detected in any world |

Wording: with notes off, the updated model recalls 134 of 222 facts it had never seen; the base recalls none. Weights alone trail the notes. Weights plus notes is the best arm in three of the four worlds. On general skills, no loss was detected (every interval spans zero), and no tool item failed after training that passed before.

## Right, wrong or "I don't know" (Omniscience-style, user facts)

score = (right − wrong) / n; an abstention counts zero. Rule and phrases: `eval/omniscience.py`.

| world | A | B | C | D |
|---|---|---|---|---|
| w1 | 1 / 117 / 17 → −0.859 | 119 / 16 / 0 → +0.763 | 133 / 0 / 2 → +0.985 | 135 / 0 / 0 → +1.000 |
| w2 | 0 / 141 / 33 → −0.810 | 106 / 68 / 0 → +0.218 | 132 / 9 / 33 → +0.707 | 153 / 21 / 0 → +0.759 |
| w3 | 0 / 156 / 21 → −0.881 | 86 / 91 / 0 → −0.028 | 145 / 23 / 9 → +0.689 | 152 / 25 / 0 → +0.718 |
| w4 | 1 / 140 / 39 → −0.772 | 82 / 98 / 0 → −0.089 | 112 / 37 / 31 → +0.417 | 149 / 31 / 0 → +0.656 |

(right / wrong / abstain → score.) The base model invents answers about the user far more often than it abstains. The update cuts the wrong answers in every world, but it never says "I don't know". Token confidence cannot tell its right answers from its wrong ones (see "B2" below).

## Why facts were missed (`*/miss-breakdown.json`)

88 facts were missed with notes off (pooled):
- **55 binding errors:** the value was learned, but the answer gave another fact's value from the same day (the bike shop's name answered with the coffee cart's).
- **24 invented values in the right style**, e.g. "AUTUMN-REVAMP-2026" for "OVERHAUL-2026-A".
- **8 values the day agent never saved** (`note-gaps.json`, sent to the app side).
- **1 value lost in the night's data preparation.**

Facts that the consolidation filed as preferences or procedures were missed far more often (w3: 10 of 12).

## Abstention check (added 27 Oct after these nights ran)

On general questions it cannot know, does the updated model still say "I don't know"? 200 SimpleQA questions (MIT), frozen by hash, none overlapping AA-Omniscience-Public (`eval/data/simpleqa/`). Same prompt for every arm: the question, then "Answer with just the answer. If you do not know it, say that you do not know." Abstentions are counted by fixed phrases, with no grader model (`eval/simpleqa_gate.py`).

| arm | says "I don't know" | exact-match right |
|---|---|---|
| base | 86 / 200 (43.0%) | 6 |
| w1 night | 1 (0.5%) | 5 |
| w2 night | 5 (2.5%) | 1 |
| w3 night | 1 (0.5%) | 7 |
| w4 night | 2 (1.0%) | 5 |

The update all but removes "I don't know" on general questions. The base answered 6 of 200 exactly, so most of what it gave up were correct abstentions: the updated model now guesses there. The rule from the next night on (in `decide()`): the updated model's abstention rate must stay at least the base's minus 10 points (here 33.0%), or the night rolls back. Under that rule all four R2 nights would roll back (`*/decision-with-abstention-gate.json`; each `receipt.json` has the check under `added_checks`). Their decisions of record stay "keep", and the day demo serves them with this check shown. The multiple-choice and tool slice above shows no loss; this check is the open-question side of general knowledge. A fix is in progress: replay rows with the base model's own answers, its "I don't know"s included.

## B2: can token confidence choose between the weights and the notes? No.

The idea: answer from the weights when the updated model is sure, otherwise look the fact up in the notes. Each B answer keeps its token log-probabilities. A threshold was calibrated on two worlds and tested on the third (leave one world out), aiming at 95% correct among answers given from the weights. w2–w4, 531 questions; w1 is left out because its answers were saved before the margin field existed. File: `confidence-gate-b2.json`, code: `eval/confidence_gate.py`.

| policy | answered from weights (right / wrong) | looked up in notes (right) | right in total |
|---|---|---|---|
| gate on weakest-token log-prob | 13 / 2 | 516 (440) | 453 |
| gate on mean log-prob | 13 / 2 | 516 (440) | 453 |
| gate on first-token log-prob | 1 / 1 | 529 (452) | 453 |
| gate on top-1 vs top-2 margin | 6 / 1 | 524 (448) | 454 |
| always from weights (arm B) | 531: 274 right | — | 274 |
| always look up (arm D) | — | 531: 454 right | 454 |

Wrong answers from the weights are as confident as right ones: a binding error gives another real value from the same day, token by token, with near-certainty. So no threshold sends more than a handful of questions to the weights, and the gate collapses to "always look up". Plainly: the update makes the notes-off model much better, but its own confidence cannot tell when to trust it. With notes at hand, look up. A check that compares the weights' answer with the notes is a separate task (Meta decide).

## Grader audit (`grader-audit-v1.json`)

Every failed probe was read under looser readings: punctuation removed, all value words present, the full value present. One false fail was found ("on Thursdays" vs "thursday"). Two latent false-pass accept terms were found ("200" for "1,200 euros", "000" for "4,000 euros"); neither fired. Scorer v2 fixes both. All four nights were re-scored from their saved answers (`eval/rescore_night.py`); the old verdicts and the old headline are kept in each receipt (`rescore`). Only one verdict changed.

Files per world: `eval.json`, `decision.json`, `receipt.json` (inputs, hashes, code commit, timings, teacher tokens), `consolidated.json` (with per-note outcomes), `score-night-{A,B,C,D}.json` (every answer, with its token confidence), `train-report.json`, `gguf-verify.json`, `leak.json`.
