# L10: Settings > Models

Code commit: `e33c100` on `afterglow/models`, GPG-signed and pushed before this note was written.
Base: `origin/main` at `92e9c62`. No changes to `web.py`, agent prompts, or the explainer hook.

## What was built

1. One Models page keeps `?section=model`, places the existing Agent controls under Agent, and adds Speech and Vision choices with a labelled local-memory estimate at the top.
2. The catalog offers five loadable speech choices and the four requested vision choices. Vision stays None until you choose it. Qwen3.5 4B is the recommended Mac offer when the agent server lacks vision.
3. Explicit downloads use stdlib HTTP, pause/resume, a pinned-revision plan, streamed LFS SHA256 or Git-blob SHA1 checks, and a completion manifest. Downloads live under the existing model folder; external cached copies are not removed.
4. Speech follows the saved choice. Existing Dictation settings and warm-worker behavior stay unchanged without an override. Explicit Keep loaded off unloads owned speech workers after a request. GET does not save settings or start workers.
5. A separate persistent Mac vision worker uses checked local weights and `mlx-vlm==0.7.11`, with thinking off. It returns validated facts or no result. Native Glimmer vision changes only an app-owned local server; remote servers are only probed.

## Files

Backend:
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/afterglow/model_catalog.py`: catalog data and provenance.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/afterglow/model_download.py`: resumable multi-file Hub downloader.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/afterglow/models.py`: saved choices, hardware fit, memory estimates, states and routes.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/afterglow/vlm_runtime.py`: stdlib parent, worker lifecycle, runtime install and facts validation.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/afterglow/vlm_worker.py`: child-only MLX imports, warm image pass and image inference.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/afterglow/vision.py`: crop API and explainer-facing function.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/afterglow/asr_runtime.py` and `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/afterglow/dictation.py`: saved speech selection and Keep loaded behavior.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/afterglow/modelsetup.py`: saved projector on local starts, preserving the existing default command when disabled.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/afterglow/routes.py`: the two feature registrations, `models` and `vision`.

UI:
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/ui/src/ModelsSection.jsx` and `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/ui/src/models.css`: memory bar, rows, actions and fit tags using existing tokens.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/ui/src/Settings.jsx`: Models label, original section id and Agent wrapper; the existing ModelSection body is unchanged.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/web/`: committed Vite output.

Tests:
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/tests/test_models.py`
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/tests/test_model_download.py`
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/tests/test_speech_selection.py`
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/tests/test_vlm_runtime.py`

## Routes and integration contract

Agent continues to use its existing `/api/model` routes. New routes:
- GET `/api/models`
- POST `/api/models/download` and `/api/models/pause`, body `{"id":"..."}`
- POST `/api/models/remove`, body `{"id":"..."}`
- POST `/api/models/use`, body `{"slot":"speech|vision","id":"... or null"}` (send JSON null, not the string)
- POST `/api/models/keep-loaded`, body `{"slot":"speech|vision","on":true}`
- POST `/api/vision/describe`, body `{"png_b64":"...","question":"..."}`

GET includes hardware, speech/vision slot choices and state, fit and recommendation, memory segments, `circle: "text" | "vision"`, and agent-server vision availability. The saved file is `runs/_settings/models.json`, created only by an explicit save. The existing Agent choice remains in its own settings file.

For the explainer lane: `models.vision_mode()` returns `"none"`, `"glimmer"` or `"mac"`. `vision.describe(png_bytes, question)` returns `{"text":...,"items":[{"label":...,"box":[x,y,w,h]}],"ms":...,"observations":...,"unknowns":...,"crop_id":...}`, or None when disabled, native Glimmer is selected, or vision fails. Observations have sequential `v0...` ids, kind, text, label and finite in-bounds positive-area fractional boxes. Any invalid observation rejects the whole reply. No clamping or partial JSON acceptance. The explainer hook itself is intentionally not included.

## Run and check

```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app
python3 -m unittest discover -s tests
python3 -m unittest discover -s tests
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app/ui
~/.bun/bin/bun run build
```

