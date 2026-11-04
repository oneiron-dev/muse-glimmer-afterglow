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
- GET /api/loom: loom_state(), contradictions {line, conflicts, extra}, sealed,
  resolution, side_branches, and left_out.
- POST /api/loom/resolve: {keep_side: [note ids], add_branches: [names]}.
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
- MorningOffer belongs in the morning report for the prior day. Pass leftOut=loom.left_out,
  conflicts, extra, resolution, onResolved. Resolve acts on the open day; open the relevant
  recorded day before posting a prior-night answer. Never send prior-day ids to the current day.

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
- This lane saves the night's answer only. The night runner does not yet consume loom-resolution.json,
  enforce the owner's unanswered-main-line fallback, or add selected branches on a later night.
  Those training changes are outside the allowed files for this draft. Do not claim them as working.
- loom.alternatives is unchanged: nested regenerations expose immediate alternatives, not a flattened
  set of every cousin version. No existing branch model or note-store behavior is changed.

Implementation ready. The completion marker will be added only after the first successful branch push.
