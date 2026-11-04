# FnCapture permissions

The helper runs only when you launch it. It does not launch at login. It records only
while you hold your chosen key, up to 60 seconds. It captures no screen content unless
you drag a loop. Audio and selected context are sent to the Afterglow URL you choose.
The library itself makes no network requests. Nothing is saved by the helper.

## Build and launch yourself

From this directory:

```sh
swift test --jobs 2
swift build --jobs 2
./scripts/bundle.sh
```

Keep `build/AfterglowFn.app` in a stable location, or copy it once to Applications.
Launch the signed bundle yourself, not the unbundled SwiftPM binary. It has the
microphone usage description and a stable bundle identifier. The builder does not
launch it. Ad hoc signing is for local use, not distribution or notarisation.
macOS may require permission approval again after rebuilding or moving the bundle.

The server defaults to `http://127.0.0.1:8650`. To use another server, launch its
executable with `--url http://127.0.0.1:PORT`, or set `AFTERGLOW_URL` in that launch
environment. A Finder launch does not inherit your terminal's environment. The
explicit `--url` takes priority. The helper does not start or restart the server.
The `/api/ambient` endpoint must be present (Afterglow lane L4).

## Allow these in System Settings > Privacy & Security

Use each permission button in the menu-bar menu, then approve **Afterglow fn**:

- **Microphone**: captures your voice only during a hold.
- **Accessibility**: sends Command-V to your foreground app and reads the role,
  title and a shortened value of the item at the centre of your selected area.
- **Screen & System Audio Recording** (called **Screen Recording** on macOS 14):
  takes the crop you circled. It does not record system audio. The overlay and
  Afterglow mark beside the cursor are excluded from that image.
- **Input Monitoring**: sees fn and Right Option key transitions outside the helper.
  The key tap is listen-only and does not suppress typing or system shortcuts.

macOS can ask you to quit and reopen after granting access. Use Quit in the helper's
menu, then launch the same bundle. Reopening the menu refreshes the permission state.
Microphone is required to record. Screen Recording is needed only for a crop.
Accessibility and Input Monitoring are required for the global hotkey and paste flow.

In **System Settings > Keyboard**, set **Press the Globe key to: Do Nothing** so
fn reaches the helper without opening the emoji picker or changing input source.
If your keyboard or macOS does not deliver fn, choose **Hold Right Option** in the
helper's menu. It is a fallback, not a second simultaneous hotkey. Combining either
hold key with another key cancels recording and sends nothing.

## Owner checks (not run by the builder)

1. Start Afterglow with L4's `/api/ambient` endpoint, then launch the signed helper.
2. Approve permissions and set Globe to Do Nothing. Check the menu says it is ready.
3. Focus a text editor, hold fn, speak briefly, then release. Your cleaned words
   should paste into that same editor. The old clipboard should return after 400 ms.
4. Hold fn and drag a loop around a small area. Release the mouse before fn. Ask
   your agent to explain it. A temporary panel should appear beside the selection.
5. Close the panel with Esc or a click outside. Check it stays within a display and
   can be resized, including on a Retina display and a display left of the main one.
6. Hold fn and press another key. Confirm no request or paste occurs. Try the
   Right Option fallback. Test the 60-second limit and microphone removal.
7. Deny a permission and stop the server to check the short error pill. No captured
   text or server response body should appear in logs.

The helper temporarily replaces all materialised clipboard item representations.
If another app copies something during the 400 ms paste window, it keeps that new
clipboard rather than overwriting it. Some apps use lazy/private clipboard formats
that cannot be fully materialised; test these apps before relying on restoration.
Sending to a remote HTTP URL is not encrypted. Use a trusted local server or HTTPS.
