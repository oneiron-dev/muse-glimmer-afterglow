# ENTRY: what was built during the hackathon

Meta AI Developer Hackathon, Indie track. Building started after the kickoff session on 26 Oct 2026; submissions close 5 Nov 2026, 02:00 UTC.

Every commit in this repository was made during the event. The tag `event-start-2026-10-27` marks the first commit.

## Built during the event

| Date (UTC) | What was added | PRs |
|---|---|---|
| 26 Oct | Repository, this ledger, dependency setup script. | — |
| 26 Oct | Glimmer serving manager for one RTX 4090 (start, stop, health, endpoint) and a pass-through proxy that keeps a thought log: each chat call's reasoning and answer, one JSON line per call. | #1 |
| 26 Oct | Day agent on Glimmer with tool calls (remember, recall, calendar, tasks), a notes store where corrections supersede old values, KV snapshot identity checks and re-warm after a night update, and the local web app. | #2 |

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
