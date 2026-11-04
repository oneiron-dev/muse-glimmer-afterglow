# L4: dictation

## What is built
1. On-demand audio conversion and Whisper.cpp transcription, with optional running whisper-server support.
2. One Glimmer cleanup call with your saved rules and raw-text fallback on failure or truncation.
3. Ambient explain/paste routing and a separate dictation thread for every ambient take.
4. In-app microphone, level meter, caret insertion, and focused Right Option push-to-talk.
5. Settings for engine, model file, server address, editable rules, and a Try it recording.

## Files and routes
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/afterglow/dictation.py`
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/tests/test_dictation.py`
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/ui/src/dictation/` (hook, microphone, settings, scoped CSS)
- One-line imports/render hooks in `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/ui/src/Settings.jsx`; microphone/caret hooks in `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/ui/src/Composer.jsx`.
- Rebuilt static frontend in `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/web/`.
- `POST /api/dictation?context=composer|ambient` accepts raw WebM, MP4, WAV, or x-wav. Composer creates no thread.
- `POST /api/ambient` accepts the helper JSON contract; returns paste text plus thread id, or explainer id and URL.
- `GET /api/dictation/settings` and `POST /api/dictation/settings` use `engine`, `model`, `server_url`, `rules`.
- No change to web.py, model tool lists, day logs, or settings files when dictation is unused. No ambient listener starts here.

## Run and check
```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app
python3 -m unittest discover -s tests
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/ui
~/.bun/bin/bun run build
```

For a fresh scratch run, copy the supplied fixture to a new temporary folder, check port 8664 is free, and run:
```sh
cp -R /Users/olety/.claude/jobs/527465c3/tmp/runs-8661 /tmp/dictation-check-runs
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app
AFTERGLOW_RUNS=/tmp/dictation-check-runs python3 -m afterglow.web --world p-mira-holt --day 1 --port 8664 --slot 1 --no-restore --endpoint http://100.88.57.107:18610
```
Visit Settings > Dictation to save your settings before using Try it. A server address means an already-running whisper-server base URL (the app posts multipart WAV to `/inference`); it never launches one. Rules are plain lines in the scratch runs folder's `_settings/dictation-rules.md`; engine/model/server live in `_settings/dictation.json`. GET does not create either file.

## Verification
- Final full Python suite: 189 tests run, OK (1 existing skip). This includes 36 dictation tests, with real ffmpeg sine conversion and over-duration rejection.
- Final production build passed. Existing font warnings remain; existing unrelated tests emit ResourceWarnings.
- `git diff --check` passed.

## Live check
The one allowed speech check used macOS `say` to `/tmp/d.aiff`, converted it to `/tmp/d.wav` with ffmpeg, then posted WAV to the scratch app on port 8664. Scratch runs: `/tmp/afterglow-dictation-runs`. The scratch server was stopped after the check.

```json
{"raw": "Remind me to call Mira about the Larkspur pilot tomorrow.", "clean": "Remind me to call Mira about the Larkspur pilot tomorrow.", "engine": "whisper.cpp", "ms": {"asr": 9405, "clean": 4570}}
```

## Assumptions and added limits
- The research pick in `/Users/olety/Desktop/temp/meta-hack-20261014/builders/afterglow-ui-20261104/research/ASR-PICK.md` is Qwen3-ASR-1.7B MLX 8-bit. Its 2.46 GB weights alone exceed the lane's under-2-GB install allowance. `Qwen3ASR` is an explicit unavailable stub; no dependencies or models were installed, and Whisper.cpp remains the default.
- Added limits: 60-second server maximum, 24 MiB upload maximum, 16,000-character rules maximum. ffmpeg decodes at most 61 seconds so over-limit clips are rejected, not silently shortened. Browser recording stops at 59 seconds to leave room for codec padding and event-loop delay.
- Near-silence below RMS 16 in signed 16-bit audio skips ASR and cleanup. Known ASR silence markers also become empty text. This is a silence check, not a general speech detector.
- Cleanup uses temperature 0.2, minimal reasoning, and roughly twice the word count with a 32-token floor. A single unspaced string gets a character budget for languages such as Japanese. Truncated or empty cleanup output returns raw text without retrying.
- A crop or AX element plus the brief's question keywords selects explain. Explicit leading "explain" works without an attachment. Clear dictation stays paste; other attached requests get at most one tiny yes/no Glimmer call. Missing explainer module falls back to paste. No screen capture occurs in this lane.
- Existing constants in other modules were not changed.

Oneiron home: dictation -> Oneironer voice.

## Not done or not verified
- Qwen3-ASR execution is intentionally not implemented under this lane's size limit.
- No browser or GUI was opened. Live microphone permissions, hardware Right Option behavior, and visual layout are not verified.
- whisper-server transport and L6 explainer integration are mocked in tests, not live-checked. L5 helper capture/paste remains its lane's responsibility.
- Frontend build succeeds with the existing unresolved Newsreader/HankenGrotesk font warnings. No font files were changed.

## L4b: fast dictation

The L4 section above is a historical record. This section supersedes its engine, install-limit, server-start, and cleanup-budget descriptions.

### What is built
1. Qwen3-ASR 1.7B 8-bit is the default when no engine choice is saved. Its isolated MLX worker loads once, warms, and serves WAV paths over JSON lines.
2. Worker calls are serialized. Dead, stuck, or invalid workers fail safely, use Whisper for that take, and can restart on the next take.
3. Whisper.cpp now uses a lazy, owned, loopback whisper-server with large-v3, not whisper-cli. A saved external server remains supported.
4. Settings shows the engine used for the last take and whether the owned worker is warm. The microphone and insertion flow are unchanged.
5. Already-punctuated, sentence-case text without fillers or custom rules skips cleanup and returns `clean_skipped: true`. Other takes use minimal reasoning with a bounded token budget.

### Files and routes
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/afterglow/asr_worker.py`: isolated Qwen worker and Whisper parent-watch wrapper.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/afterglow/asr_runtime.py`: lazy process managers, protocol deadlines, restart, and shutdown.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/afterglow/dictation.py`: engine choice, warm Whisper transport, fallback, and cleanup skip rule.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/tests/test_asr_runtime.py` and `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/tests/test_dictation.py`: fake-worker protocol, lifecycle, skip, and fallback tests.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/ui/src/dictation/DictationSettings.jsx` and rebuilt `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/web`: engine/warm state and skipped-cleanup copy.
- Existing routes only: `POST /api/dictation`, `POST /api/ambient`, `GET /api/dictation/settings`, `POST /api/dictation/settings`.
- No web.py changes. No recorder, process, model request, new tool, or settings write starts when dictation is unused. Settings GET does not start a worker.

