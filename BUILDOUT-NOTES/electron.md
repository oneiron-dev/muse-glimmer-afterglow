# L8: Electron shell

## What was built
- One sandboxed 1440x960 desktop window, minimum 1000x680, with hidden inset traffic lights and a paper background.
- Existing-server detection, explicit first-run Python configuration, bounded startup polling, and owned-server cleanup on quit.
- Single-instance focus, external HTTP(S) links in your default browser, standard macOS menus, and Cmd+, for settings.
- A reproducible 1024px transparent PNG and ICNS from the supplied dusk icon, with no extra runtime packages.
- Headless config and URL-helper tests, a Bun lockfile, and a desktop README. No backend or web UI source changes.

## Files and routes
All implementation files are under:
`/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-electron/app/desktop/electron/`

Files: main.js, config.js, server.js, package.json, bun.lock, .gitignore, README.md,
scripts/icon.sh, assets/afterglow-icon.svg, build/afterglow.png,
build/afterglow.icns, test/config.test.js, test/server.test.js, test/main.test.js.

No new HTTP routes. The shell checks the existing GET /api/state and navigates to
/settings. It never edits web.py or changes the model's requests, tools, or saved runs.

The runtime config is `/Users/olety/Library/Application Support/Afterglow/config.json`.
It is created only by the owner running the shell, not during these checks.

## Run and check
```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-electron/app/desktop/electron
~/.bun/bin/bun install
~/.bun/bin/bun run icon
node --test
node --check main.js
# Owner only: this opens a native window.
~/.bun/bin/bun run start
```

For scratch use, follow the README: copy the supplied scratch runs to
/tmp/electron-runs, set AFTERGLOW_RUNS to that copy, use port 8668 and slot 1 with
--no-restore, and match AFTERGLOW_URL to that port. Do not test on port 8650.

Completed checks:
- Bun install succeeded, with Electron 45.2.2 pinned as a development dependency.
- node --test: 17 passed. Tests use ephemeral HTTP fixtures and stubbed Electron APIs, not an Afterglow server, model, or Electron runtime.
- node --check main.js: passed.
- Review regressions verified headlessly: failed-spawn cleanup, credentialed-link blocking, and repeated-quit waiting. Intentional load-abort handling was also added.
- bun run icon: passed using macOS sips and iconutil. PNG is 1024x1024 RGBA with a transparent corner; ICNS generated.
- Bundled SVG is byte-identical to the supplied buildout icon.
- cd app && python3 -m unittest discover -s tests: 153 tests, OK, one skipped. Existing ResourceWarnings were emitted.
- cd app/ui && bun run build: succeeded; tracked app/web output unchanged. Two font-resolution warnings remain.

## Assumptions and constants
Default URL: http://127.0.0.1:8650, as specified. Initial check: 1500 ms.
Startup wait: 30 seconds; polling interval: 250 ms; shutdown grace: 3 seconds.
Traffic lights: x=16, y=25. Shell-only CSS reserves top-row space and folded-header
space; the duplicate sidebar mark is hidden only in Electron. No web UI tokens changed.
The command always runs locally from the repository's app folder, even for a
custom remote AFTERGLOW_URL. Python and arguments must be chosen explicitly.
Whole-line // comments are supported in the config. No secrets or server output are logged.

Oneiron home: desktop shell, Afterglow only (Oneiron's apps/macos uses Tauri).

## Not done or not verified
No Electron, browser, or other GUI was opened. No real Afterglow server or model
was started. Native traffic-light spacing, folded sidebar appearance, Dock/menu
behavior, link handling, instance focus, and real Python startup/shutdown still
need the owner's manual check. Device permissions are denied; voice capture is
not enabled by the shell. Electron's prebuilt runtime is downloaded lazily on
first start and was not launched. Unsigned .app packaging was skipped; this is a
checkout-based development shell, not a standalone Python bundle.

Implementation commit 36f077e was pushed to origin/afterglow/electron before this completion marker.
DONE
