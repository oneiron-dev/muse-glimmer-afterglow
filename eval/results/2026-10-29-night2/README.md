# Night 2, 29 Oct (day-2 logs of all four worlds)

Every night trains from base on all facts still active after day 2 (the notes are cumulative). It is scored in
llama.cpp v0.7.0 target-only on the day-2 probes (day-1 probes still true, 3 probes per new or corrected value, stale
probes on the old values). Two chat checks of record are new tonight: the content channel (30 chat calls, >= 95%
non-empty user-channel replies) and the day agent's tool calls (20 turns, no more than 2 below base). Receipts:
`runs/<world>/2/<night|night-2b|night-2c>/`; table data in `nights.json`.

Variants:
- **n2**: R2 recipe + unknown-key rows + `--train-header` + 53 agent anchor rows + self-check rows (wrong facts' answer
  rows replaced by "I'm not sure of your <key>. Let me check your notes.").
- **2b**: n2 without the self-check, with agent anchors rebuilt from the generated token ids (see the tool-call break below).
- **2c**: 2b with the answer header masked (no `--train-header`), w2 only.
- **2d**: 2c plus `--drop-text-acks` (rows where the user asks to log/note/save and the reply acknowledges in text are
  dropped: 16 in w2) and an unknown-key reply that no longer promises to note anything, w2 only.

B/D columns are right/wrong/abstain on the forward probes. "still true" = day-1 facts still true on day 2, beside R2
(night 1) on the same probes. Never-told is abstentions out of 30. Tools = remember + recall out of 20 (base 20/20 in
every world).

| world | variant | decision (failed checks) | B still true, R2 -> night | B new | D still true | D new | stale B | never-told B/D | tools | channel | gate abstain | forgetting |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w1 | n2 | rollback (tool_calls, abstention_gate) | 105/12/0 -> 89/28/0 | 12/5/1 | 116/0/1 | 13/4/1 | 0/18 | 0/26 | 0+0 | 30/30 | 0.575 -> 0.195 | +4.5 pt [-0.6, +9.5] |
| w1 | 2b | rollback (forgot_gate, abstention_gate) | 105/12/0 -> 96/21/0 | 12/6/0 | 116/0/1 | 15/2/1 | 0/18 | 1/24 | 10+10 | 30/30 | 0.585 -> 0.355 | -1.5 pt [-6.4, +3.4] |
| w2 | 2b | rollback (tool_calls, abstention_gate) | 94/62/0 -> 88/67/1 | 8/10/0 | 141/7/8 | 15/2/1 | 1/18 | 0/25 | 0+3 | 30/30 | 0.590 -> 0.275 | +3.0 pt [-2.2, +8.1] |
| w2 | 2c | rollback (tool_calls, abstention_gate) | 94/62/0 -> 94/59/3 | 10/8/0 | 138/9/9 | 14/3/1 | 0/18 | 1/28 | 0+7 | 30/30 | 0.585 -> 0.240 | +2.0 pt [-3.2, +7.1] |
| w2 | 2d | rollback (abstention_gate) | 94/62/0 -> 87/68/1 | 8/10/0 | 140/11/5 | 13/4/1 | 1/18 | 0/20 | 10+10 | 30/30 | 0.590 -> 0.210 | +1.5 pt [-3.6, +6.6] |
| w3 | n2 | rollback (tool_calls, abstention_gate) | 76/83/0 -> 66/93/0 | 14/4/0 | 132/17/10 | 13/5/0 | 0/18 | 0/27 | 0+0 | 30/30 | 0.590 -> 0.745 | +2.0 pt [-3.3, +7.3] |
| w4 | 2b | rollback (abstention_gate) | 73/89/0 -> 90/72/0 | 12/6/0 | 119/31/12 | 11/5/2 | 0/18 | 3/30 | 10+9 | 30/30 | 0.585 -> 0.265 | +2.5 pt [-2.4, +7.4] |

No night-2 variant is kept under the rule of record (abstention gate v3). Under report-only (owner card
META-ABSTENTION-GATE, option C) w4 2b would be a keep: `eval/redecide_night.py --world w4-grad-student --day 2
--night-dir night-2b --rule report-only` (written as `decision-report-only.json`; the record is unchanged).

