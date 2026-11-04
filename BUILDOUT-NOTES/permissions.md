# L9: Afterglow fn permission setup

## What was built

- One native setup window with three required steps, one active permission action, checked grants, and Later.
- A non-activating System Settings guide with a real bundle-URL drag source, the Afterglow icon, and window-relative guidance.
- Honest non-prompting status checks, denied-microphone navigation, stale-switch repair instructions, and local fn/audio practice.
- First-circle Screen Recording setup that cancels the old recording and asks you to circle again, without resuming old content.
- Same-bundle reopening with capture teardown, arguments and connection preserved, failure recovery, and development-signing support.

No new library was added. The research and the licensed PermissionFlow/PermissionPilot patterns informed this AppKit implementation. No Permiso source was copied.

## Files and routes

All implementation paths below are under:
`/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-perms/app/desktop/FnCapture`

- `Sources/AfterglowFn/PermissionSetupModel.swift`: injected-status navigation, request serialization, optional-screen deferral, practice evidence.
- `Sources/AfterglowFn/PermissionSetupController.swift`: visible-only polling, Settings navigation, setup lifecycle and repair.
- `Sources/AfterglowFn/PermissionSetupWindow.swift`: dusk-and-cream setup window and one-action rows.
- `Sources/AfterglowFn/PermissionGuidePanel.swift`: Settings PID/window-server geometry, safe side docking, drag source and action-triggered entrance.
- `Sources/AfterglowFn/HelperRelaunch.swift`: same-bundle launch plan, argument/environment preservation and stop/launch/quit ordering.
- `Sources/AfterglowFn/PermissionArtwork.swift` and `PermissionPreview.swift`: shared mark and offscreen rendering.
- `Sources/AfterglowFn/AfterglowFnMain.swift`: menu, first launch, local practice, circle cancellation, revocation and relaunch integration.
- `Sources/FnCapture/Permissions.swift`: centralized Settings links; existing native checks remain the authorization source.
- `Sources/FnCapture/FnKeyMonitor.swift`: event-type-only practice evidence, without key text or keycodes.
- `Tests/AfterglowFnTests/PermissionSetupTests.swift`: seven injected-state tests.
- `scripts/bundle.sh`, `scripts/Info.plist`, `scripts/AfterglowFn.icns`: recognizable bundle icon and explicit ad-hoc fallback warning.
- `scripts/render-permissions.sh` and `PERMISSIONS.md`: reproduction command and owner instructions.

Routes: none. No Python, HTTP routes, web UI, real runs, model calls, server launches or remote restarts were changed or used.

## Run and check

```sh
cd "/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-perms/app/desktop/FnCapture"
swift build --jobs 2
swift test --jobs 2
./scripts/render-permissions.sh "/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-perms/BUILDOUT-NOTES"
./scripts/bundle.sh
codesign --verify --deep --strict build/AfterglowFn.app
```

The owner can launch the signed bundle at:
`/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-perms/app/desktop/FnCapture/build/AfterglowFn.app`

Use a stable bundle location. Permissions opens setup from the menu. Plain dictation does not request screen access. Practice stays local while setup is visible. Choose Hold Right Option in the menu if the keyboard does not deliver fn.

## Checks completed

- `swift build --jobs 2`: pass.
- `swift test --jobs 2`: 25 tests pass, including all seven PermissionSetupTests.
- Injected cases: existing grant, stale false and manual Settings visit, denied mic, screen deferral, one request at a time, failed relaunch, preserved bundle/arguments/URL and successful termination order.
- `./scripts/bundle.sh`: pass, signed with an available Apple Development identity, not ad hoc.
- `codesign --verify --deep --strict`: pass. The designated requirement identifies `dev.oneiron.afterglow.fn` and an Apple Development certificate.
- `git diff --check`: pass before the implementation commit.
- Offscreen PNGs rendered and visually reviewed using `NSView.cacheDisplay`. No window was ordered and no GUI app or browser was opened.
- Previews: `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-perms/BUILDOUT-NOTES/permissions-setup.png`, `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-perms/BUILDOUT-NOTES/permissions-guide.png`, `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-perms/BUILDOUT-NOTES/permissions-practice.png`. Their status values are injected examples, not claims about this Mac's grants. `permissions-icon.png` is the rendered bundle mark.

## Assumptions and constants

The existing hold-fn/Right Option, hold-to-record, release-to-paste workflow remains the default. Connection and hold-key choices are user settings. Only the setup step is saved as setup progress; captured content and permission-granted flags are never persisted. The effective server setting is retained so a system-managed reopen does not silently select localhost.

Setup checks use 0.5 seconds only while setup or the guide is visible. Guide geometry uses 150 ms only while Settings is in front. The user-triggered guide entrance lasts about 0.3 seconds; Reduce Motion uses a fade without movement. Practice voice detection uses level > 0.01, with captured samples checked before confirming. Settings links use legacy destinations on macOS 14/15 and extension destinations on 26 and later, with a user-chosen alternate link and manual instructions.

Oneiron home: desktop companion permission setup (proposed mapping; L9 does not name a canonical Oneiron area).

## Not done or not verified

- No live permission prompts, approvals, denials, revocations, microphone recording, fn delivery, pasting, screen capture, drag-to-add or relaunch were exercised on this Mac. Those require the owner's interaction.
- The macOS 14/15/26 on-device matrix remains unverified: deep-link landing, Settings move/resize/close, wrong pane, multiple displays/scales, missing rows, stale switches, stable-identity rebuilds, changed-signature recovery, first circle, system Quit & Reopen and helper-managed reopen with both URL forms.
- The guide knows only the Settings window's geometry. It cannot confirm the exact pane or app row. It hides when Settings is not in front, no visible window can be resolved, or neither side has safe docking space.
- A native preflight grant does not guarantee actual capability. Practice checks key/audio delivery, but is not a substitute for the owner's live tests. A screen failure offers repair guidance without asserting that a stale signature caused it.
- The ad-hoc warning is present but its fallback was not exercised because an Apple Development identity was available.
- Implementation commit `3422410` was pushed to `origin/afterglow/permissions` before this completion file was written. This file is the final lane artifact and is committed and pushed separately.

DONE
