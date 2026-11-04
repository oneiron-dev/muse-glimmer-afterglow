# FnCapture permissions

The helper runs only when you launch it. It does not launch at login. It records only
while you hold your chosen key, up to 60 seconds. It captures no screen content unless
you drag a loop. Audio and selected context are sent to the Afterglow URL you choose.
The library itself makes no network requests. Practice recordings are discarded without a request.
The helper saves only connection and hold-key settings, plus the current setup step during a reopen.
It does not save captured content or permission-granted flags.

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

## Set up dictation

When dictation is not ready, the helper opens **Set up Afterglow fn**. You can also
choose **Permissions…** in the menu. **Later** closes setup without asking anything.
Only one step has an action at a time. Existing grants show a check, not another Allow button.

1. **Paste into apps** asks for Accessibility. It pastes your words and reads the
   role, title and shortened value of the item inside your selected circle.
2. **Hear your fn key** asks for Input Monitoring. The listen-only tap sees hold-key
   transitions and combination cancellation, without retaining key text.
3. **Use the microphone** asks for access before practice. If access is off, the
   button opens Microphone Settings instead of requesting again.

Then hold fn and speak to practice. Key press/release and voice arrival are checked
locally. Release discards the audio. Nothing goes to your agent while setup is open.
Press and release a letter too to check the event classes used by combination cancellation.
The hold-key mode menu retains the Right Option fallback.

Settings steps have a small non-activating guide beside the Settings window. Where
supported, drag its real app icon into the list if the app is missing. It does not
find or click your app's row. The guide hides when Settings is not in front, closes,
or has no safe visible docking space. Permission status checks run every 0.5 seconds
only while setup or the guide is visible. Geometry tracking runs every 150 ms only
while Settings is in front. No screenshots or Accessibility row scraping are used.

**Screen Recording is optional.** It is requested only after you first circle without
access. That recording and circle are cancelled. Choose **Set up screen access** or
**Not now**. Dictation still works without screen access. Settings may call this
**Screen & System Audio Recording**. Afterglow captures only your circle, not system
audio. After setup, circle again. Old audio and the old crop are never resumed.

A false Accessibility or screen check means **Not set up**, not a definitive denial.
Input Monitoring and Screen Recording may remain stale within the current process.
If you enabled access but it is not confirmed, use **Reopen to finish** to check again.
The helper stops monitoring, recording, capture, requests and overlays first. It
launches the same bundle with the same arguments and `AFTERGLOW_URL`, and quits the
old copy only after the new copy launches. If opening fails, the old setup UI stays
available with manual reopening instructions. Only the setup step is saved as progress.
The effective server is a user setting, also retained for a system-managed reopen.

For a switch that is on but not working, choose **Fix a switch that is on but not
working**. Remove Afterglow fn with the minus button, then add it again, or turn it
off and on. The app never runs `tccutil`. Use the exact signed bundle at a stable
location. The builder prefers an Apple Development identity and warns when falling
back to ad hoc signing, where every rebuild asks for permissions again.

Settings links use the legacy prefix on macOS 14/15 and the extension prefix on 26.
This is a compatibility policy, not an on-device guarantee. **Try other Settings
link** tries the other form only when you choose it. **Show instructions** gives a
manual path because a successful URL open does not prove the correct pane appeared.

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
