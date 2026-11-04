# Clipboard world A (lane L7)

## What was built
- An opt-in clipboard diary, with per-person settings and per-day JSONL clips.
- A one-second watcher for macOS pbpaste, Linux wl-paste, or Linux xclip.
- Secret classification, masking, Keychain storage, labels, and deletion.
- Settings > Connectors controls and a Copied today section in Files.
- Only explicit non-secret attachments can reach chat. No agent tool was added.

## Files and routes
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-clipboard/app/afterglow/clipboard.py`: watcher, classifier, Keychain adapter, routes, feature-local day/profile lifecycle binding.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-clipboard/app/tests/test_clipboard.py`: offline tests with a fake reader and fake Keychain.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-clipboard/app/ui/src/connectors/Clipboard.jsx`, `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-clipboard/app/ui/src/clips/ClipsList.jsx`: feature UI.
- Hooks: `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-clipboard/app/ui/src/connectors/index.js` and `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-clipboard/app/ui/src/Screens.jsx` only.
- `/Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-clipboard/app/web`: rebuilt UI assets.
- GET and POST `/api/clipboard`; GET `/api/clips?day=N`; POST `/api/clips/label`, `/api/clips/delete`, `/api/clips/attach`.
- Storage: `runs/<world>/clipboard.json` and `runs/<world>/<day>/clips.jsonl`. Neither is created by an off-state read.
- Keychain service: `afterglow-clip`, with a random clip ID as account. A label is saved as the Keychain comment and local clip metadata. Raw secret text never enters clip JSONL.

## Run and check
From this worktree:

```sh
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-clipboard/app
python3 -m unittest discover -s tests
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-clipboard/app/ui
~/.bun/bin/bun run build
```

For a manual app check, copy the supplied scratch fixture first; never use real runs:

```sh
cp -R /Users/olety/.claude/jobs/527465c3/tmp/runs-8661 /tmp/clipboard-manual-runs
cd /Users/olety/Desktop/code/muse-glimmer-afterglow/.claude/worktrees/afterglow-clipboard/app
AFTERGLOW_RUNS=/tmp/clipboard-manual-runs python3 -m afterglow.web --world p-mira-holt --day 1 --port 8667 --slot 1 --no-restore --endpoint http://100.88.57.107:18610
```

Check port 8667 is free before starting. Open Settings > Connectors, turn Clipboard on, copy non-sensitive text, then inspect Files > Copied today. For secret flows use a disposable test value and remove its clip after the check. Turn Clipboard off when finished. On macOS this uses the default Keychain; Linux can capture text, but skips secret persistence and shows a capture error when macOS Keychain is unavailable.

## Assumptions and constants
- Single local app process per person's clipboard diary; no cross-process watcher coordination.
- Capture polls every 1 second, truncates stored content at 20,000 characters, and records the original character count. Classification runs before truncation.
- Prefix rules cover sk-, ghp_, github_pat_, xoxb-/xoxp-, AKIA, and AIza; JWTs, PEM blocks, password lines, and 13-19 digit Luhn-valid card numbers are secret.
- Entropy detection applies to uninterrupted tokens of at least 32 characters: 3.5 bits/character with digits or token punctuation, 4.0 for letters only. Ordinary URLs and UUIDs are excluded from entropy detection; explicit key shapes in URLs still win.
- Bare short codes and code/PIN/OTP plus 4-8 digits are not classified as secrets. Masking retains the first 2 and last 4 characters. Labels are limited to 240 characters and cannot themselves match the secret rules.
- Password assignments also cover `password=`, `passwd`, `pwd`, and quoted JSON fields; ordinary prose such as "my password is ..." is outside this shape rule.
- These are new lane constants, not changes to the app's model, tool list, or training defaults.
- The brief-mandated `security ... -w VALUE` call briefly exposes the value in the child process arguments. Command output and arguments are never logged; failures use fixed generic messages.

## Verification and integration gap
- Full suite: `cd app && python3 -m unittest discover -s tests` passed: 201 tests, 1 skipped (existing optional case). Python 3.15 emitted existing resource warnings; no failures.
- Clipboard module: 48 deterministic tests, including real watcher scheduling against fake reader/Keychain, classifier false positives, off-state no-read/no-file/no-thread, masking, truncation, label/delete, attach refusal, route errors, and day/profile switches.
- UI: `~/.bun/bin/bun run build` passed. Existing missing-font warnings remain; no new build errors. UI refreshes every 5 seconds while capture is on, with no recurring polling when off.
- Scratch HTTP app on port 8667: GET clipboard returned off, available, count 0, no pending questions. GET clips returned empty. Invalid switch/day/unknown attach requests returned 400. GET `/` served the built UI. No clipboard files appeared, and the scratch server was stopped.

**Integration gap:** The route registry has no app-start or day-change hooks. To leave `web.py` untouched, the connector initialises on the first clipboard route request (Settings or Files). A saved On setting therefore resumes only after that first request, not at process startup. Once initialised, instance-local wrappers reconcile the watcher after `open_day` and `create_profile`. Old-day captures are refused if the session changed.

Static review confirmed that night and drafter inputs use explicit daylog/notes/session filenames and do not discover `clips.jsonl`. Apple Security source review confirms that `add-generic-password -U -j` with omitted `-w` preserves an existing password: `SecurityTool/macOS/keychain_add.c` passes NULL password data to `SecKeychainItemModifyContent`, and `OSX/libsecurity_keychain/lib/Item.cpp` only replaces data when the pointer is non-NULL. Sources: https://github.com/apple-oss-distributions/Security/blob/main/SecurityTool/macOS/keychain_add.c and https://github.com/apple-oss-distributions/Security/blob/main/OSX/libsecurity_keychain/lib/Item.cpp . This was source verification, not a live Keychain test. Slow clipboard, front-app and Keychain operations do not hold the chat lock; the append rechecks the active day before writing.

No browser, real clipboard reads, real Keychain writes, model calls, or training were used for validation. Screenshots, actual OS clipboard permissions/front-app access, Linux clipboard utilities, and live Keychain behaviour remain unverified. The classifier is rules-based and cannot guarantee detection of every secret format.

Oneiron home: clipboard -> FIX 12583-12587.

Implementation pushed in signed commit `ba49ccb` to `afterglow/clipboard`.

DONE
