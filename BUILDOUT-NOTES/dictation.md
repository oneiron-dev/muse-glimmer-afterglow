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

DONE
