# L3: Connectors A

## What was built
1. Per-person connector settings in `runs/<world>/connectors.json`, created only by a save.
2. Read-only ICS uploads/subscriptions with cached feeds, recurrence expansion and source tags.
3. Optional UTF-8 `.eml` drafts with `X-Unsent: 1` and an explicit Open in Mail action.
4. Exa setup with a Keychain credential, per-person cap and a counted one-search Check.
5. A validated files-folder setting and a metadata-only list, plus inline setup forms for all four.

## Files
- Backend: `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app/afterglow/connectors.py`, `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app/afterglow/web_search.py`.
- Tool hooks: `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app/afterglow/tools.py` (calendar list merge and mail-file write only).
- UI: `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app/ui/src/Settings.jsx`, `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app/ui/src/Panel.jsx`, `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app/ui/src/Screens.jsx`, `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app/ui/src/lib.js`.
- Tests: `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app/tests/test_connectors.py`, `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app/tests/test_connectors_search.py`.
- Built assets: `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app/web/index.html` and `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app/web/app-assets/`.
- `web.py`, `agent.py`, route registration and unrelated feature modules were not edited.

## Routes
- GET `/api/connectors`: public state of all four; never returns the search key.
- POST `/api/connectors/calendar`: `{name, ics_text | url}`.
- POST `/api/connectors/calendar/remove`: `{name}`.
- GET `/api/connectors/calendar/events` (optional `?date=YYYY-MM-DD`): feed events for the Calendar views.
- POST `/api/connectors/mail`: `{on, folder}`.
- POST `/api/connectors/mail/open`: `{id}`; validates the open day's draft and never sends mail.
- POST `/api/connectors/search`: `{on, key?, cap?}`.
- POST `/api/connectors/search/check`: one public documentation search; returns a result count.
- POST `/api/connectors/files`: `{folder}` (empty clears it).
- GET `/api/connectors/files`: up to 200 names and byte sizes, no contents.
- Python integration: `connectors.files_folder(world)` returns `Path | None` for the separate files-tool lane.

## Run and check
```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app
python3 -m unittest discover -s tests
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app/ui
/Users/olety/.bun/bin/bun run build
```

For an optional scratch HTTP app, copy the supplied fixture first. Never use the real runs directory:
```sh
cp -R /Users/olety/.claude/jobs/527465c3/tmp/runs-8661 /tmp/afterglow-connectors-runs
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-connectors/app
AFTERGLOW_RUNS=/tmp/afterglow-connectors-runs python3 -m afterglow.web --world p-mira-holt --day 1 --port 8663 --slot 1 --no-restore --endpoint http://100.88.57.107:18610
```
Check that port 8663 is free before starting it. Open Settings > Connectors and save a feed, mail folder, Exa cap/key or files folder. The Exa Check is a real billed search when run outside the tests.

## Verification
Full suite: `python3 -m unittest discover -s tests` passed (188 tests, 1 skipped), including 35 new connector/search tests. The suite emits ResourceWarnings for mocked HTTP errors and existing socket fixtures, not failures. The production build passed; Vite's existing font URL warnings refer to fonts already present under `app/web/fonts/`. The Check reserves its counted usage while synchronized with chat, then releases that lock before the Exa request. A Bun-only fetch mock checked that connector activation precedes chat and that a connector error prevents chat (2 checks). No browser was launched.

## Assumptions and limits
- Floating calendar times use JST, matching the app. TZID and UTC events convert to JST; recurrence expands in source wall time before conversion, including DST. Default feed listing covers today through 60 days ahead; a date query can also list older events.
- Only the simple daily/weekly recurrence subset is supported. Unsupported recurrence exceptions and broken events are skipped and counted, not guessed. All-day DTEND is exclusive; multi-day events appear on each date they touch.
- New limits: 5 MiB per calendar body, hourly refresh including failed attempts, 200 files per metadata list. No existing model or serving constants were changed.
- The fixed Keychain service/account is `afterglow-web-search` / `exa`, as specified. Switches, caps and Check usage are per person, but that named Keychain credential is shared on this Mac. It never enters connector JSON, API responses or logs.
- Mail's required default folder and `<id>.eml` names can collide across people using the same folder. Use separate folders per person to retain all exported drafts. Open in Mail rewrites the selected person's current draft before opening it.
- The allowed-file boundary excludes a startup hook in `agent.py`. The UI calls GET `/api/connectors` before every chat to apply persisted search settings after restart or person/day switches. Direct backend API consumers must also GET `/api/connectors` before chat; standalone DaySession callers must use the world-aware setting themselves. An enabled user key takes precedence over the default/CLI search choice; disabling it restores that original choice.
- Live Exa, real Keychain access, external calendar subscriptions, opening Apple Mail and visual screenshots were not tested. All test credentials, fetches and open commands are fake. The files tool itself is intentionally not built here.

Oneiron home: mail -> FIX 12559/12560.
Oneiron home: files -> documents organ.
Oneiron home: web search -> search connector.
Oneiron home: calendar -> calendar connector (no Oneiron home named yet).