## What each result says
- **Answer channel, fixed**: R2 adapters put 10-37% of chat replies in the self channel (content ''). Their training
  masked the answer header's recipient, so the adapter never learned it. Every night-2 variant gets 30/30, including
  2c with the header masked. The agent anchors' plain-answer turns seem to cover it. The app's one answer-channel
  retry turns R2 w2 from 27/30 into 30/30.
- **Tool calls, the n2 break**: n2 made no tool call in w1 and w3 (0/20). Raw `/completion` shows the adapter writing
  the right call. It drops one token, `<|eom|>`, between its self message and the call, so the server's parser keeps
  everything as reasoning (`tool-turn-raw-w1-n2.json`, `tools-chat-w1-n2-serial.json`). Cause: the agent anchor rows
  were built from the server's "content" text, which silently drops `<|eom|>`. 2b rebuilds them from the generated
  token ids, with guards at build time and in the trainer: re-tokenizing the text must give exactly those ids, or the night fails.
- **Tool calls in w2 stay broken**: with exact anchors, w1 2b gets 20/20 and w4 2b 19/20, but w2 2b gets 3/20 and w2 2c 7/20.
  The w2 adapter answers a stated fact without saving it: "Got it, I saved the offsite date as 2026-11-20." with
  no remember call (10 of 10 remember turns). The note is never written. Masking the header (2c) does not change
  this. **2d fixes it (10 + 10 of 20)** by dropping the 16 rows in which the rewriter answers a store request in text:
  "Can you save my business name for future invoice reminders?" -> "Saved, your business name is Northloop Studio Oy."
  Those rows teach the exact failure. Caveat kept beside the result: R2 w2 had 28 such rows and still made 20/20
  calls, so the filter is necessary here, but the reason it bit on night 2 and not night 1 is not shown. Night 3 runs
  2c + the filter on all four worlds.
- **Self-check (n2 only, dropped)**: in w1 it replaced 7 facts' answer rows. Of those facts' 15 probes, 8 went from
  right to wrong and none became an abstention. It flagged facts the adapter gets right on the held-out wording, and
  the deferral row did not carry over to "What's my X?". 2b without it gets 7 more right on B.
- **Corrections**: stale 0-1 of 18 in every variant. The night's consolidation dropped 5 of the 7 corrections the day
  agent left unlinked (`consolidated.json` "replacements", each with who linked it).
- **Never-told with the notes in the prompt (D)**: 24-30 of 30 in every variant, up from 0-2 in R2 (unknown-key rows).
  Without notes (B) the adapter still invents a value (0-3 of 30).
- **Forgetting noise**: w1 2b misses the forgetting bar by its interval's lower bound (-6.4 vs -5). On this 200-item
  slice the base arm A alone moves 118 -> 121 between runs, so a -1.5 point delta is noise. The rule stays unchanged.

## Abstention in the product's own form (option B, Meta steering, 29 Oct ~08:30)
The day agent's real loop (its prompt, tools and the world's notes), base vs adapter. General = the 200 SimpleQA
gate-slice questions (100 asked per world), never-told = 30 per world. Scored with `eval/product_abstention.py`; rule
(one-sided): adapter general abstain rate >= base's - 10 pt and never-told abstentions >= base's - 3. Records:
`runs/<w>/2/<night-2b|night-2c>/eval/product-abstention-night.json`, `decision-product.json` beside each (the record
stays v3 until the owner rules).

| world | variant | general abstain, base -> adapter (of 100) | never-told, base -> adapter (of 30) | product-form check | decision under B |
|---|---|---|---|---|---|
| w1 | 2b | 50 -> 71 | 29 -> 22 | fail | rollback (also forgetting) |
| w2 | 2b | 55 -> 84 | 30 -> 27 | pass | rollback (tool calls) |
| w2 | 2c | 55 -> 37 | 30 -> 27 | fail | rollback (also tool calls) |
| w4 | 2b | 44 -> 50 | 28 -> 28 | pass | **keep** |

The rule is one-sided, so declining too much passes (w2 2b, +29 pt on general). A two-sided band on the general set
(within 10 pt of base) would still keep w4 2b (+6) and fail w2 2b. In the agent loop R2 abstains on never-told 0, 12,
6 and 16 of 30 (w1-w4), while the forced-answer B arm gives 0 everywhere: the two forms measure different things.