Both final full-suite runs passed: **444 tests, one skipped**, in 11.328 s and 11.176 s. `test_prompt_fingerprint` is green in both. Fake hardware and Hub tests cover Mac/NVIDIA fit, zero local cost for remote Glimmer, resume, checksums, corrupt-file progress, state changes, read-only GET and unchanged speech defaults.

Build passed. Vite reports the existing unresolved-at-build-time Newsreader.ttf and HankenGrotesk.ttf references. Authored-source whitespace checks pass. The generated minified bundle retains upstream template-string whitespace; it was not hand-edited.

Scratch app command used (copy the fixture first if making a new scratch folder):

```sh
cp -R /Users/olety/.claude/jobs/527465c3/tmp/runs-8661 /tmp/models-check-runs
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/app
AFTERGLOW_RUNS=/tmp/models-check-runs python3 -m afterglow.web --world p-mira-holt --day 1 --port 8678 --slot 1 --no-restore --endpoint http://100.88.57.107:18610
```

Check that 8678 is free before starting. The actual verification used `/tmp/afterglow-models-runs`. The scratch app, worker and reverse tunnel were stopped afterward. The real runs folder and remote serving processes were never changed.

## Live checks on this Mac

Hardware: Apple M4 Max, 64 GB unified memory; hardware() reports about 48 GB usable by the GPU.

- Default vision was downloaded through the app's own Download handler with scratch settings: **3,061,131,350 bytes** across 11 repo files, with published checksum verification. The HTTP Download endpoint was also checked against the completed download.
- Weights: `/Users/olety/Models/Afterglow/qwen3.5-4b-mac`, revision `32f3e8ecf65426fc3306969496342d504bfa13f3`.
- The explicit Download action installed `/Users/olety/.cache/afterglow/vlm-venv` with `mlx-vlm==0.7.11` on Python 3.12. The ASR venv was not changed. The worker loads this local directory with Hub offline mode, not a bare repo id.
- Keep loaded was enabled; the same warm worker handled both successful crop requests and remained resident afterward. Turning it off stopped the worker.
- Source image: `/Users/olety/Desktop/temp/meta-hack-20261014/builders/afterglow-ui-20261104/shots/core-chat.png`. Python crops were `(900,460,1230,550)` and `(870,620,1230,745)` in original pixels.

| Crop | Pixels / PNG bytes | Inference | HTTP wall time | Result |
| --- | --- | --- | --- | --- |
| Emoji chat bubble | 330 x 90 / 5,384 | 1,795 ms | 5,609 ms including cold worker startup and image warmup | Valid facts; read thumbs-up and the chat text |
| File and message | 360 x 125 / 7,315 | 1,675 ms | 1,679 ms with worker warm | Valid facts; read `larkspur-brief.md` and its message |

Observed post-request worker RSS: **3,497,200 KiB**, about **3.58 decimal GB**. A separate earlier diagnostic reported MLX peak allocation of **3.72 GB**; that is a different run, not a measured peak for the two final HTTP requests. The fit budget remains the conservative **5.0 GB estimate**.

Initial crop requests were rejected for out-of-bounds boxes. A diagnostic confirmed valid JSON with an invalid width, not truncation. The prompt now spells out width/height arithmetic and sequential ids. Both final requests passed the unchanged validator. This is a two-crop direction check, not a quality benchmark or a promise of localization accuracy.

Speech switched through HTTP from Qwen to the existing Whisper large-v3 download and back to Qwen. No new speech model was downloaded or transcribed in this lane. None changed circle to text; selecting the Mac model changed it to vision. GET left the saved file's bytes and modification time unchanged.

Projector metadata was checked against the Hub's authoritative LFS SHA256 at revision `b44f1072cf75e918a1b82a3a17b45c4389b076e7`: 1,400,328,928 bytes, `f48b452316f9b213758e8659444029b961a24a07f99a1abb2a9f88b06f7c00c6`. It matches the brief. No projector was downloaded or added to a remote server.

Proof: `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/BUILDOUT-NOTES/models-checks.json`.

