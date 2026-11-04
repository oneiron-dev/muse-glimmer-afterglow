# Loom first draft (L2)

## What is built

1. Streamed user-message edits fork the open day at a user turn.
2. Reply regeneration forks with the same user text; switching restores the chosen path.
3. Loom state includes alternatives, fact conflicts and side facts, without model calls.
4. Explicit answers are saved atomically in the open day's loom-resolution.json.
5. Five standalone UI components are exported for the integrator; App.jsx and Thread.jsx are unchanged.

Backend: `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/afterglow/loom_api.py`. Tests: `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/tests/test_loom_api.py`.
UI: `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/ui/src/loom/{BranchSwitcher,EditMessage,RegenerateButton,NightQuestion,MorningOffer}.jsx`,
`/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/ui/src/loom/index.js`, and `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/ui/src/loom/stream.js`.

Routes:
- POST /api/branch: {at_turn, text}, the same SSE events as /api/chat, under Stream(lock=True).
- POST /api/branch/regenerate: {turn}, using the user text already recorded for that turn.
- POST /api/branch/switch: {branch}, returning loom_state().
- GET /api/loom (or /api/loom?day=N for a prior recorded day): loom_state(), contradictions {line, conflicts, extra}, sealed,
  resolution, side_branches, and left_out.
- POST /api/loom/resolve: {day?: N, keep_side: [note ids], add_branches: [names]}.
  Returns the saved object, including world, day, line, turn and resolved_at.

Oneiron home: loom -> board forks/branches.

## Assumptions and boundaries

- A receipt or consolidated/training/evaluation/decision output under day/night* seals forks.
  An empty night folder, steering settings or night-links alone does not seal it.
  NIGHT_OUTPUTS is a new module-local marker list; no existing constants changed.
- Only side paths with different user words are offered. Identical full user paths are grouped
  under their first branch. A shorter identical prefix is not new user evidence.
  Shared prefix records occur once via loom.line_records. Comparisons use saved facts, not reply prose.
- An unchanged remember may reuse a note without listing it in notes_written. Facts from a
  same-word earlier reply remain baseline for a regenerated path. Explicit current-path facts win.
- Forks are rejected with HTTP 409 on a sealed day. Switching is still allowed by the brief;
  it does not rewrite past training. Morning answers can still be recorded on a sealed day.
- A saved resolution replaces the whole answer. Forms preserve the half they do not edit.
  Answers only apply to their saved line and turn; editing or continuing the day makes them stale.
- Features are opt-in HTTP actions. No new ambient work, model requests, tools, day-log writes,
  night scheduling, or startup changes occur when they are not used.

## Integration

Import from `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/ui/src/loom/index.js`. Fetch GET /api/loom alongside the app state.
Key the question and offer by world/day/line/turn so their local selection state resets on navigation.