### Install and run
```sh
uv venv --python 3.11 /Users/olety/.cache/afterglow/asr-venv
uv pip install --python /Users/olety/.cache/afterglow/asr-venv/bin/python 'mlx-audio[stt]==0.6.4'
/Users/olety/.cache/afterglow/asr-venv/bin/hf download mlx-community/Qwen3-ASR-1.7B-8bit
```

Installed outside the repo. Model snapshot: `a8379a2e2f9e313c9292cdf1af4055ab56d50d55`; the weights file is 2,463,307,541 bytes. No keys were needed or saved.

Use the L4 scratch-server command above with a fresh copy of the supplied fixture; the L4b check used `/tmp/afterglow-dictation-l4b-runs` on port 8664. Never point it at real runs. In Settings > Dictation, select Qwen3-ASR, clear custom rules for the skip check, and save. A saved Whisper choice is retained rather than silently migrated. First recording loads the model. Later recordings reuse it. Local Whisper behaves the same way when selected. No browser or GUI was opened for these checks.

```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app
python3 -m unittest discover -s tests
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-dictation/app/ui
~/.bun/bin/bun run build
```

For a repeatable 3-second clip:
```sh
say -v Samantha -r 180 -o /tmp/afterglow-short.aiff 'Please call Mira tomorrow morning.'
/opt/homebrew/bin/ffmpeg -hide_banner -loglevel error -y -i /tmp/afterglow-short.aiff -af apad -t 3 -ac 1 -ar 16000 -c:a pcm_s16le /tmp/afterglow-short.wav
curl -s -H 'Content-Type: application/json' -d '{"engine":"qwen3","rules":""}' http://127.0.0.1:8664/api/dictation/settings
curl -s -H 'Content-Type: audio/wav' --data-binary @/tmp/afterglow-short.wav 'http://127.0.0.1:8664/api/dictation?context=composer'
```
Repeat the last request after the cold request. For 10 seconds, use Samantha at rate 160, `-t 10`, and: "Please remind me to call Mira about the Larkspur pilot tomorrow morning. We need to review the latest notes before the meeting." Padding makes the WAV exactly 3 or 10 seconds. Select `whisper.cpp` through the same settings endpoint to check the warm fallback server. `ps -axo pid,ppid,rss,command` reports process RSS in KiB.

### Measured on this M4 Max
All values below are milliseconds from POST start to JSON response, including conversion, silence check, worker protocol, ASR, and cleanup. Clips were exactly 3 s and 10 s mono 16 kHz WAVs made with macOS say. These clips were already clean after ASR, so every warm result had `clean_skipped: true` and 0 ms cleanup.

