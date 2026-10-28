# ENTRY: what was built during the hackathon

Meta AI Developer Hackathon, Indie track. Building started after the kickoff session on 26 Oct 2026; submissions close 5 Nov 2026, 02:00 UTC.

Every commit in this repository was made during the event. The tag `event-start-2026-10-27` marks the first commit.

## Built during the event

| Date (UTC) | What was added | PRs |
|---|---|---|
| 26 Oct | Repository, this ledger, dependency setup script. | — |
| 26 Oct | Glimmer serving manager for one RTX 4090 (start, stop, health, endpoint) and a pass-through proxy that keeps a thought log: each chat call's reasoning and answer, one JSON line per call. | #1 |
| 26 Oct | Day agent on Glimmer with tool calls (remember, recall, calendar, tasks), a notes store where corrections supersede old values, KV snapshot identity checks and re-warm after a night update, and the local web app. | #2 |
| 26 Oct | Snapshots cover only each world's stable prefix (about 144 MB); the day is prefilled after restore. | #3 |
| 26 Oct | Night learning engine: one teacher interface (Glimmer or Spark), fact rewrites, self-distilled general anchors, leak check, QLoRA trainer on the NF4 checkpoint, four-arm scorer, sweep runner, and a llama.cpp patch plus check for converting Glimmer LoRA adapters to GGUF. First sweep world `w1-ines-varga` with held-out probes frozen by hash before any training data existed. | #4 |
| 26 Oct | World generator for synthetic personas (work, projects, people, places and procedures only), three new worlds with day-2 corrections and frozen held-out probes, a day simulator that plays a day's script through the real agent, and the Oneiron notes backend (witness and recall on the user's vault, agent credentials only). | #5 |
| 26 Oct | Morning report page: facts learned with notes off against the notes control, what it lost on general skills, per-fact results, stale values, the keep or roll back checks, each number linked to its receipt. | #6 |
| 26 Oct | "Ask it yourself, notes off" on the morning report: one question answered by the model before and after the night update, with no notes and no tools. | #7 |
| 26 Oct | Learning sweep (GO): one night update takes held-out facts from 0/45 to 43–45/45 with notes off, no loss detected on a 200-item general and tool slice; Glimmer teaching itself matches Spark as teacher. Results and receipts under `eval/results/2026-10-27-learning-sweep/`. Also the forgetting slice of record, the llama.cpp evaluator, the eval.json builder, GGUF adapter conversion with a per-module delta check, and the night job orchestrator. | #8 |
| 26 Oct | Overnight thought log page: each Glimmer call made by Oneiron's Dreamer and by the night job, with its task, reasoning and answer. | #9 |
| 26 Oct | Serving loads one LoRA adapter per world from the night job's manifest, applied per request. | #10 |
| 26 Oct | Night consolidation fixes: duplicates decided by exact key and value (the model's merge proposals are recorded and overruled when values differ), per-note outcomes with reasons, the persona's name per world. | #12 |
| 26 Oct | Day app README: what each piece does, how to run it end to end, how to work without a GPU. | #11 |
| 26 Oct | One page nav across the day, night and morning pages; an empty day invites the first message. | #13 |
| 26 Oct | Each speaker is witnessed into the vault with its own credential (the user's person credential, the assistant's agent credential), so Oneiron's Dreamer knows whose facts they are; vault writes run off the chat path. | #14 |
| 26 Oct | Fixes from a review of the day app: each world's adapter is matched by its full path when several are loaded, adapter and snapshot errors stop the run instead of passing silently, resumed vault pushes never repeat a turn, and the report names a rolled-back update. | #16 |
| 26 Oct | Adapters load at scale 0 and the proxy gives any request that names no adapter scales all-zero scales, so no caller (the Dreamer, health checks) gets every world's adapter at once and no cached prompt computed under another adapter is reused; a check script for the swap. | #18 |
| 27 Oct | "What it chose to learn" on the morning report: per note kept, merged as a duplicate or dropped as superseded, with the reason, and the merges the model proposed that the night refused; plain wording for the keep checks and the forgetting line. | #15 |
| 27 Oct | Every witnessed message carries a deterministic id, so a timed-out vault write is resent with the same ids and cannot be stored twice; 90 s read timeout. | #19 |
| 27 Oct | Night job end to end on four synthetic people (R2): every night kept its update; with notes off the updated model recalls 134 of 222 facts (base 0), no loss detected on general skills and no new tool failures. Results and receipts under `eval/results/2026-10-27-r2-day1-nights/`. Also a grader audit and scorer v2 that re-scores from saved answers, an Omniscience-style score (right minus wrong, "I don't know" neutral), a miss breakdown (binding, invented, not in notes), per-answer token confidence, a first confidence gate, contrast rows for the binding experiment, and the serving manifest. | #22 |
| 27 Oct | Morning report: one line across all four synthetic people and a person switcher; the day page says when a kept update is not loaded on the server yet; the serving manager stops a booking cleanly when the GPU scheduler defers the stop. | #20, #21, #23 |
| 27 Oct | Day agent note-taking: it saves facts mentioned inside requests, what a name stands for, the relation in the key and the exact value. Re-simulated day 1 with notes in the prompt: 191 -> 195 of 222 facts; on the weakest person 42 -> 50 of 60, facts never noted 6 -> 0. | #24 |
| 27 Oct | Morning report shows the abstention check (added after the first nights ran) as a sixth check: how often the model still says it does not know on general questions, before and after the night; the first four nights fail it, and the report says so. | #25 |
| 27 Oct | Abstention check of record (frozen 200-question SimpleQA slice; a night must keep its "I don't know" rate within 10 points of the base), never-told probe sets (questions whose right answer is "that's not in your notes"), an AA-Omniscience-Public harness (own harness, verbatim prompts, own arithmetic), the confidence-gate result (single-pass token confidence cannot separate binding errors from right answers; always looking up notes wins), and the binding-experiment and prompt-hint arms. | #26 |
| 28 Oct | Serving moves to the second RTX 4090 (mel) on the llama.cpp build that loads the night's adapters; the adapter manifest names the box it was staged on, and the server refuses a manifest staged on another box. | #30 |
| 28 Oct | Day 2 for the first synthetic person: six corrections to day-1 facts (a call moved, a contact replaced, a date, a room, an extension, a status note), new and stale-value probes frozen by hash before any day-2 data existed, day-1 probes and their freeze record kept unchanged. | #31 |
| 28 Oct | Glimmer as its own decision model, groundwork: the notes-agreement baseline (answer from the weights, compare with the matching note, look up or ask on a mismatch), decision rows for three kinds (which note answers a question or none, are two notes the same fact, does a new note replace an old one) with labels computed from the world files, a test person frozen by hash and kept out of training, a one-pass readout that reads each option's probability from the next token, its scorer and the training launcher. | #29 |
| 28 Oct | Abstention experiments written up, nulls included: training on general "I don't know" examples flips the check's prompt form without restoring general abstention (line closed); a prompt hint and contrast rows with notes do not help; rows for questions the user never answered teach the model to say its notes lack a fact when notes are in the prompt. Also night 2: training from the base on every fact still true, "let me check your notes" rows for facts the current model gets wrong, and a leak check on the questions it is asked. | #32 |
| 28 Oct | Steering with control vectors (re:dvx): five axes (says when it does not know, concise, formal, decisive, asks before acting) fitted on Glimmer's residual stream and exported as llama.cpp control vectors. A behaviour check reads each axis on the base model and on a night's adapter with no generation: the first night made the model more decisive (+3.5 persona contrasts on everyday prompts), more concise (+1.8) and less likely to say it does not know (-1.6). A fixed-strength screen on llama-server and a night step that writes the check into the night's record. Results under `eval/results/2026-10-29-steering/`. | #33 |
| 28 Oct | Night proposals: after the behaviour check, Glimmer reads the day's messages and how last night's update changed it, and proposes up to two dial changes for tomorrow, each with its own reason; a proposal takes effect only when the user accepts it. For the first synthetic person it kept "more concise" (she asked for three-bullet summaries) and proposed more cautious, citing her rule to ask before emailing her pilot customer. | #35 |
| 28 Oct | Day 2 on four synthetic people: the agent links a correction to the note it replaces (it is shown up to five earlier notes and decides), and a turn that ends with an empty reply is asked once more for the answer; every such retry is logged and counted on the day page. | #34 |

## Pre-existing work used as external dependencies

Fetched at setup or called over the network. None of it is copied into this repository.

| Dependency | Pin | Used for |
|---|---|---|
| [oneiron-dev/afterglow](https://github.com/oneiron-dev/afterglow) (ours, published before the event) | commit `06e9ecf754a25ffbf0fed3eae812aea72994a9a4` | llama.cpp + DFlash serving setup, KV snapshot harness, 14 Oct QLoRA and drafter measurements |
| [Oneiron](https://github.com/oneiron-dev/oneiron) memory engine (ours, published before the event) | engine commit recorded in each run receipt | notes store and overnight consolidation (Dreamer) |
| Muse Glimmer 30B (Meta) | `meta-models/Muse-Glimmer-30B-GGUF` @ `70bf1b61`; `unsloth/Muse-Glimmer-30B-unsloth-bnb-4bit` @ `ecedda39` | the model that runs by day and learns at night |
| Official DFlash drafter for Glimmer (Meta) | same GGUF repo and revision | faster decoding |
| llama.cpp, Unsloth, PEFT, Transformers | versions recorded in each run receipt | serving and training |
| [re:dvx](https://github.com/lumpenspace/redvx) (MIT) | `redvx` 0.1.0 from PyPI, installed with `--no-deps` (it pins transformers <5.9; Glimmer needs 5.15, under which every module imports) | contrast-pair protocol, direction fit, common-component removal, vector format |

## Hosted model use during development

- Development-time Muse Spark calls (synthetic test worlds, answer grading, a Spark-vs-Glimmer teacher comparison) run on the owner's Meta subscription. The product itself calls only the local Muse Glimmer.
- The event's Meta Model API credit is not used so far.
