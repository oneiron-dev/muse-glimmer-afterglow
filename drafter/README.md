# Drafter retraining on each world's own traffic

Glimmer serves with Meta's DFlash drafter: a 2.6 B model that drafts up to 15 tokens, and Glimmer checks every
drafted token. A better-fitted drafter cannot change an answer. It only makes replies faster.

This folder tests one idea: each night, retrain the drafter on the agent's own traffic from the day. The test uses
the four Afterglow worlds (w1-w4). Days 1-9 are training data and day 10 is held out.

**Result (Tue 3 Nov 2026, 23:2x JST): the idea works.** One drafter retrained on the four worlds' own traffic
(days 1-9) is the fastest arm in every world on held-out day 10. Live in llama.cpp at the serving settings it reaches
**6.40 accepted tokens per verify step and 118.0 tok/s (2.85× the target alone)**. Meta's stock drafter reaches 5.74
and 107.4 tok/s (2.59×). That is **+11.4 % acceptance and +9.9 % decode speed** over Meta's file. The outputs stay
the stock drafter's on 151 of 153 rounds; the 2 splits are near-ties in the target itself.

- Serving: llama-server takes one drafter for all worlds, and the pooled drafter is that one file
  (`pooled-Q4_K_M.gguf`, sha256 `d85bf7f19e9af53dd82616f035ef3c52f225f02e834165e38078cdbaa9db57c7`).
- Per-world drafters also win in all four worlds (+7.9 % tok/s over stock). The pooled drafter wins by more,
  because most of the gain is the agent's shared format. A user's own facts add only a little.
- Step A's generic retrain (14 Oct, on generic chat) gives no gain here (5.65, 105.6 tok/s).

## Method

1. **Sequences** (`build_sequences.py`, on the Mac). Each served day is rebuilt the way `app/afterglow/agent.py`
   sent it: the system message, the day's context message (`session.json`), then every user turn and every
   assistant round from `daylog.jsonl`, with the day's tool list. The served chat template keeps every round's
   reasoning, so one day renders as one sequence. The prompt of each round is a prefix of that sequence. Each
   assistant round becomes one span of generated tokens: everything the model wrote after `<|start|>assistant`
   (reasoning, tool call or reply), up to and including its end token. The output is 40 days, about 9 k tokens and
   3.5 k generated tokens per day.
2. **Taps** (`extract_taps.py`). The served target for a world is Glimmer with that world's kept LoRA adapter at
   scale 1. The drafter reads the target's hidden states, so the taps come from the NF4 target with the world's
   PEFT adapter applied (layers 1, 13, 25, 37, 49, as in step A). The adapter moves the taps a lot: for w1 the taps
   change by 40 % relative to base Glimmer.
3. **Training** (`train_drafter.py`). This is step A's recipe (14 Oct): the DFlash objective, starting from Meta's
   drafter, with the embedding table and lm_head frozen. Anchors are only inside generated spans. A block's labels
   stop at its span's end, because the drafter only drafts inside one generation. The step takes 1024 anchors in
   64-anchor windows. The optimizer runs on the CPU. On a rented box, cap torch's threads to the container's CPU
   quota: with 255 threads on a 30-core quota a step takes 23 s; with the cap it takes 7 s.
4. **Recipe pilot** (w1, train on days 1-8, check on day 9). The stock drafter scores 7.60. With lr 2e-5 the score
   is 8.29 after epoch 1, 8.47 after epoch 2 and 8.50 after epoch 3. With lr 6e-5 it is 8.15 after epoch 1. The
   recipe for every final drafter is lr 2e-5 with 2 epochs.
5. **Conversion** (`convert.sh`). llama.cpp 620d2225 converts the drafter to GGUF and quantizes it to Q4_K_M with
   attn_v and ffn_down at Q6_K. This gives the same tensor layout as Meta's file (26 Q4_K, 10 Q6_K, 22 F32).
6. **Live eval** (`run_live.sh`, `eval_live.py`, `analyze_live.py`). The eval runs llama.cpp 620d2225 with the Glimmer
   LoRA patch (the serving build). It uses the serving DFlash flags (`--spec-draft-n-max 15`, greedy drafting), the
   world's adapter loaded as served (`--lora-scaled ...:0`, scale 1 per request), greedy decoding, concurrency 1 and
   one slot. Every assistant round of the held-out day 10 runs from its exact served prompt (token ids). Acceptance
   length = 1 + accepted ÷ verify steps, from `/metrics`.
   - Arms: the target alone; Meta's drafter file; Meta's drafter through our conversion ("stock ours", the fair
     baseline); step A's generic retrain; the world's own retrain; the pooled retrain (four worlds).
7. **Teacher-forced check** (`train_drafter.py --eval-only`). This is greedy acceptance over every anchor of the
   held-out day, on the logged tokens. It is the first half of the keep rule. The live eval is the second half.

## Results

### Teacher-forced acceptance on held-out day 10 (tokens per verify block, max 16)