| Engine | First 3 s request (new worker) | Warm 3 s, two takes | Warm 10 s, three takes | Warm 10 s median |
| --- | ---: | --- | --- | ---: |
| Qwen3-ASR 1.7B 8-bit | 3236 | 177, 222 | 337, 397, 318 | 337 |
| Whisper large-v3 | 4027 | 694, 646 | 851, 892, 928 | 892 |

The new-worker requests include load/warmup with an already-downloaded model and cached files. Download time is not included. The warm 10-second Qwen HTTP path is below the 1.5-second target on all three takes. This is a small synthetic check, not a latency distribution.

Peak process RSS, sampled with `ps` every 100 ms during cold start plus the six-take matrix:
- Qwen worker: 2,574,576 KiB (2.46 GiB).
- Whisper server: 3,583,312 KiB (3.42 GiB).
Both engines stayed warm in Settings after their takes. Scratch artifacts on this Mac: `/tmp/afterglow-l4b-metrics.json`, `/tmp/afterglow-l4b-clean-metrics.json`, and `/tmp/afterglow-l4b-clips/`.

### Cleanup check
- `Please call Mira tomorrow morning.`: unchanged, 0 ms, no Glimmer call, `clean_skipped: true`.
- `um please call Mira tomorrow morning`: `Please call Mira tomorrow morning`, 546 ms, minimal reasoning, max_tokens 128, finish reason `stop`. The model removed the filler and fixed casing but omitted the final period; cleanup is still model-generated, not guaranteed grammar correction.
- Initial budgets of 32 and 64 tokens truncated because minimal reasoning counts toward completion tokens. Both safely returned the raw transcript. A diagnostic call finished in 80 completion tokens. The final budget is `min(512, 96 + max(32, 2 * word_count, character_count_for_unspaced_text))`: 128 for the short check. There is no retry or hidden extra model call.

### Assumptions, constants, and limits
- The old 2 GB install cap is lifted. Qwen uses `mlx-audio[stt]==0.6.4` in the requested Python 3.11 venv. The app itself stays stdlib-only and never imports MLX.
- New default engine: `qwen3`. Existing saved engine/model/server/rules are retained. The model path field and server URL apply to Whisper, not Qwen.
- Qwen startup deadline: 180 s; per-take deadline: 120 s. Whisper startup: 60 s; inference HTTP timeout: 120 s. Stop grace: 5 s before termination. Worker warmup uses a 0.5-second silence clip with at most 16 tokens.
- ASR keeps mlx-audio's default maximum of 8192 generated tokens rather than imposing a 512-token cap on long Japanese takes. Cleanup, separately, is capped at 512 completion tokens.
- Worker startup/read failures, malformed responses, and timeouts do not send silent success. Qwen failure uses warm Whisper and reports actual `engine: "whisper.cpp"` plus `fallback_from: "qwen3"`. If both fail, the request fails.
- Custom cleanup rules always run through Glimmer. The local skip heuristic checks sentence starts, terminal punctuation, known English/Japanese fillers and corrections, repeated adjacent words, and lowercase standalone English "i". It cannot recognize every filler or misspelled proper name. Conservative false negatives may make an extra cleanup call.
- Existing 60-second/24 MiB upload limits and silence threshold are unchanged. Both engines can remain resident after being used; both stop with the app. External Whisper warm state is reported as unknown, not invented.
- Normal exit uses atexit cleanup. Parent-watch workers also handle abrupt parent death without changing the app's signal handlers.

Oneiron home: dictation -> Oneironer voice.

### Verification and remaining limits
- Final full Python suite: 233 tests, OK (1 existing skip), including 14 offline warm-worker tests. They cover partial JSON-line deadlines, reuse, serialized calls, crash/restart, bad replies, fallback, health readiness, model switches, parent death, and normal exit during a stuck daemon request.
- Frontend production build passed. The generated bundle is committed with the UI.
- Live scratch checks confirm both warm engines, `language=auto` with whisper-server, settings warm state, and worker cleanup after SIGTERM and normal SIGINT exit.
- Browser microphone permissions, MediaRecorder finalization, hardware Right Option, insertion timing, layout, Japanese/mixed-language accuracy, and long-clip accuracy were not live-tested. No browser was opened. The timing claim is for the HTTP dictation path with synthetic English WAVs, not a measured physical release-to-caret event.
- The required ps RSS figure is not total unified-memory use; MLX GPU allocations are not fully represented in process RSS. It is a sampled peak, not a guaranteed instantaneous maximum.
- First-use latency includes loading and is above 1.5 s. The target is met only by the measured warm path with cleanup skipped. Custom rules, real corrections, and a busy Glimmer slot can add latency.
- Existing font-resolution build warnings and unrelated test ResourceWarnings remain. No font files changed. Source whitespace checks pass. Git also flags trailing whitespace inside the emitted dependency JavaScript; that exact Vite output is retained rather than hand-editing generated code.

DONE
