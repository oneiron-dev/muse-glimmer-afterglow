# The day app

The part of Afterglow the user sees: a Muse Glimmer agent that keeps notes by day, the morning report of
what the night's training changed, and the log of what Glimmer reasoned overnight. Everything here was
built during the hackathon. Python 3.11+ standard library; only the Oneiron notes backend needs two
packages (`uv run --extra oneiron`).

## Pieces

| Path | What it does |
|---|---|
| `serve/serve_on_host.py` | Runs llama-server (Muse Glimmer 30B Q4_K_M + the DFlash drafter, two 32K slots, a fixed model alias) on the serving box's loopback, behind the trace proxy, inside one GPU booking. |
| `serve/trace_proxy.py` | The public endpoint. Passes every request through unchanged and logs each chat completion (reasoning included) to `/data/afterglow/traces/<date>.jsonl`. |
| `serve/glimmer_server.py` | From the Mac: `start`, `stop`, `health [--deep]`, `endpoint`, `status`. `start --adapters runs/serving/adapters.json` serves the night job's kept adapters. |
| `serve/hosts.json` | The serving boxes and the pinned weight hashes. |
| `serve/mock_glimmer.py` | A stand-in server for working without a GPU. |
| `afterglow/agent.py` | The day agent: one user, one world, one day; tool calls (`remember`, `recall`, calendar, tasks); one day-log record per turn. |
| `afterglow/notes.py` | The notes store: local JSON, or an Oneiron vault (`OneironNotes`) with the local file as its mirror. |
| `afterglow/snapshot.py` | The KV snapshot of each world's stable prompt prefix, with an identity (weights, adapter, template, prefix) that refuses a mismatch. |
| `afterglow/oneiron_vault.py` | A paired-agent client for an Oneiron vault (`vault.witness`, `vault.recall`); `push-day` witnesses a recorded day. |
| `afterglow/thoughts.py` | Copies the night's trace records (the Dreamer's and the night job's calls) for the thought log. |
| `afterglow/web.py` + `web/` | The local web app: the day (`/`), the morning report (`/report`), the overnight thought log (`/night`). |
| `simulate_day.py` | Plays a world's day script through the real agent, so every night has a reproducible day. |

## Run it

```bash
# 1. The model server on the serving box (from the Mac; prints the endpoint when healthy)
python3 app/serve/glimmer_server.py start
python3 app/serve/glimmer_server.py health --deep

# 2. A simulated day for a world (worlds/<world>/days/<n>/script.jsonl), into runs/<world>/<n>/
python3 app/simulate_day.py --world w1-ines-varga --day 1

# 3. The web app on http://127.0.0.1:8650
cd app && python3 -m afterglow.web --world w1-ines-varga --day 1

# 4. After the night job: serve its adapters, then re-warm every world's snapshot
python3 app/serve/glimmer_server.py stop
python3 app/serve/glimmer_server.py start --adapters runs/serving/adapters.json
cd app && python3 -m afterglow.snapshot rewarm-all

# 5. The thought log for a night (JST date of the trace file)
cd app && python3 -m afterglow.thoughts fetch --date 2026-10-27
```

Without a GPU: `python3 app/serve/mock_glimmer.py --port 18690 --state-file /tmp/mock-state.json`, then run
the app with `AFTERGLOW_ENDPOINT=http://127.0.0.1:18690 AFTERGLOW_SERVE_STATE=/tmp/mock-state.json`.

`runs/` resolves to the main checkout's `runs/` from every git worktree, so the day app and the night job
share one copy (override with `AFTERGLOW_RUNS`).

## Serving notes: which update chats, the supersede decision and the route decision

`runs/serving/serving-rule.json` (set by the lead, read at session start, never hard-coded):

- `checks`: the checks a night's update must pass to be served (from the night's decision.json and the checks
  added after it ran). The chat uses the latest loaded update that passes every one; with none, the base weights
  read the notes. The day page says which one and why, in one line.
