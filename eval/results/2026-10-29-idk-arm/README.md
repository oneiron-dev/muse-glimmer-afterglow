# The IDK arm (owner's ask, R-Tuning style), 29 Oct, w1 night 3

The owner's question: does the update make things up less if it learns "I don't know" only where the base model
truly does not know? Rows (`night/idk_rows.py`): 200 SimpleQA questions held apart from the gate slice, both anchor
sets and AA-Omniscience look-alikes; the base model (every adapter at scale 0) answers each in three forms (bare
question; the gate's "say so if you do not know" wording; the day agent's real prompt with its tools). Each answer is
graded against SimpleQA gold: deterministic first, the AA grader prompt on Muse Spark (development subscription) for
the rest (`base-grades-summary.json`). Base WRONG -> "I don't know." in that form; base RIGHT -> its own answer;
answers cut at the 64-token limit are left out; 7 right + 7 wrong per form, 42 rows (`rows-graded.jsonl`).

Arms, all on w1 night 3's data (the same consolidation, rewrites and agent anchors), trained on nelly:
- **n3**: the night-3 recipe.
- **idk**: n3 + the 42 graded rows.
- **idkc** (control): n3 + the same 42 questions, every row keeping the base model's own answer (`rows-ungraded.jsonl`).

## AA-Omniscience-Public, own harness (600 questions; not a leaderboard number)
95% paired bootstrap intervals (`eval/aa_omniscience_ci.py`, files in `aa/`). Base answers are mel's 27 Oct base run
(same GGUF, llama.cpp and prompt). Deterministic audit: 49/49, 49/49, 50/50, 49/50.

| arm | hallucination rate | omniscience index | attempt rate | right | wrong | not attempted |
|---|---|---|---|---|---|---|
| base | 54.6% [50.3, 58.8] | -29.3 [-35.2, -23.5] | 62.8% | 98 | 274 | 223 |
| n3 | 94.4% [92.2, 96.3] | -55.2 [-61.5, -48.7] | 95.7% | 121 | 452 | 26 |
| idk | **11.7%** [9.1, 14.4] | **-1.8** [-5.3, +1.7] | 19.8% | 53 | 64 | 481 |
| idkc | 97.9% [96.5, 99.2] | -58.7 [-65.0, -52.2] | 98.7% | 119 | 471 | 8 |

idk - n3 = -82.7 pt [-85.9, -79.3]; idk - idkc = -86.2 pt [-89.0, -83.1]; idkc - n3 = +3.6 pt [+1.2, +6.0].

Known-item retention (Wilson 95%): of the 98 questions base answers right, idk still answers 45 (46% [36, 56]) and
says "I don't know" on 45; idkc keeps 74. Of the 274 base gets wrong, idk says "I don't know" on 211 (77% [72, 82]),
idkc on 3.

## The night's own checks (w1 day-3 probes)

| | n3 | idk | idkc |
|---|---|---|---|
| B never-told (abstains, of 30) | 1 | **15** | 0 |
| B day-1 facts right/wrong/abstain | 93/14/1 | 74/12/22 | 91/16/1 |
| B day-3 facts | 20/19/0 | 23/12/4 | 21/17/1 |
| D never-told | 27 | 29 | 27 |
| SimpleQA gate abstain (base 58.5%), disclosed | 35.5% | 91.0% | 3.0% |
| forgetting | +2.5 pt [-2.7, +7.7] | -1.5 pt [-5.7, +2.8] | +0.5 pt [-4.0, +5.0] |
| tools, channel | 20/20, 30/30 | 20/20, 30/30 | 19/20, 30/30 |

Of the user facts n3 answers right (125 B probes), idk keeps 94 and says "I don't know" on 20; idkc keeps 116.

## Reading
- The effect is the labels: the same questions with the base model's own answers make it guess slightly more
  (+3.6 pt), and the "I don't know" labels move hallucination from ~95% to 12% and B never-told from 1 to 15 of 30.
- The boundary is set too wide: 42 rows switch "I don't know" on past what base itself knows (it gives up 46% of the
  general answers base knew, 16% of the user facts n3 knew, and the gate overshoots to 91%). idk is a rollback as a
  night on forgetting (its lower bound misses -5 by 0.7) and serves as a direction screen.
- Next, ruled by the lead: a narrower boundary, the same 21 IDK rows with 33 base-right rows
  (`rows-graded-2to1-avail.jsonl`); the half dose (`rows-graded-half.jsonl`, a subset of the 42) after it.

## Look-it-up (the owner's follow-up), dropped before training
Built with `night/lookup_rows.py` on the day server with search_web offered (nothing is searched; `lookup/`): the w1
n3 adapter calls recall first on 41 of 43 user facts asked in the agent prompt (0 answered from memory), and the base
model calls search_web on 31 of 32 general questions, 24 of the 25 it knows. In the agent loop the model already
reaches for its tools; the rows had nothing new to teach, so the lead dropped the arm. The made-up answers come from
the forced-answer form and from attaching a recalled note to the wrong person.