Screenshots:
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/BUILDOUT-NOTES/models-top.png`
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/BUILDOUT-NOTES/models-speech.png`
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-models/BUILDOUT-NOTES/models-vision.png`

Screenshots used the supplied drive_page.mjs, with its Chrome executable path adapted for Linux on sel and run through a fleet CPU booking plus a private reverse tunnel. The browser console was clean. No browser was run on this Mac, in accordance with the overriding house rule.

## Assumptions, constants and limits

- Vision None is a real opt-out, including when a remote server advertises vision. The Mac 4B choice is recommended, not silently enabled.
- Fit is an estimate: Fits leaves at least 2 GB headroom, Tight reaches the usable-memory limit, and Too big exceeds it or fails the runtime requirement. Local Glimmer uses its file size plus a 1.2 GB planning allowance (18 GB fallback when no path is available), not measured live server RSS. Remote Glimmer counts zero on this computer.
- Speech memory budgets and the other vision memory budgets are research-based estimates. The memory bar subtracts configured model budgets from usable capacity; it is not an OS memory-pressure monitor.
- Native local-server projector launch, owned-server restart and CUDA fit were CPU/fake-tested, not exercised on a running local Glimmer or CUDA box. Remote server checks were read-only.
- Only Qwen3.5 4B was actually run here, so only that vision row has tested=True. Other Mac vision models and the optional speech alternatives were not loaded here.
- The worker caps output at 256 tokens and six observations, and resizes model inputs above one megapixel. Dense text or charts can still fail or be incomplete; invalid results return no vision facts. The decoded-PNG API limit remains 6 MB.
- No full-screen or five-crop regression, combined ASR+vision pressure measurement, native-vision comparison, or sub-1.5-second claim is made. The two valid box sets were format-checked, not scored against hand-labelled target boxes.
- Download Pause drains the current network read before becoming paused. Existing external cached models remain on disk after Remove, with a note explaining that.
- The brief did not name a Oneiron subsystem; the home below is inferred from the feature's responsibility.

Oneiron home: local model management and runtime workers.

DONE

## Integrator pass (14:3x): the five-crop check on the Mac fallback
Five crops in /Users/olety/Desktop/temp/meta-hack-20261014/builders/afterglow-ui-20261104/research/vision-probe/ (the owner's 👍 bubble, the top-bar icons, the speed card, a Japanese and English invoice form with a disabled Send, a painted sky), through the app's own VlmWorker (Qwen3.5 4B, 4-bit, warm, M4 Max):
- 👍 and its text (1.12 s); 🌙 and "Night when you end the day" (1.23 s; the panel icon on the right was not listed); the speed text with both numbers (0.72 s); every line of the form including "送信 Send (disabled)" and "送信先が未確認です" (2.61 s); "watercolor sky with yellow and orange clouds" (0.71 s). Results: results-worker.json beside the crops.
- With text-only Glimmer explaining from the facts (probe.py --mode facts, an earlier wording): all five answers were right and careful, including "The Send button is disabled, so you cannot send now" and "the time cannot be known"; end to end 1.7 to 3.3 s.
Changes from that check:
- The prompt asks for one line of compact JSON, controls and their state first, each emoji and icon as its own observation, and a short description for photos, icons and charts. Pretty-printed JSON ran out of tokens on the form; a one-item example made the model list only one item. MAX_TOKENS 256 -> 384.
- The validator still rejects a broken reply (not ok, another crop, no observation list) but repairs the model's own mistakes instead of throwing every fact away: ids renumbered, the first 6 kept, corner boxes and Qwen's 0-1000 grid converted, an unusable box dropped (the text stays a fact without a pin), a missing label or state empty, a description under another key used as the text. An observation's state (for example disabled) goes with its text to the explainer.
- AFTERGLOW_VLM_DEBUG=1 prints the raw model text to the worker's stderr, for local diagnosis only.
- Settings: "Glimmer's own vision" on a remote server shows "On your agent's server" instead of a fit tag, and no "Not yet tried on this Mac".
