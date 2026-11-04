# FnCapture

A standalone SwiftPM library for macOS 14 and Swift 5.9 or later. Apple frameworks
only. `FnCapture` does not import Afterglow, send HTTP requests, store captures, or
register a login item. The `afterglow-fn` executable is a separate, opt-in consumer.

## Use the library

Add this directory as a local Swift package dependency and link the `FnCapture`
product from your menu-bar app. Your app owns its microphone usage description,
permissions, capture lifetime and destination. Call UI APIs on the main actor.

```swift
import FnCapture

@MainActor
final class VoiceCapture {
    let key = FnKeyMonitor()
    let audio = AudioRecorder()
    let overlay = LassoOverlay()

    func start() throws {
        // Ask for permissions through your own explicit user action first.
        key.onHoldBegan = { [weak self] in
            DispatchQueue.main.async {
                guard let self else { return }
                do { try self.audio.start(); self.overlay.show() }
                catch { /* Present your own error. */ }
            }
        }
        key.onHoldEnded = { [weak self] in
            DispatchQueue.main.async {
                self?.overlay.hide()
                if let wav = self?.audio.stop() { self?.consume(wav) }
            }
        }
        key.onHoldCancelled = { [weak self] in
            DispatchQueue.main.async { self?.audio.cancel(); self?.overlay.hide() }
        }
        audio.onAutoStop = { [weak self] in
            self?.overlay.hide()
            if let wav = self?.audio.stop() { self?.consume(wav) }
        }
        try key.start()
    }
    func consume(_ wav: Data) { /* Your voice flow. */ }
    func stop() { key.stop(); audio.cancel(); overlay.hide() }
}
```

Keep key callbacks short: the tap returns promptly when heavy work is queued onto
the main run loop. `stop()` removes the event tap; call it before releasing a
monitor. Call `hide()` / `close()` before releasing visible overlays or panels.

## Public building blocks

- `FnKeyMonitor`: fn hold or Right Option fallback, with cancellation for shortcuts.
- `AudioRecorder`: in-memory mono 16 kHz signed 16-bit WAV, meter callback, 60 s cap.
- `LassoOverlay`: one transparent, nonactivating window per display, freeform selection.
- `ScreenCapture`: ScreenCaptureKit screenshot of the selected display region, PNG.
- `TextRecognizer`: accurate English/Japanese Vision text and normalised boxes.
- `AXInspector`: the centre element's role, title, shortened value and front app name.
- `Paster`: complete materialised pasteboard snapshots, Command-V, delayed restoration.
- `FloatingPanel`: resizable, nonactivating WebKit panel with outside-click/Esc dismissal.
- `Permissions`: check/request each permission and get a `PermissionState` snapshot.
- `Geometry`, `WAV`, `OCRText`, `AXContext`: data and coordinate utilities.

All selection and panel rectangles use **global AppKit points**, bottom-left
origin, including negative display origins. `Geometry.quartzRect` converts them
for Quartz/AX. ScreenCapture selects one display and uses its backing scale for
PNG pixels. A selection is confined to the display where the drag begins. Vision
OCR boxes are **normalised 0..1, top-left origin**, independent of Retina scale.

## Afterglow consumer

`afterglow-fn` resolves `--url`, then `AFTERGLOW_URL`, then
`http://127.0.0.1:8650`. It posts JSON to `/api/ambient`:

```json
{
  "audio_b64": "...",
  "mime": "audio/wav",
  "app_name": "Your foreground app",
  "crop_png_b64": "...",
  "ocr": [{"text": "...", "x": 0, "y": 0, "w": 1, "h": 1}],
  "ax": {"role": "AXTextField", "title": "...", "value": "...", "app_name": "..."},
  "screen_rect": {"x": 100, "y": 200, "w": 300, "h": 150}
}
```

The crop, OCR, AX and screen rect fields are omitted without a selection. The rect
is in AppKit points, not image pixels. Responses are `{ "mode": "paste", "text":
"..." }` or `{ "mode": "explain", "url": "/relative/path" }`. Explanation URLs
must be server-relative on the configured server. Paste replies are not inserted
if the foreground app changed while the request was in flight. Nothing is logged
or written to disk. The server's own retention rules still apply.

See [PERMISSIONS.md](PERMISSIONS.md) for build, signing, setup and manual checks.

Oneiron home: Oneiron's menu-bar app (`apps/macos`) adopts FnCapture unchanged;
dictation goes to Oneironer voice, and explanation goes to genui.
