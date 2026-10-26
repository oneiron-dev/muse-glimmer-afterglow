# Muse Glimmer Afterglow

> **Pre-existing:** our open-source Glimmer serving tools, [oneiron-dev/afterglow](https://github.com/oneiron-dev/afterglow) at commit `06e9ecf` (llama.cpp + DFlash setup, KV snapshots, a tuned drafter), and the [Oneiron](https://github.com/oneiron-dev/oneiron) memory engine. Both are external dependencies, fetched at setup and never copied in. **Built during the Meta AI Developer Hackathon**, after the kickoff session: everything in this repository. [ENTRY.md](ENTRY.md) lists it.

**A personal Muse Glimmer that learns your facts and procedures while you sleep.**

Muse Glimmer 30B runs on your own GPU. By day it works with you as an agent and keeps notes. At night it updates its own weights: a job turns the day into training data, trains a small QLoRA adapter on the same consumer GPU, and tests it against held-out questions and general skills. It keeps the update only if it learned without forgetting. The morning result: what it learned, which stale facts it dropped, and what it lost, measured against a notes-in-context control.

Status: being built during the hackathon (26 Oct – 4 Nov 2026).

## Setup

```bash
scripts/setup_deps.sh   # fetches oneiron-dev/afterglow at the pinned commit into .deps/ (gitignored)
```

## Licence

Apache-2.0 ([LICENSE](LICENSE)). The Muse Glimmer weights are Meta's, under their own licence and usage policy.
