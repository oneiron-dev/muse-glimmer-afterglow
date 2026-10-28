# AA-Omniscience-Public, own harness (not a leaderboard number)

600 questions from HF `ArtificialAnalysis/AA-Omniscience-Public` (Apache-2.0, revision in `aa-omniscience-score.json`),
the verbatim AA answer prompt, temperature 0, max 100 tokens, llama.cpp v0.7.0 target-only on mel. Graded with the
verbatim 4-way grader prompt on Muse Spark (development subscription); a deterministic pre-pass grades exact
matches and abstain phrases, and 50 of its grades per arm are also sent to the grader as an audit.
OI = 100 (correct - incorrect) / N; hallucination = incorrect / (incorrect + partial + not attempted). Eval only:
never training data, never part of `decide()`. Deltas under 5 points are noise.

| arm | adapter | OI | hallucination | attempts | not attempted | audit |
|---|---|---|---|---|---|---|
| base | none | -29.3 | 54.6% | 62.8% | 223 | 49/49 |
| w1 | R2 w1 (kept) | -59.3 | 98.8% | 100% | 0 | 18/18 |
| w4 | R2 w4 (kept) | -61.7 | 99.6% | 100% | 0 | 26/26 |
| w1e | arm (e): 10% SimpleQA abstaining anchors | -55.7 | 94.2% | 95.5% | 27 | 46/46 |
| w1e2 | arm (e2): ~5%, half bare questions | -58.7 | 95.9% | 97.5% | 15 | 50/50 |

w1e was graded on 27 Oct with the older pre-pass; on 29 Oct `recheck` dropped its 4 deterministic grades the
current pre-pass no longer gives and they were graded again (OI -55.3 -> -55.7).

Reading: the night update removes the base model's general-knowledge abstention (223 -> 0 not attempted). SimpleQA
abstaining anchors move the SimpleQA gate slice hard (e: 58.5% -> 88% abstain; e2: 59% -> 75.5%) but barely move
AA (27 and 15 of 600 not attempted): the anchors teach the gate prompt's form, not a general "I don't know".
The SimpleQA-anchor line was closed on 29 Oct.