- `supersede_decision`: `{"from_day": 4, "weights": "base", "link_p": 0.9, "reasoned": false}`. From day 4 on,
  a fact saved under a new key is checked in one pass by base Glimmer (`decide/decider.py`, every adapter at 0)
  against its five most likely earlier notes, and the earlier note is linked as superseded only at p >= 0.9;
  otherwise both notes stay and the night's consolidation sees the pair. The reasoning step stays off (reasoning
  made pair decisions worse), and the decision adapter D1 is never used for it. Every decision is logged in the
  day log (`supersede_decisions`: pick, p, ms), and the day page shows "replaces the earlier note ..., decided in
  one pass, p .., .. s" or "kept both, left for the night ...". Evidence of record (29 Oct): on day 3's 34
  never-trained note pairs, base links 8 of 12 true corrections at p >= 0.9 with 0 wrong links, including 1 of
  the 3 the agent left unlinked. `--decide-supersede` / `--no-decide-supersede` force it for one run.
- `route_decision`: `{"from_day": 4, "adapter": "decision-d1b.gguf", "threshold": 0}`. From day 4 on, when the agent
  calls recall, one pass of the decision adapter (found by path on the server, at 1 with every other adapter at 0,
  slot 1) reads the user's question against every active note (`decide/decider.py` route, prompt route-v1) and picks
  the note that answers it, or none. The chosen note is the recall result; with none, recall says the notes do not
  answer it and the agent asks the user. Below the threshold (0 = never) the recall search answers instead, and a
  failed call falls back to the search too. One decision per turn, logged in the day log (`route_decisions`: pick,
  p, ms, options); the day page shows "looked it up in your notes ...: decided in one pass (p .., .. s, N options)"
  or "not in your notes, so it asks you: ...". Evidence of record (29 Oct): on the w4 test, D1b's route scores 0.695
  against 0.419 for always looking up, and asks on 30 of 30 never-told questions. `--decide-route` /
  `--no-decide-route` force it for one run.

## Web search (off unless switched on)

`app/afterglow/web_search.py` adds one tool, `search_web`, for public information the notes cannot hold. It is OFF
unless `runs/serving/serving-rule.json` has `"web_search": {"on": true, "provider": "exa", "key_name": "<skate key
name>", "max_results": 3}`; absent or off, the tool is not offered and every request (and the KV snapshot's prefix) is
byte-equal to a chat without it. On, the query goes to Exa (`POST https://api.exa.ai/search`), and up to 3 results
come back as title, snippet and url. The key is read once per session with `skate get <key_name>`, kept in memory, and
never printed, logged or written; errors carry the HTTP status only. Every search is logged in the day log
(`web_searches`: query and results), and the day page shows "searched the web: <query>" with the result sites, so the
data that leaves the machine is visible. `--web-search` / `--no-web-search` force it for one run.

## Steering dials in the chat

A night proposes dial changes (`runs/<world>/<day>/night/steering-proposals.json`); the user accepts or dismisses
each on the morning report (`steering-decisions.json`). `app/afterglow/dials.py` turns the accepted ones into
per-request control vector scales: day N's chat sends every dial accepted after nights 1..N-1 (the latest accepted
change per axis wins; a dismiss changes nothing), at the proposal's own scale, on the served vector for that axis
(`runs/serving/adapters-cv.json` "control_vectors"), and only inside the dial's tested range. Every chat, answer-channel
and snapshot request carries them (`GlimmerClient.scale_fields`); the server clears a slot's cached prompt when the
scales change, and the KV snapshot's identity names the dials. With no accepted dial no `control_vectors` field is
sent, so requests are byte-equal to a chat without dials. Each turn logs its dials in the day log (`dials`), and the
day page header shows them ("dials: concise +0.04").

## The Oneiron notes backend

`DaySession(..., notes_backend="oneiron")` witnesses every saved note and every turn into the vault, one
conversation per world and day; `python3 -m afterglow.oneiron_vault push-day --world W --day N` does the same
for a recorded day. Credentials come only from the environment, for one command:
`AFTERGLOW_ONEIRON_CREDENTIAL` (the paired agent's credential file) and, for a hosted vault behind Cloudflare
Access, `AFTERGLOW_CF_ACCESS_CLIENT_ID` / `AFTERGLOW_CF_ACCESS_CLIENT_SECRET`.

## Tests

```bash
python3 -m unittest discover -s app/tests     # notes and tools
python3 app/serve/test_trace_proxy.py          # the proxy against a fake llama-server
```