| Drafter | w1 | w2 | w3 | w4 | all |
|---|---|---|---|---|---|
| Stock (Meta) | 8.01 | 6.60 | 7.20 | 8.68 | 7.70 |
| own-w1 (w1 days 1-9) | **9.11** | 7.25 | 8.06 | 9.83 | 8.65 |
| own-w2 | 8.89 | **7.39** | 8.19 | 9.94 | 8.68 |
| own-w3 | 8.95 | 7.24 | **8.13** | 9.77 | 8.61 |
| own-w4 | 8.98 | 7.36 | 8.05 | **9.89** | 8.66 |
| **pooled (4 worlds, days 1-9)** | **9.18** | **7.58** | **8.43** | **10.16** | **8.92** |

- Every retrained drafter lifts every world. Most of the gain comes from the agent's shared format: its reasoning
  style, its `remember` calls and the shape of its replies.
- The world's own traffic adds a small part on top. w1 and w2 score best with their own drafter. On w3 and w4,
  w2's drafter scores a little higher than the world's own. On average the own drafter beats another world's
  drafter by about +0.09, while any retrain beats stock by about +0.85.

### Live llama.cpp on held-out day 10 (acceptance length / decode tok/s / speedup over the target alone)

| World (rounds) | Target alone | Stock (Meta's file) | Stock (our conversion) | Step-A generic | Own world | Pooled |
|---|---|---|---|---|---|---|
| w1 (47) | 41.5 tok/s | 6.04 / 112.3 / 2.71× | 5.96 / 111.5 / 2.69× | 5.82 / 107.7 / 2.60× | 6.34 / 116.4 / 2.80× | **6.55 / 120.9 / 2.91×** |
| w2 (37) | 41.1 tok/s | 5.11 / 96.0 / 2.33× | 5.05 / 94.9 / 2.31× | 5.14 / 96.5 / 2.35× | 5.44 / 101.9 / 2.48× | **5.55 / 102.4 / 2.49×** |
| w3 (33) | 41.4 tok/s | 5.30 / 99.9 / 2.42× | 5.20 / 98.0 / 2.37× | 5.22 / 98.6 / 2.38× | 5.75 / 107.9 / 2.61× | **6.11 / 112.9 / 2.73×** |
| w4 (36) | 41.5 tok/s | 6.44 / 119.7 / 2.89× | 6.48 / 120.3 / 2.90× | 6.39 / 118.6 / 2.86× | 7.24 / 133.9 / 3.23× | **7.42 / 136.6 / 3.29×** |
| **all (153)** | 41.4 tok/s | 5.74 / 107.4 / 2.59× | 5.69 / 106.5 / 2.57× | 5.65 / 105.6 / 2.55× | 6.19 / 114.9 / 2.78× | **6.40 / 118.0 / 2.85×** |

- The target alone decodes at 41 tok/s here; step A measured 51 tok/s. Here the world's adapter runs at decode
  time and the contexts are about 10 k tokens.
- Live acceptance (about 6) is lower than teacher-forced acceptance (about 8). Live, the drafter is the Q4_K_M file,
  and greedy replies differ from the logged replies, which were sampled at temperature 0.3. The ranking of the arms
  is the same in both.

### Losslessness

| World | Pooled = stock ours | Own = stock ours | Meta's file = stock ours | Stock ours = target alone |
|---|---|---|---|---|
| w1 | 46/47 | 46/47 | 47/47 | 35/47 |
| w2 | 37/37 | 37/37 | 36/37 | 24/37 |
| w3 | 33/33 | 32/33 | 33/33 | 23/33 |
| w4 | 35/36 | 33/36 | 36/36 | 22/36 |
| all | 151/153 | 148/153 | 152/153 | 104/153 |

The drafter arms differ from the target alone on some rounds. Meta's own drafter file differs in the same way, so
the drafter does not cause this. llama.cpp's speculative verify batch breaks near-ties differently from one-token
decoding (step A, 14 Oct). The w1 own-vs-stock split is a punctuation choice inside the reasoning channel
(`Title "Gullwing review"` vs `Title: Gullwing review`). At that point the target alone ranks `"` first (p 0.53) and
`:` second (p 0.41). The retrained arms take the target's first choice there; the stock arm takes its second.
`tie_check.py` reads the target's top 5 at every split (`results/live/tie_check-<world>.json`).

## Files

- `build_sequences.py`, `extract_taps.py`, `train_drafter.py`, `convert.sh`, `eval_live.py`, `run_live.sh`,
  `analyze_live.py`, `tie_check.py`: the pipeline.
- `results/`: the live summary (`live-summary.json`), the stock check (`stock-day10.json`) and every drafter's
  training report (held-out checks per epoch, step times). The drafter weights are not in git.
- `run_taps.sh`, `run_stage1.sh`, `run_stage2.sh`, `run_all.sh`, `run_series.sh`, `run_after.sh`, `launch.sh`:
  the box jobs (`launch.sh` starts each one with setsid, so an ssh or session stop does not kill it).
