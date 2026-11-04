# Explainer

## What it does
1. You hold fn, ask, and may circle part of the screen. A small window (about 420x520) opens on
   `/explain?id=…&world=…&day=…` with a
   title, a plain answer of one or two sentences, and then a figure.
2. The figure: with a circled area, numbered marks on the picture at the places that matter for the question (3 to 5,
   fewer when the picture holds fewer) and a numbered note for each; without one, a short list (or steps, a loop, or a
   comparison when the answer is one).
3. What the agent knows comes from the chosen vision mode (Settings > Models): "mac" adds what the Mac vision model saw
   (each with its place in the picture), "glimmer" sends the picture to Glimmer itself, "none" gives the words only.
   With "none", when the words cannot answer, the answer says plainly that Afterglow reads words, not pictures, and
   that Settings > Models can add vision.
4. Follow-ups in the window are a temporary chat; one that asks to see something new redraws the figure (a `spec`
   event) and keeps the first answer on top. A follow-up never trades the picture for a list.

## Files and routes
- `app/afterglow/explainer.py`: inputs (OCR, accessibility, vision), prompt, schema, repair, storage, the four routes.
- `app/afterglow/llm.py`: `response_format` on `request_body` (None, the default, leaves every request as before).
- `app/ui/src/explain/ExplainView.jsx`, `Figure.jsx`, `explain.css`, `Figure.test.jsx`; the `/explain` route in
  `app/ui/src/main.jsx`. `app/web` is not in the PR (merge rule: "Afterglow UI" rebuilds it after each batch).
- `app/tests/test_explainer.py` (52 tests; `Figure.test.jsx` 9); `app/tests/smoke_explainer_glimmer.py`, a live
  PASS/FAIL check for Glimmer's own vision (not a unit test).
- Routes. Thread ids restart at t1 for every day and profile, so every URL and follow-up names its day (world, day;
  the open day's when absent): `POST /api/explain` {question, crop_png_b64?, ocr?, ax?, app_name?, from_turn?,
  source?} -> {id, world, day, url}; `GET /api/explain?id=&world=&day=` -> {id, world, day, question, title, spec,
  crop_url, anchors, sight, source, records}; `GET /api/explain/crop?id=&world=&day=` -> the PNG (routes.Raw);
  `POST /api/explain/ask` {id, world, day, text} -> SSE content, answer, spec, error, done.
- `dictation.ambient` already calls `explainer.create` (main); no change is needed there.

## How it decides
- The model writes `{answer, answered, title, body}` (follow-ups add `redraw`) under a JSON schema; the host adds ids
  and the version. The schema allows only pins or text when there is a picture with places to pin, and never pins
  without a picture.
- Pins name a fact id with a place: OCR lines `b0..`, vision items `v0..`. A vision item whose words match an OCR line
  takes that line's exact box (the Mac model's boxes are loose). The model never writes coordinates.
- Facts carry plain places ("at middle right", "across the top") so "the button on the right" can be answered; the
  host strips those words from what you read. Answers end on a whole sentence (280 chars at most).
- The first answer is a chat call with reasoning "low" (max 1,600 tokens; Glimmer spends 400 to 600 on reasoning, so
  the old 800 cut some specs off). Follow-ups on a llama.cpp server use Glimmer's answer channel with the schema as
  grammar (no reasoning; about 4 to 5 s for the whole answer against 6 to 10 s to the first word). Without reasoning,
  the first answer once told the person it could not see the screen, so the first answer keeps it.
- "selected" states from the Mac model are dropped (it marks plain buttons selected).

## Run and check
```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-explainer/app
python3 -m unittest tests.test_explainer
cd ui && ~/.bun/bin/bun test src/explain && ~/.bun/bin/bun run build
```
Scratch app (never the real runs/): `cp -R /Users/olety/.claude/jobs/527465c3/tmp/runs-8661 /tmp/explainer-opus-runs`,
then from `app/`: `AFTERGLOW_RUNS=/tmp/explainer-opus-runs python3 -m afterglow.web --world p-mira-holt --day 1 --port 8666 --slot 1 --no-restore --endpoint http://100.88.57.107:18610`.
Vision mode: `curl -X POST 127.0.0.1:8666/api/models/use -d '{"slot":"vision","id":"qwen3.5-4b-mac"}'` (or `"id":null`).
Checks, from `BUILDOUT-NOTES/explainer/check/`: `python3 run_crops.py <label>` (the five probe crops with the OCR the
fn helper would send, `crops-ocr.json`, made by `swift ocr_png.swift <pngs>`), `python3 run_screen.py <label>` (the
Settings > Models page, the same crop spoken through `/api/ambient`, a chat-reply explainer, a general question, two
follow-ups), `python3 run_followups.py <label> <id> "<text>"`. Headless shots only (drive_page.mjs).

## Results (Wed 4 Nov, mel slot 1, scratch app on 8666; seconds; one run each)
Mode "mac" = qwen3.5-4b-mac kept loaded; mode "none" = no vision. Total = the POST until the window URL returns.

