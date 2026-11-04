# Afterglow desktop

A small Electron shell around the existing Afterglow web UI. It does not change the
Python app, its model calls, its tools, or its saved files.

## Run on macOS

You need Bun, Node.js 22.12 or newer, and Python with the app's usual environment.
The icon script uses the built-in macOS `sips` and `iconutil` tools.

```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-electron/app/desktop/electron
~/.bun/bin/bun install
~/.bun/bin/bun run icon
~/.bun/bin/bun run start
```

The owner should run the last command. It opens a window. Electron 45 downloads
its prebuilt runtime on first start if it is not cached, so that start needs a
network connection. There are no added runtime packages besides Electron itself.
The supplied icon SVG is copied unchanged into this package so icon generation
also works after the branch is merged or the checkout moves. To render directly
from the buildout's original source:

```sh
AFTERGLOW_ICON_SVG=/Users/olety/Desktop/temp/meta-hack-20261014/builders/afterglow-ui-20261104/icon/afterglow-icon.svg ~/.bun/bin/bun run icon
```

The generated PNG is 1024 by 1024 with transparent rounded corners. The PNG and
ICNS are committed in this package's `build` folder. The PNG sets the Dock icon
for development runs. A packaged app would use the ICNS as its app icon.

## Connect or start your server

The shell loads `AFTERGLOW_URL`, or `http://127.0.0.1:8650` by default. It first
checks `GET /api/state` for up to 1.5 seconds. A successful HTTP response reuses
that server. The shell will never stop a server it did not start.

If the check fails, it reads:

`/Users/olety/Library/Application Support/Afterglow/config.json`

On another account, this is the same path under your home folder. On first use,
it creates a private commented example and shows a plain setup page. No Python
command runs until you set both `python` and `args`, quit, and reopen the shell.
Only whole-line `//` comments are supported. Standard JSON works too. For example:

```json
{
  "python": "python3",
  "args": ["-m", "afterglow.web", "--world", "YOUR_WORLD", "--day", "YOUR_DAY", "--port", "8650"]
}
```

Replace `YOUR_WORLD` and `YOUR_DAY`. Use an absolute Python executable path if
needed. The command is passed directly to Python, not through a shell. Its
working directory is the repository's `app` folder, resolved from this package.
It inherits your environment, including `AFTERGLOW_RUNS` and `AFTERGLOW_ENDPOINT`.
Do not put secrets in the config or command arguments. Keep keys in macOS Keychain
as the app requires. Server output is discarded, not logged by this shell.

The shell waits up to 30 seconds for `GET /api/state`, polling every 250 ms with
a 1.5-second deadline per check. A server exit, a bad command, or a timeout shows
plain instructions. Match the Python server's host and port to `AFTERGLOW_URL`.
A custom remote URL does not deploy or start a remote server; the configured
Python command always runs locally.

If Python was started by the shell, quitting sends SIGTERM and waits for exit.
After three seconds it sends SIGKILL if needed. Closing the only window also
quits. A pre-existing server stays running.

### Safe scratch setup

Do not test against the real runs. Before opening the shell, copy the supplied
scratch runs and configure Python to use port 8668 (not the default 8650):

```sh
cp -R /Users/olety/.claude/jobs/527465c3/tmp/runs-8661 /tmp/electron-runs
```

Use these `args` in the config, with your Python executable:

```json
{
  "python": "python3",
  "args": ["-m", "afterglow.web", "--world", "p-mira-holt", "--day", "1", "--port", "8668", "--slot", "1", "--no-restore", "--endpoint", "http://100.88.57.107:18610"]
}
```

Then, **owner only**, launch with the matching URL and scratch root:

```sh
AFTERGLOW_RUNS=/tmp/electron-runs AFTERGLOW_URL=http://127.0.0.1:8668 ~/.bun/bin/bun run start
```

## Window and controls

- One 1440 by 960 window, minimum 1000 by 680, shown only when ready.
- A hidden inset title bar, paper background `#fbfaf8`, and sidebar traffic lights.
- Shell-only CSS reserves room for the native controls, including a folded sidebar.
  It hides the duplicate sidebar mark, not your profile. The website is unchanged.
- A second launch raises the existing window.
- The standard App, Edit, View, and Window menus include reload and zoom.
- `Cmd+,` opens `/settings` in the same window after the server is ready.
- Same-origin links stay inside. Other HTTP(S) origins open in your default browser.
  Other protocols and new windows are blocked. The shell has no preload script,
  disables Node integration, uses context isolation and sandboxing, and denies
  device permission requests. Voice capture is not enabled by this shell.

## Headless checks

These checks do not open Electron or contact the model. Tests create small HTTP
fixtures on ephemeral loopback ports, never port 8650 or an Afterglow server.

```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-electron/app/desktop/electron
node --test
node --check main.js
~/.bun/bin/bun run icon
```

Tests cover config validation, comments, first-run creation, permissions, URL
validation, response status, refused connections, redirects, deadlines, and abort.
A stubbed Electron test also checks server ownership, failed spawn cleanup,
repeated quit, navigation restrictions, single-instance gating, and Settings.
It does not load Electron or spawn Python and does not verify native behavior.

## Not verified or packaged

Electron and browser windows were not launched during development. The owner
still needs to check traffic-light spacing, the folded sidebar, Dock appearance,
external links, menus, single-instance focus, and real Python startup and shutdown.
Unsigned `.app` packaging with `@electron/packager` was skipped. This package runs
from a checkout and needs that checkout's Python app; it is not a standalone app
bundle with Python included.

Oneiron home: desktop shell, Afterglow only (Oneiron's apps/macos uses Tauri).
