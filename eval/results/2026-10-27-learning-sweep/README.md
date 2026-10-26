# Learning go/no-go sweep — Tue 27 Oct 2026

**Question:** can one night of QLoRA on one RTX 4090 make Muse Glimmer 30B answer questions about a day's new facts, asked in words it never trained on, without notes in the prompt and without losing general skills?

**Answer: yes.** Every arm learned. With the recipe of record (S7/S8: Glimmer writes its own training data), 45 of 45 facts pass at least 2 of 3 held-out paraphrases, against 0 of 45 for the base model. No loss was detected on the 200-item forgetting slice: the change is within noise and the interval spans zero.

## Setup

- **World** `worlds/w1-ines-varga`: a hand-written synthetic persona with 45 facts about work, projects, people and places, plus 3 preferences and 1 procedure. **Held-out probes:** 3 forward paraphrases per fact and 12 reverse questions, frozen by hash in `heldout/FROZEN.json` (commit `14d0564`) before any training data existed. The leak check drops any training row that contains a whole probe question or shares an 8-word sequence with one (1–4 rows per train set, `leak/`).
- **Training data** per fact: 12 rewrites of what the user said (4 statements, 4 questions, 2 reverse questions, 2 uses in a task), written by a teacher model, plus 10% general anchor rows. The anchors are the base model's own answers to 60 general prompts.
  - Spark: Muse Spark through the development subscription, arms S1–S6.
  - Glimmer: Muse Glimmer writing for itself, arms S7–S8.
- **Model:** `unsloth/Muse-Glimmer-30B-unsloth-bnb-4bit@ecedda39` (NF4), with LoRA on all 52 text layers, batch 16, AdamW 8-bit, constant LR after 10 warm-up steps, and loss on the assistant reply only. Training ran on mel (RTX 4090) at about 500 training tokens/s, with a peak of 19.5 GiB.
- **Scoring** in transformers (PEFT on NF4): greedy decoding, with the answer forced into the user channel, at most 64 new tokens for facts and general items, 256 for preferences and the procedure, and truncation counted as failure.
  - A fact counts as **learned** when at least 2 of its 3 paraphrases pass.
  - **B−A** is the per-fact pass-rate delta, with a paired cluster bootstrap (10,000 resamples, seed 20261014).
  - **Forgetting slice:** 180 MMLU-Pro test items at revision `b189ec76`, stratified by category with seed 20261014, plus 20 tool-call items. It is reported as after − before with a Newcombe interval and exact McNemar.
- **Arms:** A = base without notes, B = adapter without notes, C = base with the facts in the prompt (a perfect-notes control), D = adapter with the notes.

## Results (`table-v1.md`, receipts in `scores/`)

| arm (what changed) | facts learned | forward | reverse | general 30 | forgetting 200 (base 115) | B−A per fact [95% CI] |
|---|---|---|---|---|---|---|
| base A | 0/45 | 0/135 | 1/12 | 29/30 | 115/200 | — |
| base C (notes) | 45/45 | 135/135 | 12/12 | 29/30 | — | +1.000 |
| S1 r64 attn+MLP, Spark ×12, LR 1e-4, epoch 2 | 42/45 | 126/135 | 9/12 | 29/30 | — | +0.933 [0.874, 0.978] |
| S1 epoch 4 | 43/45 | 126/135 | 8/12 | 29/30 | 124 (+4.5 pt [−0.5, +9.5]) | +0.933 [0.867, 0.985] |
| S1 epoch 4, arm D (adapter + notes) | 45/45 | 135/135 | 12/12 | 29/30 | — | D−C 0.000 |
| S2 attention only | 43/45 | 119/135 | 8/12 | 29/30 | — | +0.881 [0.822, 0.933] |
| S3 rank 16 | 44/45 | 124/135 | 9/12 | 29/30 | 124 (+4.5 pt [−0.4, +9.3]) | +0.918 [0.867, 0.963] |
| S4 4 rewrites per fact | 38/45 | 112/135 | 6/12 | 29/30 | — | +0.830 [0.741, 0.911] |
| S5 LR 5e-5, epoch 2 | 41/45 | 122/135 | 6/12 | 29/30 | — | +0.904 [0.844, 0.956] |
| S5 epoch 4 | 44/45 | 127/135 | 9/12 | 29/30 | 125 (+5.0 pt [+0.1, +9.9]) | +0.941 [0.896, 0.978] |
| S6 1 epoch (107 s of training) | 38/45 | 115/135 | 5/12 | 29/30 | — | +0.852 [0.763, 0.933] |
| S7 Glimmer self-teaching (S1 recipe), epoch 2 | 39/45 | 114/135 | 8/12 | 29/30 | — | +0.844 [0.748, 0.926] |
| S7 epoch 4 | 45/45 | 129/135 | 10/12 | 29/30 | see S8 run | +0.956 [0.918, 0.985] |

Tool items: 20/20 before and 20/20 after in every scored arm.

## What it shows

1. The 14 Oct smoke learned nothing (0/30) because it trained on 1 row per fact for 1 pass. It did not fail because LoRA cannot hold facts: with 12 varied rewrites per fact and 4 epochs, even attention-only LoRA learns 43/45.
2. Rank 16 learns as well as rank 64. 12 rewrites per fact beat 4. Two epochs get most of the way, and four finish it.
3. Glimmer teaching itself (S7) is at least as good as Spark as the teacher (S1). The private path holds.
4. **No loss detected** on general skills: the 200-item change is +4.5 to +5.0 points, within noise (McNemar p 0.08–0.12, intervals that span or touch zero). Part of the rise is a format effect: the base model ran past the 64-token limit on 7 of 180 MMLU-Pro items, and the adapters on none.
5. Weights alone remain below perfect notes, by −0.04 to −0.07 per fact (B−C). The misses are cross-fact binding errors: the model learned the facts but sometimes attaches one to the wrong question. Examples: the workshop town answered with the backup shop's town ("Moravik"); the safety officer answered "Sunniva Brekke" (the test pilot). The per-fact list of misses is in `table-v1.md`.

## Recipe of record for the night job (approved 27 Oct)

Rank 16, attention + MLP (`q/k/v/o`, `mlp.gate/up/down`; the attention gate is excluded), all 52 layers, 12 Glimmer self-teaching rewrites per fact, LR 1e-4, 4 epochs, 10% self-distilled anchors.

## Also here

- `gguf-verify-S1e4.json`: the S1 adapter converted to a GGUF LoRA (llama.cpp v0.7.0 with `night/llamacpp/muse-glimmer-lora.patch`). It matches PEFT on all 364 modules (worst relative error 4.3e-4).
- `data/`: the train sets, rewrites, anchors and teacher token logs.
  - Spark: 49 calls, 18,049 prompt and 211,291 completion tokens.
  - Glimmer: 49 calls, 20,401 prompt and 44,725 completion tokens. A further 31 Glimmer calls were lost when the serving box rebooted.
- `scores/base-parity.json`: batched and one-at-a-time scoring give the same pass/fail on 16/16 probes.

Limits: one persona and one day; scored in transformers on the NF4 weights. The morning eval of record runs in llama.cpp on the Q4_K_M target with the GGUF adapter.
