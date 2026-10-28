# Glimmer as its own decision model — design

One Glimmer on one llama-server does chat, the night teacher, the learner and the decisions. Afterglow decides in one
forward pass when it is sure, and thinks it through when it is not.

## One-pass decision
A decision is a question with a fixed option list. Every option label is one token in Glimmer's tokenizer (the
numbers 0–999 are single tokens; `decide/readout.py` checks each label). The row's prompt is rendered with Glimmer's
chat template exactly as the eval of record renders a probe (reasoning strength minimal, same system text; 72/72
tokens on w1 F01-q1), followed by the forced answer prefix `<|start|>assistant to=user<|message|>`, so the next token
is the label. One `/completion` call with `n_predict` 1 and pre-sampling `n_probs`; the label probabilities are
renormalized over the label set (`label_mass` keeps the share they held). Decision = top label; confidence = its
probability. Every call names every adapter's scale and sends `cache_prompt: false`.

## Kinds (gold labels from code only)
| kind | question | options | gold |
|---|---|---|---|
| route (day) | which note answers the user's question? | 1..N = every note of the night (`key: value`), 0 = none of them (ask the user) | the note whose value holds the fact (`eval/scoring.py`), else 0; never-told probes = 0 |
| duplicate (night) | are two notes the same fact? | 1 same fact (keep one), 2 different facts (keep both) | same item id (note vs the night's restatement of it) vs different facts (same user sentence, or keys sharing a word) |
| supersede (night) | does the new note replace the earlier one? | 1 replaces (a correction), 2 both stay true | `superseded_by` in world.json (new note as key: value and as the user's day-2 sentence) vs facts that share a key word |

Route is the retrieval fix the D5 baseline asks for (lead, 29 Oct 03:1x). The chosen note goes through the D5 value
compare (`decide/notes_agreement_gate.py`): the weights answer agrees with it → answer from the weights; it does not →
answer from the notes; 0 → ask. All notes are options because lexical top-k misses the answering note too often
(BM25 recall@8 = 0.48 on the 609 forward probes whose fact is in the notes).

## Splits (frozen before any training row; `decide/data/FROZEN.json`)
- **test** = w4 (a world the decision adapter never sees): route 180 forward + 30 never-told, duplicate 126, supersede 36.
- **calibration** = w1–w3 held-out probes + never-told (route 576), 20% of the w1–w3 pair rows by row-id hash. Thresholds are fit here only.
- **train** = w1–w3: route questions from the night's own training rows (forms qa_forward, in_context, qa_reverse; notes shuffled per row; 20% of rows drop every answering note → 0), the other pair rows. Leak check (`night/leak_check.py` `check`) against every held-out and never-told question: 1 hit dropped, 0 left (`decide/data/leak.json`).

## Arms (frozen before tuning)
- D0 base Glimmer, one pass. D1 decision LoRA (night/train_adapter.py rows with the label as the assistant reply), one pass. D2 D1 + auto-extend (reasoning on below a per-kind threshold fit on calibration; route escalates to ask/notes, not reasoning, unless reasoning is measured to help). D3 base with reasoning always on. D4 a Clef-style head on last hidden states (offline, mel). D5 notes agreement gate with app recall (baseline, done).
- Per kind: accuracy with a paired cluster bootstrap against D0 (10,000 resamples, seed 20261014), ECE (15 bins), wrong at p ≥ 0.9, escalation rate, p50/p95 latency.
- Route: the D5 score over 666 forward + 120 never-told — right from weights | looked up (scored by arm D) | wrong | asked; score = (right − wrong)/n — beside always-D, D5-app and D5-oracle; w4 reported alone as the clean test.
- Chat path check: decision adapter loaded at scale 0, 20 chat answers byte-identical to the server without it.
- Receipts: `eval/results/<date>-decision/`.