| mode | case | total | vision | model | figure | answer (start) |
|---|---|---|---|---|---|---|
| mac | emoji | 8.0 | 3.8 | 4.3 | 1 pin | The emoji shown is a thumbs up 👍. |
| mac | icon | 7.0 | 1.6 | 5.4 | 4 pins | The button on the right is a calendar icon. It likely opens a date picker… |
| mac | chart | 4.6 | 0.8 | 3.8 | 4 pins | Replies are +9.9% faster…, 118.0 versus 107.4 tokens a second. |
| mac | invoice (JP) | 11.5 | 2.8 | 8.7 | 3 pins | No. The Send button is disabled and the screen shows the recipient is unconfirmed… |
| mac | photo | 7.4 | 0.8 | 6.5 | 1 pin | A watercolor painting of a sky… No time information is visible… |
| none | emoji | 3.9 | | 3.9 | picture | Afterglow reads the words in what you circled, not the pictures… |
| none | icon | 7.2 | | 7.2 | picture | …a square control to its right. From the label it likely toggles… |
| none | chart | 11.9 | | 11.9 | 4 pins | …+9.9% change…, 118.0 vs 107.4 tokens per second… |
| none | invoice (JP) | 3.6 | | 3.6 | 3 pins | No. The screen shows the recipient is not confirmed… |
| none | photo | 7.2 | | 7.2 | picture | Afterglow reads the words in what you circled, not the pictures… |
| mac | Settings > Models | 9.5 | 3.8 | 5.7 | 5 pins | This is the Models settings screen… You cannot train models here… |
| mac | same, spoken (ambient) | 13.7 | 4.1 | 5.7 | 5 pins | The Models section is for choosing which models run on this computer… |
| mac | chat reply (no crop) | 10.8 | | 10.8 | list | The reply confirms it understood your message and records the workshop… |
| mac | general question | 6.2 | | 6.2 | list | A LoRA adapter changes what a model says by adding small low-rank… |
| mac | follow-up "What would I need to train here?" | 4.0 | | | kept | You cannot train models here… |
| mac | follow-up "Show me the steps to add vision instead" | 5.0 | | | kept | I cannot show steps to add vision from the visible screen… |
| none | Settings > Models | 6.1 | | 6.1 | 5 pins | …shows which agent models fit your computer… not a training interface. |
| none | same, spoken (ambient) | 7.8 | | 4.7 | 5 pins | The Models section shows which models fit this computer… |

Against the probe's expected answers (research/vision-probe/crops.json): mode "mac" gets the emoji, chart, invoice
(disabled Send, unconfirmed recipient) and photo (no time) right; the icon answer names the right control and says
"likely", but the Mac model calls it a calendar where the probe expects a panel icon. Mode "none" answers the chart and
the invoice from the words and gives the plain vision line for the emoji and the photo. Model time varies from 3.6 to
11.9 s with reasoning length and other work on mel. Results JSON and every window shot are in `explainer/check/`
(`mac-*.png`, `none-*.png`, `mac-followups-chat.png`; `*-results.json`, `*-screen.json`).

## Review fixes (Sol xhigh on #202, reviews/explainer-202.md)
- A window, its picture and its follow-ups name their day and profile. Any recorded day can be read; a follow-up runs
  only on the open day (409 with a plain line otherwise), and the stream checks the day and the thread's kind again
  under the lock, so a switch between the request and the stream writes nothing.
- create() remembers the open day before Mac vision and refuses (409, "You switched day or profile while Afterglow
  was looking. Ask again.") when it changed, so nothing is saved under the wrong profile.
- The follow-up stream joins escaped surrogate pairs (`\ud83d\udc4d` is 👍) and holds back half a pair until the
  rest arrives, so an emoji never breaks the UTF-8 event stream.

## Found on the way (sent to "Afterglow UI")
- The fn helper's OCR read Japanese as Latin junk with `["en-US", "ja-JP"]`; `["ja-JP", "en-US"]` reads it right and
  English the same. Fixed in 378c056 on ui/fn-helper. The checks here use the Japanese-first order.
- The Mac vision worker returned nothing on busy screens (its JSON was cut at the token limit). Fixed on main in #194.

## Not done or not verified
Handed off Wed 4 Nov 17:1x: the Meta lead owns the live "glimmer" check below; "Afterglow UI" takes any later
explainer P1.
- Mode "glimmer" (Glimmer reads the picture) is covered by unit tests only: mel's server has no vision projector
  (`/props` modalities.vision false), so no live check. After mel's Thu 05:00 restart (projector loaded), the Meta
  lead's 05:00 checks run `python3 app/tests/smoke_explainer_glimmer.py --base http://127.0.0.1:8651`: one explain
  request with the 👍 crop (`app/tests/fixtures/explain-thumbs.png`); it prints PASS or FAIL with the reason. A FAIL
  is logged and the glimmer-vision shot is skipped.
- One sample per case; no latency percentiles. The wrong icon guess is the Mac model's.
- The fn helper's own window placement and the chat's "explain this reply" control are other lanes' work; the routes
  and `create` they call are here.
- The `/api/explain` errors for malformed requests (bad base64, unknown id) are developer text; a person only meets
  them if the helper sends a bad request.

Oneiron home: explainer -> genui.
