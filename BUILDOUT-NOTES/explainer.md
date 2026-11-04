# L6 explainer: first draft

## What I built
1. Explicit explainer creation stores a separate `explain` subthread and optional crop, without changing the day log.
2. One Glimmer JSON-schema call writes data; local validation and repair always produce a nonempty figure.
3. A dependency-free renderer draws screen pins, ordered flows, cycles with outcomes, parts, and comparisons.
4. `/explain?id=tN` works as a small 420x520 window, with a one-line follow-up composer and streamed temporary chat.
5. Fake-client tests cover OCR, prompts, repairs, routes, failure fallback, shared-slot locking, and chat persistence.

## Files and routes
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app/afterglow/explainer.py`: backend and all four routes.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app/afterglow/llm.py`: minimal optional `response_format` request-body hook, required by `chat`; the default emits exactly the same request body as before.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app/tests/test_explainer.py`: 31 backend tests.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app/ui/src/explain/ExplainView.jsx`: isolated route view and SSE consumer.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app/ui/src/explain/Figure.jsx`: fixed layouts, text-only model fields, no generated markup.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app/ui/src/explain/explain.css`: scoped Hourlight token styles, no new colors or animation.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app/ui/src/explain/Figure.test.jsx`: six renderer tests.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app/ui/src/main.jsx`: route/import hooks only.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app/web/`: committed UI build output.

Routes:
- `POST /api/explain`: question, optional crop/OCR/accessibility/app/from_turn; returns id and URL.
- `GET /api/explain?id=tN`: title, repaired spec, OCR, crop URL, records.
- `GET /api/explain/crop?id=tN`: **JSON data URL in this draft**, not a raw PNG response. See blocker below.
- `POST /api/explain/ask`: SSE content deltas and done; follow-ups are stored in the subthread.

## Contract, assumptions, and limits
The supplied research asset `GENUI-SKILL.md` existed before implementation. This draft uses its v1 `{v,title,body}` contract rather than the provisional free-edge spec. The renderer owns topology. The backend also accepts the original lane-brief template shape and converts it. The research recommendation for an extra model repair call is deliberately not used: the lane requires exactly one call on creation, so all repair and fallback are local.

Research limits adopted: title 48 characters, labels 28, details 100, condition 40, fallback text 160; screen pins 4, flow steps 6, cycle steps 2-4 and outcomes 2-3 (6 items total), parts 5 plus whole, comparison rows 4. These replace the provisional lane counts/lengths where the research contract differs. New input budgets: 40 OCR boxes stored, first 24 offered to the model; 6 MB decoded PNG; 800 spec tokens and 400 follow-up tokens. No existing chat defaults or feature settings changed.

OCR arrays use crop-relative `[x,y,width,height]` fractions. Envelopes `{width,height,boxes}` accept pixel boxes and normalize them to fractions. Returned box IDs are `b0`, `b1`, etc. UI pins use only these host rectangles. Accessibility text without a reliable rectangle is a fact, never a pin location. Crop bytes never reach the text-only model. OCR text is untrusted evidence. This route is explicit user-requested work; it enables no ambient capture, connectors, tools, or background model calls.

## Run and check
```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app
python3 -m unittest discover -s tests
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app/ui
~/.bun/bin/bun test src/explain/Figure.test.jsx
~/.bun/bin/bun run build
```

Scratch app, never the real runs directory:
```sh
cp -R /Users/olety/.claude/jobs/527465c3/tmp/runs-8661 /tmp/afterglow-explainer-runs
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app
AFTERGLOW_RUNS=/tmp/afterglow-explainer-runs python3 -m afterglow.web --world p-mira-holt --day 1 --port 8666 --slot 1 --no-restore --endpoint http://100.88.57.107:18610
```
Check first that port 8666 is free. The server binds loopback. Example creation:
```sh
curl -s http://127.0.0.1:8666/api/explain -H 'Content-Type: application/json' -d '{"question":"Explain this reply", "from_turn":1, "source":"chat"}'
```
Open the returned `/explain?id=...` URL. For the measured screen check, with the scratch app already running:
```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app
python3 /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/BUILDOUT-NOTES/explainer/live_check.py
```
That command makes **one live model call** and creates another scratch thread. It consumes the saved Model screen PNG and DOM-derived OCR stand-in in this notes folder.

## Verification
- Python: 184 tests passed, one existing serving-manifest skip. The full suite emits ResourceWarnings from existing HTTP/socket test cleanup.
- Explainer backend: 31 tests passed. Renderer: six tests passed.
- Vite build succeeds. It warns that the existing font URLs resolve at runtime; the bundled fonts were present and loaded in screenshots.
- Live mel slot 1, endpoint `http://100.88.57.107:18610`: one creation call returned four grounded screen pins in 5.29 seconds. This is one sample, not a latency benchmark or quality guarantee.
- One additional short live follow-up streamed successfully and persisted. No training or model/server restarts were done.
- All browser work ran headlessly on **Linux sel**, under `fleet run --need linux --host sel --mem 2G --threads 2`, through an SSH reverse tunnel to scratch port 8666. No browser ran on this Mac.
- Evidence in `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/BUILDOUT-NOTES/explainer/`: `settings-model.png`, `settings-ocr.json`, `live-explain.json`, `live-followup.json`, `explain-window.png`, and the final layout captures.

## Not done or not verified
- **Host-interface blocker:** `web._feature` supports only JSON and SSE. The required raw PNG route cannot be implemented cleanly without changing forbidden `web.py`. The crop is saved as PNG beside the thread; the crop route returns `{id,data_url}` and the UI reads that JSON. The integrator must add a binary-response route type to provide raw `image/png`. No `web.py` edits or monkeypatches were made.
- Follow-ups stream text only. Optional figure replacement events, undo, and changed-item highlights are not implemented.
- Lane L4/L5 ambient invocation, native floating-window ownership, chat-reply launch controls, and integration across branches are not exercised here. The exported `create` function and `from_turn` route input are ready for their callers.
- Structure and references are checked, but semantic completeness cannot be proved by schema. No performance budget, p95 render latency, cold-load benchmark, or comprehensive visual collision check is claimed.
- Closely spaced OCR anchors can put numbered pins near each other on a scaled-down full-screen crop. Long figures and notes scroll within the window.

Oneiron home: explainer -> genui.

Final Linux layout check passed with a clean console. At 420x520 the image loaded, all four pins rendered, the composer remained visible (top 433, bottom 492), and no horizontal overflow was detected. The four non-screen fixtures and restored follow-up history were also captured; fixtures are layout tests, not model-generated evidence.

The Linux-only CDP script is saved as `drive_page_linux.mjs`. It must never run on this Mac. Capture jobs used fleet bookings `1104-112532-6ac2` (Model), `1104-112905-472f` (initial explanation), and `1104-113047-aa72` (final layouts). The lane scratch server and reverse tunnel are stopped after verification.

Publication: implementation commit `5db71b9` was GPG-signed and pushed to `origin/afterglow/explainer` before this completion marker was written.

DONE
