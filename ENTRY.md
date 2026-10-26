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

## Pre-existing work used as external dependencies

Fetched at setup or called over the network. None of it is copied into this repository.

| Dependency | Pin | Used for |
|---|---|---|
| [oneiron-dev/afterglow](https://github.com/oneiron-dev/afterglow) (ours, published before the event) | commit `06e9ecf754a25ffbf0fed3eae812aea72994a9a4` | llama.cpp + DFlash serving setup, KV snapshot harness, 14 Oct QLoRA and drafter measurements |
| [Oneiron](https://github.com/oneiron-dev/oneiron) memory engine (ours, published before the event) | engine commit recorded in each run receipt | notes store and overnight consolidation (Dreamer) |
| Muse Glimmer 30B (Meta) | `meta-models/Muse-Glimmer-30B-GGUF` @ `70bf1b61`; `unsloth/Muse-Glimmer-30B-unsloth-bnb-4bit` @ `ecedda39` | the model that runs by day and learns at night |
| Official DFlash drafter for Glimmer (Meta) | same GGUF repo and revision | faster decoding |
| llama.cpp, Unsloth, PEFT, Transformers | versions recorded in each run receipt | serving and training |

## Hosted model use during development

- Development-time Muse Spark calls (synthetic test worlds, answer grading, a Spark-vs-Glimmer teacher comparison) run on the owner's Meta subscription. The product itself calls only the local Muse Glimmer.
- The event's Meta Model API credit is not used so far.