- BranchSwitcher goes under a user message with alternatives. Pass alternatives (the full mapping
  or this turn's versions), turn, currentBranch=loom.line, onReload, disabled while streaming.
- EditMessage goes under a user message. Pass turn, text, sealed, disabled, onLive, onNote, onReload.
- RegenerateButton goes under an assistant message. Pass turn, userText, sealed, disabled,
  onLive, onNote, onReload. User and assistant records share a turn number.
- onLive receives the existing live-turn shape and null at completion. onNote receives note events.
  onReload should refresh both /api/state and /api/loom, replacing the old suffix, not appending it.
- NightQuestion belongs before the night starts. Pass contradictions.conflicts, contradictions.extra,
  resolution, onResolved; the callback should reload the loom state.
- MorningOffer belongs in the morning report for the prior day. Pass day=report.day,
  leftOut=loom.left_out, conflicts, extra, resolution, onResolved. The L2b loader reads that
  prior day's loom and every save includes day explicitly, without opening it.

## Run and check

```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app
python3 -m unittest discover -s tests
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/ui
/Users/olety/.bun/bin/bun run build
/Users/olety/.bun/bin/bun build src/loom/index.js --target browser --external react --outfile /tmp/afterglow-loom-components.js
```

Scratch app (copy the fixture first; never point this at real runs):

```sh
cp -R /Users/olety/.claude/jobs/527465c3/tmp/runs-8661 /tmp/loom-check-runs
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app
AFTERGLOW_RUNS=/tmp/loom-check-runs python3 -m afterglow.web --world p-mira-holt --day 1 --port 8662 --slot 1 --no-restore --endpoint http://100.88.57.107:18610
```

## Checks observed

Live HTTP check on the isolated /tmp/afterglow-loom-runs-20261104 fixture against mel:
- Two chats saved Room Cedar and workshop time 09:00 on main.
- Editing turn 2 to 10:00 streamed turn 3 on b2; the alternatives and 09:00/10:00 fact conflict appeared.
- Regenerating turn 3 streamed turn 4 on b3 with the same user words.
- Switching back to main restored its original line and alternatives.
- A read-only HTTP recheck confirmed the reused 10:00 fact still conflicts with 09:00 on the regenerated line.
- Resolve wrote the answer. Adding a fake receipt only in the scratch fixture made the fork endpoint return 409.
- The owned scratch server was stopped afterward. No mel or bel server was restarted.

Validation completed:
- `cd app && python3 -m unittest discover -s tests`: 178 tests, OK, one existing skip.
  This includes 25 new loom tests. Existing ResourceWarning messages are unchanged from baseline.
- `cd app/ui && /Users/olety/.bun/bin/bun run build`: passed. The existing font URL warnings remain;
  no new build warnings were introduced. Tracked app/web output is byte-for-byte unchanged.
- The standalone `src/loom/index.js` browser bundle passed (9 modules), so the unmounted components
  were compiled independently rather than relying only on Vite's unchanged entry point.
- React server-render smoke checks passed for all five components, including inherited alternative
  position, hidden sealed controls, question facts, and morning facts.
- The shared stream helper passed split UTF-8 / chunked SSE and HTTP 409 checks with mocked fetch.
- No browser was used. Scratch validation scripts are at /tmp/afterglow-loom-live-check.py and
  /tmp/afterglow-loom-ui-smoke.jsx on this machine, not application dependencies.

## Not done or not verified

- App/Thread and morning-screen wiring is deliberately left to the integrator.
- No browser or GUI was run. Component layout, focus behavior and full screen interaction remain unverified.
- The production app build is clean; app/web is unchanged because the new components are not yet imported.
- The night reads loom-resolution.json since #178 (night/loom_line.resolve_branch): a "line" naming a known
  branch is the line it trains, else the log's own current line. Moving to another line removes a saved answer,
  so the night follows the line you are on. Not built yet: adding the chosen side branches (add_branches) and
  side facts (keep_side) to a later night.
- loom.alternatives is unchanged: nested regenerations expose immediate alternatives, not a flattened
  set of every cousin version. No existing branch model or note-store behavior is changed.

Published implementation commit 53a4a77 to origin/afterglow/loom before writing the completion marker.

## L2b: the morning half

### What is built

1. GET /api/loom?day=N reads an earlier recorded day of your open person without opening or writing it.
2. POST /api/loom/resolve accepts day=N and atomically saves that day's answer, not today's answer.
3. Both kept and rolled-back morning cards load the report day's loom and offer left-out branches or unanswered conflicts.
4. The morning transition also includes the offer. Nothing appears when there is nothing to offer.
5. An edited day shows a small notice under its divider; Back to the first switches to main and reloads the thread on an unsealed day. It is disabled on sealed days.

Files changed or added in this follow-up (all under this worktree):
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/afterglow/loom_api.py`
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/tests/test_loom_api.py`
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/ui/src/Thread.jsx`
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/ui/src/loom/{MorningOffer,MorningLoom,EditedDay}.jsx`
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/ui/src/loom/loom.test.jsx`
- Rebuilt `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/web/` assets.

Routes remain the same five routes listed above; no route registration or web.py changes.
The L2 integration instruction to open the prior day before resolving is superseded: pass the report's day explicitly.
App/Thread morning wiring is now done, including the morning transition. The L2 unwired-component caveats no longer apply.

### Run and check

```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app
python3 -m unittest discover -s tests
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app/ui
/Users/olety/.bun/bin/bun test src/loom/loom.test.jsx
/Users/olety/.bun/bin/bun run build
```

Scratch HTTP check (server is owned by this lane, stopped after validation):
```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-loom/app
AFTERGLOW_RUNS=/tmp/afterglow-loom-l2b-runs-20261104 python3 -m afterglow.web --world p-mira-holt --day 1 --port 8662 --slot 1 --no-restore --endpoint http://100.88.57.107:18610
curl 'http://127.0.0.1:8662/api/loom?day=1'
curl -H 'Content-Type: application/json' -d '{"day":2}' http://127.0.0.1:8662/api/day/open
curl -H 'Content-Type: application/json' -d '{"day":1,"add_branches":["b2"]}' http://127.0.0.1:8662/api/loom/resolve
```

The scratch root was copied from `/Users/olety/.claude/jobs/527465c3/tmp/runs-8661`.
The replay script `/tmp/afterglow-loom-l2b-live.py` makes three short chat turns against mel:
main workshop time 09:00, edit b2 10:00, edit b3 11:00, then back to main.
It is a one-shot script for a fresh copy, not a safe replay against an already checked copy.
The next day was prepared only in scratch, then opened with POST /api/day/open.
The scratch night report is a **test-only rollback fixture**; its receipt says no training was run.
This checks morning rendering, not a new night result or training behavior.

### Checks observed

- Python: 302 tests pass, one existing skip. Seven new prior-day tests cover same-shape reads,
  no file/mtime changes on GET, exact prior-day answer writes, bad days, cross-day choices,
  staleness using the prior line/global turn, empty recorded days, and open-person isolation.
- UI: eight Bun/React server-render tests pass. Quiet/left-out/conflict-only states, both verdicts,
  the original missing-leftOut behavior, explicit line/side choices, saved side picks,
  edited-day notice placement, and server-sealed disabled state are covered.
- Vite production build passes. The two existing font URL warnings remain.
- Live mel: two real forks and saved conflicting facts appear in the scratch day.
  Prior GET after opening day 2 leaves all scratch bytes and mtimes unchanged.
- HTTP bad/unrecorded days and cross-day branch choices return 400. The active day stays 2.
- Browser checks pass on sel through fleet, using its existing headless Chrome and Node runtimes.
  No browser ran on this Mac, no dependencies were installed, and no model/training calls were made by the browser.
  Both b2 and b3 appeared in the morning offer. Selecting b2 posted
  {"day":1,"keep_side":[],"add_branches":["b2"]}; fresh readback offered only b3.
  The parent verified the saved prior-day answer and absence of any day-2 resolution file.
  On edited day 1, the notice was directly under the divider. Clicking Back to the first
  restored main and removed the notice. This first check predated the sealed-button repair below.
  The new action is now intentionally disabled on a sealed day; the final regression rechecks both states.
- Screenshots viewed by the parent:
  `/tmp/afterglow-loom-browser/morning-offer.png` (two branches before save) and
  `/tmp/afterglow-loom-browser/morning-verdict-and-offer.png` (verdict plus remaining b3 after save).
  Those two images predate the review fixes; the final form screenshot is listed below.
  Evidence: `/tmp/afterglow-loom-browser/evidence.json`. The browser scripts and transcripts are beside it.
  Test logs: `/tmp/afterglow-loom-l2b-validation/`.
- The owned scratch app on 8662 and temporary Tailscale-only screenshot proxy on 18662 were stopped.
  No existing app or mel/bel service was restarted.

### Assumptions, boundaries, and handoff

- Recorded prior days follow the existing history convention: a session.json file marks the day.
  Missing daylog/notes files are read as empty and are never created by GET.
- Omitted day still means the open day. An explicit current day works too. Future days are rejected.
  POST day must be a positive integer; GET uses a positive numeric query string.
- World always comes from the open session; a supplied world is ignored. No cross-person API was added.
- Read and resolve retain the existing non-blocking app lock: a live chat can return 409.
  The morning loader stays quiet on failed reads. It does not retry in the background.
- No existing constants changed. No new colors, model requests, tools, startup writes, timers,
  ambient behavior, night runner changes, or night-code changes were introduced.
- Morning saves only record an answer for a later night. No night consumer or unanswered-main-line
  training fallback was implemented or verified here; those remain outside this lane's allowed scope.
- The implementation is ready for the 16:00 handoff. No implementation work remains in this lane.
  Kept-card and quiet-state behavior were tested through React rendering, not a real trained kept night.
  Browser interaction was verified on the rollback fixture. The morning transition is build-tested and wired,
  but its timed night-to-morning animation was not exercised in the browser.

### Final review repairs

- The review confirmed prior-day GET/resolve have no cross-day writes or session initialization.
- The morning form now shows explicit line/side choices for each conflict, as well as branch checkboxes.
  Every Save records the full visible fact answer: side choices populate keep_side, and an absent
  side id means keep the line value. Existing resolutions are complete answers under the original contract.
  A subsequent save preserves the selected facts and already-added branches; it does not silently reset them.
- The new Back to the first action is disabled and handler-guarded when either the UI or the server
  says the day is sealed. The server's loom.sealed value covers fresh browser profiles too.
  The pre-existing branch/switch API allowance on sealed days is unchanged; this lane's new UI no longer invokes it.
- Final scratch recheck uses port 18663 because port 8662 became occupied by the separate
  afterglow-pr-core worktree while the review ran. That process was not stopped or sent test writes.
  The temporary screenshot proxy still binds only the Mac's Tailscale IP at 18662.
  No production constants changed. Test day 2 has model-free fork records for the unsealed button check.
- Final regression browser run: six checks passed on sel. The combined form showed four radios
  for two conflicts and two branch checkboxes. Choosing the b2 side fact posted
  {"day":1,"keep_side":["N0004"],"add_branches":["b2"]}; adding b3 next preserved N0004.
  Fresh reload was quiet after the complete answer. On a sealed edited day, Back was disabled,
  made zero POSTs, and left the loom unchanged. On an unsealed model-free edited day 2,
  Back restored main. Parent HTTP readback confirmed day 1 has both branches and N0004,
  while day 2 has no resolution file. Log: `/tmp/afterglow-loom-l2b-validation/final-browser.txt`.
  Test-driver retries corrected a truncated API response and reset the already-saved scratch answer;
  these were driver/fixture repairs, not application fixes. No extra model or training calls were made.

Oneiron home: loom -> board forks/branches.

### Publication

Signed implementation commit `1ee4e85` was pushed to `origin/afterglow/loom` before the final completion marker.
Only this lane branch was pushed. The pre-existing untracked node_modules symlink was left untouched.

Reviewed fixes were signed and pushed as `8609c23` to `origin/afterglow/loom` before writing DONE.
Final current-build screenshot, viewed by the parent:
`/tmp/afterglow-loom-browser/final-morning-form.jpg` (1280x1450): rollback verdict,
two line/side fact choices, two branch checkboxes, Save/Not now, and the next-day thread.
It was captured read-only on sel, with no browser on the Mac. A small-range artifact transfer
avoided the driver's truncated stdout; the original image also remains on sel in the synced browser folder.
The exact verified day-1 answer was restored after screenshot-only fixture setup.
All owned scratch services on 18663 and 18662 are stopped. The other lane's 8662 app was left untouched.
No implementation work remains for the 16:00 handoff. The night-reader integration remains explicitly out of scope.

DONE
