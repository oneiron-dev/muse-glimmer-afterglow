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
- **2to1** (a narrower boundary, run after idkc showed the effect is the labels): n3 + the same 21 IDK rows and 33
  base-right rows (`rows-graded-2to1-avail.jsonl`; the held-apart 200 have only 11 usable right answers per form).

## AA-Omniscience-Public, own harness (600 questions; not a leaderboard number)
95% paired bootstrap intervals (`eval/aa_omniscience_ci.py`, files in `aa/`). Base answers are mel's 27 Oct base run
(same GGUF, llama.cpp and prompt). Deterministic audit: 49/49, 49/49, 50/50, 49/50.

| arm | hallucination rate | omniscience index | attempt rate | right | wrong | not attempted |
|---|---|---|---|---|---|---|
| base | 54.6% [50.3, 58.8] | -29.3 [-35.2, -23.5] | 62.8% | 98 | 274 | 223 |
| n3 | 94.4% [92.2, 96.3] | -55.2 [-61.5, -48.7] | 95.7% | 121 | 452 | 26 |
| idk | **11.7%** [9.1, 14.4] | **-1.8** [-5.3, +1.7] | 19.8% | 53 | 64 | 481 |
| idkc | 97.9% [96.5, 99.2] | -58.7 [-65.0, -52.2] | 98.7% | 119 | 471 | 8 |
| 2to1 | 60.8% [56.4, 65.1] | -32.7 [-38.5, -26.8] | 68.0% | 105 | 301 | 192 |
| idkr | 33.1% [29.1, 37.2] | -13.8 [-19.0, -8.7] | 43.0% | 87 | 170 | 342 |

idk - n3 = -82.7 pt [-85.9, -79.3]; idk - idkc = -86.2 pt [-89.0, -83.1]; idkc - n3 = +3.6 pt [+1.2, +6.0];
2to1 - n3 = -33.6 pt [-37.8, -29.2]; 2to1 - base = +6.2 pt [+1.8, +10.7]; idkr - base = -21.4 pt [-25.8, -17.0].

Known-item retention (Wilson 95%): of the 98 questions base answers right, idk still answers 45 (46% [36, 56]) and
says "I don't know" on 45; idkc keeps 74; 2to1 keeps 74 (5 "I don't know"). Of the 274 base gets wrong, idk says "I
don't know" on 211 (77% [72, 82]), 2to1 on 44 (16%), idkc on 3.

## The night's own checks (w1 day-3 probes)

| | n3 | idk | idkc | 2to1 |
|---|---|---|---|---|
| B never-told (abstains, of 30) | 1 | **15** | 0 | 1 |
| B day-1 facts right/wrong/abstain | 93/14/1 | 74/12/22 | 91/16/1 | 92/16/0 |
| B day-3 facts | 20/19/0 | 23/12/4 | 21/17/1 | 22/17/0 |
| D never-told | 27 | 29 | 27 | 27 |
| SimpleQA gate abstain (base ~58%), disclosed | 35.5% | 91.0% | 3.0% | 47.0% |
| forgetting | +2.5 pt [-2.7, +7.7] | -1.5 pt [-5.7, +2.8] | +0.5 pt [-4.0, +5.0] | +3.0 pt [-2.0, +7.9] |
| tools, channel | 20/20, 30/30 | 20/20, 30/30 | 19/20, 30/30 | 19/20, 30/30 |
| product form, general declines (base 94/200) | 86 | 166 | 75 | (read after 15:10) |
| product form, never-told (base 30) | 22 | 29 | 28 | (read after 15:10) |
| product-2s | rollback | rollback | passes, not promoted (control arm) | pending |

