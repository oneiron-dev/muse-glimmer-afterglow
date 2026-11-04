# Lane L5: fn helper

## What was built
1. A macOS 14+, Swift 5.9+ SwiftPM package with the reusable `FnCapture` library and separate `afterglow-fn` executable.
2. Hold-to-talk fn monitoring, explicit Right Option fallback, 16 kHz mono 16-bit WAV recording with a meter and 60-second cap.
3. Per-screen lasso selection, ScreenCaptureKit PNG crops, English/Japanese Vision OCR and a shortened accessibility snapshot.
4. Clipboard-preserving paste and an on-screen, resizable WebKit explanation panel.
5. An opt-in menu-bar app with permission actions, a recording/error pill, URL configuration and an ad hoc signed bundle script.

## Files and routes
All implementation is under `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-fn/app/desktop/FnCapture`:
- `Package.swift`, `Sources/FnCapture/*.swift`: public Apple-framework-only library, no Afterglow dependencies or HTTP client.
- `Sources/AfterglowFn/*.swift`: menu-bar lifecycle, pill, request/response JSON and networking.
- `Tests/FnCaptureTests/*.swift`, `Tests/AfterglowFnTests/*.swift`: pure tests, including fake pasteboard save/restore.
- `scripts/bundle.sh`, `scripts/Info.plist`, `PERMISSIONS.md`, `README.md`, `.gitignore`.
No existing app files, route registry, model requests, tools, frontend sources or recorded runs changed.
No server routes added. The executable consumes POST `/api/ambient` from lane L4 and loads its returned explanation path.

## Run and check
```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-fn/app/desktop/FnCapture
swift test --jobs 2
swift build --jobs 2
./scripts/bundle.sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-fn/app
python3 -m unittest discover -s tests
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-fn/app/ui
~/.bun/bin/bun run build
```
Do not launch the helper until the owner is ready to grant permissions. See `PERMISSIONS.md` for the owner checklist.

## Assumptions and constants
- The helper is off until manually launched, never added to login items, and stores no captures or settings.
- Base URL precedence: `--url`, `AFTERGLOW_URL`, `http://127.0.0.1:8650`. No live server was started or restarted.
- `screen_rect` is `{x,y,w,h}` in global AppKit points with a bottom-left origin. OCR boxes use a normalised top-left origin.
- Each loop is confined to its starting display. The mouse is released before the hold key; a click without a region attaches nothing.
- Audio cap 60 seconds, output 16,000 Hz / mono / signed 16-bit; pasteboard restore 400 ms; errors 3 seconds.
- AX strings are limited to 200 characters. Lasso minimum region is 4 points per axis, with softened system pink for the requested rose stroke.
- Panel minimum 360x420 points. If app focus changes during a request, a paste reply is refused instead of sent to a different app.
- No existing constants changed. No keys or secrets are used or logged.

## Verification
- `swift test --jobs 2`: 18 tests passed (14 library tests and 4 executable request tests), no failures.
- `swift build --jobs 2`: passed.
- `./scripts/bundle.sh`: release build passed and produced `build/Afterglow fn.app`; ad hoc signing passed.
- `codesign --verify --deep --strict`: passed for that bundle. `plutil -lint scripts/Info.plist` and `bash -n scripts/bundle.sh`: passed.
- Existing Python suite: 153 tests run, one skipped, no failures. Existing resource warnings about mocked sockets/temporary cleanup appeared.
- `~/.bun/bin/bun run build`: passed, with existing font resolution warnings; no tracked web asset changes.
- `git diff --check`: passed. Swift compilation used Apple Swift 6.4 in Swift 5 language mode with deployment target macOS 14; an actual Swift 5.9 toolchain was not available to test.
- The generated bundle/build outputs are ignored, not committed. The source script reproduces them. Existing untracked `app/ui/node_modules` was left untouched.

## Not done or not verified
No GUI or browser was opened, and the helper was not run. Real fn/Globe delivery, microphone capture, TCC permission prompts, screenshot/AX results, actual paste into apps, and panel interaction require the owner's manual test. No live L4 endpoint request or model call was made. Mixed-display/Retina coordinate logic is unit-tested, but actual multi-display screenshots are not verified. The bundle is ad hoc signed for local use, not notarised. macOS can require permissions again after a rebuild or move. Materialised clipboard representations are preserved, but private/lazy formats need app-specific testing.

Oneiron home: Oneiron's menu-bar app (apps/macos) adopts FnCapture unchanged; dictation -> Oneironer voice; explainer -> genui.

Implementation commit `3f92dcf` was pushed to `origin/afterglow/fn` before this completion marker was written.

DONE