Of the user facts n3 answers right (125 B probes), idk keeps 94 and says "I don't know" on 20; idkc keeps 116; 2to1
keeps 107. In the product form idk nearly removes the never-told wrong-person answers (1 against n3's 8) and declines
75 general questions base answers.

## Privacy: never-told questions and the web search (`privacy/`)
The 30 w1 never-told questions about the user, asked in the day agent's prompt with search_web offered (the call is
read, never executed): 0 web searches in every arm (base, n3, idk, 2to1). First actions: base recall 30; n3 recall 28,
answer 2; idk recall 30; 2to1 recall 22, answer 8.

## Reading
- The effect is the labels: the same questions with the base model's own answers make it guess slightly more
  (+3.6 pt), and the "I don't know" labels move hallucination from ~95% to 12% and B never-told from 1 to 15 of 30.
- The boundary is set too wide: 42 rows switch "I don't know" on past what base itself knows (it gives up 46% of the
  general answers base knew, 16% of the user facts n3 knew, and the gate overshoots to 91%). idk is a rollback as a
  night on forgetting (its lower bound misses -5 by 0.7) and serves as a direction screen.
- The narrower boundary (2to1) puts AA honesty back near base (60.8% vs 54.6%) and keeps the known items (74 of
  base's 98, 107 of n3's 125 user facts), but the never-told B lift goes with it (1 of 30). Its product-form read
  decides whether it keeps idk's fix of the wrong-person answers.

## Look-it-up (the owner's follow-up), dropped before training
Built with `night/lookup_rows.py` on the day server with search_web offered (nothing is searched; `lookup/`): the w1
n3 adapter calls recall first on 41 of 43 user facts asked in the agent prompt (0 answered from memory), and the base
model calls search_web on 31 of 32 general questions, 24 of the 25 it knows. In the agent loop the model already
reaches for its tools; the rows had nothing new to teach, so the lead dropped the arm. The made-up answers come from
the forced-answer form and from attaching a recalled note to the wrong person.

## The format-kept arm (idkr), after 2to1 collapsed in the agent form
2to1 answered a bare "I don't know." to all 200 general questions in the product form (base 94 declines, 18 right ->
0). Its 7 agent-form IDK rows were the only general rows that answer at once ("to=user I don't know.", 57 characters)
while every right row and 49 of 53 agent anchors reason first, so the first generated token tied "answer at once" to
"I don't know". idkr (`rows-graded-reasoned.jsonl`, 58 rows) keeps the base's own reasoning in each of 19 agent-form
IDK rows and replaces only its final answer; bare and gate rows are idk's 7+7. Night checks (w1 day 3): B day-1
91/17/0, day-3 25/14/0, never-told B 1 / D 27, gate 90.5% (disclosed), forgetting +0.5 pt [-4.2, +5.2], tools 20/20.
AA: hallucination 33.1% (base 54.6%), 64 of the 98 base-right answers kept (19 "I don't know", 15 wrong), 120 of the
274 base-wrong turned into "I don't know". Product form (w1 day-3 notes, route on as served): general declines
94 -> 187 of 200 (+46.5 pt), a bare "I don't know." to 170; right answers 18 -> 2 (16 lying IDK); never-told 30/30, with
the route off as well. product-2s and the honesty rule's knowledge floor both fail. Keeping the reasoning did not stop
the agent-form collapse.

## Conclusion (29 Oct 20:26; the lead stopped IDK-row arms for the hackathon)
On general knowledge in the bare AA form, the "I don't know" labels do what the owner asked: hallucination falls from
94% (n3) to 12% (idk) or 33% (idkr), against base's 55%, and the effect is the labels (the control with the base's own
answers: 98%). In the day agent's own form every variant over-declines (idk 166, 2to1 200, idkr 187 of 200 general
questions, against base's 94), so none can be served under product-2s or the honesty rule. The product form's general
questions are SimpleQA trivia that base gets right 18 times in 200; tens of SimpleQA-style "I don't know" rows teach a
30B model "an obscure question in this style -> decline", which is nearly true on that slice, not where its own
knowledge ends. A real boundary needs a confidence signal (a logit-margin gate at serving, no training; on the later
list) or far more rows.
